#!/usr/bin/env python3
"""
curate_selection.py — Gemini-based editorial selection (nahrazuje KROK 4 + 4.5,
které dosud dělal agent v Coworku).

Pipeline:
  1. Sestaví dedupe blocklist (published + digested + stale_evergreen) přes
     dedupe_helper a profiltruje dnešní inbox -> kandidáti.
  2. Načte kontext (brand.md, cilovky.md) + titulky/URL z _selected.json za
     poslední 3 dny (pro sémantický dedup).
  3. Zavolá Gemini (gemini-2.5-pro -> flash fallback, responseMimeType=JSON):
     vybere top N s kvótami a vyrobí přesné schéma selected.json.
  4. Deterministicky zvaliduje (8 polí, summary_cs = list 3 stringů, žádný
     em-dash, unikátní URL z množiny kandidátů, kategorie z whitelistu,
     why_matters <= 20 slov). Em-dash auto-opraví; strukturální chybu pošle
     Gemini zpět k opravě (1 retry).
  5. Zapíše digest/${DATE}_selected.json.

Spuštění:
  cd "AI News" && set -a && source .env && set +a
  python3 scripts/curate_selection.py [--date YYYY-MM-DD] [--top 15] \\
      [--ai-news .] [--ainamiru ../ai-na-miru]

Klíč: GEMINI_API_KEY z prostředí (NIKDY se neloguje).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dedupe_helper  # noqa: E402

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip() or \
    os.environ.get("GOOGLE_API_KEY", "").strip()

CATEGORIES = ["labs", "open-source", "tools", "business", "research", "news", "wildcard"]
EM_DASH = "—"

# Cílové kvóty (měkké) — odpovídají dosavadnímu zadání v SKILL.md.
QUOTAS = {"labs": 5, "open-source": 3, "tools": 3, "business": 2, "wildcard": 2}

# Thinking budget pro flash modely (tokeny). Vyssi = lepsi usudek, pomalejsi.
# Konfigurovatelne pres env GEMINI_THINK_BUDGET (produkce ~2048, test 0 pro rychlost).
THINK_BUDGET = int(os.environ.get("GEMINI_THINK_BUDGET", "2048"))


# ----------------------------------------------------------------------------
# Gemini
# ----------------------------------------------------------------------------
def gemini_json(prompt: str, model: str = "gemini-2.5-pro",
                temperature: float = 0.4, max_tokens: int = 16384,
                retries: int = 4) -> dict:
    """Zavolá Gemini a vrátí naparsovaný JSON dict. responseMimeType=application/json."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    import requests

    # Fallback retezec: primarni model -> levnejsi/stabilni nahrady pri vypadku.
    FALLBACKS = ["gemini-3.1-flash-lite", "gemini-2.5-flash"]
    models_to_try = [model] + [m for m in FALLBACKS if m != model]

    last_err = None
    for m in models_to_try:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{m}:generateContent?key={GEMINI_API_KEY}")
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                # Omez thinking: flash bez thinkingu je rychly; pro ma minimalni budget.
                "thinkingConfig": {"thinkingBudget": (THINK_BUDGET if "flash" in m else 4096)},
            },
        }
        for attempt in range(retries):
            try:
                r = requests.post(url, json=payload, timeout=120)
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = (2 ** attempt) * 4 + 1
                    print(f"  ↻ {m} {r.status_code}, retry za {wait}s "
                          f"({attempt + 1}/{retries})", file=sys.stderr)
                    time.sleep(wait)
                    last_err = f"HTTP {r.status_code} on {m}"
                    continue
                r.raise_for_status()
                data = r.json()
                parts = (data.get("candidates", [{}])[0]
                         .get("content", {}).get("parts", []))
                text = "".join(p.get("text", "") for p in parts).strip()
                return _loads_lenient(text)
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as e:
                wait = (2 ** attempt) * 4 + 1
                print(f"  ↻ {m} {type(e).__name__}, retry za {wait}s "
                      f"({attempt + 1}/{retries})", file=sys.stderr)
                time.sleep(wait)
                last_err = f"{type(e).__name__} on {m}"
        if m != models_to_try[-1]:
            print(f"  ⤷ {m} se nedaří, zkouším {models_to_try[-1]}", file=sys.stderr)
    raise RuntimeError(f"Gemini JSON failed: {last_err}")


def _loads_lenient(text: str) -> dict:
    """JSON parse i kdyby model obalil odpověď fenced blokem."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


# ----------------------------------------------------------------------------
# Kontext
# ----------------------------------------------------------------------------
def read_text(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    t = path.read_text(encoding="utf-8", errors="ignore")
    return t[:limit]


def recent_selected_context(ai_news_root: Path, date: str, days: int = 3) -> list[dict]:
    """Vrátí [{date, title_cs, url, category}] z _selected.json za posledních `days` dnů (mimo dnešek)."""
    out = []
    d0 = datetime.strptime(date, "%Y-%m-%d").date()
    for delta in range(1, days + 1):
        d = (d0 - timedelta(days=delta)).strftime("%Y-%m-%d")
        p = ai_news_root / "digest" / f"{d}_selected.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for a in data.get("articles", []):
            out.append({
                "date": d,
                "title_cs": a.get("title_cs", ""),
                "url": a.get("url", ""),
                "category": a.get("category", ""),
            })
    return out


# ----------------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------------
def build_prompt(candidates: list[dict], recent: list[dict],
                 brand: str, cilovky: str, top: int) -> str:
    quota_line = ", ".join(f"~{n} {c}" for c, n in QUOTAS.items())
    cats_str = ", ".join(CATEGORIES)

    cand_lines = []
    for a in candidates:
        summ = (a.get("summary_raw") or "").replace("\n", " ").strip()
        if len(summ) > 320:
            summ = summ[:320] + "…"
        cand_lines.append(json.dumps({
            "id": a.get("id", ""),
            "title": a.get("title", ""),
            "source": a.get("source_name", ""),
            "category": a.get("category", ""),
            "published_at": a.get("published_at", ""),
            "summary": summ,
        }, ensure_ascii=False))
    cand_block = "\n".join(cand_lines)

    recent_block = "\n".join(
        json.dumps({"date": r["date"], "title_cs": r["title_cs"],
                    "url": r["url"]}, ensure_ascii=False)
        for r in recent
    ) or "(žádné předchozí dny k dispozici)"

    return f"""\
Jsi zkušený český editor AI zpravodaje "AI na míru". Z dnešních kandidátů vyber
TOP {top} nejdůležitějších článků a vyrob z nich strukturovaný JSON digest.

━━━ BRAND / TÓN (zkráceně) ━━━
{brand}

━━━ CÍLOVKA (zkráceně) ━━━
{cilovky}

━━━ KVÓTY (měkké, drž se přibližně) ━━━
Přibližné rozložení {top} článků: {quota_line}. Pokud kvalita kandidátů v nějaké
kategorii chybí, dorovnej z jiné — kvalita > kvóta.

━━━ SÉMANTICKÝ DEDUP ━━━
Níže jsou články z posledních 3 dnů. NEVYBÍREJ článek, který pokrývá STEJNOU
UDÁLOST jako některý z nich (i kdyby měl jinou URL, jiný zdroj nebo follow-up
úhel). Povolený je jen skutečně NOVÝ fakt/úhel.
PŘEDCHOZÍ DNY:
{recent_block}

━━━ DNEŠNÍ KANDIDÁTI (vybírej VÝHRADNĚ podle jejich "id") ━━━
{cand_block}

━━━ VÝSTUPNÍ SCHÉMA (PŘESNĚ) ━━━
Vrať JSON objekt:
{{
  "articles": [ <{top} záznamů>, seřazené dle důležitosti (rank 1 = nejdůležitější) ]
}}
Každý záznam MUSÍ mít přesně tyto klíče:
- "rank": int 1..{top}
- "title_cs": string — český titulek (přelož/přepiš, výstižný, bez em-dash)
- "title_orig": string nebo null — původní (anglický) titulek, pokud se liší
- "id": string — MUSÍ být přesně jedno z "id" kandidátů výše. NEOPISUJ URL ani
  název, vrať jen to krátké "id". URL a zdroj doplní systém sám.
- "category": string — jedno z: {cats_str}
- "summary_cs": pole PŘESNĚ 3 stringů — české bullety, každý 1-2 věty, bez čísel
  na začátku, bez em-dash
- "why_matters": string — jedna krátká věta (MAX 20 slov), proč to je důležité
  pro českého čtenáře

TVRDÁ PRAVIDLA:
- summary_cs je vždy LIST se 3 prvky (ne string!).
- Žádný znak em-dash ({EM_DASH}) v žádném textovém poli; používej "-" nebo "–".
- Každé id je unikátní a pochází z množiny kandidátů.
- why_matters má max 20 slov.
- Vše v korektní češtině, UTF-8.
Vrať POUZE ten JSON, nic víc.
"""


# ----------------------------------------------------------------------------
# Validace (port KROK 4.5 schema check)
# ----------------------------------------------------------------------------
REQUIRED = ["rank", "title_cs", "title_orig", "url", "source",
            "category", "summary_cs", "why_matters"]


def coerce_categories(articles):
    """Neznamou kategorii deterministicky zmapuje na 'news'. Vrati pocet zasahu."""
    n = 0
    for a in articles:
        if a.get("category") not in CATEGORIES:
            a["category"] = "news"
            n += 1
    return n


def validate(articles: list[dict], cand_urls: set[str], top: int) -> list[str]:
    errs: list[str] = []
    if len(articles) != top:
        errs.append(f"počet článků = {len(articles)}, očekáváno {top}")
    seen = set()
    for i, a in enumerate(articles):
        tag = f"#{i + 1}(rank={a.get('rank')})"
        for k in REQUIRED:
            if k not in a:
                errs.append(f"{tag}: chybí pole '{k}'")
        sc = a.get("summary_cs")
        if not isinstance(sc, list):
            errs.append(f"{tag}: summary_cs není list")
        elif len(sc) != 3:
            errs.append(f"{tag}: summary_cs má {len(sc)} bullety, musí 3")
        elif not all(isinstance(x, str) for x in sc):
            errs.append(f"{tag}: summary_cs obsahuje ne-string")
        cat = a.get("category")
        if cat not in CATEGORIES:
            errs.append(f"{tag}: kategorie '{cat}' není ve whitelistu")
        url = a.get("url", "")
        if url in seen:
            errs.append(f"{tag}: duplicitní url {url}")
        seen.add(url)
        if cand_urls and url not in cand_urls:
            errs.append(f"{tag}: url není mezi kandidáty: {url}")
        wm = a.get("why_matters", "")
        if isinstance(wm, str) and len(wm.split()) > 20:
            errs.append(f"{tag}: why_matters má {len(wm.split())} slov (>20)")
    return errs


def strip_em_dash(articles: list[dict]) -> int:
    """In-place náhrada em-dash za '-'. Vrátí počet zásahů."""
    n = 0

    def fix(s):
        nonlocal n
        if isinstance(s, str) and EM_DASH in s:
            n += s.count(EM_DASH)
            return s.replace(EM_DASH, "-")
        return s

    for a in articles:
        for k in ("title_cs", "title_orig", "source", "why_matters"):
            if isinstance(a.get(k), str):
                a[k] = fix(a[k])
        if isinstance(a.get("summary_cs"), list):
            a["summary_cs"] = [fix(x) for x in a["summary_cs"]]
    return n


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: dnes CEST)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--model", default="gemini-3.5-flash",
                    help="Gemini model pro selekci (default gemini-2.5-pro; "
                         "fallbacky se zkouší automaticky)")
    ap.add_argument("--ai-news", default=".")
    ap.add_argument("--ainamiru", default="../ai-na-miru")
    ap.add_argument("--inbox", default=None, help="cesta k inbox JSON (default inbox/<date>.json)")
    args = ap.parse_args()

    date = args.date or datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d")
    ai_news = Path(args.ai_news).resolve()
    inbox_path = Path(args.inbox) if args.inbox else ai_news / "inbox" / f"{date}.json"

    if not inbox_path.exists():
        print(f"❌ Inbox neexistuje: {inbox_path}", file=sys.stderr)
        return 2
    inbox = json.loads(inbox_path.read_text(encoding="utf-8"))
    raw_articles = inbox.get("articles", [])
    print(f"[curate] inbox {date}: {len(raw_articles)} článků")

    # 1) dedupe blocklist
    try:
        bl = dedupe_helper.build_blocklist(str(ai_news), args.ainamiru, date)
        kept, blocked = dedupe_helper.filter_articles(raw_articles, bl)
        print(f"[curate] dedupe: kept={len(kept)} blocked={len(blocked)} "
              f"(published={len(bl.get('published', []))}, "
              f"digested={len(bl.get('digested', []))}, "
              f"stale={len(bl.get('stale_evergreen', []))})")
    except Exception as e:
        print(f"[curate] ⚠ dedupe_helper selhal ({e}); pokračuji bez blocklistu", file=sys.stderr)
        kept = raw_articles

    if len(kept) < args.top:
        print(f"❌ Po dedupu jen {len(kept)} kandidátů (< {args.top}). Stop.", file=sys.stderr)
        return 3

    cand_urls = {a.get("url", "") for a in kept}
    id_map = {a.get("id", ""): a for a in kept if a.get("id")}

    # 2) kontext
    brand = read_text(ai_news / "brand.md")
    cilovky = read_text(ai_news / "cilovky.md")
    recent = recent_selected_context(ai_news, date, days=3)
    print(f"[curate] kontext: brand={len(brand)}B cilovky={len(cilovky)}B "
          f"recent_titles={len(recent)}")

    # 3) Gemini selekce (+1 retry s feedbackem chyb)
    prompt = build_prompt(kept, recent, brand, cilovky, args.top)
    articles: list[dict] = []
    for round_i in range(2):
        result = gemini_json(prompt, model=args.model)
        articles = result.get("articles", [])
        # přečísluj rank pro jistotu dle pořadí
        for idx, a in enumerate(articles, 1):
            a["rank"] = idx
        # Doplň url + source deterministicky z kandidáta podle id (anti-halucinace URL).
        inject_errs = []
        for a in articles:
            cand = id_map.get(a.get("id", ""))
            if cand is None:
                inject_errs.append(f"rank{a.get('rank')}: neznámé/chybějící id '{a.get('id')}'")
                continue
            a["url"] = cand.get("url", "")
            a["source"] = cand.get("source_name", "")
            a.pop("id", None)
        fixed = strip_em_dash(articles)
        if fixed:
            print(f"[curate] em-dash auto-oprava: {fixed} zásahů")
        coerced = coerce_categories(articles)
        if coerced:
            print(f"[curate] kategorie mimo whitelist -> news: {coerced} zasahu")
        errs = inject_errs + validate(articles, cand_urls, args.top)
        if not errs:
            break
        print(f"[curate] ⚠ validace kolo {round_i + 1}: {len(errs)} chyb", file=sys.stderr)
        for e in errs[:15]:
            print(f"         - {e}", file=sys.stderr)
        if round_i == 0:
            prompt = (build_prompt(kept, recent, brand, cilovky, args.top)
                      + "\n\n━━━ OPRAV TYTO CHYBY Z PŘEDCHOZÍHO POKUSU ━━━\n"
                      + "\n".join(f"- {e}" for e in errs[:25]))
        else:
            print("❌ Validace selhala i po retry. Nezapisuji.", file=sys.stderr)
            return 4

    # 4) zápis
    out = {"date": date, "articles": articles}
    out_path = ai_news / "digest" / f"{date}_selected.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    cats = Counter(a["category"] for a in articles)
    print(f"[curate] OK zapsano {out_path}")
    print(f"[curate] kategorie: {dict(cats)}")
    print("[curate] ranky 1-3: " + " | ".join(
        f"{a['rank']}. {a['title_cs'][:60]}" for a in articles[:3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
