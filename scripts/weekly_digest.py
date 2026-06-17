#!/usr/bin/env python3
"""
weekly_digest.py — týdenní digest pro šéfa.

Z posledních N dní (default 7) denních digest/*_selected.json poskládá pool,
přes Gemini vybere TOP 20-30 nejdůležitějších/nejzajímavějších, znovu seřadí
a uloží digest/weekly_<end>.json ve stejném schématu jako denní (kvůli
generate_pdf.py). Summary se přebírají z denních výběrů (negenerují se znovu).

Spuštění:
  python3 scripts/weekly_digest.py [--ai-news .] [--end YYYY-MM-DD] \
      [--days 7] [--target 25]
Klíč: GEMINI_API_KEY z prostředí.
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from curate_selection import gemini_json   # sdílený Gemini JSON helper
import dedupe_helper

EM_DASH = "—"


def gather_pool(ai_news: Path, end: str, days: int) -> list[dict]:
    end_d = datetime.strptime(end, "%Y-%m-%d").date()
    seen, pool = set(), []
    for delta in range(0, days + 1):
        d = (end_d - timedelta(days=delta)).strftime("%Y-%m-%d")
        p = ai_news / "digest" / f"{d}_selected.json"
        if not p.exists():
            continue
        try:
            arts = json.loads(p.read_text(encoding="utf-8")).get("articles", [])
        except Exception:
            continue
        for a in arts:
            cu = dedupe_helper.canonical_url(a.get("url", ""))
            if cu and cu not in seen:
                seen.add(cu)
                a = dict(a); a["_day"] = d
                pool.append(a)
    return pool


def build_prompt(pool: list[dict], target: int) -> str:
    lines = []
    for n, a in enumerate(pool):
        lines.append(json.dumps({
            "n": n,
            "den": a.get("_day", ""),
            "title_cs": a.get("title_cs", ""),
            "source": a.get("source", ""),
            "category": a.get("category", ""),
            "why_matters": a.get("why_matters", ""),
            "summary0": (a.get("summary_cs") or [""])[0],
        }, ensure_ascii=False))
    block = "\n".join(lines)
    return f"""\
Jsi šéfredaktor. Z týdenního poolu AI novinek níže vyber TOP {target}
nejdůležitějších a zároveň nejzajímavějších pro vytíženého ředitele firmy
(strategický přehled za týden). Vybírej napříč tématy (labs, byznys, nástroje,
výzkum), vyhni se variacím téže události. Cíl je {target} položek (přijatelné
20 až 30 podle kvality).

Seřaď je od NEJdůležitější po nejméně.

KANDIDÁTI (každý má "n"):
{block}

Vrať POUZE JSON: {{"picks": [n, n, ...]}}  — hodnoty "n" zvolených článků
v pořadí důležitosti. Žádný text navíc.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai-news", default=".")
    ap.add_argument("--end", default=None, help="poslední den okna (default dnes CEST)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--target", type=int, default=25)
    ap.add_argument("--model", default="gemini-3.5-flash")
    args = ap.parse_args()

    ai_news = Path(args.ai_news).resolve()
    end = args.end or datetime.now(ZoneInfo("Europe/Prague")).strftime("%Y-%m-%d")
    pool = gather_pool(ai_news, end, args.days)
    print(f"[weekly] pool: {len(pool)} unikátních článků za {args.days} dní do {end}")
    if len(pool) < 5:
        print(f"❌ Pool příliš malý ({len(pool)}). Nejspíš chybí denní selected.json.", file=sys.stderr)
        return 3

    target = max(20, min(30, args.target))
    prompt = build_prompt(pool, target)
    res = gemini_json(prompt, model=args.model)
    picks = res.get("picks", [])
    # validace indexů
    chosen, seen = [], set()
    for n in picks:
        if isinstance(n, int) and 0 <= n < len(pool) and n not in seen:
            seen.add(n); chosen.append(pool[n])
    # ořež na rozumný rozsah
    chosen = chosen[:30]
    if len(chosen) < 15:
        print(f"⚠ Gemini vybral jen {len(chosen)}; doplňuji z poolu dle pořadí.", file=sys.stderr)
        for a in pool:
            if dedupe_helper.canonical_url(a.get("url","")) not in {dedupe_helper.canonical_url(c.get("url","")) for c in chosen}:
                chosen.append(a)
            if len(chosen) >= target:
                break

    # přečísluj + očisti pomocná pole + em-dash
    out_articles = []
    for i, a in enumerate(chosen, 1):
        a = {k: v for k, v in a.items() if not k.startswith("_")}
        a["rank"] = i
        for k in ("title_cs", "title_orig", "source", "why_matters"):
            if isinstance(a.get(k), str):
                a[k] = a[k].replace(EM_DASH, "-")
        if isinstance(a.get("summary_cs"), list):
            a["summary_cs"] = [s.replace(EM_DASH, "-") if isinstance(s, str) else s for s in a["summary_cs"]]
        out_articles.append(a)

    out = {"date": end, "articles": out_articles, "weekly": True}
    out_path = ai_news / "digest" / f"weekly_{end}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[weekly] ✓ {out_path.name}: {len(out_articles)} článků")
    from collections import Counter
    print(f"[weekly] kategorie: {dict(Counter(a['category'] for a in out_articles))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
