# AI News — scripts/

Python skripty pro denní news pipeline.

## Instalace

```bash
cd "AI News/scripts"
pip install -r requirements.txt
```

Python 3.10+.

## Spuštění

```bash
python fetch_sources.py
```

Skript stáhne všechny zdroje definované v `SOURCES` uvnitř `fetch_sources.py`,
normalizuje je do jednoho JSON souboru a uloží do `../inbox/YYYY-MM-DD.json`.

Log běhu jde do `./logs/fetch_YYYY-MM-DD.log` i na stdout.

## Výstupní schéma

```json
{
  "generated_at": "2026-04-21T10:00:00Z",
  "date": "2026-04-21",
  "article_count_unique": 150,
  "article_count_raw": 180,
  "source_stats": [
    {"source_id": "techcrunch_ai", "status": "ok", "count": 12},
    {"source_id": "anthropic_news", "status": "error", "error": "404", "count": 0}
  ],
  "articles": [
    {
      "id": "a1b2c3d4e5f67890",
      "title": "...",
      "url": "https://...",
      "published_at": "2026-04-21T08:30:00+00:00",
      "author": "...",
      "summary_raw": "...",
      "hero_image": "https://.../hero.jpg",
      "source_id": "techcrunch_ai",
      "source_name": "TechCrunch AI",
      "category": "news",
      "also_seen_in": ["verge_ai"]
    }
  ]
}
```

Pole `also_seen_in` se objeví jen u článků, které se objevily ve více zdrojích.

## Poznámky

- Články starší než 48 hodin jsou odfiltrovány (viz `MAX_AGE_HOURS`).
- Dedup se dělá podle hash(canonical_url + title).
- Výpadek jednoho zdroje (404, timeout) nezabije běh — chyba je v `source_stats`.
- Papers with Code není v MVP (nemá stabilní RSS, vyřeší se v2).
- Newsletterové zdroje (Import AI, TLDR AI, Ben's Bites, ...) fungují přes Substack/beehiiv RSS — není potřeba e-mail.

## Další skripty

Zatím jen `fetch_sources.py`. Další přibývají postupně:

- `process_digest.py` — (TODO) čte `inbox/YYYY-MM-DD.json`, volá LLM/Claude pro shrnutí, generuje PDF a carousel, přesouvá inbox do archivu
- `design_post.py` — (TODO) vytváří IG a LinkedIn slidy z template + Gemini Image
