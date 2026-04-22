#!/usr/bin/env python3
"""
generate_posts.py — Vygeneruje brand-consistent IG carousel + LinkedIn PDF
z výběru článků v digest/YYYY-MM-DD_selected.json.

Výstupy:
    ../instagram/YYYY-MM-DD/slide_01.png ... slide_09.png   (1080×1080 PNG)
    ../instagram/YYYY-MM-DD/caption.txt                     (IG popisek)
    ../linkedin/YYYY-MM-DD/carousel.pdf                     (9-str. 1920×1080 PDF)
    ../linkedin/YYYY-MM-DD/slide_01.png ... slide_09.png    (landscape PNG zdroje)
    ../linkedin/YYYY-MM-DD/caption.txt                      (LinkedIn popisek)

Struktura carouselu (stejná pro IG i LinkedIn, jen jiný aspect ratio):
    Slide 1: Cover — top novinka dne + velký titul „AI News | 21. 4. 2026"
    Slide 2-8: 7 newsových slidů — jeden článek per slide (title + 3 bulletů)
    Slide 9: Outro — statický brand asset s CTA

Brand identita viz ../brand.md:
    Cormorant Garamond Light pro nadpisy, Space Grotesk pro UI
    Copper #9C4A28 akcent, amber #C49A4A pro tagy, ink #1A1917 pro text
    Editorial / luxury aesthetic, žádné neon, žádné emoji

Pozadí: Gemini 2.5 Flash Image pro tematické backgrounds (preferovaná cesta,
konzistentní brand paleta, bez licenčních rizik). Fallback: brand gradient.

Použití:
    # plný běh (vyžaduje GEMINI_API_KEY v ../.env nebo env prom.)
    python generate_posts.py ../digest/2026-04-21_selected.json

    # test bez Gemini — brand gradient místo obrázků
    python generate_posts.py ../digest/2026-04-21_selected.json --no-gemini

    # jen IG / jen LinkedIn
    python generate_posts.py ../digest/2026-04-21_selected.json --only ig
    python generate_posts.py ../digest/2026-04-21_selected.json --only linkedin

Cache: vygenerované pozadí se ukládá do `../assets/bg_cache/<hash>.png`,
takže opakované běhy nad stejným článkem nespotřebovávají kvóty.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------- brand palette (RGB pro PIL) ----------

COPPER = (156, 74, 40)          # #9C4A28
COPPER_DEEP = (122, 53, 25)     # #7A3519
COPPER_DARK = (62, 26, 8)       # #3E1A08
AMBER = (196, 154, 74)          # #C49A4A
INK = (26, 25, 23)              # #1A1917
INK_SOFT = (74, 72, 69)         # #4A4845
INK_DIM = (138, 136, 132)       # #8A8884
INK_GHOST = (196, 192, 184)     # #C4C0B8
IVORY = (255, 255, 255)         # #FFFFFF
IVORY_2 = (247, 247, 245)       # #F7F7F5
IVORY_3 = (238, 238, 236)       # #EEEEEC
BORDER = (229, 229, 227)        # #E5E5E3
HERO_DARK = (10, 8, 6)          # #0A0806

# ---------- paths ----------

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
BRAND_DIR = ASSETS_DIR / "brand"
BG_CACHE_DIR = ASSETS_DIR / "bg_cache"
IG_DIR = ROOT / "instagram"
LI_DIR = ROOT / "linkedin"

LOGO_ZNAK = BRAND_DIR / "logo-znak.png"
LOGO_NAPIS = BRAND_DIR / "logo-napis.png"

# ---------- Gemini config ----------

GEMINI_MODEL = "gemini-2.5-flash-image"
GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/{model}:generateContent?key={key}"
)

# --- Brand prompt (cover + hero assets) ---
# Drží naši "editorial magazine" estetiku — bronze/copper/ivory.
BRAND_PROMPT_PREFIX = (
    "Editorial photography, muted copper-bronze-ivory palette, "
    "soft cinematic lighting, shallow depth of field, "
    "bespoke luxury magazine aesthetic. "
    "No text in image, no typography, no UI elements, no logos, "
    "no tech stock imagery, no neon, no loud gradients. "
    "Composition leaves bottom third relatively unbusy for text overlay. "
)

# --- News slide prompt ---
# Brand drží cover a outro; news obrázky můžou být plně barevné,
# ale musí být dramaticky cinematické, koncept musí být čitelný
# na první pohled a spodní třetina musí být klidná (pro text overlay).
NEWS_PROMPT_PREFIX = (
    "Editorial photograph, magazine cover quality "
    "(The Atlantic / WIRED / NYT Magazine). "
    "Photorealistic, cinematic lighting, shallow depth of field, "
    "rich natural colors. "
    "No text, no letters, no numbers, no logos, no UI. "
    "Before finishing: verify no rendered text overlaps with anything — "
    "image must contain zero typography. "
)

# ---------- text normalizace ----------

# Brand pravidlo: ŽÁDNÁ dlouhá pomlčka v renderovaném textu.
# Em-dash "—" a en-dash "–" se nahrazuje čárkou (když je jako oddělovač
# vět se spacy okolo) nebo jednoduchým "-" (když jde o range 2025-2026).
_DASH_RE_SPACED = re.compile(r"\s+[—–]\s+")
_DASH_RE_BARE = re.compile(r"[—–]")


def strip_brand_dashes(text: str | None) -> str:
    """Odstraní em-dash/en-dash z renderovaného textu (brand rule).

    " slovo — slovo " → " slovo, slovo "
    "2025—2026"        → "2025-2026"
    """
    if not text:
        return text or ""
    text = _DASH_RE_SPACED.sub(", ", text)
    text = _DASH_RE_BARE.sub("-", text)
    return text


# ---------- font loading ----------


def load_fonts() -> dict[str, Path]:
    """Vrátí mapu rolí → cestu k TTF. Fonty jsou v ../assets/fonts/."""
    fonts: dict[str, Path] = {}
    wanted = {
        "display":        "CormorantGaramond-Light.ttf",
        "display_italic": "CormorantGaramond-LightItalic.ttf",
        "body":           "SpaceGrotesk-Regular.ttf",
        "body_bold":      "SpaceGrotesk-SemiBold.ttf",
    }
    missing = []
    for role, filename in wanted.items():
        path = FONTS_DIR / filename
        if path.exists():
            fonts[role] = path
        else:
            missing.append(filename)
    if missing:
        print(f"[WARN] Chybí fonty: {missing}. Fallbackuji na default PIL font.",
              file=sys.stderr)
    return fonts


def pil_font(fonts: dict[str, Path], role: str, size: int) -> ImageFont.FreeTypeFont:
    path = fonts.get(role)
    if path is None:
        return ImageFont.load_default()
    return ImageFont.truetype(str(path), size=size)


# ---------- Gemini client ----------


def load_env() -> None:
    """Načte ../.env do os.environ (pokud existuje)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def gemini_generate_image(
    prompt: str,
    api_key: str,
    *,
    aspect_ratio: str = "1:1",
    timeout: int = 90,
) -> bytes:
    """Zavolá Gemini 2.5 Flash Image a vrátí bytes prvního vráceného obrázku.

    aspect_ratio: "1:1" (IG), "16:9" (LinkedIn landscape), "4:3", atd.

    Raises:
        RuntimeError pokud API odpoví chybou nebo nevrátí inline image.
    """
    import requests  # lokální import, aby --no-gemini režim nevyžadoval requests

    url = GEMINI_URL_TMPL.format(model=GEMINI_MODEL, key=api_key)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            # Gemini 2.5 Flash Image podporuje imageConfig.aspectRatio
            # (hodnoty: "1:1", "16:9", "9:16", "4:3", "3:4").
            "imageConfig": {"aspectRatio": aspect_ratio},
        },
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Gemini API {resp.status_code}: {resp.text[:500]}"
        )
    data = resp.json()
    # odpověď: candidates[0].content.parts[*].inlineData.data (base64)
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    raise RuntimeError(
        f"Gemini odpověď neobsahuje inline image: {json.dumps(data)[:500]}"
    )


def get_or_generate_bg(
    prompt: str,
    *,
    api_key: str | None,
    use_gemini: bool,
    aspect_ratio: str = "1:1",
) -> Image.Image:
    """Vrátí PIL Image s pozadím. Cache podle hash(prompt + aspect_ratio).

    Generujeme zvlášť 1:1 pro IG a 16:9 pro LinkedIn, aby nedocházelo
    k násilnému ořezu a ztrátě kompozice.
    """
    BG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(
        (prompt + "|" + aspect_ratio).encode("utf-8")
    ).hexdigest()[:16]
    cache_path = BG_CACHE_DIR / f"{key}.png"

    if cache_path.exists():
        # sanity check: pokud jsme v gemini módu, ale cache je gradient
        # (signatura: čtverec 1536×1536 i pro non-1:1 ratio), invalidate
        cached = Image.open(cache_path).convert("RGB")
        if use_gemini and cached.size == (1536, 1536) and aspect_ratio != "1:1":
            print(f"[cache] Stale gradient pro {aspect_ratio}, regeneruji.",
                  file=sys.stderr)
            try:
                cache_path.unlink()
            except OSError:
                pass
        else:
            return cached

    if not use_gemini or not api_key:
        # fallback: generuj branded gradient v přibližně správných proporcích
        w, h = 1536, 1536
        if aspect_ratio == "16:9":
            w, h = 1920, 1080
        elif aspect_ratio == "9:16":
            w, h = 1080, 1920
        img = make_brand_gradient_bg(w, h, seed=key)
        img.save(cache_path)
        return img

    try:
        print(f"[gemini {aspect_ratio}] → {prompt[:70]}...", file=sys.stderr)
        bytes_data = gemini_generate_image(
            prompt, api_key, aspect_ratio=aspect_ratio)
        cache_path.write_bytes(bytes_data)
        return Image.open(cache_path).convert("RGB")
    except Exception as exc:
        print(f"[WARN] Gemini selhal ({exc}). Fallback na gradient.",
              file=sys.stderr)
        w, h = 1536, 1536
        if aspect_ratio == "16:9":
            w, h = 1920, 1080
        img = make_brand_gradient_bg(w, h, seed=key)
        img.save(cache_path)
        return img


def make_brand_gradient_bg(
    width: int,
    height: int,
    *,
    seed: str = "default",
) -> Image.Image:
    """Brand gradient background (copper-bronze), fallback když nemáme Gemini.

    Signature hero gradient z brand.md:
      linear-gradient(135deg, #3E1A08 0%, #7A3519 60%, #9C4A28 100%)
    """
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height), HERO_DARK)
    draw = ImageDraw.Draw(img)
    # diagonální gradient s 3 stopy
    stops = [
        (0.00, COPPER_DARK),
        (0.60, COPPER_DEEP),
        (1.00, COPPER),
    ]
    diag = width + height
    for i in range(diag):
        t = i / diag
        # najdi segment
        c = stops[-1][1]
        for j in range(len(stops) - 1):
            t0, c0 = stops[j]
            t1, c1 = stops[j + 1]
            if t0 <= t <= t1:
                u = (t - t0) / max(1e-9, t1 - t0)
                c = tuple(int(c0[k] + (c1[k] - c0[k]) * u) for k in range(3))
                break
        draw.line([(i, 0), (0, i)], fill=c)

    # jemný vignette/texture overlay
    overlay = Image.new("RGB", (width, height), HERO_DARK)
    mask = Image.new("L", (width, height), 0)
    mdraw = ImageDraw.Draw(mask)
    # radial přetavení středu
    cx, cy = width // 2, int(height * 0.55)
    for r in range(max(width, height), 0, -8):
        alpha = max(0, min(255, int(120 * (r / max(width, height)))))
        mdraw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=alpha)
    img = Image.composite(overlay, img, mask)

    # malý noise (subtle grain)
    grain = Image.new("L", (width, height))
    pixels = [rng.randint(0, 18) for _ in range(width * height // 16)]
    # přesamplujeme větší chunky ať to neni mrtvě uniformní
    small = Image.new("L", (width // 4, height // 4))
    small.putdata([rng.randint(0, 18) for _ in range(small.width * small.height)])
    grain = small.resize((width, height), Image.Resampling.BICUBIC)
    img = Image.eval(Image.merge("RGB", (
        Image.eval(img.split()[0], lambda v: v),
        Image.eval(img.split()[1], lambda v: v),
        Image.eval(img.split()[2], lambda v: v),
    )), lambda v: v)
    # aplikuj grain přes lighten
    grained = Image.new("RGB", (width, height))
    src = img.load()
    gp = grain.load()
    for y in range(0, height, 1):
        for x in range(0, width, 1):
            r, g, b = src[x, y]
            n = gp[x, y]
            grained.putpixel((x, y), (min(255, r + n // 3),
                                      min(255, g + n // 3),
                                      min(255, b + n // 3)))
    return grained


# ---------- image helpers ----------


def resize_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize + center-crop na target (object-fit: cover)."""
    src_ratio = img.width / img.height
    dst_ratio = target_w / target_h
    if src_ratio > dst_ratio:
        # src širší → scale na výšku
        new_h = target_h
        new_w = int(round(new_h * src_ratio))
    else:
        new_w = target_w
        new_h = int(round(new_w / src_ratio))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def apply_darken_gradient(
    img: Image.Image,
    *,
    top_alpha: int = 0,
    bottom_alpha: int = 210,
    start_frac: float = 0.35,
) -> Image.Image:
    """Přidá ink-tmavý vertikální gradient overlay pro čitelnost textu."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    w, h = img.size
    start_y = int(h * start_frac)
    for y in range(h):
        if y < start_y:
            a = top_alpha
        else:
            t = (y - start_y) / max(1, h - start_y)
            a = int(top_alpha + (bottom_alpha - top_alpha) * (t ** 1.4))
        odraw.line([(0, y), (w, y)], fill=(HERO_DARK[0], HERO_DARK[1], HERO_DARK[2], a))
    base = img.convert("RGBA")
    base.alpha_composite(overlay)
    return base.convert("RGB")


def paste_logo(
    canvas: Image.Image,
    logo_path: Path,
    *,
    target_height: int,
    anchor: str = "top-right",
    margin: int = 48,
    invert: bool = False,
) -> None:
    """Vloží monogram znak do rohu.

    invert=True → pokud pozadí je světlé, chceme tmavou verzi loga.
    (Brand logo je měděné, tedy pracuje dobře na světlém i tmavém bg, ale
    na světlém ho můžeme mírně ztmavit pro lepší kontrast.)
    """
    if not logo_path.exists():
        return
    logo = Image.open(logo_path).convert("RGBA")
    ratio = logo.width / logo.height
    h = target_height
    w = int(round(h * ratio))
    logo = logo.resize((w, h), Image.Resampling.LANCZOS)

    W, H = canvas.size
    if anchor == "top-right":
        x, y = W - w - margin, margin
    elif anchor == "top-left":
        x, y = margin, margin
    elif anchor == "bottom-right":
        x, y = W - w - margin, H - h - margin
    elif anchor == "bottom-left":
        x, y = margin, H - h - margin
    elif anchor == "center":
        x, y = (W - w) // 2, (H - h) // 2
    else:
        x, y = margin, margin
    canvas.paste(logo, (x, y), logo)


def draw_brand_frame(
    canvas: Image.Image,
    *,
    inset: int = 24,
    width: int = 2,
    color: tuple[int, int, int] = COPPER,
) -> None:
    """Jemný copper rámeček kolem celého slidu — brand konzistence.

    Pracuje na dark i ivory bg; copper tone má dost saturace aby
    zůstal čitelný na obojím.
    """
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    W, H = canvas.size
    # rgba alfa cca 200 — nechceme hard fotorámček, jen jemné ohraničení
    fill = (color[0], color[1], color[2], 210)
    odraw.rectangle(
        [inset, inset, W - inset - 1, H - inset - 1],
        outline=fill, width=width,
    )
    base = canvas.convert("RGBA")
    base.alpha_composite(overlay)
    # mutate in-place
    canvas.paste(base.convert("RGB"))


# ---------- text helpers ----------


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Zalomí text na řádky nepřesahující max_width."""
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = " ".join(cur + [w])
        tw = draw.textlength(trial, font=font)
        if tw <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    *,
    fill: tuple[int, int, int] | tuple[int, int, int, int],
    max_width: int,
    line_height: float = 1.15,
) -> tuple[int, int]:
    """Vykreslí zalomený text, vrátí (konečné x, konečné y = y po poslední lince)."""
    x, y = xy
    lines = wrap_text(draw, text, font, max_width)
    lh = int(font.size * line_height)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += lh
    return x, y


def fit_text_by_font_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_width: int,
    max_height: int,
    *,
    start_size: int,
    min_size: int = 24,
    line_height: float = 1.08,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Najde největší font_size, kdy se `text` vejde do max_width × max_height."""
    for size in range(start_size, min_size - 1, -2):
        font = ImageFont.truetype(str(font_path), size=size)
        lines = wrap_text(draw, text, font, max_width)
        total_h = int(size * line_height) * len(lines)
        if total_h <= max_height:
            return font, lines, int(size * line_height)
    # pod min — vrať min stejně
    font = ImageFont.truetype(str(font_path), size=min_size)
    lines = wrap_text(draw, text, font, max_width)
    return font, lines, int(min_size * line_height)


# ---------- prompt generation pro Gemini ----------


def theme_prompt_for_article(article: dict) -> str:
    """Staví Gemini prompt unikátní pro každý článek.

    Strategie:
    - brand prefix (fixní paleta a styl)
    - skutečný titulek + first bullet + why_matters jako koncept k vizualizaci
    - explicitní hard ban na text, screens, logos, stock tech imagery
    - instrukce aby Gemini zvolil editorial still life / architektonický detail
      místo literární ilustrace

    Díky skutečnému obsahu článku v promptu dostane každá novinka
    unikátní obrázek a Gemini má reálný kontext co kreslit.
    """
    title = article.get("title_cs") or article.get("title_orig") or ""
    bullets = article.get("summary_cs") or []
    first_bullet = bullets[0] if bullets else ""
    why = article.get("why_matters") or ""

    # sestavíme kompaktní koncept: titulek + 1-2 vět kontextu
    concept_parts = [title]
    if first_bullet:
        concept_parts.append(first_bullet)
    if why and len(why) > 20:
        concept_parts.append(why)
    concept = " ".join(concept_parts)
    # zkrátíme aby Gemini prompt nebyl přes 2000 tokenů
    concept = concept[:600]

    return (
        NEWS_PROMPT_PREFIX
        + "\n\nStory this image illustrates (context only, never render any "
          "of these words as text): «" + concept + "»\n\n"
          "The image MUST concretely illustrate THIS specific story. "
          "First, identify what the story is actually about (a deal, a "
          "product launch, a competition, a research result, a regulation, "
          "an infrastructure buildout, a funding round, …) and then choose "
          "a single real-world scene that a reader would instantly read "
          "as depicting THAT specific event. No generic AI imagery, no "
          "abstract sculpture that could illustrate any story. If two "
          "parties are involved, show both of them visually (objects, "
          "materials, symbols). If money/a deal is involved, show a "
          "commercial tableau. If it's a new model/product, show it as a "
          "tangible hero object.\n\n"
          "Composition: hero subject in the upper 55% of the frame; "
          "the bottom 45% calm and darker (will be covered by text overlay). "
          "Keep top-left and top-right corners simple and low-detail "
          "(eyebrow + logo sit there). "
          "Before finishing, self-check the image: (1) does it clearly and "
          "specifically depict the story above, not a generic AI scene? "
          "(2) is there truly zero text/letters/numbers anywhere, including "
          "no labels in the corners that would overlap the eyebrow or the "
          "logo? If not, revise."
    )


def cover_prompt() -> str:
    """Samostatný prompt pro cover slide.

    Obrázek slouží jako POZADÍ pod logo-znak ('A'), který se nakomponuje
    doprostřed ručně. Prompt proto žádá PRÁZDNOU scénu — drapérie ivory
    lnu na tmavém pedestále, warm copper rim light, žádný centerpiece.
    """
    return (
        BRAND_PROMPT_PREFIX
        + "Editorial still life background: softly draped ivory linen "
          "cloth cascading over a dark mahogany pedestal, deep rich "
          "shadowy background, single warm copper rim light from the "
          "side, quiet museum gallery atmosphere. "
          "THE CENTER OF THE FRAME IS EMPTY — no object, no sculpture, "
          "no centerpiece. Composition is just cloth, light, and "
          "shadow, leaving the center clear for a logo to be placed "
          "later. "
          "STRICT: no text, no letters, no numbers, no logos, no screens, "
          "no people, no objects in the center."
    )


# ---------- caption generation ----------


def cz_date(date_str: str) -> str:
    """ '2026-04-21' → '21. dubna 2026'. """
    months = ["ledna", "února", "března", "dubna", "května", "června",
              "července", "srpna", "září", "října", "listopadu", "prosince"]
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.day}. {months[dt.month - 1]} {dt.year}"


def build_ig_caption(articles: list[dict], date_str: str) -> str:
    """Instagram caption — populárně naučný, widespread, čt."""
    header = f"AI News | {cz_date(date_str)}"
    teaser = (
        "Týden, kdy Anthropic vystřelil Claude Opus 4.7, Moonshot ukázal, "
        "že open-source dohání frontier labs, a hyperscalery se dál "
        "„kruhově\" financují. Ve zkratce:"
    )
    lines = [header, "", teaser, ""]
    for i, a in enumerate(articles, 1):
        title = a.get("title_cs") or a.get("title_orig") or ""
        # zkrať na max ~90 znaků
        if len(title) > 90:
            title = title[:87] + "..."
        lines.append(f"{i}. {title}")
    lines += [
        "",
        "Celý digest v PDF + více kontextu na ainamiru.cz",
        "",
        "#AINews #AI #Anthropic #OpenAI #Gemini #AInaMiru",
    ]
    return "\n".join(lines)


def build_linkedin_caption(articles: list[dict], date_str: str) -> str:
    """LinkedIn caption — profi tón, hook → kontext → takeaway → CTA."""
    hook = (
        f"7 věcí, které se za posledních pár dní staly v AI "
        f"a měly by vás zajímat ({cz_date(date_str)})."
    )
    context = (
        "Anthropic vypustil Opus 4.7 a Claude Design (přímý útok na Figmu/Canvu), "
        "Moonshot s Kimi K2.6 ukázal, že open-weight modely drží tempo, "
        "a hyperscalery se dál skládají do \"kruhových\" dealů, co nafukují compute "
        "kapacitu i vykazované tržby."
    )
    takeaway = (
        "Co si z toho odnést: aplikační vrstva se zrychluje (design, coding, agenti), "
        "open-source přestává být alternativa pro šetřílky a stává se reálnou volbou "
        "pro produkční nasazení, a kapitálová dynamika v AI má dál svoji "
        "vlastní gravitaci."
    )
    cta = (
        "Plné shrnutí (s odkazy na originální zdroje) najdete v priloženém PDF. "
        "Který z těchto bodů rezonuje s vaším nejbližším roadmapem?"
    )
    lines = [hook, "", context, "", takeaway, "", cta, "",
             "#AINews #ArtificialIntelligence #LLM #Anthropic #OpenAI #AInaMiru"]
    return "\n".join(lines)


# ---------- slide composition ----------


@dataclass
class SlideSize:
    width: int
    height: int
    margin: int
    logo_h: int
    title_start: int
    title_max: int


IG_SIZE = SlideSize(
    width=1080, height=1080, margin=72,
    logo_h=72,
    title_start=54,  # velikost titulku
    title_max=120,
)
LI_SIZE = SlideSize(
    width=1920, height=1080, margin=96,
    logo_h=80,
    title_start=72,
    title_max=120,
)


def compose_cover_slide(
    article: dict,
    bg_img: Image.Image,
    fonts: dict[str, Path],
    *,
    size: SlideSize,
    date_str: str,
) -> Image.Image:
    """Cover slide:
        — rozmazané pozadí (cloth + logo-znak zabudované do scény) s blurem
        — velký hero tagline uprostřed přes pozadí
        — spodní blok: 'AI News' + datum + '7 věcí, co se za posledních pár
          dní stalo v AI světě'
        — malé logo vpravo nahoře a eyebrow vlevo nahoře (konzistentně
          se zbytkem carouselu)
    """
    W, H = size.width, size.height
    bg = resize_cover(bg_img, W, H)

    # --- KOMPOZICE POZADÍ: logo zapečeme DO scény, pak celé rozmažeme ---
    # Tak bude logo vypadat jako atmosférický prvek scény (ne jako vrstva
    # nalepená navrch). Logo dělá cca 45 % výšky a je pod blurem.
    if LOGO_ZNAK.exists():
        hero = Image.open(LOGO_ZNAK).convert("RGBA")
        hero_target_h = int(H * 0.48)
        ratio = hero.width / hero.height
        hero = hero.resize(
            (int(hero_target_h * ratio), hero_target_h),
            Image.Resampling.LANCZOS,
        )
        # lehce ztlumíme alphu — logo bude splývat s pozadím, nebude plné
        alpha = hero.split()[-1]
        alpha = alpha.point(lambda v: int(v * 0.82))
        hero.putalpha(alpha)
        hx = (W - hero.width) // 2
        hy = int(H * 0.10)
        bg.paste(hero, (hx, hy), hero)

    # Teď rozmažem celou scénu i s logem — působí to pak jako jeden obraz
    bg = bg.filter(ImageFilter.GaussianBlur(radius=8))
    # darken, aby byl hero tagline i spodní blok čitelný
    bg = apply_darken_gradient(bg, top_alpha=70, bottom_alpha=215, start_frac=0.40)

    draw = ImageDraw.Draw(bg)
    margin = size.margin

    # malé logo vpravo nahoře (nad blur, ostré) — brand mark
    paste_logo(bg, LOGO_ZNAK, target_height=size.logo_h,
               anchor="top-right", margin=margin)

    # UPPERCASE eyebrow vlevo nahoře
    eyebrow = "AI NEWS  ·  DENNÍ DIGEST"
    eb_font = pil_font(fonts, "body_bold", 20 if W <= 1200 else 24)
    eb_text = add_letter_spacing(eyebrow, 0.24)
    draw.text((margin, margin + 10), eb_text, font=eb_font, fill=AMBER)

    # --- HERO TAGLINE (uprostřed, velký, přes rozmazanou fotku) ---
    # Brand pravidlo: zakazana em-dash "—" i en-dash "–" v textu.
    tagline_lines = [
        "AI se posouvá rychleji",
        "než kdy dřív.",
        "Swajpni pro aktuální novinky.",
    ]
    tag_size = 100 if W > 1200 else 78
    tag_font = ImageFont.truetype(str(fonts["display_italic"]), size=tag_size)
    ta_asc, ta_desc = tag_font.getmetrics()
    tag_line_h = int((ta_asc + ta_desc) * 1.05)
    tag_block_h = tag_line_h * len(tagline_lines)
    tag_y0 = int(H * 0.38) - tag_block_h // 2

    # Tmavý měkký halo za textem — dvě vrstvy (široká měkká + užší tvrdší),
    # pak ivory text v popředí. Dá to výrazný kontrast i na busy bg.
    def _render_halo(alpha: int, radius: int) -> Image.Image:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for i, ln in enumerate(tagline_lines):
            tw = ld.textlength(ln, font=tag_font)
            tx = (W - tw) // 2
            ld.text((tx, tag_y0 + i * tag_line_h), ln,
                    font=tag_font, fill=(0, 0, 0, alpha))
        return layer.filter(ImageFilter.GaussianBlur(radius=radius))

    wide_halo = _render_halo(alpha=200, radius=28)
    tight_halo = _render_halo(alpha=245, radius=10)
    bg = Image.alpha_composite(bg.convert("RGBA"), wide_halo)
    bg = Image.alpha_composite(bg, tight_halo).convert("RGB")
    draw = ImageDraw.Draw(bg)

    # hlavní text v ivory přes halo — dvojitý stroke pro extra čitelnost
    for i, ln in enumerate(tagline_lines):
        tw = draw.textlength(ln, font=tag_font)
        tx = (W - tw) // 2
        draw.text((tx, tag_y0 + i * tag_line_h), ln,
                  font=tag_font, fill=IVORY,
                  stroke_width=1, stroke_fill=(0, 0, 0, 160))

    # --- SPODNÍ BLOK: titul AI News + datum + teaser '7 věcí...' ---
    title = "AI News"
    title_size = 160 if W > 1200 else 120
    title_font = ImageFont.truetype(str(fonts["display"]), size=title_size)
    t_asc, t_desc = title_font.getmetrics()
    title_line_h = t_asc + t_desc

    sub_text = cz_date(date_str)
    sub_size = 30 if W > 1200 else 26
    sub_font = pil_font(fonts, "body", sub_size)
    s_asc, s_desc = sub_font.getmetrics()
    sub_line_h = s_asc + s_desc

    teaser_text = "7 věcí, co se za posledních pár dní stalo v AI světě"
    teas_size = 36 if W > 1200 else 30
    teaser_font = ImageFont.truetype(str(fonts["display_italic"]), size=teas_size)
    te_asc, te_desc = teaser_font.getmetrics()
    teas_line_h = te_asc + te_desc

    gap_title_sub = int(title_size * 0.18)
    gap_sub_teaser = int(sub_size * 1.1)

    block_h = (
        title_line_h + gap_title_sub + sub_line_h + gap_sub_teaser + teas_line_h
    )
    y = H - margin - block_h

    draw.line([(margin, y - 24), (margin + 120, y - 24)],
              fill=COPPER, width=3)

    draw.text((margin, y), title, font=title_font, fill=IVORY)
    y += title_line_h + gap_title_sub
    draw.text((margin, y), sub_text, font=sub_font, fill=INK_GHOST)
    y += sub_line_h + gap_sub_teaser
    draw.text((margin, y), teaser_text, font=teaser_font, fill=AMBER)

    # brand frame
    draw_brand_frame(bg)

    return bg


def compose_news_slide(
    article: dict,
    bg_img: Image.Image,
    fonts: dict[str, Path],
    *,
    size: SlideSize,
    number: int,
) -> Image.Image:
    """News slide — split layout:
        top ~55 % = clean hero image (malý darken u horního rohu pro eyebrow)
        bottom ~45 % = rozmazaný text panel (kopie stejného bg + tmavý overlay)
    """
    W, H = size.width, size.height
    # landscape = LinkedIn 16:9; square-ish = IG 1:1
    is_landscape = (W / H) > 1.3

    if is_landscape:
        # LinkedIn: full-bleed 16:9 obrázek přes celý slide (žádný crop —
        # 1920×1080 ≈ 16:9 tak resize_cover sedí). Text jde jako overlay
        # dole přes plynulý alpha gradient, aby obrázek zůstal celý viditelný.
        split_y = int(H * 0.55)
        bg = resize_cover(bg_img, W, H)
        # lehký celoplošný darken + silný přechod v dolní části pro text
        bg = apply_darken_gradient(
            bg, top_alpha=50, bottom_alpha=235, start_frac=0.40,
        )
    else:
        # IG: split layout. Horní obrázková zóna W × split_y ≈ 16:9 tak
        # obrázek sedí téměř bez ořezu. Pod ní samostatný tmavý text panel.
        split_y = int(H * 0.55)
        bg = Image.new("RGB", (W, H), INK)
        top_img = resize_cover(bg_img, W, split_y)
        bg.paste(top_img, (0, 0))
        # jemný darken gradient přes horní obrázek
        top_only = bg.crop((0, 0, W, split_y))
        top_only = apply_darken_gradient(
            top_only, top_alpha=60, bottom_alpha=110, start_frac=0.00,
        )
        bg.paste(top_only, (0, 0))
        # spodní blurovaný strip + tmavý overlay
        strip_src_h = max(8, int(split_y * 0.2))
        bottom_src = top_img.crop((0, split_y - strip_src_h, W, split_y))
        bottom_strip = bottom_src.resize(
            (W, H - split_y), Image.Resampling.LANCZOS)
        bottom_strip = bottom_strip.filter(ImageFilter.GaussianBlur(radius=48))
        dark = Image.new("RGBA", bottom_strip.size, (*INK, 220))
        bottom_strip = Image.alpha_composite(
            bottom_strip.convert("RGBA"), dark).convert("RGB")
        bg.paste(bottom_strip, (0, split_y))

    draw = ImageDraw.Draw(bg)
    margin = size.margin

    # logo vpravo nahoře
    paste_logo(bg, LOGO_ZNAK, target_height=size.logo_h,
               anchor="top-right", margin=margin)

    # ---- eyebrow (číslo + category) v horní části ----
    num_text = f"{number:02d} / 07"
    num_font = pil_font(fonts, "body", 24 if W > 1200 else 20)
    draw.text((margin, margin + 12), add_letter_spacing(num_text, 0.20),
              font=num_font, fill=INK_GHOST)

    cat = (article.get("category") or "").upper()
    if cat:
        cat_font = pil_font(fonts, "body_bold", 20 if W > 1200 else 18)
        draw.text((margin, margin + 44),
                  add_letter_spacing(cat, 0.28),
                  font=cat_font, fill=AMBER)

    # ---- tenký copper divider mezi obrázkem a text panelem ----
    # U landscape (LinkedIn) je přechod řešen gradientem → divider by tam
    # byl tvrdý řez přes obrázek. Jen u split (IG) ho kreslíme.
    if not is_landscape:
        draw.line([(0, split_y), (W, split_y)], fill=COPPER, width=3)

    # ---- text panel (titul + bullets) ----
    # Zóna: split_y + 36  →  H - margin - 32 (footer)
    panel_top = split_y + 36
    panel_bottom = H - margin - 32

    # titul — auto-fit na 2-3 řádky uvnitř panelu
    title = strip_brand_dashes(
        article.get("title_cs") or article.get("title_orig") or ""
    )
    title_max_width = W - 2 * margin
    # titulu dáme zhruba 40 % výšky panelu (zbytek na bullets + mezery)
    title_max_height = int((panel_bottom - panel_top) * 0.45)
    # startovní velikost lehce menší než origo (panel je menší než celý slide)
    start_size = 72 if W > 1200 else 56
    title_font, title_lines, title_lh = fit_text_by_font_size(
        draw, title, fonts["display"],
        max_width=title_max_width,
        max_height=title_max_height,
        start_size=start_size,
        min_size=36,
        line_height=1.08,
    )

    y = panel_top
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=IVORY)
        y += title_lh
    y += 24  # gap title→bullets

    # ---- bullets (top 3) ----
    bullets = [strip_brand_dashes(b) for b in (article.get("summary_cs") or [])[:3]]
    b_font_size = 22 if W <= 1200 else 26
    b_font = pil_font(fonts, "body", b_font_size)
    b_lh = int(b_font_size * 1.35)
    bullet_indent = 36
    bullet_max_w = W - 2 * margin - bullet_indent

    for bullet in bullets:
        # copper dot marker
        dot_r = 4
        dot_y = y + b_font_size // 2 + 2
        draw.ellipse(
            [margin + 6, dot_y - dot_r, margin + 6 + 2 * dot_r, dot_y + dot_r],
            fill=COPPER,
        )
        lines = wrap_text(draw, bullet, b_font, bullet_max_w)
        for ln in lines:
            draw.text((margin + bullet_indent, y), ln, font=b_font, fill=IVORY)
            y += b_lh
        y += 8  # gap mezi bullets
        if y > panel_bottom - 28:
            break

    # ---- patička (zdroj vlevo + logo-napis uprostřed) ----
    source = strip_brand_dashes(article.get("source_name") or "")
    foot_font_size = 16 if W > 1200 else 14
    foot_font = pil_font(fonts, "body", foot_font_size)
    foot_y = H - margin - 22
    if source:
        draw.text((margin, foot_y),
                  f"Zdroj: {source}", font=foot_font, fill=INK_GHOST)

    # logo-napis "ai na miru" uprostřed dole, mírně větší než zdroj
    if LOGO_NAPIS.exists():
        napis = Image.open(LOGO_NAPIS).convert("RGBA")
        # Soubor má obrovský transparent padding + ghost alpha v rozích,
        # kvůli kterému PIL getbbox() vrací plné plátno. Uděláme tight bbox
        # přes threshold alpha > 30.
        alpha = napis.split()[-1]
        mask = alpha.point(lambda v: 255 if v > 30 else 0)
        tight = mask.getbbox()
        if tight:
            napis = napis.crop(tight)
        # 2.8× velikost zdrojového textu — viditelné, ale stále diskrétní
        target_h = int(foot_font_size * 2.8)
        ratio = napis.width / napis.height
        target_w = int(target_h * ratio)
        napis = napis.resize((target_w, target_h), Image.Resampling.LANCZOS)
        nx = (W - target_w) // 2
        # vertikálně mírně pod linkou zdroje (user feedback: níže)
        ny = foot_y + (foot_font_size - target_h) // 2 + 14
        bg.paste(napis, (nx, ny), napis)

    # ---- brand frame (jemná copper linka kolem celého slidu) ----
    draw_brand_frame(bg)

    return bg


def compose_outro_slide(
    fonts: dict[str, Path],
    *,
    size: SlideSize,
) -> Image.Image:
    """Outro slide: ivory bg, velké logo + CTA."""
    W, H = size.width, size.height
    bg = Image.new("RGB", (W, H), IVORY)

    # jemný radial overlay kolem středu — trochu warm
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    cx, cy = W // 2, int(H * 0.42)
    for r in range(max(W, H), 0, -16):
        a = max(0, min(20, int(20 * (r / max(W, H)))))
        odraw.ellipse([cx - r, cy - r, cx + r, cy + r],
                      fill=(COPPER[0], COPPER[1], COPPER[2], a))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    # velké logo (monogram) uprostřed nahoře
    if LOGO_ZNAK.exists():
        logo = Image.open(LOGO_ZNAK).convert("RGBA")
        target_h = 360 if W > 1200 else 280
        ratio = logo.width / logo.height
        logo = logo.resize((int(target_h * ratio), target_h),
                           Image.Resampling.LANCZOS)
        lx = (W - logo.width) // 2
        ly = int(H * 0.22)
        bg.paste(logo, (lx, ly), logo)

    draw = ImageDraw.Draw(bg)

    # firma jméno pod logem (napis variant)
    if LOGO_NAPIS.exists():
        napis = Image.open(LOGO_NAPIS).convert("RGBA")
        target_h = 80 if W > 1200 else 60
        ratio = napis.width / napis.height
        napis = napis.resize((int(target_h * ratio), target_h),
                             Image.Resampling.LANCZOS)
        nx = (W - napis.width) // 2
        ny = int(H * 0.22) + (360 if W > 1200 else 280) + 32
        bg.paste(napis, (nx, ny), napis)
        y_after_brand = ny + napis.height
    else:
        name_font = pil_font(fonts, "display", 64 if W > 1200 else 48)
        name = "AI NA MÍRU"
        nw = draw.textlength(name, font=name_font)
        nx = (W - nw) // 2
        ny = int(H * 0.22) + (360 if W > 1200 else 280) + 32
        draw.text((nx, ny), name, font=name_font, fill=INK)
        y_after_brand = ny + 64

    # CTA blok
    cta_h_font = ImageFont.truetype(str(fonts["display_italic"]),
                                    size=56 if W > 1200 else 44)
    cta_text = "Denní digest v plné podobě"
    cta_w = draw.textlength(cta_text, font=cta_h_font)
    cta_y = y_after_brand + 64
    draw.text(((W - cta_w) // 2, cta_y), cta_text,
              font=cta_h_font, fill=COPPER)

    url_font = pil_font(fonts, "body", 32 if W > 1200 else 26)
    url_text = "ainamiru.cz"
    url_w = draw.textlength(add_letter_spacing(url_text, 0.12), font=url_font)
    draw.text(((W - url_w) // 2, cta_y + 80),
              add_letter_spacing(url_text, 0.12),
              font=url_font, fill=INK_SOFT)

    # copper rule pod CTA
    rule_w = 80
    draw.line([((W - rule_w) // 2, cta_y + 140),
               ((W + rule_w) // 2, cta_y + 140)],
              fill=COPPER, width=3)

    # footer info
    foot_font = pil_font(fonts, "body", 18 if W > 1200 else 16)
    foot_text = "AI News, kurátorovaný denní výběr novinek ze světa AI"
    fw = draw.textlength(foot_text, font=foot_font)
    draw.text(((W - fw) // 2, H - size.margin - 32),
              foot_text, font=foot_font, fill=INK_DIM)

    # brand frame
    draw_brand_frame(bg)

    return bg


def add_letter_spacing(text: str, _frac: float) -> str:
    """PIL nemá native letter-spacing. Insertujeme thin-space char mezi znaky.
    Pro jednoduchost pro UPPERCASE labely stačí standardní rendering —
    tato funkce je jen placeholder pokud by se přidal spacer hack."""
    # Použijeme hair space mezi písmeny pro UPPERCASE labely (drobný efekt)
    return "\u200a".join(text)  # hair space — viditelný jako malý gap


# ---------- PDF build (LinkedIn) ----------


def build_linkedin_pdf(slide_images: list[Image.Image], out_path: Path) -> None:
    """Slepí 9 landscape slidů do jednoho PDF (každý slide = jedna strana)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # PIL umí `img.save(pdf, save_all=True, append_images=[...])`
    first, rest = slide_images[0].convert("RGB"), [s.convert("RGB") for s in slide_images[1:]]
    first.save(
        out_path,
        format="PDF",
        save_all=True,
        append_images=rest,
        resolution=150.0,
    )


# ---------- main orchestration ----------


def prewarm_single_bg(
    selected: dict,
    bg_index: int,
    *,
    use_gemini: bool,
    api_key: str | None,
) -> None:
    """Vygeneruje (a zacachuje) jedno konkrétní pozadí a skončí.

    bg_index:
        0     → cover pozadí (cover_prompt)
        1..7  → pozadí news článku i (theme_prompt_for_article(top7[i-1]))

    Cílem je rozbít jeden velký Gemini batch do samostatných bash volání
    tak, aby se každé vešlo pod 45s sandbox timeout. Finální volání skriptu
    bez flagu pak jen skládá slidy — všechna pozadí jsou v cache.
    """
    articles = sorted(selected["articles"], key=lambda a: a.get("rank", 99))
    top7 = articles[:7]
    if bg_index < 0 or bg_index > 7:
        raise SystemExit(f"--bg-only musí být 0..7, dostal {bg_index}")

    if bg_index == 0:
        prompt = cover_prompt()
        label = "cover"
    else:
        art = top7[bg_index - 1]
        prompt = theme_prompt_for_article(art)
        label = f"news-{bg_index}"

    # Sdílená pozadí držíme v 16:9 (IG si je pak center-cropne).
    get_or_generate_bg(
        prompt, api_key=api_key, use_gemini=use_gemini, aspect_ratio="16:9",
    )
    print(f"[bg-only {bg_index}] {label} OK")


def process_articles_for_day(
    selected: dict,
    *,
    use_gemini: bool,
    only: str,
    api_key: str | None,
) -> None:
    date_str = selected["date"]
    articles = sorted(selected["articles"], key=lambda a: a.get("rank", 99))
    # bereme top 7 pro carousel
    top7 = articles[:7]
    cover_article = top7[0]

    fonts = load_fonts()

    # --- Sdílená pozadí pro IG i LinkedIn ---
    # Generujeme JEDNO pozadí per článek v 16:9 (širší kompozice). IG pak
    # dostane stejný obrázek, jen center-cropnutý na 1:1 přes resize_cover.
    # Důvod: chceme aby ten samý článek měl stejný hero obraz na obou
    # kanálech, jinak to vypadá divně.
    SHARED_RATIO = "16:9"

    shared_bgs: list[Image.Image] = []
    for art in top7:
        prompt = theme_prompt_for_article(art)
        shared_bgs.append(get_or_generate_bg(
            prompt, api_key=api_key, use_gemini=use_gemini,
            aspect_ratio=SHARED_RATIO,
        ))

    shared_cover_bg = get_or_generate_bg(
        cover_prompt(), api_key=api_key, use_gemini=use_gemini,
        aspect_ratio=SHARED_RATIO,
    )

    IG_DIR.mkdir(parents=True, exist_ok=True)
    LI_DIR.mkdir(parents=True, exist_ok=True)
    ig_day = IG_DIR / date_str
    li_day = LI_DIR / date_str
    ig_day.mkdir(parents=True, exist_ok=True)
    li_day.mkdir(parents=True, exist_ok=True)

    def do_ig():
        slides: list[Image.Image] = []
        # 01 cover — stejný bg jako pro LinkedIn, center-crop na 1:1
        slides.append(compose_cover_slide(
            cover_article, shared_cover_bg, fonts,
            size=IG_SIZE, date_str=date_str))
        # 02-08 news
        for i, art in enumerate(top7, start=1):
            slides.append(compose_news_slide(
                art, shared_bgs[i - 1], fonts, size=IG_SIZE, number=i))
        # 09 outro
        slides.append(compose_outro_slide(fonts, size=IG_SIZE))

        for i, s in enumerate(slides, 1):
            out = ig_day / f"slide_{i:02d}.png"
            s.save(out, format="PNG", optimize=True)
            print(f"[ig] {out.relative_to(ROOT)}")

        (ig_day / "caption.txt").write_text(
            build_ig_caption(top7, date_str), encoding="utf-8")
        print(f"[ig] caption -> {(ig_day / 'caption.txt').relative_to(ROOT)}")

    def do_linkedin():
        slides: list[Image.Image] = []
        # 01 cover
        slides.append(compose_cover_slide(
            cover_article, shared_cover_bg, fonts,
            size=LI_SIZE, date_str=date_str))
        # 02-08 news
        for i, art in enumerate(top7, start=1):
            slides.append(compose_news_slide(
                art, shared_bgs[i - 1], fonts, size=LI_SIZE, number=i))
        # 09 outro
        slides.append(compose_outro_slide(fonts, size=LI_SIZE))

        # Ulozit i jako PNG pro kontrolu
        for i, s in enumerate(slides, 1):
            out = li_day / f"slide_{i:02d}.png"
            s.save(out, format="PNG", optimize=True)
            print(f"[li] {out.relative_to(ROOT)}")

        # A hlavni deliverable: jedno PDF
        pdf_path = li_day / f"ai-news_{date_str}.pdf"
        build_linkedin_pdf(slides, pdf_path)
        print(f"[li] pdf -> {pdf_path.relative_to(ROOT)}")

        (li_day / "caption.txt").write_text(
            build_linkedin_caption(top7, date_str), encoding="utf-8")
        print(f"[li] caption -> {(li_day / 'caption.txt').relative_to(ROOT)}")

    if only in ("ig", "both"):
        do_ig()
    if only in ("linkedin", "both"):
        do_linkedin()


# ---------- CLI ----------


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate AI News IG + LinkedIn posts.")
    ap.add_argument("selected_json", type=Path, help="Path to _selected.json (from digest).")
    ap.add_argument(
        "--only",
        choices=["ig", "linkedin", "both"],
        default="both",
        help="Which channel to generate (default: both).",
    )
    ap.add_argument(
        "--no-gemini",
        action="store_true",
        help="Skip Gemini image generation; use branded gradient backgrounds instead.",
    )
    ap.add_argument(
        "--clear-cache",
        action="store_true",
        help="Wipe the Gemini image cache directory before generating.",
    )
    ap.add_argument(
        "--bg-only",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Pre-warm cache pro jedno pozadi a skonci. 0=cover, 1..7=news. "
            "Pouzito scheduled taskem pro rozbiti Gemini batche na samostatna "
            "bash volani (45s sandbox timeout)."
        ),
    )
    args = ap.parse_args()

    load_env()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    use_gemini = (not args.no_gemini) and bool(api_key)

    if args.clear_cache and BG_CACHE_DIR.exists():
        for f in BG_CACHE_DIR.iterdir():
            try:
                f.unlink()
            except OSError:
                pass
        print(f"[cache] cleared {BG_CACHE_DIR}")

    if not args.selected_json.exists():
        raise SystemExit(f"selected_json not found: {args.selected_json}")

    selected = json.loads(args.selected_json.read_text(encoding="utf-8"))

    if args.bg_only is not None:
        prewarm_single_bg(
            selected,
            args.bg_only,
            use_gemini=use_gemini,
            api_key=api_key,
        )
        return

    process_articles_for_day(
        selected,
        use_gemini=use_gemini,
        only=args.only,
        api_key=api_key,
    )


if __name__ == "__main__":
    main()
