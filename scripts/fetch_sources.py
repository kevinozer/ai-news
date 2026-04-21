#!/usr/bin/env python3
"""
fetch_sources.py — Denní fetch všech AI news zdrojů pro AI News bota.

Čte interní seznam SOURCES, pro každý zdroj zavolá odpovídající fetcher
(RSS / HN Algolia / Reddit / HTML list scrape), normalizuje výstup do
jednotné JSON struktury, deduplikuje podle URL+titulku, a uloží do
../inbox/YYYY-MM-DD.json.

Design:
- Výpadek jednoho zdroje nezabije celý run (try/except na úrovni zdroje).
- Každý článek má stabilní ID (hash z canonical URL + titulku).
- Články starší než MAX_AGE_HOURS jsou odfiltrovány (per-source override
  přes klíč 'max_age_hours' v SOURCES).
- Per-source filtry: 'keyword_filter' (AI keywords only), 'max_items'
  (limit počtu článků).
- Statistiky per-zdroj (ok/error + počet + filter diff) v výstupu.

Spuštění:
    python fetch_sources.py

Výstup:
    ../inbox/YYYY-MM-DD.json     (hlavní výstup)
    ./logs/fetch_YYYY-MM-DD.log  (log soubor)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser
import requests

# ---------- konfigurace ----------

ROOT = Path(__file__).resolve().parent.parent
INBOX_DIR = ROOT / "inbox"
LOG_DIR = Path(__file__).resolve().parent / "logs"

# Defaultní okno. Low-freq zdroje (labs, newslettery, komentátoři) mají
# v SOURCES nastaveno max_age_hours=168 (7 dní).
MAX_AGE_HOURS = 48

HTTP_TIMEOUT = 30
USER_AGENT = "AI-News-Bot/1.0 (+https://ainamiru.cz)"

# Parametry, které znamenají tracking (vyházíme při kanonizaci URL)
TRACKING_PARAM_PREFIXES = ("utm_", "ref_", "fbclid", "gclid", "mc_", "_hs")
TRACKING_PARAM_EXACT = {"ref", "source", "share", "fbclid", "gclid"}

# AI keyword regex — slouží k filtrování obecných zdrojů (CzechCrunch,
# Lupa.cz), které nemají dedikovanou AI kategorii. Slovní hranice \b
# zabrání matchování 'ai' uvnitř slov jako 'chair' nebo 'pain'.
AI_KEYWORD_RE = re.compile(
    r"\b(?:ai|a\.i\.|gpt|chatgpt|claude|gemini|llm|openai|anthropic|"
    r"deepmind|hugging\s*face|huggingface|llama|copilot|mistral|"
    r"transformer|midjourney|dall[-·]?e|stable\s+diffusion|nvidia|"
    r"machine\s+learning|deep\s+learning|neural\s+network|agentic)\b"
    r"|um[eě]l[aáéou]\s+inteligenc"
    r"|um[eě]l[éo]u\s+inteligenci"
    r"|strojov[eé]\s+u[cč]en"
    r"|neuronov[aáéy]\s+s[ií]t"
    r"|generativn[ií]",
    re.IGNORECASE,
)

# ---------- definice zdrojů ----------
#
# Per-source klíče:
#   type: fetcher typ — 'rss' | 'hn_algolia' | 'reddit' | 'html_list'
#   url: hlavní URL / subreddit / search URL
#   max_age_hours: override pro filtr stáří článku (default 48)
#   max_items: tvrdý limit počtu článků po fetchi (pro arxiv, reddit)
#   keyword_filter: True → filtruje články podle AI_KEYWORD_RE
#   url_must_contain: (jen html_list) substring, který musí být v hrefu
#

SOURCES: list[dict[str, Any]] = [
    # PRIMARY NEWS (6) — většinou denní pulse, default 48h stačí
    {"id": "techcrunch_ai", "category": "news", "name": "TechCrunch AI",
     "type": "rss", "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
    {"id": "verge_ai", "category": "news", "name": "The Verge AI",
     "type": "rss", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    {"id": "arstechnica", "category": "news", "name": "Ars Technica",
     "type": "rss", "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"id": "mit_tech_review", "category": "news", "name": "MIT Technology Review",
     "type": "rss", "url": "https://www.technologyreview.com/feed/"},
    {"id": "wired_ai", "category": "news", "name": "Wired AI",
     "type": "rss", "url": "https://www.wired.com/feed/tag/ai/latest/rss"},
    {"id": "venturebeat_ai", "category": "news", "name": "VentureBeat AI",
     "type": "rss", "url": "https://feeds.feedburner.com/venturebeat/SZYF",
     "max_age_hours": 168, "keyword_filter": True},

    # OFFICIAL LABS (6) — low freq, blogy publikují týdně/méně → 7 dní
    {"id": "openai_blog", "category": "labs", "name": "OpenAI Blog",
     "type": "rss", "url": "https://openai.com/news/rss.xml",
     "max_age_hours": 168},
    {"id": "anthropic_news", "category": "labs", "name": "Anthropic News",
     "type": "html_list", "url": "https://www.anthropic.com/news",
     "url_must_contain": "/news/", "max_items": 20, "max_age_hours": 168},
    {"id": "deepmind_blog", "category": "labs", "name": "Google DeepMind Blog",
     "type": "rss", "url": "https://deepmind.google/blog/rss.xml",
     "max_age_hours": 168},
    # NOTE: Meta AI Blog vyřazen (2026-04-21) — ai.meta.com/blog nemá RSS ani
    # scrape-friendly HTML. Meta oznámení stejně pokrývá TechCrunch / Verge / HN.
    {"id": "microsoft_ai", "category": "labs", "name": "Microsoft AI Blog",
     "type": "rss", "url": "https://blogs.microsoft.com/ai/feed/",
     "max_age_hours": 168},
    {"id": "huggingface_blog", "category": "labs", "name": "Hugging Face Blog",
     "type": "rss", "url": "https://huggingface.co/blog/feed.xml",
     "max_age_hours": 168},

    # NEWSLETTERS (6) — weekly, 7 dní
    {"id": "import_ai", "category": "newsletter", "name": "Import AI (Jack Clark)",
     "type": "rss", "url": "https://importai.substack.com/feed",
     "max_age_hours": 168},
    # NOTE: The Batch (deeplearning.ai) vyřazen (2026-04-21) — oficiální RSS
    # neexistuje, URL /rss/ ani /feed/ nevrací XML.
    {"id": "tldr_ai", "category": "newsletter", "name": "TLDR AI",
     "type": "rss", "url": "https://tldr.tech/api/rss/ai"},
    {"id": "bens_bites", "category": "newsletter", "name": "Ben's Bites",
     "type": "rss", "url": "https://www.bensbites.com/feed",
     "max_age_hours": 168},
    {"id": "last_week_ai", "category": "newsletter", "name": "Last Week in AI",
     "type": "rss", "url": "https://lastweekin.ai/feed",
     "max_age_hours": 168},
    {"id": "one_useful_thing", "category": "newsletter", "name": "One Useful Thing (Mollick)",
     "type": "rss", "url": "https://www.oneusefulthing.org/feed",
     "max_age_hours": 168},

    # COMMENTARY (6) — většina weekly, 7 dní
    {"id": "simonw", "category": "commentary", "name": "Simon Willison's Weblog",
     "type": "rss", "url": "https://simonwillison.net/atom/everything/"},
    {"id": "latent_space", "category": "commentary", "name": "Latent Space",
     "type": "rss", "url": "https://www.latent.space/feed",
     "max_age_hours": 168},
    {"id": "marcus_on_ai", "category": "commentary", "name": "Marcus on AI",
     "type": "rss", "url": "https://garymarcus.substack.com/feed",
     "max_age_hours": 168},
    {"id": "dwarkesh", "category": "commentary", "name": "Dwarkesh Patel",
     "type": "rss", "url": "https://www.dwarkesh.com/feed",
     "max_age_hours": 168},
    {"id": "zvi_vase", "category": "commentary", "name": "Don't Worry About the Vase (Zvi)",
     "type": "rss", "url": "https://thezvi.substack.com/feed",
     "max_age_hours": 168},
    {"id": "interconnects", "category": "commentary", "name": "Interconnects (Nathan Lambert)",
     "type": "rss", "url": "https://www.interconnects.ai/feed",
     "max_age_hours": 168},

    # RESEARCH (2) — arxiv firehose, tvrdý limit 15 per feed
    {"id": "arxiv_cs_ai", "category": "research", "name": "ArXiv cs.AI",
     "type": "rss", "url": "http://export.arxiv.org/rss/cs.AI", "max_items": 15},
    {"id": "arxiv_cs_lg", "category": "research", "name": "ArXiv cs.LG",
     "type": "rss", "url": "http://export.arxiv.org/rss/cs.LG", "max_items": 15},

    # COMMUNITY (2) — HN: search_by_date + points>50. Reddit: top 15 by score.
    {"id": "hackernews_ai", "category": "community", "name": "Hacker News (AI filter)",
     "type": "hn_algolia",
     "url": "https://hn.algolia.com/api/v1/search_by_date?"
            "query=AI+OR+LLM+OR+GPT+OR+Claude+OR+Gemini+OR+OpenAI+OR+Anthropic"
            "&tags=story&numericFilters=points%3E20&hitsPerPage=50"},
    {"id": "reddit_ml", "category": "community", "name": "Reddit (ML + LocalLLaMA)",
     "type": "reddit", "url": "MachineLearning+LocalLLaMA", "max_items": 15},

    # CZECH (2) — RSS obsahuje i non-AI; aplikujeme AI keyword filter
    {"id": "lupa_cz", "category": "cz", "name": "Lupa.cz",
     "type": "rss", "url": "https://www.lupa.cz/rss/clanky/",
     "keyword_filter": True},
    {"id": "czechcrunch", "category": "cz", "name": "CzechCrunch",
     "type": "rss", "url": "https://www.czechcrunch.cz/feed/",
     "keyword_filter": True},
]

# ---------- pomocné funkce ----------


def canonical_url(url: str) -> str:
    """Strip tracking parametrů a fragmentu z URL pro stabilní dedup."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        filtered = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if not k.lower().startswith(TRACKING_PARAM_PREFIXES)
            and k.lower() not in TRACKING_PARAM_EXACT
        ]
        return urlunparse(parsed._replace(query=urlencode(filtered), fragment=""))
    except Exception:
        return url


def article_hash(url: str, title: str) -> str:
    """Stabilní krátký hash pro identifikaci článku napříč zdroji."""
    base = (canonical_url(url) + "|" + (title or "").strip().lower()).encode("utf-8")
    return hashlib.sha1(base).hexdigest()[:16]


HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def clean_text(html: str | None) -> str:
    """Odstraní HTML tagy a normalizuje whitespace."""
    if not html:
        return ""
    text = HTML_TAG_RE.sub(" ", html)
    return WHITESPACE_RE.sub(" ", text).strip()


def parse_entry_datetime(entry: Any) -> datetime | None:
    """Pokusí se z feedparser entry vytáhnout datetime v UTC."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        val = entry.get(key)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def extract_hero_image(entry: Any) -> str | None:
    """Z feedparser entry vyzobne URL hlavního obrázku, pokud existuje."""
    # media:content
    media_content = entry.get("media_content")
    if media_content:
        for m in media_content:
            url = m.get("url")
            if url:
                return url

    # media:thumbnail
    media_thumb = entry.get("media_thumbnail")
    if media_thumb:
        for m in media_thumb:
            url = m.get("url")
            if url:
                return url

    # enclosures
    enclosures = entry.get("enclosures") or []
    for enc in enclosures:
        etype = enc.get("type", "")
        if etype.startswith("image"):
            return enc.get("href") or enc.get("url")

    # fallback: parsujeme první <img> ze summary/content
    html = entry.get("summary") or ""
    if "content" in entry and entry.content:
        html = (entry.content[0].get("value") or "") + html
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    if match:
        return match.group(1)

    return None


def get_cutoff(source: dict[str, Any]) -> datetime:
    """Per-source cutoff datetime podle max_age_hours override."""
    hours = source.get("max_age_hours", MAX_AGE_HOURS)
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def article_matches_ai(article: dict[str, Any]) -> bool:
    """Check zda článek obsahuje AI keyword v title nebo summary."""
    haystack = (article.get("title") or "") + " " + (article.get("summary_raw") or "")
    return bool(AI_KEYWORD_RE.search(haystack))


# ---------- fetchers ----------


def fetch_rss(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Generický RSS/Atom fetcher."""
    url = source["url"]
    logging.info(f"[{source['id']}] RSS fetch {url}")

    feed = feedparser.parse(
        url,
        agent=USER_AGENT,
        request_headers={
            "Accept": "application/rss+xml, application/atom+xml, "
                      "application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
        },
    )

    if feed.bozo and not feed.entries:
        raise RuntimeError(
            f"Feed parse error: {feed.get('bozo_exception', 'unknown parse error')}"
        )

    cutoff = get_cutoff(source)
    articles = []
    for entry in feed.entries:
        dt = parse_entry_datetime(entry)
        if dt and dt < cutoff:
            continue

        title = clean_text(entry.get("title", ""))
        link = entry.get("link", "") or ""
        summary_html = entry.get("summary") or entry.get("description") or ""
        # Některé Atom feedy mají plný obsah jen v content
        if not summary_html and entry.get("content"):
            summary_html = entry.content[0].get("value") or ""

        summary = clean_text(summary_html)
        if len(summary) > 2000:
            summary = summary[:2000] + "…"

        author = ""
        if entry.get("author"):
            author = entry["author"]
        elif entry.get("authors"):
            author = ", ".join(a.get("name", "") for a in entry["authors"] if a.get("name"))

        articles.append({
            "id": article_hash(link, title),
            "title": title,
            "url": canonical_url(link),
            "published_at": dt.isoformat() if dt else None,
            "author": author,
            "summary_raw": summary,
            "hero_image": extract_hero_image(entry),
            "source_id": source["id"],
            "source_name": source["name"],
            "category": source["category"],
        })

    return articles


def fetch_hn_algolia(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Hacker News Algolia search API."""
    url = source["url"]
    logging.info(f"[{source['id']}] HN Algolia fetch")

    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    cutoff = get_cutoff(source)
    articles = []
    for hit in data.get("hits", []):
        created = hit.get("created_at_i")
        dt = datetime.fromtimestamp(created, tz=timezone.utc) if created else None
        if dt and dt < cutoff:
            continue

        title = hit.get("title", "") or ""
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

        articles.append({
            "id": article_hash(link, title),
            "title": title,
            "url": canonical_url(link),
            "published_at": dt.isoformat() if dt else None,
            "author": hit.get("author", "") or "",
            "summary_raw": clean_text(hit.get("story_text", ""))[:2000],
            "hero_image": None,
            "source_id": source["id"],
            "source_name": source["name"],
            "category": source["category"],
            "extra": {
                "points": hit.get("points", 0),
                "num_comments": hit.get("num_comments", 0),
                "hn_id": hit.get("objectID"),
            },
        })

    return articles


def fetch_reddit(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Reddit public JSON endpoint — top za posledních 24h / 2 dny, sort by score.

    Místo paušálního min_score cutoffu vrátíme prvních max_items podle score
    (default 20). Tím se přirozeně filtrují memes a low-signal posty —
    necháváme si jen nejsdílenější content.
    """
    subreddit = source["url"]  # např. 'MachineLearning+LocalLLaMA'
    feed_url = f"https://www.reddit.com/r/{subreddit}/top.json?t=day&limit=100"
    logging.info(f"[{source['id']}] Reddit fetch r/{subreddit}")

    # Reddit je citlivý na User-Agent, musí vypadat jako specifický klient
    headers = {"User-Agent": "AI-News-Bot/1.0 (by kevin@ainamiru.cz)"}
    resp = requests.get(feed_url, headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    cutoff = get_cutoff(source)
    articles = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        created = post.get("created_utc")
        dt = datetime.fromtimestamp(created, tz=timezone.utc) if created else None
        if dt and dt < cutoff:
            continue

        title = post.get("title", "") or ""
        permalink = f"https://www.reddit.com{post.get('permalink', '')}"
        link = post.get("url") or permalink
        # Reddit vrací "self" pro textové posty — v tom případě chceme permalink
        if link == post.get("url") and post.get("is_self"):
            link = permalink

        thumb = post.get("thumbnail") or ""
        if not (thumb.startswith("http://") or thumb.startswith("https://")):
            thumb = None

        articles.append({
            "id": article_hash(link, title),
            "title": title,
            "url": canonical_url(link),
            "published_at": dt.isoformat() if dt else None,
            "author": post.get("author", "") or "",
            "summary_raw": clean_text(post.get("selftext", ""))[:2000],
            "hero_image": thumb,
            "source_id": source["id"],
            "source_name": f"{source['name']} — r/{post.get('subreddit', '')}",
            "category": source["category"],
            "extra": {
                "score": post.get("score"),
                "num_comments": post.get("num_comments"),
                "subreddit": post.get("subreddit"),
                "permalink": permalink,
            },
        })

    # Sort podle score desc a vezmeme top max_items
    articles.sort(key=lambda a: a.get("extra", {}).get("score", 0) or 0, reverse=True)
    max_items = source.get("max_items", 20)
    return articles[:max_items]


# Regex pro vyřezání <a href="..."...>inner</a> — DOTALL kvůli vnořeným
# elementům uvnitř <a> (často <h3>, <span>, <img>).
HTML_A_RE = re.compile(
    r'<a\s+[^>]*href="([^"#]+)"[^>]*>(.+?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def fetch_html_list(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Scraper HTML listing page (Anthropic news, Meta AI blog, ...).

    Najde všechny <a href="..."> odkazy, které obsahují klíč 'url_must_contain'
    v hrefu. Titulek se vezme z textového obsahu kotvy (po stripu HTML tagů).

    Omezení: neumí extrahovat published_at ani summary. Filter podle
    MAX_AGE tedy pro html_list nefunguje — spoléháme na to, že listing
    stránka zobrazuje články chronologicky a 'max_items' vezme prvních N.
    """
    url = source["url"]
    logging.info(f"[{source['id']}] HTML scrape {url}")

    resp = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    html = resp.text

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    url_must_contain = source.get("url_must_contain", "")
    seen: set[str] = set()
    articles: list[dict[str, Any]] = []

    for match in HTML_A_RE.finditer(html):
        href = match.group(1).strip()
        inner = match.group(2)

        # Filtr podle substring v URL
        if url_must_contain and url_must_contain not in href:
            continue

        # Absolutní URL
        full_url = href if href.startswith("http") else base + ("/" + href.lstrip("/"))

        # Neberem samotnou listing stránku (např. '/news/' nebo '/blog/')
        path = urlparse(full_url).path.rstrip("/")
        must_path = url_must_contain.rstrip("/")
        if must_path and path.endswith(must_path):
            continue

        # Dedup podle canonical URL
        cu = canonical_url(full_url)
        if cu in seen:
            continue
        seen.add(cu)

        title = clean_text(inner).strip()
        # Smysluplný titulek = nejméně 15 znaků, obsahuje písmeno
        if len(title) < 15 or not re.search(r"[A-Za-zÁ-Ýá-ý]", title):
            continue

        articles.append({
            "id": article_hash(full_url, title),
            "title": title,
            "url": cu,
            "published_at": None,  # listing page nezná publikační datum
            "author": "",
            "summary_raw": "",
            "hero_image": None,
            "source_id": source["id"],
            "source_name": source["name"],
            "category": source["category"],
        })

    # Při html_list scrape nelze filtrovat MAX_AGE ani sortit podle data —
    # 'max_items' se pak aplikuje v main loopu v pořadí, jak jsou v HTML
    # (obvykle chronologicky).
    return articles


FETCHERS: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    "rss": fetch_rss,
    "hn_algolia": fetch_hn_algolia,
    "reddit": fetch_reddit,
    "html_list": fetch_html_list,
}


# ---------- orchestrace ----------


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = LOG_DIR / f"fetch_{today}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplikace podle article_hash; sleduje, odkud všude článek přišel."""
    seen: dict[str, dict[str, Any]] = {}
    for art in articles:
        aid = art["id"]
        if aid in seen:
            existing = seen[aid]
            also = existing.setdefault("also_seen_in", [])
            if art["source_id"] not in also and art["source_id"] != existing["source_id"]:
                also.append(art["source_id"])
        else:
            seen[aid] = art
    return list(seen.values())


def apply_post_filters(source: dict[str, Any],
                       articles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Aplikuje keyword_filter a max_items po fetchi. Vrací (articles, stats)."""
    before = len(articles)
    stats = {"raw": before, "after_keyword": before, "final": before}

    if source.get("keyword_filter"):
        articles = [a for a in articles if article_matches_ai(a)]
        stats["after_keyword"] = len(articles)

    # max_items: reddit fetcher si to dělá sám uvnitř (sort by score),
    # ostatní zdroje řežeme tady podle published_at desc.
    max_items = source.get("max_items")
    if max_items and source.get("type") != "reddit" and len(articles) > max_items:
        articles.sort(key=lambda a: (a.get("published_at") or ""), reverse=True)
        articles = articles[:max_items]
    stats["final"] = len(articles)
    return articles, stats


def main() -> int:
    setup_logging()
    logging.info("=" * 60)
    logging.info(f"fetch_sources.py start @ {datetime.now().isoformat()}")
    logging.info(f"Sources to fetch: {len(SOURCES)}")

    all_articles: list[dict[str, Any]] = []
    source_stats: list[dict[str, Any]] = []

    for source in SOURCES:
        sid = source["id"]
        fetcher = FETCHERS.get(source["type"])
        if not fetcher:
            logging.warning(f"[{sid}] unknown fetcher type '{source['type']}', skipping")
            source_stats.append({
                "source_id": sid,
                "status": "error",
                "error": f"unknown fetcher type '{source['type']}'",
                "count": 0,
            })
            continue

        try:
            articles = fetcher(source)
            articles, filter_stats = apply_post_filters(source, articles)

            if filter_stats["raw"] != filter_stats["final"]:
                logging.info(
                    f"[{sid}] OK — {filter_stats['final']} articles "
                    f"(raw {filter_stats['raw']} → "
                    f"kw {filter_stats['after_keyword']} → "
                    f"final {filter_stats['final']})"
                )
            else:
                logging.info(f"[{sid}] OK — {len(articles)} articles")

            all_articles.extend(articles)
            source_stats.append({
                "source_id": sid,
                "status": "ok",
                "count": len(articles),
                "raw_count": filter_stats["raw"],
            })
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            logging.error(f"[{sid}] FAILED — {msg}")
            source_stats.append({
                "source_id": sid,
                "status": "error",
                "error": msg[:300],
                "count": 0,
            })

    deduped = dedupe(all_articles)
    deduped.sort(key=lambda a: (a.get("published_at") or ""), reverse=True)

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    out_path = INBOX_DIR / f"{today}.json"

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "article_count_unique": len(deduped),
        "article_count_raw": len(all_articles),
        "source_stats": source_stats,
        "articles": deduped,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for s in source_stats if s["status"] == "ok")
    failed = sum(1 for s in source_stats if s["status"] == "error")
    logging.info(f"DONE — {len(deduped)} unique / {len(all_articles)} raw articles")
    logging.info(f"Sources: {ok} OK, {failed} FAILED (of {len(SOURCES)})")
    logging.info(f"Written: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
