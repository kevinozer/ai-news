# Zdroje — AI News Bot

**Finalizováno:** 2026-04-21
**Celkem:** 28 aktivních zdrojů (2 vyřazeny při ladění: Meta AI Blog, The Batch — viz „Změny" níže)
**Filtr:** jen volně dostupné (paywall vyřazen)

Sloupec "Získání" značí, jak bude bot zdroj monitorovat:
- **RSS** — standardní RSS/Atom feed, triviální integrace
- **Email** — newsletter, nutno si zřídit e-mailovou adresu + parser
- **API** — oficiální API (Hugging Face, HN Algolia, Reddit)
- **Scrape** — parsing HTML, fragilní, záložní varianta

---

## 1. Primární news weby (6)

| # | Zdroj | Získání | Poznámka |
|---|---|---|---|
| 1 | TechCrunch — AI | RSS | `techcrunch.com/category/artificial-intelligence/feed/` |
| 2 | The Verge — AI | RSS | `theverge.com/rss/ai-artificial-intelligence/index.xml` |
| 3 | Ars Technica | RSS | Hlavní feed, filtr na AI tagy |
| 4 | MIT Technology Review — AI | RSS | Některé články s paywallem — bot sbírá jen volně dostupné |
| 5 | Wired — AI | RSS | `wired.com/feed/tag/ai/latest/rss` |
| 6 | VentureBeat AI | RSS | `feeds.feedburner.com/venturebeat/SZYF` + AI keyword filter (kategorijní feed 0 výsledků) |

## 2. Oficiální blogy laboratoří (6)

| # | Zdroj | Získání | Poznámka |
|---|---|---|---|
| 7 | OpenAI blog | RSS | Launche, research |
| 8 | Anthropic news | HTML scrape | RSS neexistuje, scrape listing page `/news` |
| 9 | Google DeepMind blog | RSS | Research-heavy |
| 10 | ~~Meta AI blog~~ | ~~RSS~~ | **Vyřazeno 2026-04-21** — nemá RSS ani scrape-friendly HTML |
| 11 | Microsoft AI blog | RSS | Copilot, enterprise |
| 12 | Hugging Face blog | RSS | Open-source komunita |

## 3. Newslettery (6)

| # | Zdroj | Získání | Poznámka |
|---|---|---|---|
| 13 | Import AI (Jack Clark) | Email + web archiv | `importai.substack.com` |
| 14 | ~~The Batch (deeplearning.ai)~~ | ~~RSS~~ | **Vyřazeno 2026-04-21** — RSS neexistuje, `/rss/` i `/feed/` vrací HTML |
| 15 | TLDR AI | RSS | `tldr.tech/api/rss/ai` |
| 16 | Ben's Bites | RSS | `www.bensbites.com/feed` |
| 17 | Last Week in AI | Email / podcast | `lastweekin.ai` |
| 18 | One Useful Thing (Ethan Mollick) | RSS (Substack) | `oneusefulthing.org` |

## 4. Analýza a komentář (6) — *rozšířeno dle přání*

| # | Zdroj | Získání | Poznámka |
|---|---|---|---|
| 19 | Simon Willison's Weblog | RSS | Pragmatický vývojářský pohled |
| 20 | Latent Space (Swyx & Alessio) | RSS (Substack) | Hloubkové rozhovory |
| 21 | Marcus on AI (Gary Marcus) | RSS (Substack) | Kritický hlas |
| 22 | Dwarkesh Patel | RSS | Podcast + transkripty |
| 23 | Don't Worry About the Vase (Zvi Mowshowitz) | RSS (Substack) | **NOVĚ.** Týdenní komplexní AI roundupy, velmi opinionated |
| 24 | Interconnects (Nathan Lambert) | RSS (Substack) | **NOVĚ.** ML/RL research commentary, technicky laděné |

## 5. Research a technické zdroje (2)

| # | Zdroj | Získání | Poznámka |
|---|---|---|---|
| 25 | ArXiv cs.AI / cs.LG | RSS | Filtrovatelný feed na kategorie |
| 26 | Papers with Code — trending | API / scrape | Dobrý signál pro hot research |

## 6. Komunita a agregátory (2)

| # | Zdroj | Získání | Poznámka |
|---|---|---|---|
| 27 | Hacker News — AI filter | API (Algolia) | Veřejná API, žádný scrape |
| 28 | r/MachineLearning + r/LocalLLaMA | API (Reddit) | Top daily posty |

## 7. České zdroje (2)

| # | Zdroj | Získání | Poznámka |
|---|---|---|---|
| 29 | Lupa.cz | RSS | Česká tech publikace |
| 30 | CzechCrunch | RSS | Startupy, české AI firmy |

---

## Souhrn

- **RSS-friendly:** 25 z 28 aktivních (hlavní páteř pipeline, všechny Substack/beehiiv newslettery přes RSS, nepotřebují e-mail)
- **API:** 2 (HN Algolia, Reddit public JSON)
- **HTML scrape:** 1 (Anthropic news — RSS neexistuje)

## Změny oproti prvnímu návrhu

- **Vyřazeno před startem:** Stratechery (paywall) a NVIDIA Developer Blog (úzký focus, překryv s newslettery)
- **Přidáno před startem:** Don't Worry About the Vase (Zvi Mowshowitz), Interconnects (Nathan Lambert) — navýšení komentářové vrstvy podle Kevinova přání
- **Vyřazeno při ladění 2026-04-21 (2. iterace fetch_sources.py):**
  - Meta AI Blog — `ai.meta.com/blog` nemá ani RSS, ani extrakci linků přes scraper. Meta oznámení stejně pokrývá TechCrunch / The Verge / HN.
  - The Batch (DeepLearning.AI) — oficiální RSS neexistuje, obě URL vrací HTML. Andrew Ngova obsah částečně kompenzují ostatní newslettery (Import AI, TLDR, Ben's Bites).
- **Přehled funkčnosti (2. fetch run):** 136 unique articles, 29/30 OK zdrojů (před vyřazením). Arxiv limitován na top 15/feed, Reddit top 15 by score, CzechCrunch/Lupa s AI keyword filtrem.

## X a Threads

Zatím **nejsou zařazeny**. Rozhodnutí Kevina 2026-04-21: MVP pojede bez nich, po 2–4 týdnech se vyhodnotí, jestli něco uniká a zda má smysl přidat X API Basic ($100/měsíc).
