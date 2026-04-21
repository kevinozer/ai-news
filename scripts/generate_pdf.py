#!/usr/bin/env python3
"""
generate_pdf.py — Vygeneruje brand-consistent CZ PDF digest z výběru článků.

Vstup: JSON soubor se strukturou (typicky digest/YYYY-MM-DD_selected.json):

    {
      "date": "2026-04-21",
      "generated_at": "...",
      "selected_count": 15,
      "articles": [
        {
          "id": "...",
          "rank": 1,
          "title_cs": "Český titulek (přeloženo / přepsáno Claudem)",
          "title_orig": "Original title (pokud byl jiný)",
          "url": "https://...",
          "source_name": "TechCrunch AI",
          "category": "news",
          "hero_image": "https://..." | null,
          "summary_cs": ["bod 1", "bod 2", "bod 3"],
          "why_matters": "Krátká věta — proč to je důležité"
        },
        ...
      ]
    }

Výstup: ../digest/news_YYYY-MM-DD.pdf

Brand identita (viz brand.md):
- Cormorant Garamond (Light) pro nadpisy a display
- Space Grotesk (Regular/SemiBold) pro body a UI
- Copper #9C4A28 jako primární akcent
- Amber #C49A4A jako sekundární akcent (kategorijní tagy)
- Ink #1A1917 pro text, ink-soft #4A4845 pro sekundární, ink-dim #8A8884 pro meta
- Ivory pozadí
- Editorial / luxury aesthetic, žádné neon, žádné emoji

Fonty:
- Skript hledá Cormorant a Space Grotesk v ../assets/fonts/. Pokud nejsou,
  fallback na Helvetica/Times. Stáhnout z fonts.google.com:
    CormorantGaramond-Light.ttf, CormorantGaramond-LightItalic.ttf,
    SpaceGrotesk-Regular.ttf, SpaceGrotesk-SemiBold.ttf

Spuštění:
    python generate_pdf.py ../digest/2026-04-21_selected.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    KeepTogether,
    ListFlowable,
    ListItem,
    HRFlowable,
)

# ---------- brand palette ----------

COPPER = colors.HexColor("#9C4A28")
COPPER_DEEP = colors.HexColor("#7A3519")
AMBER = colors.HexColor("#C49A4A")
INK = colors.HexColor("#1A1917")
INK_SOFT = colors.HexColor("#4A4845")
INK_DIM = colors.HexColor("#8A8884")
INK_GHOST = colors.HexColor("#C4C0B8")
IVORY = colors.HexColor("#FFFFFF")
IVORY_2 = colors.HexColor("#F7F7F5")
BORDER = colors.HexColor("#E5E5E3")

# ---------- paths ----------

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
DIGEST_DIR = ROOT / "digest"

# ---------- font registration s fallback ----------


def register_fonts() -> dict[str, str]:
    """Zaregistruje brand fonty, pokud existují. Vrací mapu rolí na font names."""
    fonts = {
        "display": "Times-Roman",         # fallback pro Cormorant
        "display_italic": "Times-Italic",
        "body": "Helvetica",              # fallback pro Space Grotesk
        "body_bold": "Helvetica-Bold",
        "body_italic": "Helvetica-Oblique",
    }

    candidates = [
        ("display",         "CormorantGaramond-Light.ttf",       "CormorantGaramond"),
        ("display_italic",  "CormorantGaramond-LightItalic.ttf", "CormorantGaramond-Italic"),
        ("body",            "SpaceGrotesk-Regular.ttf",          "SpaceGrotesk"),
        ("body_bold",       "SpaceGrotesk-SemiBold.ttf",         "SpaceGrotesk-Bold"),
        ("body_italic",     "SpaceGrotesk-Italic.ttf",           "SpaceGrotesk-Italic"),
    ]

    for role, filename, font_name in candidates:
        font_path = FONTS_DIR / filename
        if font_path.exists():
            try:
                pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
                fonts[role] = font_name
            except Exception as exc:
                print(f"[WARN] Registrace fontu {filename} selhala: {exc}",
                      file=sys.stderr)

    if fonts["display"] == "Times-Roman":
        print("[WARN] Cormorant Garamond nenalezen v ../assets/fonts/, "
              "používám Times-Roman jako fallback.", file=sys.stderr)
    if fonts["body"] == "Helvetica":
        print("[WARN] Space Grotesk nenalezen v ../assets/fonts/, "
              "používám Helvetica jako fallback.", file=sys.stderr)

    # Font family mappings — aby <i> a <b> tagy v Paragraph XML
    # mapovaly na TTF variantu (jinak ReportLab fallbackuje na
    # Helvetica-Oblique / -Bold Type 1 fonty, které NEMAJÍ unicode
    # CZ glyfy — tam vznikají čtverečky místo háčků).
    if fonts["display"] != "Times-Roman":
        # Pro display (Cormorant) nemáme samostatnou bold variantu —
        # pro <b> použij stejnou, pro <i> italic variant.
        registerFontFamily(
            fonts["display"],
            normal=fonts["display"],
            bold=fonts["display"],
            italic=fonts["display_italic"],
            boldItalic=fonts["display_italic"],
        )
    if fonts["body"] != "Helvetica":
        # Space Grotesk bez pravé italic variantu → italic = normal
        # (ReportLab udělá faux-oblique skew), hlavně že zůstane TTF.
        italic_face = fonts.get("body_italic")
        if italic_face == "Helvetica-Oblique":
            italic_face = fonts["body"]
        bolditalic_face = italic_face if italic_face != fonts["body"] else fonts["body_bold"]
        registerFontFamily(
            fonts["body"],
            normal=fonts["body"],
            bold=fonts["body_bold"],
            italic=italic_face,
            boldItalic=bolditalic_face,
        )

    return fonts


# ---------- styles ----------


def build_styles(fonts: dict[str, str]) -> dict[str, ParagraphStyle]:
    """Vrátí pojmenované Paragraph styles podle brand identity."""
    return {
        # Cover
        "cover_eyebrow": ParagraphStyle(
            "cover_eyebrow",
            fontName=fonts["body_bold"], fontSize=9, leading=12,
            textColor=AMBER, spaceAfter=10,
        ),
        "cover_h1": ParagraphStyle(
            "cover_h1",
            fontName=fonts["display"], fontSize=44, leading=52,
            textColor=INK, spaceAfter=8,
        ),
        "cover_subtitle": ParagraphStyle(
            "cover_subtitle",
            # base je display (ne display_italic) — italic přidáváme tagem <i>
            # uvnitř Paragraph stringu, aby family mapping fungoval a glyfy s
            # háčky prošly přes TTF italic variantu.
            fontName=fonts["display"], fontSize=16, leading=22,
            textColor=INK_SOFT, spaceAfter=24,
        ),
        "cover_meta": ParagraphStyle(
            "cover_meta",
            fontName=fonts["body"], fontSize=11, leading=15,
            textColor=INK_DIM, spaceAfter=18,
        ),
        # Table of contents
        "toc_heading": ParagraphStyle(
            "toc_heading",
            fontName=fonts["display"], fontSize=15, leading=19,
            textColor=COPPER, spaceAfter=10,
        ),
        "toc_item": ParagraphStyle(
            "toc_item",
            fontName=fonts["body"], fontSize=10.5, leading=14,
            textColor=INK, spaceAfter=3, leftIndent=0,
        ),
        "toc_index": ParagraphStyle(
            "toc_index",
            fontName=fonts["body_bold"], fontSize=10.5, leading=14,
            textColor=COPPER,
        ),
        # Article block
        "art_eyebrow": ParagraphStyle(
            "art_eyebrow",
            fontName=fonts["body_bold"], fontSize=8.5, leading=11,
            textColor=AMBER, spaceAfter=4,
        ),
        "art_title": ParagraphStyle(
            "art_title",
            fontName=fonts["display"], fontSize=22, leading=26,
            textColor=INK, spaceAfter=4,
        ),
        "art_title_orig": ParagraphStyle(
            "art_title_orig",
            # base display + italic přes tag → family mapping zachytí TTF italic
            fontName=fonts["display"], fontSize=12, leading=15,
            textColor=INK_DIM, spaceAfter=10,
        ),
        "art_bullet": ParagraphStyle(
            "art_bullet",
            fontName=fonts["body"], fontSize=11, leading=15,
            textColor=INK, leftIndent=4, spaceAfter=3,
        ),
        "art_why": ParagraphStyle(
            "art_why",
            # base body (ne body_italic) — italic pouze sémantikou přes tag <i>
            fontName=fonts["body"], fontSize=11, leading=15,
            textColor=INK_SOFT, spaceAfter=6,
        ),
        "art_source": ParagraphStyle(
            "art_source",
            fontName=fonts["body"], fontSize=8.5, leading=11,
            textColor=INK_DIM, spaceAfter=4,
        ),
    }


# ---------- helpers ----------

CZ_MONTHS = [
    "ledna", "února", "března", "dubna", "května", "června",
    "července", "srpna", "září", "října", "listopadu", "prosince",
]

CATEGORY_LABEL = {
    "news":        "AKTUALITY",
    "labs":        "OFICIÁLNÍ ZDROJ",
    "newsletter":  "NEWSLETTER",
    "commentary":  "KOMENTÁŘ",
    "research":    "VÝZKUM",
    "community":   "KOMUNITA",
    "cz":          "ČESKO",
}


def format_cz_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.day}. {CZ_MONTHS[dt.month - 1]} {dt.year}"


def format_cz_datetime(iso_str: str | None) -> str:
    """Převede ISO timestamp na 'DD. měsíce YYYY' (bez času, pro eyebrow)."""
    if not iso_str:
        return ""
    try:
        # ISO 8601 s TZ — Python 3.11+ zvládne 'Z' nativně, starší ne.
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(iso_str[:10], "%Y-%m-%d")
        except ValueError:
            return ""
    return f"{dt.day}. {CZ_MONTHS[dt.month - 1]} {dt.year}"


def category_label(cat: str | None) -> str:
    return CATEGORY_LABEL.get(cat or "", (cat or "").upper())


def xml_escape(s: str | None) -> str:
    if not s:
        return ""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


# Brand pravidlo: zadna em-dash/en-dash v renderovanem textu.
_DASH_RE_SPACED = re.compile(r"\s+[\u2014\u2013]\s+")
_DASH_RE_BARE = re.compile(r"[\u2014\u2013]")


def strip_brand_dashes(text: str | None) -> str:
    """Nahradi em-dash/en-dash carkou (spaced) nebo '-' (bare)."""
    if not text:
        return text or ""
    text = _DASH_RE_SPACED.sub(", ", text)
    text = _DASH_RE_BARE.sub("-", text)
    return text


def short_url(url: str, limit: int = 70) -> str:
    if len(url) <= limit:
        return url
    return url[: limit - 1] + "…"


# ---------- page decorations ----------


LOGO_PATH = ASSETS_DIR / "brand" / "logo-znak.png"


def make_page_chrome(fonts: dict[str, str]):
    """Vrátí callable, který kreslí brand logo (header) + footer na každou stranu."""

    body = fonts["body"]

    # Výška loga v mm — znak (monogram AK) zabere viditelné místo v pravém rohu
    logo_target_h_mm = 16.0
    logo_w_mm = logo_target_h_mm
    logo_available = LOGO_PATH.exists()
    if logo_available:
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(str(LOGO_PATH)) as im:
                ratio = im.size[0] / im.size[1]
            logo_w_mm = logo_target_h_mm * ratio
        except Exception:
            logo_available = False

    def _draw(canvas, doc):
        page_w, page_h = A4
        canvas.saveState()

        # ===== HEADER (brand logo vpravo nahoře) =====
        if logo_available:
            img_h = logo_target_h_mm * mm
            img_w = logo_w_mm * mm
            x = page_w - 18 * mm - img_w
            y = page_h - 8 * mm - img_h
            canvas.drawImage(
                str(LOGO_PATH), x, y,
                width=img_w, height=img_h,
                preserveAspectRatio=True, mask='auto',
            )
        else:
            canvas.setFont(body, 9)
            canvas.setFillColor(COPPER)
            canvas.drawRightString(page_w - 18 * mm, page_h - 14 * mm,
                                   "AI NA MÍRU")

        # ===== FOOTER =====
        canvas.setFont(body, 8)
        canvas.setFillColor(INK_DIM)
        canvas.drawString(20 * mm, 10 * mm, "AI NA MÍRU  ·  AI News digest")
        canvas.drawRightString(page_w - 20 * mm, 10 * mm, f"strana {doc.page}")
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.4)
        canvas.line(20 * mm, 14 * mm, page_w - 20 * mm, 14 * mm)

        canvas.restoreState()

    return _draw


# ---------- builders ----------


def build_cover(story: list, styles: dict, date: str, count: int) -> None:
    cz_date = format_cz_date(date)
    # Zkrácený top spacing — bývalo 24 mm + eyebrow řádek, teď jen 4 mm
    story.append(Spacer(1, 4 * mm))
    # (eyebrow "AI NEWS · DENNÍ DIGEST" odebrán — redundantní vůči názvu)
    story.append(Paragraph("AI News", styles["cover_h1"]))
    story.append(Paragraph(f"<i>{cz_date}</i>", styles["cover_subtitle"]))
    story.append(HRFlowable(width="30%", thickness=0.6, color=COPPER,
                            spaceBefore=0, spaceAfter=10, hAlign="LEFT"))
    story.append(Paragraph(
        f"Vybráno {count} novinek z 28 zdrojů světa AI · "
        f"česky shrnuté pro denní 5–10 minutové čtení",
        styles["cover_meta"]
    ))


def build_toc(story: list, styles: dict, articles: list[dict[str, Any]]) -> None:
    # Mírnější top spacer — šetříme místo, aby se 15 položek vešlo na 1 stránku
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Dnešní přehled", styles["toc_heading"]))
    for i, art in enumerate(articles, 1):
        cat = category_label(art.get("category"))
        title = xml_escape(strip_brand_dashes(
            art.get("title_cs") or art.get("title_orig") or "..."
        ))
        line = (f'<font name="{styles["toc_index"].fontName}" '
                f'color="#9C4A28"><b>{i:02d}</b></font>'
                f'  &nbsp;{title}'
                f'  &nbsp;<font color="#8A8884" size="8">· {cat}</font>')
        story.append(Paragraph(line, styles["toc_item"]))
    story.append(PageBreak())


def build_article_block(art: dict[str, Any], idx: int,
                        styles: dict) -> KeepTogether:
    """Vrátí jeden článek-blok jako KeepTogether (drží pohromadě na stránce)."""
    block: list = []

    cat = category_label(art.get("category"))
    src = xml_escape(art.get("source_name") or "")
    pub = format_cz_datetime(art.get("published_at"))

    eyebrow_parts = [f"#{idx:02d}", xml_escape(cat), src]
    if pub:
        eyebrow_parts.append(pub)
    block.append(Paragraph(
        "  ·  ".join(eyebrow_parts),
        styles["art_eyebrow"],
    ))

    title_cs = xml_escape(strip_brand_dashes(
        art.get("title_cs") or art.get("title_orig") or "..."
    ))
    block.append(Paragraph(title_cs, styles["art_title"]))

    title_orig = art.get("title_orig")
    if title_orig and title_orig != art.get("title_cs"):
        # <i> tag teď správně zachytí TTF italic přes registerFontFamily
        block.append(Paragraph(
            f"<i>{xml_escape(strip_brand_dashes(title_orig))}</i>",
            styles["art_title_orig"],
        ))

    bullets = art.get("summary_cs") or []
    if bullets:
        items = [
            ListItem(Paragraph(xml_escape(strip_brand_dashes(b)),
                               styles["art_bullet"]),
                     leftIndent=10, value="•")
            for b in bullets
        ]
        block.append(ListFlowable(
            items, bulletType="bullet", start="•",
            bulletColor=COPPER, bulletFontSize=11,
            leftIndent=14, bulletDedent=12,
        ))
        block.append(Spacer(1, 4))

    why = art.get("why_matters")
    if why:
        # base style je body (regular), italic jen pro samotný text „proč je…"
        # přes tag <i>, bold označení v copper přes <b>.
        block.append(Paragraph(
            f'<font color="#9C4A28"><b>Proč je to důležité: </b></font>'
            f'<i>{xml_escape(strip_brand_dashes(why))}</i>',
            styles["art_why"],
        ))

    url = art.get("url") or ""
    if url:
        block.append(Paragraph(
            f'Zdroj: <link href="{url}" color="#9C4A28">{xml_escape(short_url(url))}</link>',
            styles["art_source"],
        ))

    # Subtilní separator pod blokem
    block.append(Spacer(1, 4))
    block.append(HRFlowable(width="100%", thickness=0.3,
                            color=BORDER, spaceBefore=2, spaceAfter=14))

    return KeepTogether(block)


# ---------- main ----------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("selected_json", help="Cesta k selected articles JSON")
    args = ap.parse_args()

    selected_path = Path(args.selected_json)
    if not selected_path.is_absolute():
        selected_path = Path.cwd() / selected_path
    if not selected_path.exists():
        print(f"[ERROR] Soubor neexistuje: {selected_path}", file=sys.stderr)
        return 2

    data = json.loads(selected_path.read_text(encoding="utf-8"))
    date = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    articles = data.get("articles", [])
    if not articles:
        print("[ERROR] articles je prázdné nebo chybí.", file=sys.stderr)
        return 2

    fonts = register_fonts()
    styles = build_styles(fonts)

    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DIGEST_DIR / f"news_{date}.pdf"

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=32 * mm, bottomMargin=22 * mm,
        title=f"AI News · {format_cz_date(date)}",
        author="AI NA MÍRU",
        subject="Denní AI news digest",
        creator="AI News bot (AI NA MÍRU)",
    )

    story: list = []
    build_cover(story, styles, date, len(articles))
    build_toc(story, styles, articles)
    for i, art in enumerate(articles, 1):
        story.append(build_article_block(art, i, styles))

    chrome = make_page_chrome(fonts)
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)

    print(f"[OK] Written: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
