#!/usr/bin/env python3
"""
dedupe_helper.py — Cross-day URL dedupe pro ai-news pipeline.

Tři vrstvy dedupe:

1. PUBLISHED — URL už visí jako článek na ainamiru.cz (články/index.json).
2. DIGESTED — URL byl vybrán do nějakého _selected.json za posledních N dnů
   (i kdyby na webu ještě nebyl).
3. STALE_EVERGREEN — URL je v inboxech delší než STALE_DAYS dnů a nikdy se
   nedostal do _selected.json. Typicky Anthropic news / OpenAI blog posty,
   které RSS/scrape vrací bez published_at, takže klasický MAX_AGE filter
   je nezachytí. Tento check je proxy pro "stale story".

Použití z Python kódu:
    from dedupe_helper import build_blocklist, is_blocked

Použití z CLI (pro debugging):
    python3 scripts/dedupe_helper.py --check-url https://...
    python3 scripts/dedupe_helper.py --filter-inbox inbox/2026-05-16.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Konstanty
DIGEST_LOOKBACK_DAYS = 30
INBOX_LOOKBACK_DAYS = 30
STALE_DAYS = 7  # URL bez published_at v inboxu déle = pravděpodobně evergreen leak

# Tracking parametry, které canonicalizace strhne (kopie z fetch_sources.py
# pro nezávislost — pokud se tam logika změní, sjednotit).
TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAM_EXACT = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
    "yclid", "_hsenc", "_hsmi", "hsctatracking", "campaign_id",
    "share", "shared", "feature", "src", "from",
}


def canonical_url(url: str) -> str:
    """Strip tracking parametrů a fragmentu pro stabilní dedup."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        filtered = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if not k.lower().startswith(TRACKING_PARAM_PREFIXES)
            and k.lower() not in TRACKING_PARAM_EXACT
        ]
        # Lowercase host, strip trailing slash z path (kromě "/")
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        return urlunparse((
            parsed.scheme.lower() if parsed.scheme else "https",
            host,
            path,
            "",  # params (skoro nikdy nepoužité)
            urlencode(filtered) if filtered else "",
            "",  # fragment
        ))
    except Exception:
        return url.strip()


def _safe_load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"  ! načtení {path.name} selhalo: {e}")
        return None


def _extract_urls(data: Any) -> list[str]:
    """Z různých JSON struktur vytáhne všechna URLka."""
    if not data:
        return []
    urls: list[str] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("articles") or data.get("items") or []
    else:
        return []
    for it in items:
        if not isinstance(it, dict):
            continue
        for k in ("url", "source_url", "link"):
            v = it.get(k)
            if isinstance(v, str) and v.strip():
                urls.append(v.strip())
                break
    return urls


def _published_index_urls(ainamiru_root: Path) -> set[str]:
    """Načti URLs z articles/index.json (publikované články na ainamiru)."""
    idx_path = ainamiru_root / "articles" / "index.json"
    if not idx_path.exists():
        logging.warning(f"  ! articles/index.json neexistuje: {idx_path}")
        return set()
    data = _safe_load_json(idx_path)
    urls = {canonical_url(u) for u in _extract_urls(data) if u}
    urls.discard("")
    logging.info(f"  • published index: {len(urls)} URLs ({idx_path})")
    return urls


def _digested_urls(ai_news_root: Path, today: str, lookback_days: int) -> set[str]:
    """Načti URLs z digest/*_selected.json za posledních N dnů (bez dneška)."""
    digest_dir = ai_news_root / "digest"
    if not digest_dir.exists():
        return set()
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    cutoff = today_dt - timedelta(days=lookback_days)
    urls: set[str] = set()
    files_used = 0
    for p in sorted(digest_dir.glob("*_selected.json")):
        name = p.stem.replace("_selected", "")
        try:
            d = datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        if d == today_dt:
            continue  # dnešek nezahrnuj
        if d < cutoff:
            continue
        data = _safe_load_json(p)
        for u in _extract_urls(data):
            cu = canonical_url(u)
            if cu:
                urls.add(cu)
        files_used += 1
    logging.info(f"  • digested ({lookback_days}d, {files_used} souborů): {len(urls)} URLs")
    return urls


def _stale_evergreen_urls(
    ai_news_root: Path, today: str, lookback_days: int, stale_days: int,
    digested: set[str],
) -> set[str]:
    """
    URL, které jsou v inboxech déle než `stale_days` dní a nikdy nebyly v _selected.json.
    Typicky evergreen content od Anthropic/OpenAI bez published_at v RSS.
    """
    inbox_dir = ai_news_root / "inbox"
    if not inbox_dir.exists():
        return set()
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    cutoff_old = today_dt - timedelta(days=lookback_days)
    threshold = today_dt - timedelta(days=stale_days)

    # url -> first_seen_date
    first_seen: dict[str, datetime] = {}
    for p in sorted(inbox_dir.glob("*.json")):
        name = p.stem
        try:
            d = datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        if d < cutoff_old:
            continue
        if d > today_dt:
            continue
        data = _safe_load_json(p)
        for u in _extract_urls(data):
            cu = canonical_url(u)
            if not cu:
                continue
            if cu not in first_seen or first_seen[cu] > d:
                first_seen[cu] = d

    stale: set[str] = set()
    for cu, fs in first_seen.items():
        if fs <= threshold and cu not in digested:
            stale.add(cu)
    logging.info(f"  • stale evergreen (≥{stale_days}d v inboxu, nikdy v digestu): {len(stale)} URLs")
    return stale


def build_blocklist(
    ai_news_root: str | os.PathLike,
    ainamiru_root: str | os.PathLike,
    today: str,
    digest_lookback: int = DIGEST_LOOKBACK_DAYS,
    inbox_lookback: int = INBOX_LOOKBACK_DAYS,
    stale_days: int = STALE_DAYS,
) -> dict[str, set[str]]:
    """
    Vrátí dict {published, digested, stale_evergreen} se setem canonical URLs.
    Volající si rozhodne, jak striktně blokovat (typicky union všech tří).
    """
    ai_news_root = Path(ai_news_root)
    ainamiru_root = Path(ainamiru_root)

    logging.info(f"Building blocklist for {today}…")
    published = _published_index_urls(ainamiru_root)
    digested = _digested_urls(ai_news_root, today, digest_lookback)
    stale = _stale_evergreen_urls(
        ai_news_root, today, inbox_lookback, stale_days,
        digested=digested,
    )
    return {
        "published": published,
        "digested": digested,
        "stale_evergreen": stale,
    }


def is_blocked(url: str, blocklist: dict[str, set[str]]) -> tuple[bool, str | None]:
    """Vrátí (blocked, reason)."""
    cu = canonical_url(url)
    if not cu:
        return False, None
    if cu in blocklist.get("published", set()):
        return True, "published"
    if cu in blocklist.get("digested", set()):
        return True, "digested"
    if cu in blocklist.get("stale_evergreen", set()):
        return True, "stale_evergreen"
    return False, None


def filter_articles(
    articles: list[dict[str, Any]],
    blocklist: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rozdělí seznam článků na (kept, blocked) s reason v blocked[i]['_block_reason']."""
    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for a in articles:
        url = a.get("url") or a.get("source_url") or a.get("link") or ""
        is_blk, reason = is_blocked(url, blocklist)
        if is_blk:
            a = dict(a)
            a["_block_reason"] = reason
            blocked.append(a)
        else:
            kept.append(a)
    return kept, blocked


# ---------- CLI ----------

def _cli_main() -> int:
    ap = argparse.ArgumentParser(description="Cross-day URL dedupe helper.")
    ap.add_argument("--ai-news", default=".",
                    help="kořen AI News repo (default: cwd)")
    ap.add_argument("--ainamiru",
                    default="../ai-na-miru",
                    help="kořen ai-na-miru (default: ../ai-na-miru)")
    ap.add_argument("--date",
                    default=datetime.now().strftime("%Y-%m-%d"),
                    help="dnešní datum YYYY-MM-DD")
    ap.add_argument("--digest-lookback", type=int, default=DIGEST_LOOKBACK_DAYS)
    ap.add_argument("--inbox-lookback", type=int, default=INBOX_LOOKBACK_DAYS)
    ap.add_argument("--stale-days", type=int, default=STALE_DAYS)

    ap.add_argument("--check-url", help="vypiš jestli daný URL je v blocklistu")
    ap.add_argument("--filter-inbox", help="filtruj inbox JSON, vypiš počty a stats")
    ap.add_argument("--dump", action="store_true",
                    help="vypiš celý blocklist (debug)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    bl = build_blocklist(
        args.ai_news, args.ainamiru, args.date,
        digest_lookback=args.digest_lookback,
        inbox_lookback=args.inbox_lookback,
        stale_days=args.stale_days,
    )

    total = sum(len(v) for v in bl.values())
    print(f"\nBlocklist total (s overlapem): {total}")
    print(f"  published:       {len(bl['published'])}")
    print(f"  digested:        {len(bl['digested'])}")
    print(f"  stale_evergreen: {len(bl['stale_evergreen'])}")
    print(f"  unique union:    {len(bl['published'] | bl['digested'] | bl['stale_evergreen'])}")

    if args.dump:
        for k, v in bl.items():
            print(f"\n=== {k} ===")
            for u in sorted(v):
                print(f"  {u}")

    if args.check_url:
        blocked, reason = is_blocked(args.check_url, bl)
        print(f"\n{args.check_url}")
        print(f"  canonical: {canonical_url(args.check_url)}")
        print(f"  blocked: {blocked}  reason: {reason}")

    if args.filter_inbox:
        data = _safe_load_json(Path(args.filter_inbox))
        if not data:
            return 1
        arts = data.get("articles") if isinstance(data, dict) else data
        kept, blocked = filter_articles(arts or [], bl)
        print(f"\nInbox filter: {args.filter_inbox}")
        print(f"  raw:     {len(arts or [])}")
        print(f"  kept:    {len(kept)}")
        print(f"  blocked: {len(blocked)}")
        by_reason: dict[str, int] = {}
        for b in blocked:
            r = b.get("_block_reason", "?")
            by_reason[r] = by_reason.get(r, 0) + 1
        for r, n in sorted(by_reason.items()):
            print(f"    {r}: {n}")

    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
