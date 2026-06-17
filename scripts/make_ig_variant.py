#!/usr/bin/env python3
"""
make_ig_variant.py — nahrazuje KROK 7.7 (chirurgicky prepis zargonu pro IG)
a KROK 8.5 (prepis captionu) generatoru, ktere dosud delal agent.

Dve Gemini ulohy:
  A) Z digest/<date>_selected.json udela digest/<date>_selected_ig.json:
     u PRVNICH 7 clanku chirurgicky zameni jen zargon v title_cs a
     summary_cs[0]. Bullety [1], [2], why_matters i clanky 8-15 zustavaji
     beze zmeny. Pole "date" zustava.
  B) Z _selected_ig.json napise instagram/<date>/caption.txt v pevnem
     formatu, tvrdy limit 2200 znaku (pri prekroceni iterativne zkrati).

Spusteni:
  cd "AI News" && set -a && source .env && set +a
  python3 scripts/make_ig_variant.py [--date YYYY-MM-DD] [--ai-news .] \
      [--model gemini-3.5-flash]

Klic: GEMINI_API_KEY z prostredi (NIKDY se neloguje).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from curate_selection import gemini_json  # sdileny Gemini JSON helper

EM_DASH = "—"
CAPTION_LIMIT = 2200
CAPTION_SAFE = 2100

MONTHS_CS = ["", "ledna", "února", "března", "dubna", "května", "června",
             "července", "srpna", "září", "října", "listopadu", "prosince"]

JARGON_GUIDE = """\
SLOVNIK ZAMEN (rozsiruj podle smyslu, ale jen pro NEZNAME terminy):
- Series A/B/C/D -> "investiční kolo"
- pre-money / post-money valuace -> "ocenění před/po investici"
- ARR -> "roční tržby"
- frontier model -> "špičkový model" / "nejlepší AI"
- sparse attention / difuzní jazykové modely -> "nová technika" / "pokročilé AI modely"
- agentic / agentní úlohy -> "AI úlohy"
- enterprise (IT/účty/verze) -> "firemní (IT/účty/verze)"
- troubleshooting -> "hledání chyb"; deployment -> "nasazování"; incident response -> "řešení výpadků"
- benchmark -> "test"; context window -> (vynech ze slidu)
- NPU / edge zařízení -> "čip v telefonu" / "v zařízení"; offline režim -> "bez připojení"
- open-source (v IG kontextu) -> "otevřený"
- Partner Network -> "partnerská síť"; ChatGPT Enterprise -> "firemní verze ChatGPT"
"""


# ----------------------------------------------------------------------------
# A) Jargon rewrite
# ----------------------------------------------------------------------------
def rewrite_jargon(top7: list[dict], model: str) -> dict[int, dict]:
    items = [{"rank": a["rank"], "title_cs": a["title_cs"],
              "summary0": a["summary_cs"][0]} for a in top7]
    items_json = "\n".join(json.dumps(x, ensure_ascii=False) for x in items)
    prompt = f"""\
Jsi český editor. Níže je 7 článků (title_cs + první bullet summary0) z odborného
AI digestu. IG slidy cílí na ŠIRŠÍ publikum, takže CHIRURGICKY nahraď jen konkrétní
žargonové výrazy, které běžný čtenář nezná. NIC víc.

PŘÍSNĚ ZAKÁZÁNO:
- Přepisovat celé věty od základu, měnit strukturu, styl nebo délku.
- Zjednodušovat na infantilní úroveň ("jako pro dítě").
- Lifestyle hooky typu "Víš, že...?".
- Měnit názvy firem (Anthropic, OpenAI, Nvidia, BBVA, TCS...) a JAKÁKOLIV čísla.
- Em-dash ({EM_DASH}); používej "-" nebo "–".
Délka výsledku se nesmí lišit od originálu o víc než ±20 %. Pokud věta žádný žargon
neobsahuje, vrať ji BEZE ZMĚNY.

{JARGON_GUIDE}

VSTUP (7 položek):
{items_json}

Vrať JSON: {{"items": [{{"rank": int, "title_cs": "...", "summary0": "..."}}, ... 7x]}}
Jen ten JSON.
"""
    res = gemini_json(prompt, model=model)
    out = {}
    for it in res.get("items", []):
        r = it.get("rank")
        if r is not None:
            out[int(r)] = {"title_cs": it.get("title_cs", ""),
                           "summary0": it.get("summary0", "")}
    return out


def apply_ig_rewrite(selected: dict, rewrites: dict[int, dict]) -> tuple[dict, list]:
    ig = copy.deepcopy(selected)
    warns = []
    for a in ig["articles"][:7]:
        rw = rewrites.get(a["rank"])
        if not rw:
            warns.append(f"rank{a['rank']}: chybí v rewrite odpovědi, ponechán originál")
            continue
        for field, newval, orig in (
            ("title_cs", rw["title_cs"], a["title_cs"]),
            ("summary0", rw["summary0"], a["summary_cs"][0]),
        ):
            if not newval:
                continue
            newval = newval.replace(EM_DASH, "-")
            # ochrana proti runaway délce
            if orig and not (0.6 <= len(newval) / max(len(orig), 1) <= 1.5):
                warns.append(f"rank{a['rank']} {field}: délka mimo rozsah, ponechán originál")
                continue
            if field == "title_cs":
                a["title_cs"] = newval
            else:
                a["summary_cs"][0] = newval
    return ig, warns


# ----------------------------------------------------------------------------
# B) Caption
# ----------------------------------------------------------------------------
def date_cs(date: str) -> str:
    d = datetime.strptime(date, "%Y-%m-%d")
    return f"{d.day}. {MONTHS_CS[d.month]} {d.year}"


def assemble_caption(date: str, points: list[str], hashtags: list[str]) -> str:
    head = f"AI News | {date_cs(date)}\n\nDnešní novinky:\n"
    body = "\n".join(f"\n{i}. {p.strip()}" for i, p in enumerate(points, 1))
    tags = " ".join(hashtags)
    return f"{head}{body}\n\n{tags}\n"


def normalize_hashtags(raw: list[str]) -> list[str]:
    seen, clean = set(), []
    for h in raw:
        h = h.strip()
        if not h:
            continue
        if not h.startswith("#"):
            h = "#" + h
        if h.lower() not in seen:
            seen.add(h.lower())
            clean.append(h)
    # vynut povinne
    must_start = ["#AINews", "#AI"]
    out = []
    for m in must_start:
        out.append(m)
        seen.add(m.lower())
    for h in clean:
        if h.lower() in ("#ainews", "#ai", "#ainamiru"):
            continue
        out.append(h)
    out = out[:11]  # nechej misto pro #AInaMiru -> max 12
    out.append("#AInaMiru")
    return out


def make_caption(ig_top7: list[dict], date: str, model: str) -> tuple[str, list]:
    arts = []
    for a in ig_top7:
        arts.append({"rank": a["rank"], "title_cs": a["title_cs"],
                     "summary_cs": a["summary_cs"], "why_matters": a["why_matters"]})
    arts_json = "\n".join(json.dumps(x, ensure_ascii=False) for x in arts)
    base_prompt = f"""\
Napiš text (caption) pod Instagram carousel "AI News" pro 7 článků níže.
Pro každý článek napiš PŘESNĚ 2 věty (cca 30-40 slov, MAX 280 znaků včetně čísla).
Nejde o opakování titulku - přidej kontext, který se na slide nevejde (historie,
business dopad, srovnání s konkurencí), ale stručně.

PRAVIDLA:
- Čeština, aktivní hlas, konkrétní čísla a názvy firem zachovej.
- Žádný úvodní odstavec, žádné "Celý digest jako PDF", žádné emoji v bodech.
- Žádný em-dash ({EM_DASH}); používej "-" nebo "–".
- Žádný žargon (text už je pro širší publikum).

K tomu navrhni 3 až 6 specifických hashtagů podle dnešního obsahu (firmy, témata),
BEZ #AINews/#AI/#AInaMiru (ty doplní systém). Bez mezer v hashtazích.

ČLÁNKY:
{arts_json}

Vrať JSON: {{"points": ["bod 1", ... 7 bodů], "hashtags": ["#X", ...]}}
Jen ten JSON.
"""
    notes = []
    prompt = base_prompt
    for attempt in range(3):
        res = gemini_json(prompt, model=model)
        points = [re.sub(r"^\s*\d+[\.\)]\s*", "", p.replace(EM_DASH, "-").strip()) for p in res.get("points", [])][:7]
        hashtags = normalize_hashtags(res.get("hashtags", []))
        if len(points) != 7:
            notes.append(f"pokus {attempt+1}: dostal jsem {len(points)} bodů místo 7")
            prompt = base_prompt + "\n\nPŘEDCHOZÍ POKUS VRÁTIL ŠPATNÝ POČET BODŮ. Vrať PŘESNĚ 7."
            continue
        caption = assemble_caption(date, points, hashtags)
        n = len(caption)
        notes.append(f"pokus {attempt+1}: {n} znaků")
        if n <= CAPTION_LIMIT:
            return caption, notes
        # přes limit -> požádej o zkrácení
        prompt = (base_prompt + f"\n\nPŘEDCHOZÍ VERZE MĚLA {n} znaků, LIMIT JE "
                  f"{CAPTION_LIMIT}. Zkrať každý bod na max 1-2 kratší věty "
                  f"(cíl {CAPTION_SAFE} znaků celkem). Zachovej čísla a firmy.")
    # i po 3 pokusech přes limit -> tvrdě ořízni body (fallback, ať to neblokuje)
    points = [p[:240].rstrip() for p in points]
    caption = assemble_caption(date, points, hashtags)
    notes.append(f"fallback tvrdé oříznutí -> {len(caption)} znaků")
    return caption, notes


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--ai-news", default=".")
    ap.add_argument("--model", default="gemini-3.5-flash")
    ap.add_argument("--mode", choices=["both", "ig", "caption"], default="both",
                    help="ig=jen _selected_ig.json, caption=jen caption.txt, both=oboje")
    args = ap.parse_args()

    date = args.date or datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d")
    ai_news = Path(args.ai_news).resolve()
    sel_path = ai_news / "digest" / f"{date}_selected.json"
    ig_path = ai_news / "digest" / f"{date}_selected_ig.json"

    if not sel_path.exists():
        print(f"❌ selected.json neexistuje: {sel_path}", file=sys.stderr)
        return 2
    selected = json.loads(sel_path.read_text(encoding="utf-8"))
    if len(selected.get("articles", [])) < 7:
        print("❌ Méně než 7 článků, IG variant nelze udělat.", file=sys.stderr)
        return 3

    # A) jargon rewrite -> _selected_ig.json
    if args.mode in ("both", "ig"):
        top7 = selected["articles"][:7]
        print("[ig] rewrite žargonu pro 7 článků...")
        rewrites = rewrite_jargon(top7, args.model)
        ig, warns = apply_ig_rewrite(selected, rewrites)
        for w in warns:
            print(f"[ig]   ⚠ {w}")
        ig_path.write_text(json.dumps(ig, ensure_ascii=False, indent=2), encoding="utf-8")
        changed = sum(1 for s, i in zip(top7, ig["articles"][:7])
                      if s["title_cs"] != i["title_cs"] or s["summary_cs"][0] != i["summary_cs"][0])
        print(f"[ig] ✓ {ig_path.name} ({changed}/7 článků upraveno)")

    # B) caption -> instagram/<date>/caption.txt (z _ig pokud existuje, jinak ze selected)
    if args.mode in ("both", "caption"):
        src = ig_path if ig_path.exists() else sel_path
        ig_data = json.loads(src.read_text(encoding="utf-8"))
        print(f"[ig] píšu caption (zdroj: {src.name})...")
        caption, notes = make_caption(ig_data["articles"][:7], date, args.model)
        for no in notes:
            print(f"[ig]   {no}")
        cap_dir = ai_news / "instagram" / date
        cap_dir.mkdir(parents=True, exist_ok=True)
        cap_path = cap_dir / "caption.txt"
        cap_path.write_text(caption, encoding="utf-8")
        n = len(caption)
        status = "OK" if n <= CAPTION_LIMIT else "PŘES LIMIT"
        print(f"[ig] ✓ {cap_path} — {n}/{CAPTION_LIMIT} znaků [{status}]")
        if n > CAPTION_LIMIT:
            return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
