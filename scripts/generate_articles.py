#!/usr/bin/env python3
"""
generate_articles.py — Vygeneruje top-N (default 7) článků z digestu denního výběru.

Pipeline:
  1. Gemini text → strukturovaný markdown s delimitery
  2. Per-article validace (text + fact-check + image)
  3. Po-run second check — co chybí, regeneruje
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# ---------- paths ----------
ROOT = Path(__file__).resolve().parent.parent
DIGEST_DIR = ROOT / "digest"
OUTPUT_DIR = ROOT / "output" / "articles"
TEMPLATE_PATH = ROOT.parent / "ai-na-miru" / "articles" / "article-template.html"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------- ENV ----------
def load_env():
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


# ---------- categories ----------
CATEGORIES = {
    "vyvoj-ai":     "Vývoj AI",
    "ai-ekonomika": "AI ekonomika",
    "chatgpt":      "ChatGPT",
    "claude":       "Claude",
    "gemini":       "Gemini",
    "copilot":      "Copilot",
    "dalsi":        "Další",
}

KEYWORD_CATEGORY = [
    ("chatgpt",      ["chatgpt", "openai", "gpt-4", "gpt-5", "sam altman", "dall-e", "sora"]),
    ("claude",       ["claude", "anthropic"]),
    ("gemini",       ["gemini", "deepmind", "google ai"]),
    ("copilot",      ["copilot", "microsoft"]),
    ("ai-ekonomika", ["ipo", "valuace", "investice", "miliard", "kapitál", "fundraising", "akvizice", "regulace", "ai act"]),
    ("vyvoj-ai",     ["model", "trénink", "research", "paper", "benchmark", "fine-tun", "rlhf", "scaling"]),
]

def keyword_category(text: str) -> str:
    t = text.lower()
    for cat, kws in KEYWORD_CATEGORY:
        if any(kw in t for kw in kws):
            return cat
    return "dalsi"


# ---------- slugify ----------
def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "article"


# ---------- Gemini text ----------
def gemini_text(prompt: str, model: str = "gemini-2.5-flash", temperature: float = 0.7, max_tokens: int = 8192, retries: int = 4) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    import requests
    models_to_try = [model]
    if model == "gemini-2.5-flash":
        models_to_try.extend(["gemini-2.5-flash-lite", "gemini-1.5-flash"])
    last_err = None
    for m in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        for attempt in range(retries):
            try:
                r = requests.post(url, json=payload, timeout=60)
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = (2 ** attempt) * 4 + 1
                    print(f"  ↻ {m} {r.status_code}, retry za {wait}s ({attempt+1}/{retries})", file=sys.stderr)
                    time.sleep(wait)
                    last_err = f"HTTP {r.status_code} on {m}"
                    continue
                r.raise_for_status()
                data = r.json()
                parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                return "".join(p.get("text", "") for p in parts).strip()
            except requests.exceptions.Timeout:
                wait = (2 ** attempt) * 4 + 1
                print(f"  ↻ {m} timeout, retry za {wait}s ({attempt+1}/{retries})", file=sys.stderr)
                time.sleep(wait)
                last_err = f"timeout on {m}"
            except requests.exceptions.ConnectionError:
                wait = (2 ** attempt) * 4 + 1
                print(f"  ↻ {m} connection error, retry za {wait}s ({attempt+1}/{retries})", file=sys.stderr)
                time.sleep(wait)
                last_err = f"connection on {m}"
        if len(models_to_try) > 1 and m != models_to_try[-1]:
            print(f"  ⤷ {m} se nedaří, zkouším {models_to_try[-1]}", file=sys.stderr)
    raise RuntimeError(f"Gemini text failed: {last_err}")


# ---------- Gemini Image ----------
SAFE_FALLBACK_IMAGE_PROMPT = (
    "Editorial documentary photography, photorealistic, professional press photo style. "
    "Subject: a quiet modern office workspace with monitors and notebooks on a desk, large window with soft daylight, no people. "
    "Composition: cinematic wide angle, neutral lighting, real-world environment. "
    "Mood: serious, journalistic, business publication aesthetic. "
    "Color palette: muted earth tones, warm but desaturated. "
    "Quality: high resolution, sharp focus, real photo. "
    "Strict NO: no text, no logos, no watermarks, no AI artifacts, no fantasy, no neon."
)


def _gemini_image_call(model: str, prompt: str) -> tuple[bytes | None, str | None]:
    """Jeden Gemini Image call. Vrátí (bytes, error_str)."""
    import requests
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    try:
        r = requests.post(url, json=payload, timeout=120)
        if r.status_code == 404:
            return None, f"{model} 404"
        if r.status_code in (429, 500, 502, 503, 504):
            return None, f"{model} {r.status_code}"
        if r.status_code == 400:
            try:
                err = r.json().get("error", {}).get("message", "")[:120]
            except Exception:
                err = r.text[:120]
            return None, f"{model} 400 ({err})"
        r.raise_for_status()
        data = r.json()
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"]), None
            finish = cand.get("finishReason", "")
            if finish and finish != "STOP":
                return None, f"{model} blocked (finishReason={finish})"
        return None, f"{model} no image in response"
    except Exception as e:
        return None, f"{model} {type(e).__name__}: {str(e)[:80]}"


def gemini_image(prompt: str, retries_per_model: int = 2) -> bytes | None:
    """Robustní Gemini Image s fallbacky.

    Strategie: primary 2.5-flash-image (retry s backoffem) → safe prompt na
               primary → fallback model gemini-3.1-flash-image (retry).
    """
    if not GEMINI_API_KEY:
        return None
    primary_models = ["gemini-2.5-flash-image"]
    # POZOR: gemini-2.0-flash-exp-image-generation byl Googlem vyřazen (404) —
    # fallback byl měsíce mrtvý, ověřeno 12.8.2026. Držet aktuální model.
    fallback_models = ["gemini-3.1-flash-image"]
    errors = []

    for m in primary_models:
        for attempt in range(retries_per_model):
            data_bytes, err = _gemini_image_call(m, prompt)
            if data_bytes:
                return data_bytes
            errors.append(f"[{m} t{attempt+1}] {err}")
            if err and any(s in err for s in ("503", "429", "504", "502", "500")):
                time.sleep(5 + attempt * 10)
            else:
                break

    # Safe prompt fallback (pro safety blocks)
    for m in primary_models:
        data_bytes, err = _gemini_image_call(m, SAFE_FALLBACK_IMAGE_PROMPT)
        if data_bytes:
            print(f"  ↻ použit safe fallback prompt", file=sys.stderr)
            return data_bytes
        errors.append(f"[{m} safe] {err}")

    for m in fallback_models:
        for attempt in range(retries_per_model):
            data_bytes, err = _gemini_image_call(m, prompt)
            if data_bytes:
                print(f"  ↻ použit fallback model {m}", file=sys.stderr)
                return data_bytes
            errors.append(f"[{m} t{attempt+1}] {err}")
            if err and "404" in err:
                break
            time.sleep(2)

    print(f"  ⚠ Gemini Image vyčerpalo všechny pokusy:", file=sys.stderr)
    for e in errors[-6:]:
        print(f"     • {e}", file=sys.stderr)
    return None


def looks_like_gradient(image_bytes: bytes) -> bool:
    """Heuristika: je obrázek jen hladký gradient (fallback), ne fotka?

    Gradient fallback má po downsamplu velmi málo unikátních barev a nízkou
    směrodatnou odchylku kanálů; reálné fotky řádově víc. Změřeno na incidentu
    12.8.2026: gradienty ~820 uniq / sd ~11, skutečné fotky 3000+ uniq / sd 25+.
    """
    try:
        import statistics

        from PIL import Image
        im = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((100, 60))
        px = list(im.getdata())
        uniq = len(set(px))
        sd = sum(statistics.pstdev([p[c] for p in px]) for c in range(3)) / 3
        return uniq < 1500 and sd < 18
    except Exception:
        return False


# ---------- Article LLM prompt ----------
ARTICLE_PROMPT = """Jsi senior copywriter studia AI na míru. Píšeš redakční články pro firemní publikum (C-level, manažeři, IT). Český jazyk, věcný, bez marketingových frází, bez AI hype, bez emoji.

NA VSTUPU máš shrnutí jedné AI novinky. Vyrob z toho **kratší redakční článek 500-800 slov** v češtině. NESNAŽ se článek prodlužovat vatou.

ZÁKLADNÍ TÉMA:
- Titulek (CS): {title_cs}
- Originální titulek: {title_orig}
- Zdroj: {source}
- URL: {url}
- Klíčové body shrnutí:
{summary_lines}
- Proč to je důležité: {why_matters}

NEJDŮLEŽITĚJŠÍ PRAVIDLO — ŽÁDNÉ HALUCINACE:
- NIKDY si nevymýšlej konkrétní statistiky, procenta, čísla, finanční částky, data, jména osob/firem ani citace, které NEJSOU v zadání výše.
- Pokud potřebuješ kontext, používej obecné formulace bez konkrétních čísel: „Analytici upozorňují...", „Trend v sektoru naznačuje...", „Pro firmy to typicky znamená...".
- Cituj výhradně zadaný zdroj (URL).
- Pokud detail nevíš, neuvádějí ho.
- Doporučení v závěru musí být obecná („zvažte audit interních procesů", „naplánujte pilotní projekt") — NE specifická čísla nebo termíny.

POŽADAVKY NA VÝSTUP — PŘESNÝ FORMÁT s delimitery `===`:

===TITLE===
Český titulek (max 90 znaků)
===LEAD===
1-2 věty leadu (90-160 znaků).
===CATEGORY===
jedna_z: vyvoj-ai, ai-ekonomika, chatgpt, claude, gemini, copilot, dalsi
===EXCERPT===
2-3 věty perex (180-240 znaků)
===IMAGE_PROMPT===
ANGLICKE jednovete zadani pro hero fotku - MUSI byt JEDINECNA a TEMATICKY VAZANA k tomuto KONKRETNIMU clanku.

KRITICKA PRAVIDLA - bez vyjimek:

1. PREFERUJ STILL-LIFE / OBJEKTY / EXTERIERY pred lidmi. Lide ve scene jsou OK jen obcas a vzdy v pozadi, NIKDY v centru. Vetsina fotek BEZ lidi.

2. VYHNI SE genericky "office se monitoru" scenam. Zadne "people in office at desks", "tech workers", "business meeting", "handshake" - klise.

3. POJMENUJ KONKRETNI OBJEKT/SCENU spojeny s tematem clanku:
   - clanek o dohode -> close-up of signed paper contract on wooden desk with vintage fountain pen, no people
   - clanek o vyzkumu/research paper -> close-up of stacked academic journals and notebook with handwritten equations
   - clanek o compute/datacenter -> empty server hall corridor with ceiling lights, no technician
   - clanek o manifest/principles -> still life of open vintage book on windowsill with morning sunlight
   - clanek o AI agentech/benchmark -> overhead shot of wooden desk with keyboard, coffee cup, paper printout
   - clanek o regulaci/AI Act -> exterior of brutalist government building stairs at dusk, no people
   - clanek o ekonomice -> close-up of financial newspaper folded next to coffee on cafe table
   - clanek o OpenAI -> exterior of nondescript San Francisco office building facade
   - clanek o Anthropic -> minimalist still life of office plants and ceramic mug on light wood desk
   - clanek o Google/DeepMind -> exterior of generic glass office building with reflective windows
   - clanek o vzdelavani -> empty classroom with neat rows of chairs, projector screen blank

4. VARIACE KOMPOZICE - vybirej nahodne:
   - close-up still life (objects on desk, in hand from above)
   - empty interior wide shot (no people)
   - exterior of building (architectural)
   - overhead flat lay
   - through-window view

5. ZAKAZANO uvest v promptu:
   - "neural network", "data visualization", "holographic", "AR/VR glasses"
   - "futuristic", "glowing", "circuit board", "neon", "abstract"
   - "team meeting", "office workers at desks", "person in front of screen"

6. Cil = ilustrace ke clanku v deniku, prefer object-driven a atmospheric nez lide-driven.

Vrat jen jednu vetu, zadne quotes, zadny prefix.
===CONTENT===
<p>První odstavec...</p>
<p>Druhý odstavec...</p>
<h2>Podtitulek</h2>
<p>...</p>
<h2>Co to znamená pro vaši firmu</h2>
<ul>
<li>Doporučení 1</li>
<li>Doporučení 2</li>
<li>Doporučení 3</li>
</ul>
===END===

PRAVIDLA PRO CONTENT (500-800 slov, čistá HTML):
- Povolené tagy: <p>, <h2>, <h3>, <ul>, <li>, <strong>, <em>, <a>. NIC jiného.
- 1-2 odstavce kontextu, 1-2 podtitulky <h2>
- Závěrečná sekce <h2>Co to znamená pro vaši firmu</h2> s 3-4 obecnými doporučeními
- Žádné fráze „v dnešní rychle se měnící době...", „je důležité si uvědomit..."
- Žádné emoji, žádné em-dashy „—" (použij běžnou pomlčku „-")
- Citace zdroje v textu: <a href="{url}">{source}</a>"""


IMAGE_PROMPT_TEMPLATE = (
    "Editorial documentary photograph for a serious business newspaper (Wall Street Journal style). "
    "Photorealistic press photo. Real 35mm DSLR shot with shallow depth of field. "
    "Subject: {subject}. "
    "Composition: natural perspective, real-world environment, available natural light, often objects-only or empty space - minimal or no people. "
    "Mood: contemplative, journalistic, atmospheric, restrained. NOT marketing, NOT tech advertising, NOT key visual. "
    "Color palette: muted earth tones, warm but desaturated, with shadows and textures visible. "
    "Strict NO: NO group of people in office, NO team meeting, NO handshakes, NO smiling business people, "
    "NO person centered in frame, NO portrait of worker at desk, NO stock-photo cliche poses, "
    "no neural network or data visualizations, no holographic UI, no glowing screens, "
    "no AR/VR glasses, no futuristic sci-fi elements, no abstract circuit-board patterns, "
    "no neon, no glow effects, no obvious AI-generated artifacts, "
    "no paper documents, no books, no journals, no notebooks, no contracts, no signed papers, no handwriting, no charts on paper, "
    "no text overlays, no logos, no watermarks, no captions."
)


def parse_delimited_output(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:[a-z]+)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    sections = {}
    pattern = re.compile(r"===\s*([A-Z_]+)\s*===\s*\n?", re.MULTILINE)
    matches = list(pattern.finditer(raw))
    if not matches:
        raise RuntimeError(f"No delimiters found. Raw start: {raw[:300]}")
    for i, m in enumerate(matches):
        key = m.group(1).strip().lower()
        if key == "end":
            continue
        start = m.end()
        end = matches[i+1].start() if i+1 < len(matches) else len(raw)
        sections[key] = raw[start:end].strip()
    out = {
        "title": sections.get("title", "").strip(),
        "lead": sections.get("lead", "").strip(),
        "category": sections.get("category", "dalsi").strip().lower(),
        "excerpt": sections.get("excerpt", "").strip(),
        "image_prompt": sections.get("image_prompt", "").strip(),
        "content_html": sections.get("content", "").strip(),
    }
    if not out["title"] or not out["content_html"]:
        raise RuntimeError(f"Missing title or content. Got keys: {list(sections.keys())}")
    return out


def call_article_llm(art: dict) -> dict:
    summary_lines = "\n".join(f"  - {b}" for b in art.get("summary_cs", []))
    prompt = ARTICLE_PROMPT.format(
        title_cs=art.get("title_cs", ""),
        title_orig=art.get("title_orig", art.get("title_cs", "")),
        source=art.get("source", "AI News"),
        url=art.get("url", ""),
        summary_lines=summary_lines,
        why_matters=art.get("why_matters", ""),
    )
    raw = gemini_text(prompt)
    return parse_delimited_output(raw)


COMPOSITION_STYLES = [
    "architectural exterior of a corporate or institutional building at dawn, low angle, no people, brutalist or modernist style",
    "wide empty interior shot of an industrial corridor or server hall with deep perspective, no people, fluorescent overhead lighting",
    "overhead aerial drone shot of a city district or campus at golden hour, no people visible from this height",
    "atmospheric urban street scene at dusk with one anonymous silhouette in the distance walking away from camera, wet asphalt reflections",
    "extreme close-up macro detail of industrial textures (steel, glass, concrete, fiber optics, circuitry inside casing) - abstract but real, no paper",
    "interior public lobby of a modernist museum or library at night, large empty space with sculpture or installation, no people",
    "exterior wide shot of a single isolated structure in landscape (radio mast, satellite dish, monument, observatory) at twilight, no people",
]


def make_image_prompt(article: dict, llm_image_prompt: str = "", composition_idx: int = 0) -> str:
    style = COMPOSITION_STYLES[composition_idx % len(COMPOSITION_STYLES)]
    if llm_image_prompt:
        # LLM-suggested subject + forced composition style
        subject = llm_image_prompt.strip().rstrip(".")
        # Combine: forced composition + thematic subject hint
        combined = f"{style}, thematically connected to: {subject}"
        return IMAGE_PROMPT_TEMPLATE.format(subject=combined)
    cat = article.get("category", "dalsi")
    title = article.get("title_cs", "")
    return IMAGE_PROMPT_TEMPLATE.format(subject=f"{style}, theme: {title[:60]}")


# ---------- HTML rendering ----------
def render_article_html(template: str, ctx: dict) -> str:
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def write_article(art: dict, gen: dict, image_bytes: bytes | None, date_iso: str) -> dict:
    slug = f"{date_iso}-{slugify(gen['title'])}"
    article_dir = OUTPUT_DIR / slug
    article_dir.mkdir(parents=True, exist_ok=True)

    hero_filename = "hero.webp"
    if image_bytes:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            target_w, target_h = 1400, 800
            src_w, src_h = img.size
            src_aspect = src_w / src_h
            tgt_aspect = target_w / target_h
            if src_aspect > tgt_aspect:
                new_w = int(src_h * tgt_aspect)
                left = (src_w - new_w) // 2
                img = img.crop((left, 0, left + new_w, src_h))
            elif src_aspect < tgt_aspect:
                new_h = int(src_w / tgt_aspect)
                top = (src_h - new_h) // 2
                img = img.crop((0, top, src_w, top + new_h))
            img = img.resize((target_w, target_h), Image.LANCZOS)
            img.save(article_dir / hero_filename, "WEBP", quality=85, method=6)
            marker = article_dir / ".placeholder"
            if marker.exists():
                try: marker.unlink()
                except Exception: pass
        except Exception as e:
            print(f"  ⚠ image save failed: {e}", file=sys.stderr)
            image_bytes = None
    if not image_bytes:
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (1400, 800), (241, 236, 227))
            draw = ImageDraw.Draw(img)
            for y in range(800):
                t = y / 800.0
                r = int(241 - 50*t); g = int(236 - 60*t); b = int(227 - 80*t)
                draw.line([(0, y), (1400, y)], fill=(r, g, b))
            img.save(article_dir / hero_filename, "WEBP", quality=85, method=6)
            (article_dir / ".placeholder").write_text("gradient placeholder", encoding="utf-8")
        except Exception:
            pass

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        date_human = f"{dt.day}. {dt.month}. {dt.year}"
    except Exception:
        date_human = date_iso

    cat_key = gen.get("category", "dalsi")
    if cat_key not in CATEGORIES:
        cat_key = "dalsi"
    cat_label = CATEGORIES[cat_key]

    html = render_article_html(template, {
        "TITLE": gen["title"],
        "EXCERPT": gen.get("excerpt", gen.get("lead", ""))[:240],
        "DATE_ISO": date_iso,
        "DATE_HUMAN": date_human,
        "CATEGORY_LABEL": cat_label,
        "LEAD": gen.get("lead", ""),
        "CONTENT_HTML": gen.get("content_html", ""),
        "SOURCE_URL": art.get("url", "#"),
        "SOURCE_NAME": art.get("source", "Zdroj"),
        "SLUG": slug,
    })
    (article_dir / "index.html").write_text(html, encoding="utf-8")

    return {
        "slug": slug,
        "date": date_iso,
        "title": gen["title"],
        "excerpt": gen.get("excerpt", gen.get("lead", ""))[:240],
        "lead": gen.get("lead", ""),
        "category": cat_key,
        "category_label": cat_label,
        "source": art.get("source", ""),
        "source_url": art.get("url", ""),
        "hero": f"articles/{slug}/hero.webp",
    }


# ---------- VALIDATION ----------
ALLOWED_HTML_TAGS = {"p", "h2", "h3", "ul", "ol", "li", "strong", "em", "a", "br"}


def validate_text(gen: dict) -> list[str]:
    problems = []
    title = (gen.get("title") or "").strip()
    lead = (gen.get("lead") or "").strip()
    excerpt = (gen.get("excerpt") or "").strip()
    cat = (gen.get("category") or "").strip().lower()
    content = (gen.get("content_html") or "").strip()

    if not title or len(title) < 10:
        problems.append("title chybí nebo <10 znaků")
    if len(title) > 140:
        problems.append(f"title moc dlouhý ({len(title)})")
    if not lead or len(lead) < 30:
        problems.append("lead chybí nebo <30 znaků")
    if not excerpt or len(excerpt) < 60:
        problems.append("excerpt chybí nebo <60 znaků")
    if cat not in CATEGORIES:
        problems.append(f"unknown category: {cat}")
    if not content or len(content) < 800:
        problems.append(f"content moc krátký ({len(content)} znaků)")

    text_only = re.sub(r"<[^>]+>", " ", content)
    text_only = re.sub(r"\s+", " ", text_only).strip()
    words = len(text_only.split())
    if words < 450:
        problems.append(f"content má jen {words} slov (čekáme 500-800)")
    if words > 1100:
        problems.append(f"content má {words} slov (moc dlouhý)")

    open_tags = re.findall(r"<([a-z][a-z0-9]*)[^>]*>", content)
    close_tags = re.findall(r"</([a-z][a-z0-9]*)>", content)
    op = Counter(t for t in open_tags if t != "br")
    cl = Counter(close_tags)
    for tag, n_open in op.items():
        if tag not in ALLOWED_HTML_TAGS:
            problems.append(f"nepovolený tag <{tag}>")
        if op[tag] != cl.get(tag, 0):
            problems.append(f"unbalanced <{tag}>: {op[tag]} open / {cl.get(tag,0)} close")

    if "<h2" not in content.lower():
        problems.append("chybí <h2> podtitulek")
    if content.lower().count("<p") < 2:
        problems.append("méně než 2 odstavce")
    if "—" in content:
        problems.append("obsahuje em-dash —")

    return problems


def validate_image(article_dir: Path) -> list[str]:
    problems = []
    hero = article_dir / "hero.webp"
    if not hero.exists():
        problems.append("hero.webp chybí")
        return problems
    size = hero.stat().st_size
    if size < 30_000:
        problems.append(f"hero.webp moc malý ({size} B)")
    if size > 5_000_000:
        problems.append(f"hero.webp moc velký ({size} B)")
    if (article_dir / ".placeholder").exists():
        problems.append("hero.webp je placeholder (gradient)")
    try:
        from PIL import Image
        img = Image.open(hero)
        w, h = img.size
        if w < 800 or h < 400:
            problems.append(f"hero.webp rozměry malé ({w}x{h})")
    except Exception as e:
        problems.append(f"hero.webp nelze otevřít: {e}")
    return problems


FACT_CHECK_PROMPT = """Jsi přísný editor faktů. Zde je ZADÁNÍ a ČLÁNEK vygenerovaný copywriterem.

ZADÁNÍ (jediný zdroj pravdy):
- Titulek: {title_cs}
- Zdroj: {source}
- Klíčové body:
{summary_lines}
- Proč je to důležité: {why_matters}

ČLÁNEK (HTML):
{content_html}

ÚKOL: Najdi v ČLÁNKU **konkrétní tvrzení** (čísla, procenta, peněžní částky, data, jména osob, citace, statistiky), která NEJSOU explicitně obsažena v ZADÁNÍ. Ignoruj obecné formulace („analytici upozorňují", „trend naznačuje").

VRAŤ POUZE TENTO FORMÁT (žádný úvod ani závěr):

VERDICT: OK
nebo
VERDICT: REWRITE
PROBLEMS:
- konkrétní problém 1
- konkrétní problém 2

Pravidla:
- VERDICT: OK pokud článek neobsahuje žádná konkrétně vymyšlená fakta
- VERDICT: REWRITE pokud najdeš jakákoli vymyšlená čísla/jména/citace
- Ignoruj odkaz na zdroj (<a href=...>)."""


def fact_check_text(art: dict, gen: dict) -> tuple[bool, list[str]]:
    summary_lines = "\n".join(f"  - {b}" for b in art.get("summary_cs", []))
    prompt = FACT_CHECK_PROMPT.format(
        title_cs=art.get("title_cs", ""),
        source=art.get("source", ""),
        summary_lines=summary_lines,
        why_matters=art.get("why_matters", ""),
        content_html=gen.get("content_html", ""),
    )
    try:
        raw = gemini_text(prompt, temperature=0.0, max_tokens=1024)
    except Exception as e:
        return True, [f"fact-check skipped: {e}"]
    raw = raw.strip()
    upper = raw.upper()
    if upper.startswith("VERDICT: OK"):
        return True, []
    if "VERDICT: REWRITE" in upper or upper.startswith("REWRITE"):
        problems = []
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("- ") or line.startswith("• "):
                problems.append(line[2:].strip())
        if not problems:
            problems = ["LLM označil článek jako problematický"]
        return False, problems
    return True, [f"fact-check unclear: {raw[:120]}"]


def update_index(entries: list[dict]) -> None:
    index_path = OUTPUT_DIR / "index.json"
    if index_path.exists():
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8")).get("articles", [])
        except Exception:
            existing = []
    else:
        existing = []

    by_slug = {a["slug"]: a for a in existing}
    for e in entries:
        by_slug[e["slug"]] = e
    merged = sorted(by_slug.values(), key=lambda a: a.get("date", ""), reverse=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(merged),
        "articles": merged,
    }
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Také zápis jako JS soubor pro file:// fallback (browser blokuje fetch z file://)
    js_path = OUTPUT_DIR / "index.js"
    js_path.write_text(
        "window.ARTICLES_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8"
    )
    print(f"📋 index.json + index.js updated: {len(merged)} articles total")


# ---------- generation per-article + post-run ----------
def generate_one_article(art: dict, date_iso: str, no_text: bool, no_image: bool, fact_check: bool = True, max_text_retries: int = 2) -> dict | None:
    """Vygeneruje jeden článek s validací textu + fact checkem + obrázkem. Vrátí entry nebo None."""
    gen = None
    text_attempts = 0
    while text_attempts < max_text_retries:
        text_attempts += 1
        if no_text:
            gen = {
                "title": art.get("title_cs", "Bez názvu"),
                "lead": (art.get("why_matters", "") or "")[:160],
                "category": keyword_category(art.get("title_cs", "") + " " + art.get("source", "")),
                "content_html": "<p>" + "</p><p>".join(art.get("summary_cs", [])) + "</p><h2>Co s tím</h2><p>Stub.</p>",
                "excerpt": (art.get("why_matters") or art.get("summary_cs", [""])[0])[:240],
                "image_prompt": "",
            }
            print("  ✓ stub article (no-text)")
            break
        try:
            gen = call_article_llm(art)
            print(f"  ✓ Gemini text → {len(gen.get('content_html',''))} chars, cat={gen.get('category')}")
        except Exception as e:
            print(f"  ⚠ Gemini text failed (try {text_attempts}): {e}")
            time.sleep(3)
            continue

        text_problems = validate_text(gen)
        if text_problems:
            print(f"  ⚠ text validation failed: {text_problems}")
            if text_attempts < max_text_retries:
                print(f"     → regenerating…")
                gen = None
                continue
            else:
                print(f"     → after retries, accepting with warnings")

        if fact_check and gen:
            ok, problems = fact_check_text(art, gen)
            if not ok:
                print(f"  ⚠ fact check FAILED: {problems[:3]}")
                if text_attempts < max_text_retries:
                    print(f"     → regenerating with stricter prompt…")
                    gen = None
                    continue
                else:
                    print(f"     → after retries, accepting (manual review)")
            else:
                print(f"  ✓ fact check OK")
        break

    if not gen:
        print("  ❌ skipping article — text generation failed")
        return None

    # Image — nejdřív zkusit reuse IG bg (vyrobeno generate_posts.py), pak Gemini fallback
    img_bytes = None
    if no_image:
        print("  ✓ no-image (gradient placeholder)")
    else:
        # Preferred: existující clean bg z IG carouselu (stejná foto, šetří kvótu)
        # Pozor: bere se podle rank z digestu, ne _idx — kvůli single-article digestům
        # (per-article běhy by jinak všechny dostaly bg-01.webp).
        bg_idx = art.get("rank") or (art.get("_idx", 0) + 1)  # 1-based pro article-bg-NN.webp
        ig_bg = ROOT / "instagram" / date_iso / "bg-clean" / f"article-bg-{bg_idx:02d}.webp"
        if ig_bg.exists():
            try:
                img_bytes = ig_bg.read_bytes()
                # Anti-gradient guard (incident 12.8.2026): když IG pipeline
                # spadla do gradient fallbacku, převzali bychom gradient jako
                # "fotku" bez .placeholder markeru a audit by mlčel.
                if looks_like_gradient(img_bytes):
                    print(f"  ⚠ IG bg {ig_bg.name} vypadá jako gradient fallback → "
                          f"generuji vlastní hero přes Gemini")
                    img_bytes = None
                else:
                    print(f"  ✓ použitá IG bg fotka → {len(img_bytes)} bytes ({ig_bg.name})")
            except Exception as e:
                print(f"  ⚠ chyba při čtení IG bg: {e}")
                img_bytes = None
        # Fallback: Gemini Image
        if not img_bytes:
            try:
                img_prompt = make_image_prompt(
                    {**art, "category": gen.get("category", "dalsi")},
                    llm_image_prompt=gen.get("image_prompt", ""),
                    composition_idx=art.get("_idx", 0),
                )
                img_bytes = gemini_image(img_prompt)
                print(f"  ✓ Gemini image → {len(img_bytes) if img_bytes else 0} bytes" + (" (placeholder)" if not img_bytes else ""))
            except Exception as e:
                print(f"  ⚠ Gemini image exception: {e}")
                img_bytes = None

    try:
        entry = write_article(art, gen, img_bytes, date_iso)
        print(f"  ✅ {entry['slug']}")
        return entry
    except Exception as e:
        print(f"  ⚠ write failed: {e}")
        return None


def find_latest_digest() -> Path | None:
    files = sorted(DIGEST_DIR.glob("*_selected.json"))
    return files[-1] if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", type=Path, default=None)
    ap.add_argument("--top", type=int, default=7)
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--no-text", action="store_true")
    ap.add_argument("--no-fact-check", action="store_true", help="Skip LLM fact-checker (rychlejší, ale bez kontroly halucinací)")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    digest_path = args.digest or find_latest_digest()
    if not digest_path or not digest_path.exists():
        print("❌ No digest file found")
        sys.exit(1)

    print(f"📰 Reading {digest_path.name}")
    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    date_iso = digest.get("date") or digest_path.stem.split("_")[0]
    articles = digest.get("articles", [])
    if not articles:
        print("❌ No articles in digest")
        sys.exit(1)

    top_n = articles[:args.top]
    print(f"🎯 Generating top {len(top_n)} articles for {date_iso}")

    if args.clean:
        import shutil
        prefix = f"{date_iso}-"
        removed, skipped = 0, 0
        for d in OUTPUT_DIR.iterdir():
            if d.is_dir() and d.name.startswith(prefix):
                try:
                    shutil.rmtree(d)
                    removed += 1
                except PermissionError:
                    print(f"  ⚠ permission denied na {d.name}", file=sys.stderr)
                    skipped += 1
                    continue
        index_path = OUTPUT_DIR / "index.json"
        if index_path.exists():
            try:
                idx = json.loads(index_path.read_text(encoding="utf-8"))
                kept = [a for a in idx.get("articles", []) if a.get("date") != date_iso]
                idx["articles"] = kept
                idx["count"] = len(kept)
                index_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        print(f"🧹 Cleaned {removed} dirs" + (f" ({skipped} skipped)" if skipped else ""))

    fact_check_on = not (args.no_text or args.no_fact_check)

    # Stage 1: generate
    entries = []
    for i, art in enumerate(top_n, 1):
        print(f"\n[{i}/{len(top_n)}] {art.get('title_cs', '')[:80]}")
        art["_idx"] = i - 1  # composition style rotation
        entry = generate_one_article(art, date_iso, no_text=args.no_text, no_image=args.no_image, fact_check=fact_check_on)
        if entry:
            entries.append(entry)
        time.sleep(0.5)

    # Stage 2: post-run check — co chybí, regeneruj
    print("\n" + "=" * 60)
    print("🔍 SECOND CHECK — ověřuji kompletnost runu")
    print("=" * 60)

    expected_keys = [art.get("url") or art.get("title_cs") for art in top_n]
    generated_keys = [e.get("source_url") or e.get("title") for e in entries]
    missing_arts = [art for art, key in zip(top_n, expected_keys) if key not in generated_keys]

    if missing_arts:
        print(f"⚠ {len(missing_arts)} článků chybí, regeneruji…")
        for art in missing_arts:
            print(f"\n[REGEN] {art.get('title_cs', '')[:80]}")
            entry = generate_one_article(art, date_iso, no_text=args.no_text, no_image=args.no_image, fact_check=fact_check_on)
            if entry:
                entries.append(entry)
    else:
        print("✓ všech " + str(len(entries)) + " článků přítomno")

    # Image rerolly pro placeholders
    if not args.no_image:
        needs_image_rerun = []
        for e in entries:
            article_dir = OUTPUT_DIR / e["slug"]
            img_problems = validate_image(article_dir)
            # Reroll jen když je placeholder nebo image chybí, ne pro velikostní warningy
            if any("placeholder" in p or "chybí" in p for p in img_problems):
                needs_image_rerun.append((e, img_problems))

        if needs_image_rerun:
            print(f"\n⚠ {len(needs_image_rerun)} článků s placeholder image, rerolling…")
            for e, problems in needs_image_rerun:
                print(f"  • {e['slug'][:60]}: {problems}")
                article_dir = OUTPUT_DIR / e["slug"]
                art = next((a for a in top_n if a.get("url") == e.get("source_url") or a.get("title_cs") == e.get("title")), None)
                if not art:
                    continue
                try:
                    art_idx = art.get("_idx", 0) if isinstance(art, dict) else 0
                    img_prompt = make_image_prompt({**art, "category": e.get("category", "dalsi")}, composition_idx=art_idx)
                    img_bytes = gemini_image(img_prompt)
                    if img_bytes:
                        from PIL import Image
                        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                        target_w, target_h = 1400, 800
                        sw, sh = img.size
                        sa = sw / sh; ta = target_w / target_h
                        if sa > ta:
                            nw = int(sh * ta); l = (sw - nw) // 2
                            img = img.crop((l, 0, l + nw, sh))
                        elif sa < ta:
                            nh = int(sw / ta); t = (sh - nh) // 2
                            img = img.crop((0, t, sw, t + nh))
                        img = img.resize((target_w, target_h), Image.LANCZOS)
                        img.save(article_dir / "hero.webp", "WEBP", quality=85, method=6)
                        marker = article_dir / ".placeholder"
                        if marker.exists():
                            marker.unlink()
                        print(f"     ✅ image regen OK")
                    else:
                        print(f"     ⚠ image regen failed → ponechán placeholder")
                except Exception as ex:
                    print(f"     ⚠ regen exception: {ex}")
                time.sleep(1)
        else:
            print("✓ všechny obrázky jsou real photos")

    # Stage 3: final report
    update_index(entries)
    print("\n" + "=" * 60)
    print("📊 FINAL REPORT")
    print("=" * 60)
    ok_count = 0
    placeholder_count = 0
    for e in entries:
        article_dir = OUTPUT_DIR / e["slug"]
        img_problems = validate_image(article_dir)
        is_ph = (article_dir / ".placeholder").exists()
        if is_ph:
            placeholder_count += 1
        if not img_problems:
            ok_count += 1
            print(f"  ✅ {e['slug'][:65]}  [{e['category_label']}]")
        else:
            print(f"  ⚠ {e['slug'][:65]}  → {img_problems}")
    print(f"\n{ok_count}/{len(entries)} článků bez problémů, {placeholder_count} s placeholder")
    if not entries:
        print("\n❌ No articles generated")
        sys.exit(2)


if __name__ == "__main__":
    main()
        