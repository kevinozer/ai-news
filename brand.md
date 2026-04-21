# Brand identity — AI News Bot (pod značkou AI NA MÍRU)

**Finalizováno:** 2026-04-21
**Zdroj:** `Claude cowork/ai-na-miru/` (vlastní web Kevina, analyzován přímo z CSS a logo souborů)

## Jméno značky

**AI NA MÍRU** — česká firma, pod kterou spadá i AI News bot jako jedna z aktivit. Značka cílí na bespoke / na míru vytvářená AI řešení.

## Logo

**Varianty dostupné** (všechny v transparentním pozadí):
- `Logo-celé- bezpozadí.png/webp` — kompletní varianta (monogram + nápis „AI NA MÍRU")
- `Logo - nápis - bezpozadí.png/webp` — jen text „AI NA MÍRU"
- `Logo - znak - bezpozadí.png/webp` — jen monogram (A s integrovanými obvodovými stopami)

**Formáty:** PNG (zdroj) + WebP (s – md – full velikosti pro web)

**Charakter loga:** luxusní serif lettermark s měděno-bronzovým metalickým přechodem; monogram je písmeno A se stylizovanými obvodovými stopami tvořícími druhou polovinu (technika + klasika). Elegantní, premium, ne hype-y.

## Barevná paleta

### Light theme (výchozí)

| Token | Hex | Použití |
|---|---|---|
| `--ivory` | `#FFFFFF` | hlavní pozadí |
| `--ivory-2` | `#F7F7F5` | jemně odsazené sekce |
| `--ivory-3` | `#EEEEEC` | ještě subtilnější bloky |
| `--ink` | `#1A1917` | hlavní text |
| `--ink-soft` | `#4A4845` | sekundární text |
| `--ink-dim` | `#8A8884` | labels, meta |
| `--ink-ghost` | `#C4C0B8` | divider, disabled |
| `--border` | `#E5E5E3` | borders |
| `--border-soft` | `#EFEFED` | subtle borders |

### Brand accent — měděno-bronzová škála

| Token | Hex | Použití |
|---|---|---|
| `--green` | `#9C4A28` | primární akcent (copper) — pozor, název „green" je historický, je to měď |
| `--green-2` | `#7A3519` | hover, hlubší copper |
| `--green-3` | `#3E1A08` | nejtemnější brown-copper, gradienty |
| `--amber` | `#C49A4A` | zlatý akcent, decentní highlights, LED/lesky |

### Dark theme

| Token | Hex | Použití |
|---|---|---|
| `--ivory` | `#141210` | dark background |
| `--ivory-2` | `#1C1A17` | |
| `--ivory-3` | `#242118` | |
| `--ink` | `#F0EDE7` | light text na dark |

### Signature pozadí a efekty

- Hero box: `#0A0806` (skoro černá) s jemnými texturami
- `theme-color` meta: `#0A0806`
- Hero gradient: `linear-gradient(135deg, #3E1A08 0%, #7A3519 60%, #9C4A28 100%)` — signature brand gradient

## Typografie

### Display / headings

- **Font:** Cormorant Garamond (serif)
- **Token:** `--font-d`
- **Weight:** 300 (light) — klíčový detail, ne tučné
- **Použití:** h1, h2, h3, velké nápisy, CTA v italic
- **Letter-spacing:** -0.01em až -0.02em (jemně stažené)

### Body / UI

- **Font:** Space Grotesk (geometrický sans-serif)
- **Token:** `--font-b`
- **Weight:** 400 (body), 500 (buttons, labels), 600 (emphasized)
- **Použití:** odstavce, navigace, buttons, meta texty
- **Letter-spacing:** 0.02–0.04em (mírně rozestouplé pro labels a UPPERCASE)

### Kombinace

Klasický editorial pattern: **serif pro nadpisy, sans pro obsah a UI**. Občas se v nadpisech objevuje italic Cormorant pro zvýraznění (např. CTA „*dozvědět se víc*").

## Aesthetic / mood

- **Styl:** editorial / luxury / bespoke
- **Reference:** kvalitní časopisový layout, ne „AI tech startup"
- **Obsahová hustota:** velkorysé spacing, prostor na vydechnutí
- **Tón vizuálů:** tlumený, premium, žádné neon, žádné loud tech gradienty
- **Textury:** jemné šrafy, radiální gradienty, světelné odlesky (jako papír nebo kůže)
- **Mikro-detaily:** LED tečky, camera-dots v rozích (hero box), vrstvené stíny — připomíná luxusní elektroniku nebo studio

## Tón komunikace (z webu)

Odvozeno z použité typografie a layoutu:
- **Profesionální, ale ne korporátní.**
- **Zdvořilý, ne amatérský.**
- **Používá klasické výrazy, ne tech žargon.**
- **Krátké hooky + delší vysvětlující text.**
- **Žádné emoji, žádné „🚀" a podobné hype prvky.**

## Implikace pro AI News posty

### Instagram posty — carousel 9 slidů (1080×1080)

- **Struktura:** 1 cover + 7 newsových slidů + 1 outro
- **Cover:** reuses pozadí top novinky dne s cover textem („AI News | 21. 4. 2026") v overlay
- **Newsové slidy (7):** každá vlastní pozadí. Primárně zdrojový hero obrázek (pokud kvalitní a použitelný), fallback Gemini tematický background
- **Outro:** statický brand asset s logem a CTA, reusable napříč dny
- Nadpis na slidu: Cormorant Garamond weight 300, 48–72px
- Text novinky: Space Grotesk weight 400, 16–18px
- Akcent: copper `#9C4A28` nebo zlatá `#C49A4A` — decentně, ne plošně
- Logo-znak (monogram) v rohu, 40–60px
- Tag/kategorie: Space Grotesk UPPERCASE 10px, letter-spacing 0.24em, barva `--amber`
- Zdroj/link jako patička slidu, malý ale čitelný
- **Aspect ratio problém:** zdrojové hero obrázky bývají landscape, pro IG 1:1 potřeba smart crop (zaostření na hlavní subjekt) nebo padding v brand barvě

### LinkedIn posty — document post, 9-stránkové PDF (1920×1080 landscape)

- **Struktura:** stejná jako IG (1 cover + 7 news + 1 outro), jen jiný aspect ratio
- **Cover:** reuses pozadí top novinky (landscape verze) s cover textem
- **Newsové slidy (7):** zdrojové hero obrázky tady fungují líp (landscape match), Gemini jen když zdroj nemá dobrý vizuál
- **Outro:** landscape verze brand outra, reusable
- Tón textu: business-úhel, detailnější než IG, víc profi (viz `cilovky.md`)
- Zdroj/link jasně v patičce každého slidu
- PDF finální export pro LinkedIn document post upload

### Gemini prompt design (pro konzistenci napříč slidy)

Fixní brand část promptu (přidána ke každému volání):

> "muted editorial photography, copper/bronze/ivory palette, soft cinematic lighting, shallow depth of field, bespoke luxury magazine aesthetic, no text in image, no UI elements, no tech stock imagery"

Variabilní tematická část odvozená z konkrétní novinky, např.:
- „datacenter server rack with warm ambient glow"
- „neural network visualization as abstract copper filament sculpture"
- „researcher silhouette against a holographic model display"

Aspect ratio specifikován v API volání (`1:1` pro IG, `16:9` pro LinkedIn).

## Soubor s logy

Pro generování postů máme k dispozici:
- `Logo - znak - bezpozadí.png` — ideální pro malý rožek postu
- `logo-napis.webp` — pokud chceme jen typografické logo
- `logo-cele.webp` — plná varianta pro větší formát

Při stavbě pipeline tyto soubory zkopírujeme do `AI News/assets/`.

## Co ještě chybí

- Fonty musí být dostupné i mimo web. Cormorant Garamond a Space Grotesk jsou oba volně dostupné na Google Fonts, takže pro post generator je stáhneme lokálně.
- Pokud má brand oficiální brand guidelines dokument (PDF, Figma), stojí za to ho nahrát — mohl by obsahovat pravidla, která z CSS nejsou patrná (marže, minimální velikost loga, zakázané kombinace).
