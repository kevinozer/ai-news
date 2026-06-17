#!/usr/bin/env python3
"""
check_articles.py — sanity audit nad vygenerovanými články pro dané datum.

Exit codes:
  0 — vše OK
  1 — nalezeny problémy (duplicitní hero.webp, placeholder, chybějící, špatné rozměry)
  2 — chyba při běhu skriptu

Použití:
  python3 scripts/check_articles.py --date 2026-05-13
  python3 scripts/check_articles.py --date 2026-05-13 --root output/articles
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = ROOT / "output" / "articles"


def md5_bytes(p: Path) -> str:
    h = hashlib.md5()
    h.update(p.read_bytes())
    return h.hexdigest()


def check(date_iso: str, articles_root: Path) -> tuple[list[str], list[str]]:
    """Vrátí (errors, warnings). Errors → exit 1."""
    errors: list[str] = []
    warnings: list[str] = []

    if not articles_root.exists():
        errors.append(f"articles_root neexistuje: {articles_root}")
        return errors, warnings

    dirs = sorted(
        d for d in articles_root.iterdir()
        if d.is_dir() and d.name.startswith(f"{date_iso}-")
    )
    if not dirs:
        errors.append(f"Žádné článkové složky pro {date_iso} v {articles_root}")
        return errors, warnings

    hashes: dict[str, list[str]] = {}
    for d in dirs:
        hero = d / "hero.webp"
        html = d / "index.html"
        if not html.exists():
            errors.append(f"{d.name}: chybí index.html")
        if not hero.exists():
            errors.append(f"{d.name}: chybí hero.webp")
            continue
        if (d / ".placeholder").exists():
            errors.append(f"{d.name}: hero je gradient placeholder (.placeholder marker)")
            continue
        size = hero.stat().st_size
        if size < 5000:
            errors.append(f"{d.name}: hero.webp je podezřele malý ({size} B)")
            continue
        # rozměry zkusíme pouze pokud máme PIL
        try:
            from PIL import Image
            with Image.open(hero) as img:
                w, h = img.size
            if (w, h) != (1400, 800):
                warnings.append(f"{d.name}: hero.webp rozměry {w}x{h} (očekáváme 1400x800)")
        except Exception as e:
            warnings.append(f"{d.name}: PIL nedostupný / chyba čtení: {e}")
        # hash až po validních základech
        try:
            digest = md5_bytes(hero)
            hashes.setdefault(digest, []).append(d.name)
        except Exception as e:
            errors.append(f"{d.name}: nelze hashovat hero.webp: {e}")

    # duplicitní hero
    for digest, slugs in hashes.items():
        if len(slugs) > 1:
            errors.append(
                f"Duplicate hero.webp (md5={digest[:10]}…) sdílí {len(slugs)} článků:"
            )
            for s in slugs:
                errors.append(f"    - {s}")

    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    ap.add_argument("--date", required=True, help="datum ve formátu YYYY-MM-DD")
    ap.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help=f"adresář s článkovými složkami (default: {DEFAULT_ROOT})",
    )
    args = ap.parse_args()

    errors, warnings = check(args.date, Path(args.root))

    if warnings:
        print(f"⚠ {len(warnings)} warning(ů):")
        for w in warnings:
            print(f"  ⚠ {w}")

    if not errors:
        print(f"✓ check_articles {args.date}: OK ({len(warnings)} warning(ů))")
        return 0

    print(f"❌ check_articles {args.date}: {len(errors)} chyb(a)")
    for e in errors:
        print(f"  ❌ {e}")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"💥 audit selhal: {e}", file=sys.stderr)
        sys.exit(2)
