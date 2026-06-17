#!/usr/bin/env python3
"""
sync-ai-news.py — Inkrementální synchronizace AI Novinek z ai-na-miru do CBRE marketplace.

Default mode (incremental):
  - Načte existující aiNews.ts (pokud existuje) a parsuje seznam slugů.
  - Najde nové články z ai-na-miru/articles/index.json.
  - Přidá pouze ty, které v aiNews.ts ještě nejsou (dedup by slug).
  - Pro každý nový článek zkopíruje hero.webp do public/ai-news/<slug>/hero.webp.

Reset mode (--reset):
  - Vymaže existující aiNews.ts a hero obrázky.
  - Načte nejnovější N dní (default --days 1).
  - Použít jen ručně, pro one-shot vyčištění.

Použití:
  python3 sync-ai-news.py                    # incremental, jen dnešek
  python3 sync-ai-news.py --reset            # one-shot reset, jen dnešek
  python3 sync-ai-news.py --reset --days 1   # one-shot reset, posledních 1 den
  python3 sync-ai-news.py --reset --days 7   # last 7 days reset
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup, NavigableString, Tag


CATEGORY_MAP = {
    "vyvoj-ai":     {"id": "vyvoj-ai",     "label_cs": "Vývoj AI",     "label_en": "AI Development"},
    "ai-ekonomika": {"id": "ai-ekonomika", "label_cs": "AI ekonomika", "label_en": "AI Economy"},
    "chatgpt":      {"id": "chatgpt",      "label_cs": "ChatGPT",      "label_en": "ChatGPT"},
    "claude":       {"id": "claude",       "label_cs": "Claude",       "label_en": "Claude"},
    "gemini":       {"id": "gemini",       "label_cs": "Gemini",       "label_en": "Gemini"},
    "copilot":      {"id": "copilot",      "label_cs": "Copilot",      "label_en": "Copilot"},
    "dalsi":        {"id": "dalsi",        "label_cs": "Další",        "label_en": "Other"},
}


def ts_string(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def parse_article_html(html_path: Path) -> list[dict]:
    if not html_path.exists():
        return []
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    body = soup.find("div", class_="a-body")
    if not body:
        return []
    blocks = []
    for el in body.children:
        if isinstance(el, NavigableString):
            continue
        if not isinstance(el, Tag):
            continue
        name = el.name.lower()
        if name == "h2":
            blocks.append({"type": "heading", "level": 2, "text": el.get_text(strip=True)})
        elif name == "h3":
            blocks.append({"type": "heading", "level": 3, "text": el.get_text(strip=True)})
        elif name == "p":
            text = el.get_text(" ", strip=True)
            if text:
                blocks.append({"type": "paragraph", "text": text})
        elif name in ("ul", "ol"):
            items = [li.get_text(" ", strip=True) for li in el.find_all("li", recursive=False)]
            items = [x for x in items if x]
            if items:
                blocks.append({"type": "list", "ordered": name == "ol", "items": items})
    return blocks


# CBRE-specifická post-processing transformace.
# Volá se na blocks po parsu HTML, PŘED zápisem do TS.
# ai-na-miru zdroj zůstává netknutý (jen čteme).
import re as _re_cbre
def cbre_transform_blocks(blocks: list[dict]) -> list[dict]:
    """Odřízne závěrečnou sekci 'Co to znamená pro vaši/vaše firmu/podnikání...'."""
    if not blocks:
        return blocks

    # Hledá H2 nadpis začínající 'co to znamená pro' (case-insensitive)
    # a vše OD něj dál (heading + následující bloky) odřízne.
    pattern = _re_cbre.compile(r"^co to znamená", _re_cbre.IGNORECASE)

    cut_index = None
    for i, b in enumerate(blocks):
        if b.get("type") == "heading" and b.get("level") == 2:
            text = (b.get("text") or "").strip()
            if pattern.match(text):
                cut_index = i
                break

    if cut_index is not None:
        return blocks[:cut_index]
    return blocks


def block_to_ts(block: dict, indent: str = "    ") -> str:
    t = block["type"]
    if t == "heading":
        return f"{indent}{{ type: 'heading', level: {block['level']}, text: {ts_string(block['text'])} }}"
    if t == "paragraph":
        return f"{indent}{{ type: 'paragraph', text: {ts_string(block['text'])} }}"
    if t == "list":
        items_ts = ", ".join(ts_string(i) for i in block["items"])
        ordered = "true" if block.get("ordered") else "false"
        return f"{indent}{{ type: 'list', ordered: {ordered}, items: [{items_ts}] }}"
    return f"{indent}/* unknown */"


def reading_minutes(blocks: list[dict]) -> int:
    word_count = 0
    for b in blocks:
        if b["type"] == "paragraph":
            word_count += len(b["text"].split())
        elif b["type"] == "list":
            word_count += sum(len(i.split()) for i in b["items"])
        elif b["type"] == "heading":
            word_count += len(b["text"].split())
    return max(1, round(word_count / 200))


def category_id(cat: str) -> str:
    return CATEGORY_MAP.get(cat, CATEGORY_MAP["dalsi"])["id"]


def category_label(cat: str) -> str:
    return CATEGORY_MAP.get(cat, CATEGORY_MAP["dalsi"])["label_cs"]


def parse_existing_articles(ts_path: Path) -> list[dict]:
    """Naivní parser existujícího aiNews.ts pro získání slugů + raw obsahu mezi articles.

    SAFETY: pokud soubor existuje a je velký (>1KB), ale parser z něj nedostane žádné
    slugy, vyhodí výjimku místo aby vrátil prázdný seznam. Tím se zabrání tomu, aby
    INCREMENTAL sync přepsal celý soubor jen 7 novými články (regression z 2026-05-17).
    """
    if not ts_path.exists():
        return []
    src = ts_path.read_text(encoding="utf-8")

    # Najdi blok s `aiNewsArticles` array obsahem
    m = re.search(r"export const aiNewsArticles[^=]*=\s*\[(.*?)^\];\s*$", src, re.S | re.M)
    if not m:
        # SAFETY: soubor je netriviální, ale strukturu jsme nenašli — to není OK
        if len(src) > 1024:
            raise RuntimeError(
                f"parse_existing_articles: nenalezl jsem aiNewsArticles array v {ts_path} "
                f"(soubor má {len(src)} bytů). NEPŘEPISUJU — oprav ručně."
            )
        return []
    body = m.group(1)

    # Slug seznam — akceptuj jak single-quoted tak double-quoted (translate skript
    # používá double-quotes přes JSON.stringify, sync skript single-quotes).
    slugs = re.findall(r"""slug:\s*['"]([^'"]+)['"]""", body)

    # Pro každý článek extrahuj jeho TS literál (hrubě — od `{` po vyvážený `}`).
    # Používáme jednoduchý balanced-brace tracker.
    articles = []
    i = 0
    while i < len(body):
        if body[i] == '{':
            depth = 1
            start = i
            i += 1
            in_str = False
            str_char = None
            while i < len(body) and depth > 0:
                ch = body[i]
                if in_str:
                    if ch == '\\' and i + 1 < len(body):
                        i += 2
                        continue
                    if ch == str_char:
                        in_str = False
                elif ch in ("'", '"', '`'):
                    in_str = True
                    str_char = ch
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                i += 1
            articles.append(body[start:i])
        else:
            i += 1

    if not slugs:
        # SAFETY: soubor má aiNewsArticles array, ale 0 slugů — pravděpodobně poškozený
        if len(src) > 1024:
            raise RuntimeError(
                f"parse_existing_articles: 0 slugů v {ts_path} (soubor má {len(src)} bytů). "
                f"NEPŘEPISUJU — oprav ručně."
            )
        return []
    if len(slugs) != len(articles):
        raise RuntimeError(
            f"parse_existing_articles: mismatch — {len(slugs)} slugů vs {len(articles)} "
            f"článků v {ts_path}. NEPŘEPISUJU."
        )
    return list(zip(slugs, articles))


def article_to_ts_literal(a: dict) -> str:
    content_ts = ",\n".join(block_to_ts(b) for b in a["content"])
    if not content_ts:
        content_ts = "    /* žádný obsah */"
    hero_line = f"\n    heroImage: {ts_string(a['hero_image'])}," if a.get("hero_image") else ""
    summary_line = f"\n    summary: {ts_string(a['summary'])}," if a.get("summary") else ""
    return f"""{{
    slug: {ts_string(a['slug'])},
    title: {ts_string(a['title'])},
    excerpt: {ts_string(a['excerpt'])},
    lead: {ts_string(a.get('lead') or a['excerpt'])},
    category: {ts_string(a['category_id'])},
    categoryLabel: {ts_string(a['category_label'])},
    source: {ts_string(a['source'])},
    sourceUrl: {ts_string(a['source_url'])},
    publishedAt: {ts_string(a['date'])},
    readingMinutes: {a['reading_minutes']},{hero_line}{summary_line}
    content: [
{content_ts}
    ],
  }}"""


def build_ts_file(article_literals: list[str]) -> str:
    cat_objects = ",\n".join(
        f"  {{ id: {ts_string(v['id'])}, label: {{ cs: {ts_string(v['label_cs'])}, en: {ts_string(v['label_en'])} }} }}"
        for v in CATEGORY_MAP.values()
    )
    articles_block = ",\n  ".join(article_literals) if article_literals else "  /* žádné články */"
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return f"""/**
 * AI Novinky — auto-generováno z ai-na-miru/articles/.
 *
 * NEEDITUJ RUČNĚ. Soubor přepíše daily Cowork scheduled task
 * (scripts/sync-ai-news.py) inkrementálně — vždy přidá nové články,
 * existující nechá netknuté.
 *
 * Generated: {generated_at}
 * Articles: {len(article_literals)}
 */

import type {{ Localized }} from '@/utils/localized';

export type AiNewsCategoryId = {' | '.join(ts_string(v['id']) for v in CATEGORY_MAP.values())};

export interface AiNewsCategory {{
  id: AiNewsCategoryId;
  label: Localized<string>;
}}

export type AiNewsContentBlock =
  | {{ type: 'heading'; level: 2 | 3; text: string }}
  | {{ type: 'paragraph'; text: string }}
  | {{ type: 'list'; ordered: boolean; items: string[] }};

export interface AiNewsArticle {{
  slug: string;
  title: Localized<string>;
  excerpt: Localized<string>;
  lead: Localized<string>;
  category: AiNewsCategoryId;
  categoryLabel: string;
  source: string;
  sourceUrl: string;
  publishedAt: string;
  readingMinutes: number;
  /** Volitelná cesta k hero obrázku (relativně k webroot, např. '/ai-news/<slug>/hero.webp'). */
  heroImage?: string;
  /** Krátké shrnutí pro 'Ve zkratce' box (z index.json excerpt). */
  summary?: Localized<string>;
  content: Localized<AiNewsContentBlock[]>;
}}

export const aiNewsCategories: AiNewsCategory[] = [
{cat_objects},
];

export const aiNewsArticles: AiNewsArticle[] = [
  {articles_block},
];
"""


def copy_hero(ainamiru: Path, slug: str, marketplace_public: Path) -> str | None:
    """Zkopíruje hero.webp do public/ai-news/<slug>/. Vrátí relativní URL (od webroot) nebo None."""
    src = ainamiru / "articles" / slug / "hero.webp"
    if not src.exists():
        return None
    dst_dir = marketplace_public / "ai-news" / slug
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "hero.webp"
    shutil.copy2(src, dst)
    return f"/ai-news/{slug}/hero.webp"


def _autodetect_mnt():
    """Sandbox ID se mezi sessions mění, hledáme /sessions/*/mnt dynamicky.
    Některé entries patří jiným sandboxům — bez práva čtení je tiše přeskočíme."""
    sessions = Path("/sessions")
    if not sessions.exists():
        return None
    try:
        entries = sorted(sessions.iterdir())
    except PermissionError:
        return None
    for entry in entries:
        candidate = entry / "mnt"
        try:
            if candidate.is_dir() and any(candidate.iterdir()):
                return candidate
        except PermissionError:
            continue
    return None


def main():
    auto_mnt = _autodetect_mnt()
    default_ainamiru = str(auto_mnt / "ai-na-miru") if auto_mnt else ""
    default_marketplace = str(auto_mnt / "CBRE (1)") if auto_mnt else ""

    parser = argparse.ArgumentParser()
    parser.add_argument("--ainamiru", default=default_ainamiru)
    parser.add_argument("--marketplace", default=default_marketplace)
    parser.add_argument("--reset", action="store_true", help="Vymaže existující články a obrázky, regeneruje od nuly.")
    parser.add_argument("--days", type=int, default=1,
                        help="V reset módu: kolik posledních dní zahrnout (default 1 = jen dnes/nejnovější).")
    args = parser.parse_args()

    ainamiru = Path(args.ainamiru)
    marketplace = Path(args.marketplace)
    out_path = marketplace / "src" / "data" / "aiNews.ts"
    public_dir = marketplace / "public"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    index_path = ainamiru / "articles" / "index.json"
    if not index_path.exists():
        print(f"WARN: {index_path} neexistuje — nelze sync.", file=sys.stderr)
        sys.exit(0)

    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    raw_articles = index_data.get("articles", [])

    # ---------- RESET mód ----------
    if args.reset:
        print(f"[sync-ai-news] RESET — vymazávám hero obrázky + článečky, načítám posledních {args.days} dní")
        # Vymaž obrázky
        ai_news_dir = public_dir / "ai-news"
        if ai_news_dir.exists():
            try:
                shutil.rmtree(ai_news_dir)
                print(f"  ✓ Vymazán {ai_news_dir}")
            except (OSError, PermissionError) as e:
                # OneDrive občas nepovoluje rmtree — jen přepíšeme jednotlivé hero soubory
                print(f"  ⚠ rmtree selhal ({e.__class__.__name__}: {str(e)[:80]}), pokračuji s overwrite")

        # Najdi unikátní data, vezmi posledních N
        all_dates = sorted({a["date"] for a in raw_articles}, reverse=True)
        target_dates = set(all_dates[:args.days])
        target_articles = [a for a in raw_articles if a["date"] in target_dates]
        print(f"  Cílové dny: {sorted(target_dates, reverse=True)}, články: {len(target_articles)}")

        article_literals = []
        for entry in target_articles:
            slug = entry["slug"]
            html_path = ainamiru / "articles" / slug / "index.html"
            content_blocks = parse_article_html(html_path)
            content_blocks = cbre_transform_blocks(content_blocks)
            hero_url = copy_hero(ainamiru, slug, public_dir)
            article = {
                "slug": slug,
                "date": entry["date"],
                "title": entry["title"],
                "excerpt": entry.get("excerpt", ""),
                "lead": entry.get("lead", ""),
                "source": entry.get("source", "Unknown"),
                "source_url": entry.get("source_url", ""),
                "category_id": category_id(entry.get("category", "dalsi")),
                "category_label": category_label(entry.get("category", "dalsi")),
                "reading_minutes": reading_minutes(content_blocks),
                "hero_image": hero_url,
                "summary": entry.get("excerpt", ""),
                "content": content_blocks,
            }
            article_literals.append(article_to_ts_literal(article))
            print(f"  ✓ {slug} ({len(content_blocks)} blocks, hero={'yes' if hero_url else 'NO'})")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(build_ts_file(article_literals), encoding="utf-8")
        print(f"[sync-ai-news] RESET hotov. Wrote {out_path} ({len(article_literals)} articles)")
        return

    # ---------- INCREMENTAL mód ----------
    existing = parse_existing_articles(out_path)
    existing_slugs = {slug for slug, _ in existing}
    print(f"[sync-ai-news] Incremental — existujících: {len(existing_slugs)}")

    today_articles = [a for a in raw_articles if a["date"] == today]
    print(f"  Dnešních článků v ai-na-miru: {len(today_articles)}")

    new_articles = [a for a in today_articles if a["slug"] not in existing_slugs]
    if not new_articles:
        print(f"[sync-ai-news] Bez změn — žádný nový článek.")
        return

    print(f"  Nové články k přidání: {len(new_articles)}")

    new_literals = []
    for entry in new_articles:
        slug = entry["slug"]
        html_path = ainamiru / "articles" / slug / "index.html"
        content_blocks = parse_article_html(html_path)
        content_blocks = cbre_transform_blocks(content_blocks)
        hero_url = copy_hero(ainamiru, slug, public_dir)
        article = {
            "slug": slug,
            "date": entry["date"],
            "title": entry["title"],
            "excerpt": entry.get("excerpt", ""),
            "lead": entry.get("lead", ""),
            "source": entry.get("source", "Unknown"),
            "source_url": entry.get("source_url", ""),
            "category_id": category_id(entry.get("category", "dalsi")),
            "category_label": category_label(entry.get("category", "dalsi")),
            "reading_minutes": reading_minutes(content_blocks),
            "hero_image": hero_url,
            "summary": entry.get("excerpt", ""),
            "content": content_blocks,
        }
        new_literals.append(article_to_ts_literal(article))
        print(f"  + {slug}")

    # Sjednoť: nové články nahoru (newest first), pak existující
    all_literals = new_literals + [a for _, a in existing]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_ts_file(all_literals), encoding="utf-8")
    print(f"[sync-ai-news] Hotovo. Wrote {out_path} ({len(all_literals)} articles, +{len(new_literals)} new)")


if __name__ == "__main__":
    main()
