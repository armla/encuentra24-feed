# Encuentra24 Feed — The Agency Costa Rica

Automated XML feed generator for [Encuentra24](https://www.encuentra24.com/costa-rica-en/import).  
Pulls property data from the LX Costa Rica API and generates a compliant XML import file daily.

---

## How It Works

A GitHub Actions workflow runs every day at **2:00 AM Costa Rica time** and:

1. Fetches all active listings from the LX API (one bulk call)
2. Fetches full descriptions and highlights from the detail API — **only for new or modified listings** (incremental, protects the API)
3. Generates optimized bilingual titles and two-paragraph descriptions via LLM
4. Outputs `encuentra24_feed.xml` and commits it to this repository

---

## Feed Rules

### Priority System

| Priority | Category | Eligible |
|---|---|---|
| 1–2 | Exclusivas High-end | Yes |
| 3–4 | Exclusivas otras | Yes |
| 5–10 | Open listings (bonitas) | Yes |
| 10 | Investment | Yes |
| 11–15 | Open listings normales | Yes |
| 16–17 | Lotes y Fincas (open listings) | Yes |
| **18** | **EPP Casas High-end** | **Excluded (unless exclusive)** |
| **19** | **EPP Casas normales** | **Excluded always** |
| **20** | **EPP Lotes** | **Excluded always** |

> **Exclusive override:** If `exclusive_listing = true`, the listing is always eligible regardless of priority.

### 3-Tier Selection (100-listing cap)

| Tier | Rule | Count |
|---|---|---|
| **A — Exclusives** | All exclusives ≤ $1,100,000 USD, any priority | ~24 |
| **B — Rentals** | ≤ $4,750/month, no EPP | ~17 |
| **C — Residential sale** | No lots, no EPP, cheapest up to $1,500,000 | ~59 |

### Content Enrichment

- **Titles (max 70 chars):** `[Type] [X] habs/BR en [Location] - [Community] - [Hook]`
- **Descriptions:** Two paragraphs — highlights-led P1, details P2 closing with `MLS XXXXX The Agency Costa Rica`
- **Cache:** Results stored in `enrichment_cache.json`. Only new/modified listings are re-enriched.

---

## Feed URL

The XML file is committed to this repository after each run:

```
https://raw.githubusercontent.com/theagency-cr/encuentra24-feed/main/encuentra24_feed.xml
```

Point Encuentra24's automated import to this URL.

---

## Manual Usage

```bash
# Install dependencies
pip install openai

# Generate feed (uses cache, fast after first run)
python3 genera_feed.py

# Force regenerate all LLM descriptions
python3 genera_feed.py --clear-cache

# Skip LLM enrichment (fast, uses fallback descriptions)
python3 genera_feed.py --no-enrich

# Use a local API snapshot instead of live fetch
python3 genera_feed.py --input api_snapshot.json
```

---

## GitHub Secrets Required

| Secret | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key for LLM title and description generation |

Set at: **Repository Settings → Secrets and variables → Actions → New repository secret**

---

## Manual Trigger

You can trigger the workflow manually from **GitHub Actions → Generate Encuentra24 Feed → Run workflow**.  
Use the `clear_cache` option to force full regeneration of all descriptions.

---

## Configuration

All settings are at the top of `genera_feed.py`:

| Variable | Value | Description |
|---|---|---|
| `MAX_LISTINGS` | `100` | Encuentra24 plan cap |
| `EXCLUSIVE_PRICE_CAP` | `$1,100,000` | Max price for Tier A |
| `RENTAL_PRICE_CAP` | `$4,750/mo` | Max rent for Tier B |
| `SALE_PRICE_CAP` | `$1,500,000` | Max price for Tier C pool |
| `EPP_PRIORITIES` | `{18, 19, 20}` | Excluded priority numbers |
