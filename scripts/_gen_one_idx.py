#!/usr/bin/env python3
"""Generuje jeden konkrétní článek podle indexu (0-based) z digestu top-7. Idempotent: skip pokud existuje."""
import sys, os, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# load env
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from generate_articles import (
    DIGEST_DIR, OUTPUT_DIR, find_latest_digest,
    generate_one_article, update_index, validate_image,
)

def main():
    if len(sys.argv) < 2:
        print("usage: _gen_one_idx.py IDX [--fact-check]", file=sys.stderr); sys.exit(2)
    idx = int(sys.argv[1])
    fact_check = "--fact-check" in sys.argv

    digest_path = find_latest_digest()
    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    date_iso = digest.get("date") or digest_path.stem.split("_")[0]
    articles = digest.get("articles", [])[:7]
    if idx >= len(articles):
        print(f"❌ idx {idx} mimo rozsah ({len(articles)} článků)"); sys.exit(2)

    art = articles[idx]
    art["_idx"] = idx
    title = art.get("title_cs", "")[:80]
    print(f"[{idx+1}/{len(articles)}] {title}")

    # Idempotency: skip if directory with non-placeholder hero exists
    # Check using slug derived after generation; instead check if any dir for this URL exists
    # Simpler: scan output for any dir matching date-iso prefix that has source_url == art.url
    target_url = art.get("url", "")
    for d in OUTPUT_DIR.iterdir():
        if not d.is_dir() or not d.name.startswith(f"{date_iso}-"): continue
        idx_path = ROOT / "output" / "articles" / "index.json"
        if idx_path.exists():
            try:
                idx_data = json.loads(idx_path.read_text(encoding="utf-8"))
                for a in idx_data.get("articles", []):
                    if a.get("source_url") == target_url and a.get("date") == date_iso:
                        slug_dir = OUTPUT_DIR / a["slug"]
                        if (slug_dir / "index.html").exists() and (slug_dir / "hero.webp").exists():
                            placeholder = (slug_dir / ".placeholder").exists()
                            if not placeholder:
                                print(f"  ⏭ skip — již existuje: {a['slug']}")
                                return
                            else:
                                print(f"  ↻ existuje s placeholder, regeneruji…")
                                break
            except Exception: pass
        break

    entry = generate_one_article(art, date_iso, no_text=False, no_image=False, fact_check=fact_check)
    if not entry:
        print("❌ generation failed"); sys.exit(1)

    # update index with this single entry merged
    update_index([entry])
    print(f"✅ {entry['slug']}")

if __name__ == "__main__":
    main()
