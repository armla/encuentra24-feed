# Handoff: Encuentra24 XML Feed Generator

**Project Name:** The Agency Costa Rica - Encuentra24 Feed Automation  
**Date:** April 2026  
**Repository:** [github.com/armla/encuentra24-feed](https://github.com/armla/encuentra24-feed)  
**Feed URL:** `https://raw.githubusercontent.com/armla/encuentra24-feed/master/encuentra24_feed.xml`

## 1. Project Overview
This project automates the generation of an XML property feed for Encuentra24. It pulls live property data from the LX Costa Rica API (`api.lxcostarica.com`), applies a strict set of business rules to select exactly 100 properties, and uses an LLM (OpenAI `gpt-4.1-mini`) to enrich titles and descriptions for maximum conversion on the Encuentra24 platform.

The feed is generated automatically every day at 2:00 AM Costa Rica time via GitHub Actions, and the resulting XML file is committed directly to the repository where Encuentra24 can pull it.

## 2. Business Rules & Tier Logic

The Encuentra24 plan currently allows **250 listings** (temporary expanded plan). The generator uses a **five-tier prioritization system**:

### Tier A: Exclusive Residential Sales
- **Rule:** All exclusive sale listings (no lots/land/farms) priced ≤ $1,250,000 USD.
- **Note:** Exclusive flag overrides EPP priority. Sorted cheapest first.
- **Current count:** ~25

### Tier B: Rentals
- **Rule:** All rental listings priced ≤ $4,950 USD/month.
- **Filter:** Excludes EPP priorities (18, 19, 20). Sorted cheapest first.
- **Current count:** ~15

### Tier C: Exclusive Lots, Farms & Land
- **Rule:** All exclusive lot/farm/land listings at any price (exclusive flag overrides price cap).
- **Note:** Sorted cheapest first. Fills slots after Tiers A+B.
- **Current count:** ~12

### Tier D: Non-Exclusive Residential Sales
- **Rule:** Non-exclusive sale listings, no lots/land/farms, no EPP. Price ceiling ≤ $980,000 USD.
- **Note:** Sorted cheapest first. Fills slots after Tiers A+B+C.
- **Current count:** ~132

### Tier E: Non-Exclusive Lots, Farms & Land
- **Rule:** Non-exclusive lot/farm/land listings, no EPP, no price cap.
- **Note:** Sorted cheapest first. Fills remaining slots after Tiers A–D.
- **Current count:** ~66

*Note on Priorities:* Priority 18 = EPP High-end Houses, 19 = EPP Normal Houses, 20 = EPP Lots. These are excluded from Tiers B, D, and E to maintain feed quality.

## 3. LLM Enrichment
To optimize the listings for Encuentra24's search algorithms and user behavior, the script uses OpenAI to rewrite the raw API data:

1. **Detail API Fetch:** For each selected listing, the script fetches the detail endpoint (`/api/v1/listings/{id}`) to retrieve the full agent-written marketing copy and the bulleted `highlights_listings`.
2. **Optimized Titles:** Generates strict 70-character bilingual titles following the structure: `[Type] [X] habs en [Location] - [Community] - [Hook]`. The hook is extracted from the agent's top highlight.
3. **Two-Paragraph Descriptions:**
   - **Paragraph 1:** Leads with the agent's highlights woven into a compelling narrative.
   - **Paragraph 2:** Covers supporting details (specs, amenities, location) and closes with the standard signature: `MLS {ID} The Agency Costa Rica.`

**Caching:** To prevent redundant API calls and LLM costs, the enriched titles and descriptions are saved to `enrichment_cache.json`. The LLM only runs for listings that are entirely new or whose `lastmodifieddate` has changed since the last run.

## 4. Architecture & Infrastructure

### Files
- `genera_feed.py`: The core Python script that handles API fetching, tier logic, LLM enrichment, and XML generation.
- `enrichment_cache.json`: Stores the LLM-generated titles and descriptions.
- `api_snapshot.json`: A local copy of the bulk API response, cached for 23 hours to prevent hammering the LX API.
- `.github/workflows/generate_feed.yml`: The GitHub Actions workflow file that orchestrates the daily run.

### GitHub Actions Workflow
The workflow runs daily at 2:00 AM (CR time). It sets up Python, installs dependencies (`requests`, `openai`), runs `python3 genera_feed.py`, and commits any changes to the XML or cache files back to the `master` branch.

### Environment Variables
The workflow requires one GitHub Secret:
- `OPENAI_API_KEY`: Used by the Python script to authenticate with OpenAI for the enrichment module.

## 5. How to Update or Modify

**To change the 100-listing cap or price limits:**
Edit the configuration section at the top of `genera_feed.py`:
```python
MAX_LISTINGS = 100
EXCLUSIVE_PRICE_CAP = 1_100_000
RENTAL_PRICE_CAP = 4_750
```

**To force a complete LLM regeneration:**
If you change the prompt logic or want to refresh all descriptions from scratch, you must clear the cache. You can do this by running the script manually with the flag:
```bash
python3 genera_feed.py --clear-cache
```
*Note: A full regeneration of 100 listings takes approximately 10-15 minutes and will make 100 calls to the LX detail API.*

**To fix a wrong region mapping:**
If Encuentra24 reports a region error, add the city to the `REGION_MAP` dictionary in `genera_feed.py` with the correct Encuentra24 region ID. The full official region list for Costa Rica is stored at `/home/ubuntu/upload/pasted_content_4.txt`. The map was fully rebuilt in May 2026 against this official list — all 100 active listings resolve to canton- or district-level IDs with zero province-level fallbacks.

## 6. Zapier Webhook — New Listing Notifications

Every time the feed runs and detects a listing that was **not present in the previous feed**, it fires a POST to the Zapier webhook. Zapier then logs the event to Salesforce (or any connected app).

**Webhook URL:** `https://hooks.zapier.com/hooks/catch/3798504/uj79jgd/`
Override at runtime via the `ZAPIER_WEBHOOK_URL` environment variable.

**Payload per new listing:**
```json
{
  "date": "2026-04-23",
  "listing_id": "LXAR13746",
  "name": "Casa Volare in Puerto Viejo",
  "price_usd": 595000,
  "type": "Residential",
  "city": "Puerto Viejo",
  "url": "https://theagency.cr/listings/casa-volare-puerto-viejo",
  "ad_type": "property"
}
```

**How detection works:** Before regenerating the feed, the script reads the current `encuentra24_feed.xml` and extracts all `<sourceid>` values. After generation, any MLS ID in the new feed that was not in the old feed triggers a webhook call. Webhook failures are logged as warnings and do not abort the feed.

**To disable:** Set `ZAPIER_WEBHOOK_URL` to an empty string in the environment, or clear the constant in `genera_feed.py`.

---

## 7. Known Constraints & Edge Cases
- **Detail API Rate Limits:** The script is designed to fetch the detail API (`/api/v1/listings/{id}`) only when necessary. If the API returns 500 errors, the script gracefully falls back to generating descriptions using the bulk structured data.
- **Encuentra24 Import Modes:** When uploading the feed manually for testing, always use **Demo Mode** first. A live upload will deactivate any existing listings on your account that are not present in the XML.
- **Single YouTube Video:** Encuentra24 only supports one YouTube video per listing. The script prioritizes the `virtual_tour_video_url`, followed by the `live_tour_url`, and finally `vertical_video_1`.
- **Bathrooms must be whole integers:** Encuentra24 rejects fractional bath values (e.g. `3.25`) with a field validation error. The LX API can return quarter-bath values from its internal counting system. The `format_bathrooms()` function rounds to the nearest integer using `round()` to prevent this. Fixed April 2026 after import error on listing LXER13507.
