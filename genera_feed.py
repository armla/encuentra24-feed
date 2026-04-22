#!/usr/bin/env python3
"""
Encuentra24 XML Feed Generator for The Agency Costa Rica
=========================================================

Pulls property data from the LX Costa Rica API and generates an
Encuentra24-compliant XML import file.

Usage:
    python3 genera_feed.py [--output feed.xml] [--type all|sale|lot] [--limit 100]
    python3 genera_feed.py --no-enrich   # skip LLM enrichment, use fast fallback descriptions

LLM Enrichment (enabled by default):
    - Fetches full marketing descriptions + highlights from the detail API
    - Generates optimized 70-char bilingual titles (Type + Beds + Location - Community - Hook)
    - Generates two-paragraph bilingual descriptions (highlights-led P1, details P2)
    - Results are cached in enrichment_cache.json to avoid redundant API calls
"""

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime
from xml.sax.saxutils import escape

# ─────────────────────────────────────────────────────────────────────
# CONFIGURATION — edit these values to match your account
# ─────────────────────────────────────────────────────────────────────

API_URL        = "https://api.lxcostarica.com/api/v1/listings"
API_DETAIL_URL = "https://api.lxcostarica.com/api/v1/listings/{id}"

COUNTRY_ID = "2"  # Costa Rica

CONTACT_EMAIL   = "info@theagencycostarica.com"
CONTACT_PHONE   = "+506 4001-4398"
CONTACT_NAME    = "The Agency Costa Rica"
CONTACT_CITY    = "Escazú"
CONTACT_COMPANY = "The Agency Costa Rica"
CONTACT_URL     = "https://theagency.cr"
ADVERTISER_TYPE = "Agente"

LANGUAGE = "es"  # Primary language for Encuentra24 settings block

# Maximum number of photos per listing
MAX_PHOTOS = 25

# Maximum number of listings in the feed (Encuentra24 plan limit)
MAX_LISTINGS = 100

# Exclusives at or below this price are guaranteed a slot (exclusive flag overrides EPP priority)
EXCLUSIVE_PRICE_CAP = 1_100_000  # USD

# Maximum monthly rent for Tier B rentals
RENTAL_PRICE_CAP = 4_750  # USD/month

# Maximum sale price for Tier C pool
SALE_PRICE_CAP = 1_500_000  # USD

# EPP priority numbers — excluded from ALL non-exclusive listings
# 18 = EPP Casas High-end, 19 = EPP Casas normales, 20 = EPP Lotes
EPP_PRIORITIES = {18, 19, 20}

# LLM model for enrichment
LLM_MODEL = "gpt-4.1-mini"

# Cache file for LLM enrichment results (avoids re-generating on every run)
ENRICHMENT_CACHE_FILE = "enrichment_cache.json"

# ─────────────────────────────────────────────────────────────────────
# ENCUENTRA24 CATEGORY MAPPING
# ─────────────────────────────────────────────────────────────────────

SALE_CATEGORY_MAP = {
    "Single Family":    173,  # Bienes Raíces > Venta > Casas
    "Residential":      173,
    "House":            173,
    "Condominium":      179,  # Bienes Raíces > Venta > Apartamentos
    "Apartment":        179,
    "Commercial":       170,  # Bienes Raíces > Venta > Edificios
    "Building":         170,
}
SALE_DEFAULT_CATEGORY = 173  # Casas

RENT_CATEGORY_MAP = {
    "Apartment":        156,  # Bienes Raíces > Alquiler > Apartamentos
    "Condominium":      156,
    "Furnished":        155,  # Bienes Raíces > Alquiler > Alquileres Amueblados
    "House":            157,  # Bienes Raíces > Alquiler > Casas
    "Single Family":    157,
    "Room":             158,  # Bienes Raíces > Alquiler > Cuartos
    "Beach":            162,  # Bienes Raíces > Alquiler > Casas de Playa
    "Interior":         154,  # Bienes Raíces > Alquiler > Casas en el Interior
}
RENT_DEFAULT_CATEGORY = 157  # Casas

LOT_CATEGORY_MAP = {
    "Lots And Land":        178,  # Bienes Raíces > Venta > Lotes y Terrenos
    "Beach":                177,  # Bienes Raíces > Venta > Propiedades de playa
    "Farm And Agriculture": 176,  # Bienes Raíces > Venta > Fincas
    "Island":               169,  # Bienes Raíces > Venta > Propiedades en Islas
}
LOT_DEFAULT_CATEGORY = 178  # Lotes y Terrenos

# ─────────────────────────────────────────────────────────────────────
# COSTA RICA REGION ID MAP
# ─────────────────────────────────────────────────────────────────────

REGION_MAP = {
    # San José province
    "san jose":         1,
    "escazú":           2,
    "escazu":           2,
    "desamparados":     3,
    "puriscal":         4,
    "tarrazú":          5,
    "tarrazu":          5,
    "aserrí":           6,
    "aserri":           6,
    "mora":             7,
    "goicoechea":       8,
    "santa ana":        9,
    "alajuelita":       10,
    "vásquez de coronado": 11,
    "vasquez de coronado": 11,
    "acosta":           12,
    "tibás":            13,
    "tibas":            13,
    "moravia":          14,
    "montes de oca":    15,
    "turrubares":       16,
    "dota":             17,
    "curridabat":       132,
    "pérez zeledón":    19,
    "perez zeledon":    19,
    "san isidro del general": 19,
    "león cortés":      20,
    "leon cortes":      20,
    # San José districts
    "san rafael de heredia": 40,
    "san rafael":       40,
    "santa bárbara":    41,
    "santa barbara":    41,
    "belen":            42,
    "belén":            42,
    "flores":           43,
    "ciudad cariari":   44,
    "cariari":          44,
    "la uruca":         45,
    "hatillo":          46,
    "san pedro":        47,
    "rohrmoser":        48,
    "sabana":           48,
    "lindora":          9,
    "ciudad colon":     7,
    "ciudad colón":     7,
    "pozos":            9,
    "santa ana pozos":  9,
    # Alajuela province
    "alajuela":         21,
    "san ramón":        22,
    "san ramon":        22,
    "grecia":           23,
    "san mateo":        24,
    "atenas":           25,
    "naranjo":          26,
    "palmares":         27,
    "poás":             28,
    "poas":             28,
    "orotina":          29,
    "san carlos":       30,
    "zarcero":          31,
    "valverde vega":    32,
    "upala":            33,
    "los chiles":       34,
    "guatuso":          35,
    # Heredia province
    "heredia":          36,
    "barva":            37,
    "santo domingo":    38,
    "santa bárbara de heredia": 41,
    "san pablo":        39,
    "san isidro":       40,
    "belen heredia":    42,
    "flores heredia":   43,
    "san antonio":      44,
    "sarapiquí":        45,
    "sarapiqui":        45,
    # Guanacaste province
    "guanacaste":       46,
    "liberia":          47,
    "nicoya":           48,
    "santa cruz":       50,
    "bagaces":          51,
    "carrillo":         52,
    "cañas":            53,
    "canas":            53,
    "abangares":        54,
    "tilarán":          55,
    "tilaran":          55,
    "nandayure":        56,
    "la cruz":          57,
    "hojancha":         58,
    # Guanacaste beach areas
    "tamarindo":        50,
    "nosara":           48,
    "sámara":           48,
    "samara":           48,
    "playa flamingo":   52,
    "flamingo":         52,
    "playa potrero":    1496,
    "potrero":          1496,
    "playa hermosa guanacaste": 52,
    "playa del coco":   52,
    "coco":             52,
    "el coco":          52,
    "playa conchal":    52,
    "conchal":          52,
    "playa avellanas":  50,
    "avellanas":        50,
    "playa negra guanacaste": 50,
    "playa grande":     50,
    "playa junquillal":  50,
    "junquillal":       50,
    "playa langosta":   50,
    "langosta":         50,
    "peninsula papagayo": 52,
    "papagayo":         52,
    "playas del coco":  52,
    "sardinal":         52,
    "brasilito":        52,
    "huacas":           52,
    "villareal":        50,
    "villa real":       50,
    "27 de abril":      50,
    "veintisiete de abril": 50,
    # Puntarenas province
    "puntarenas":       73,
    "esparza":          74,
    "buenos aires":     75,
    "montes de oro":    76,
    "osa":              77,
    "quepos":           78,
    "manuel antonio":   78,
    "golfito":          79,
    "coto brus":        80,
    "parrita":          81,
    "corredores":       82,
    "garabito":         83,
    "jaco":             83,
    "jacó":             83,
    "playa jaco":       83,
    "herradura":        83,
    "dominical":        77,
    "uvita":            77,
    "ojochal":          77,
    "bahia ballena":    77,
    "bahía ballena":    77,
    "playa hermosa puntarenas": 83,
    "playa bejuco":     81,
    "playa esterillos":  81,
    "santa teresa":     2075,  # Cóbano, Puntarenas (NOT Guanacaste)
    "mal pais":         2075,
    "mal país":         2075,
    "montezuma":        2075,
    "cobano":           2075,
    "cóbano":           2075,
    "peninsula de nicoya": 48,
    "nicoya peninsula": 48,
    # Limón province
    "limon":            100,
    "limón":            100,
    "pococi":           101,
    "siquirres":        102,
    "talamanca":        103,
    "matina":           104,
    "guácimo":          105,
    "guacimo":          105,
    "puerto viejo":     111,
    "puerto viejo de talamanca": 111,
    "cahuita":          103,
    "manzanillo":       103,
    "playa negra limon": 111,
    "playa negra":      111,
    # Cartago province
    "cartago":          60,
    "paraíso":          61,
    "paraiso":          61,
    "la unión":         62,
    "la union":         62,
    "jiménez":          63,
    "jimenez":          63,
    "turrialba":        64,
    "alvarado":         65,
    "oreamuno":         66,
    "el guarco":        67,
    # San José Capital districts
    "san jose":         139,  # San José Capital (not province)
    "san josé":         139,
    "nunciatura":       139,
    "barrio escalante":  5211,
    "rohrmoser":        2155,
    # Escazú districts
    "guachipelín":      117,
    "guachipelin":      117,
    # Cartago districts
    "tres ríos":        5172,
    "tres rios":        5172,
    # Default fallback
    "costa rica":       116,  # San José provincia (valid for country 2)
}

DEFAULT_REGION_ID = 116  # San José provincia (valid fallback for country 2)


# ─────────────────────────────────────────────────────────────────────
# LLM ENRICHMENT MODULE
# ─────────────────────────────────────────────────────────────────────

def _get_openai_client():
    """Lazy-load OpenAI client."""
    try:
        from openai import OpenAI
        return OpenAI()
    except ImportError:
        print("WARNING: openai package not installed. Run: pip3 install openai", file=sys.stderr)
        return None


def fetch_listing_detail(listing_id):
    """
    Fetch the detail endpoint for a single listing.
    Returns the detail dict or None on failure.
    """
    url = API_DETAIL_URL.format(id=listing_id)
    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Encuentra24FeedGenerator/1.0")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # Detail endpoint returns a property object with a listings array
        listings = data.get("listings") or []
        if listings:
            return data, listings[0]
        return data, None
    except Exception as e:
        print(f"    WARNING: Could not fetch detail for {listing_id}: {e}", file=sys.stderr)
        return None, None


def extract_detail_fields(detail_prop, detail_listing):
    """
    Extract enrichment fields from the detail API response.
    Returns: (en_description, es_description, highlights)
    """
    en_description = ""
    es_description = ""
    highlights = []

    if detail_listing:
        en_description = detail_listing.get("description") or ""

        # Spanish description from multilingual array
        multilingual = detail_prop.get("multilingual") or []
        for ml in multilingual:
            if ml.get("language_code") == "es_ES":
                es_description = ml.get("description") or ""
                break

        # Highlights — semicolon-delimited string
        raw_highlights = detail_listing.get("highlights_listings") or ""
        if raw_highlights:
            highlights = [h.strip() for h in raw_highlights.split(";") if h.strip()]

    return en_description, es_description, highlights


def generate_llm_title(client, prop, listing, en_description, es_description, highlights, mls):
    """
    Generate optimized bilingual titles using LLM.
    Structure: [Type] [X] habs/BR en [Location] - [Community] - [Hook]
    Max 70 characters each.
    """
    community = listing.get("community") or ""
    city = prop.get("city") or ""
    state = prop.get("state") or ""
    address = prop.get("address") or ""
    bedrooms = int(prop.get("bedrooms") or 0)
    subtype = listing.get("property_subtype") or listing.get("propertytype") or ""
    price = listing.get("listingprice") or 0

    context = f"""Property name: {listing.get('name', '')}
Location city: {city}
State/Province: {state}
Address: {address}
Community: {community}
Bedrooms: {bedrooms}
Property type: {subtype}
Price: ${price:,.0f} USD

AGENT HIGHLIGHTS (use the most compelling one as the hook):
{chr(10).join('- ' + h for h in highlights) if highlights else '(none provided)'}"""

    SYSTEM_ES = """Usted es un optimizador de títulos para un portal de clasificados de bienes raíces de lujo en Costa Rica (Encuentra24).

Cree UN título optimizado en español siguiendo esta estructura exacta:
[Tipo] [X] habs en [Ubicación] - [Comunidad] - [Gancho]

REGLAS:
- MÁXIMO 70 caracteres (límite estricto, cuente con cuidado)
- [Tipo]: Casa, Villa, Apartamento, etc.
- [X] habs: número de habitaciones
- [Ubicación]: ciudad o distrito más relevante
- [Comunidad]: nombre del condominio o desarrollo (omita si no es conocido)
- [Gancho]: diferenciador corto extraído de los highlights del agente (ej: "con piscina", "vista al mar", "renta vacacional", "a pasos de la playa", "con casa de huéspedes")
- Use acentos correctos (á, é, í, ó, ú, ñ)
- Nunca use signos de exclamación
- Nunca use guiones dentro de palabras (use "Single Level" no "Single-Level")
- Si la comunidad no existe o no es conocida, omítala y use más espacio para el gancho

Devuelva SOLO el título, nada más. Sin comillas, sin explicación."""

    SYSTEM_EN = """You are a title optimizer for a luxury real estate classified portal in Costa Rica (Encuentra24).

Create ONE optimized title in English following this exact structure:
[Type] [X]BR in [Location] - [Community] - [Hook]

RULES:
- MAXIMUM 70 characters (strict limit, count carefully)
- [Type]: Home, Villa, Condo, etc. (omit if space is tight)
- [X]BR: bedroom count using BR abbreviation
- [Location]: most relevant city or district
- [Community]: condo or development name (omit if not well known)
- [Hook]: short compelling differentiator from agent highlights (e.g., "with Pool", "Ocean View", "Rental Income", "Steps to Beach", "Guest House")
- Never use exclamation marks
- Never use hyphens within words (use "Single Level" not "Single-Level")

Output ONLY the title text, nothing else. No quotes, no explanation."""

    try:
        es_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_ES},
                {"role": "user", "content": f"Cree un título optimizado en español (máx 70 caracteres):\n\n{context}"}
            ],
            temperature=0.4,
            max_tokens=80,
        )
        es_title = es_resp.choices[0].message.content.strip().strip('"')
        # Enforce hard limit
        if len(es_title) > 70:
            es_title = es_title[:70].rsplit(" ", 1)[0]
    except Exception as e:
        print(f"    WARNING: ES title LLM failed for {mls}: {e}", file=sys.stderr)
        es_title = ""

    time.sleep(0.3)

    try:
        en_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_EN},
                {"role": "user", "content": f"Create an optimized English title (max 70 chars):\n\n{context}"}
            ],
            temperature=0.4,
            max_tokens=80,
        )
        en_title = en_resp.choices[0].message.content.strip().strip('"')
        if len(en_title) > 70:
            en_title = en_title[:70].rsplit(" ", 1)[0]
    except Exception as e:
        print(f"    WARNING: EN title LLM failed for {mls}: {e}", file=sys.stderr)
        en_title = ""

    time.sleep(0.3)
    return es_title, en_title


def generate_llm_descriptions(client, prop, listing, en_description, es_description, highlights, mls):
    """
    Generate two-paragraph bilingual descriptions.
    P1: Highlights-led narrative (400-600 chars)
    P2: Supporting details closing with MLS reference (400-600 chars)
    """
    community = listing.get("community") or ""
    city = prop.get("city") or ""
    state = prop.get("state") or ""
    bedrooms = int(prop.get("bedrooms") or 0)
    full_bath = int(prop.get("fullbathrooms") or 0)
    half_bath = int(prop.get("halfbathrooms") or 0)
    area = int(prop.get("totalarea") or 0)
    lot = int(prop.get("lotsize") or 0)
    price = listing.get("listingprice") or 0

    context = f"""Property: {listing.get('name', '')}
Location: {city}, {state}
Community: {community}
Price: ${price:,.0f} USD
Bedrooms: {bedrooms} | Bathrooms: {full_bath} full + {half_bath} half
Built area: {area} m² | Lot: {lot} m²
MLS: {mls}

AGENT HIGHLIGHTS (use these to lead paragraph 1):
{chr(10).join('- ' + h for h in highlights) if highlights else '(none provided)'}"""

    EN_SYSTEM = f"""You are a luxury real estate copywriter for The Agency Costa Rica.

Write a two paragraph property description for a classified listing portal.

PARAGRAPH 1 (400-600 characters):
- Lead with the agent's highlighted selling points provided below
- Weave them into a compelling, flowing narrative
- Focus on what makes this property distinctive

PARAGRAPH 2 (400-600 characters):
- Cover supporting details: specifications, amenities, location context, lifestyle appeal
- End the paragraph with exactly: "MLS {mls} The Agency Costa Rica"

RULES:
- Sophisticated, measured tone. No hype, no clichés, no exclamation marks.
- Never use hyphens in any form
- Separate the two paragraphs with a blank line
- Output ONLY the two paragraphs, nothing else
- Total output should be 800-1200 characters"""

    ES_SYSTEM = f"""Usted es un redactor de bienes raíces de lujo para The Agency Costa Rica.

Escriba una descripción de propiedad en dos párrafos para un portal de clasificados.

PÁRRAFO 1 (400-600 caracteres):
- Comience con los puntos destacados del agente proporcionados abajo
- Intégrelos en una narrativa fluida y atractiva
- Enfóquese en lo que hace única esta propiedad

PÁRRAFO 2 (400-600 caracteres):
- Cubra detalles de apoyo: especificaciones, amenidades, contexto de ubicación, estilo de vida
- Termine el párrafo exactamente con: "MLS {mls} The Agency Costa Rica"

REGLAS:
- Tono sofisticado y mesurado. Sin exageraciones, sin clichés, sin signos de exclamación.
- Nunca use guiones en ninguna forma
- Separe los dos párrafos con una línea en blanco
- Escriba SOLO los dos párrafos, nada más
- El resultado total debe ser de 800-1200 caracteres
- Use tono formal (usted)"""

    try:
        en_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": EN_SYSTEM},
                {"role": "user", "content": f"Write a two paragraph English description.\n\n{context}\n\nFull marketing description for reference:\n{en_description}"}
            ],
            temperature=0.4,
            max_tokens=500,
        )
        descr_en = en_resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"    WARNING: EN description LLM failed for {mls}: {e}", file=sys.stderr)
        descr_en = ""

    time.sleep(0.3)

    try:
        es_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": ES_SYSTEM},
                {"role": "user", "content": f"Escriba una descripción en español de dos párrafos.\n\n{context}\n\nDescripción completa de referencia:\n{es_description}"}
            ],
            temperature=0.4,
            max_tokens=600,
        )
        descr_es = es_resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"    WARNING: ES description LLM failed for {mls}: {e}", file=sys.stderr)
        descr_es = ""

    time.sleep(0.3)
    return descr_en, descr_es


def enrich_listings(eligible, use_llm=True):
    """
    For each eligible (prop, listing, ad_type) tuple:
      1. Fetch detail API for full description + highlights
      2. Generate optimized titles via LLM
      3. Generate two-paragraph descriptions via LLM
    Returns a dict keyed by MLS ID with enrichment data.
    Uses a cache file to avoid redundant LLM calls.
    """
    # Load existing cache
    cache = {}
    if os.path.exists(ENRICHMENT_CACHE_FILE):
        try:
            with open(ENRICHMENT_CACHE_FILE) as f:
                cache = json.load(f)
            print(f"  Loaded enrichment cache: {len(cache)} entries")
        except Exception:
            cache = {}

    client = _get_openai_client() if use_llm else None
    if use_llm and client is None:
        print("  LLM enrichment disabled (openai not available), using fallback descriptions.")
        use_llm = False

    total = len(eligible)
    new_entries = 0

    for i, (prop, listing, ad_type) in enumerate(eligible):
        mls = listing.get("lx_mls_id") or listing.get("id") or str(i)
        listing_id = prop.get("id")

        # Skip if already cached
        if mls in cache:
            continue

        print(f"  Enriching [{i+1}/{total}] {mls} ...", end=" ", flush=True)

        # Step 1: Fetch detail
        en_description = ""
        es_description = ""
        highlights = []

        if listing_id:
            detail_prop, detail_listing = fetch_listing_detail(listing_id)
            if detail_prop and detail_listing:
                en_description, es_description, highlights = extract_detail_fields(detail_prop, detail_listing)

        # Step 2 & 3: LLM enrichment
        es_title_opt = ""
        en_title_opt = ""
        descr_en = ""
        descr_es = ""

        if use_llm and client:
            es_title_opt, en_title_opt = generate_llm_title(
                client, prop, listing, en_description, es_description, highlights, mls
            )
            descr_en, descr_es = generate_llm_descriptions(
                client, prop, listing, en_description, es_description, highlights, mls
            )

        cache[mls] = {
            "en_description_full": en_description,
            "es_description_full": es_description,
            "highlights": highlights,
            "es_title_optimized": es_title_opt,
            "en_title_optimized": en_title_opt,
            "descr_en": descr_en,
            "descr_es": descr_es,
        }
        new_entries += 1
        print("done")

        # Save cache incrementally (every 5 entries)
        if new_entries % 5 == 0:
            with open(ENRICHMENT_CACHE_FILE, "w") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)

    # Final cache save
    if new_entries > 0:
        with open(ENRICHMENT_CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        print(f"  Enrichment complete: {new_entries} new entries cached.")
    else:
        print(f"  All {total} listings served from cache.")

    return cache


# ─────────────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────

def cdata(value):
    """Wrap a value in a CDATA section."""
    if value is None:
        return "<![CDATA[]]>"
    return f"<![CDATA[{str(value)}]]>"


def resolve_region_id(prop, listing):
    """
    Resolve the Encuentra24 region ID from property location fields.
    Tries address → community → city → state in order for best specificity.
    """
    candidates = [
        prop.get("address") or "",
        listing.get("community") or "",
        prop.get("city") or "",
        prop.get("state") or "",
    ]

    for candidate in candidates:
        key = candidate.strip().lower()
        if key in REGION_MAP:
            return REGION_MAP[key]
        # Partial match: check if any known region key is contained in the candidate
        for region_key, region_id in REGION_MAP.items():
            if region_key in key and len(region_key) > 4:
                return region_id

    return DEFAULT_REGION_ID


def resolve_category_id(listing, ad_type):
    """Resolve the Encuentra24 category ID from listing subtype."""
    subtype = listing.get("property_subtype") or listing.get("propertytype") or ""

    # Prefer house-type categories over condo when multiple subtypes present
    house_types = ("Single Family", "House", "Residential")
    for ht in house_types:
        if ht.lower() in subtype.lower():
            if ad_type == "property":
                return SALE_CATEGORY_MAP.get(ht, SALE_DEFAULT_CATEGORY)
            elif ad_type == "rent":
                return RENT_CATEGORY_MAP.get(ht, RENT_DEFAULT_CATEGORY)

    if ad_type == "property":
        for key, cat_id in SALE_CATEGORY_MAP.items():
            if key.lower() in subtype.lower():
                return cat_id
        return SALE_DEFAULT_CATEGORY

    elif ad_type == "rent":
        for key, cat_id in RENT_CATEGORY_MAP.items():
            if key.lower() in subtype.lower():
                return cat_id
        return RENT_DEFAULT_CATEGORY

    elif ad_type == "lot":
        prop_type = listing.get("propertytype") or ""
        for key, cat_id in LOT_CATEGORY_MAP.items():
            if key.lower() in prop_type.lower():
                return cat_id
        return LOT_DEFAULT_CATEGORY

    return SALE_DEFAULT_CATEGORY


def determine_ad_type(prop, listing):
    """Determine which Encuentra24 ad type to use: 'property', 'rent', or 'lot'."""
    property_type = listing.get("propertytype") or ""
    listing_type = listing.get("listingtype") or "Sale"

    if property_type in ("Lots And Land", "Farm And Agriculture"):
        return "lot"
    if listing_type == "Rent":
        return "rent"
    return "property"


def format_bathrooms(full, half):
    """Convert fullbathrooms + halfbathrooms to Encuentra24 bath value."""
    full = full or 0
    half = half or 0
    total = full + (0.5 * half)
    if total <= 0:
        return "0"
    if total > 20:
        return "20+"
    if total == int(total):
        return str(int(total))
    if total <= 5.5:
        return str(total)
    return str(int(math.ceil(total)))


def format_bedrooms(bedrooms):
    """Convert bedrooms to Encuentra24 rooms value. Accepts 0-15, 15+."""
    if bedrooms is None:
        return "0"
    b = int(bedrooms)
    return "15+" if b > 15 else str(b)


def format_parking(spaces):
    """Convert parking spaces to Encuentra24 parking value. Accepts 0-10, Más."""
    if spaces is None:
        return "0"
    p = int(spaces)
    return "Más" if p > 10 else str(p)


def get_image_urls(prop):
    """Extract sorted image URLs from the media array."""
    media = prop.get("media") or []
    website_media = [m for m in media if m.get("isonwebsite")]
    website_media.sort(key=lambda m: m.get("sortonwebsite", 0))
    urls = []
    for m in website_media:
        url = m.get("url") or m.get("midresurl") or m.get("baseurl")
        if url:
            urls.append(url)
    if not urls:
        all_media = sorted(media, key=lambda m: m.get("sortonportalfeed", 0))
        for m in all_media:
            url = m.get("url") or m.get("midresurl")
            if url:
                urls.append(url)
    return urls[:MAX_PHOTOS]


def get_spanish_title(prop, listing):
    """Get the Spanish title from multilingual data, or fall back to listing name."""
    multilingual = prop.get("multilingual") or []
    for ml in multilingual:
        if ml.get("language_code") == "es_ES":
            title = ml.get("title")
            if title:
                return title
    return listing.get("name") or "Propiedad en Costa Rica"


def get_english_title(listing):
    """Get the English title from the listing name."""
    return listing.get("name") or ""


def get_youtube_url(prop, listing):
    """Extract a YouTube URL if available."""
    for field in [
        prop.get("virtual_tour_video_url") or "",
        listing.get("live_tour_url") or "",
        listing.get("vertical_video_1") or "",
    ]:
        if "youtube.com" in field or "youtu.be" in field:
            return field
    return ""


def get_agent_contact(listing):
    """Get agent contact info from listing, with fallback to company defaults."""
    agent = listing.get("agent") or {}
    office = listing.get("office") or {}
    email = agent.get("email") or CONTACT_EMAIL
    phone = agent.get("phone") or agent.get("mobile") or office.get("phone") or CONTACT_PHONE
    name = f"{agent.get('firstname', '')} {agent.get('lastname', '')}".strip()
    if not name:
        name = CONTACT_NAME
    return email, phone, name


def detect_benefits_property(listing):
    """Map LX features to Encuentra24 property benefits (comma-separated)."""
    features = listing.get("features") or {}
    all_features = []
    for category in ["internal", "external", "community", "lifestyle"]:
        raw = features.get(category) or ""
        all_features.extend([f.strip().lower() for f in raw.split(";") if f.strip()])

    mapping = {
        "jacuzzi":            "Jacuzzi",
        "bar area":           "Bar",
        "bar":                "Bar",
        "gym":                "Gimnasio",
        "gymnasium":          "Gimnasio",
        "playground":         "Parque Infantil",
        "security guard":     "Seguridad 24 Horas",
        "controlled access":  "Seguridad 24 Horas",
        "24/7 security":      "Seguridad 24 Horas",
        "ocean view":         "Vista al Mar",
        "ocean views":        "Vista al Mar",
        "mountain view":      "Vista a las Montañas",
        "mountain views":     "Vista a las Montañas",
        "lake view":          "Vista al Lago",
        "beachfront":         "Frente al Mar",
        "beach front":        "Frente al Mar",
        "terrace/patio":      "Patio",
        "patio":              "Patio",
        "garden":             "Jardín",
        "walk-in closet":     "Walk-in closet",
        "walk in closet":     "Walk-in closet",
        "a/c":                "Aire acondicionado",
        "air conditioning":   "Aire acondicionado",
        "central a/c":        "A/C central",
        "elevator":           "2 o más elevadores",
        "pets allowed":       "Pet Friendly",
        "pet friendly":       "Pet Friendly",
        "social area":        "Área Social",
        "bbq area":           "Área de BBQ",
        "barbecue":           "Área de BBQ",
    }

    benefits = []
    for feat in all_features:
        if feat in mapping:
            b = mapping[feat]
            if b not in benefits:
                benefits.append(b)
    return ",".join(benefits) if benefits else ""


def has_pool(listing):
    """Check if property has a pool based on features."""
    features = listing.get("features") or {}
    all_text = " ".join([features.get(k, "") for k in features]).lower()
    return "pool" in all_text or "piscina" in all_text


def has_balcony_terrace(listing):
    """Check if property has balcony or terrace."""
    features = listing.get("features") or {}
    all_text = " ".join([features.get(k, "") for k in features]).lower()
    if "balcony" in all_text or "balcón" in all_text:
        return "balcón"
    if "terrace" in all_text or "terraza" in all_text or "terrace/patio" in all_text:
        return "terraza"
    return ""


# ─────────────────────────────────────────────────────────────────────
# FALLBACK DESCRIPTION GENERATORS (used when LLM enrichment is off)
# ─────────────────────────────────────────────────────────────────────

def _fallback_description_es(prop, listing, ad_type):
    """Generate a basic Spanish description from structured data."""
    parts = []
    name = get_spanish_title(prop, listing)
    parts.append(f"{name}.")
    city = prop.get("city") or ""
    state = prop.get("state") or ""
    community = listing.get("community") or ""
    location_parts = [p for p in [community, city, state] if p]
    if location_parts:
        parts.append(f"Ubicación: {', '.join(location_parts)}.")
    if ad_type in ("property", "rent"):
        bedrooms = prop.get("bedrooms")
        full_bath = prop.get("fullbathrooms")
        half_bath = prop.get("halfbathrooms")
        area = prop.get("totalarea")
        lot = prop.get("lotsize")
        details = []
        if bedrooms and bedrooms > 0:
            details.append(f"{int(bedrooms)} habitaciones")
        if full_bath and full_bath > 0:
            bath_str = f"{int(full_bath)} baños"
            if half_bath and half_bath > 0:
                bath_str += f" + {int(half_bath)} medio baño"
            details.append(bath_str)
        if area and area > 0:
            details.append(f"{int(area)} m² de construcción")
        if lot and lot > 0:
            details.append(f"{int(lot)} m² de terreno")
        if details:
            parts.append(" | ".join(details) + ".")
    elif ad_type == "lot":
        lot = prop.get("lotsize")
        area = prop.get("totalarea")
        if lot and lot > 0:
            parts.append(f"Terreno de {int(lot)} m².")
        if area and area > 0:
            parts.append(f"Área construida: {int(area)} m².")
    features = listing.get("features") or {}
    internal = features.get("internal") or ""
    external = features.get("external") or ""
    if internal:
        parts.append(f"Características: {internal.replace(';', ', ')}.")
    if external:
        parts.append(f"Exteriores: {external.replace(';', ', ')}.")
    parts.append(f"MLS {listing.get('lx_mls_id', '')} The Agency Costa Rica.")
    return " ".join(parts)


def _fallback_description_en(prop, listing, ad_type):
    """Generate a basic English description from structured data."""
    parts = []
    name = get_english_title(listing)
    parts.append(f"{name}.")
    city = prop.get("city") or ""
    state = prop.get("state") or ""
    community = listing.get("community") or ""
    location_parts = [p for p in [community, city, state] if p]
    if location_parts:
        parts.append(f"Location: {', '.join(location_parts)}.")
    if ad_type in ("property", "rent"):
        bedrooms = prop.get("bedrooms")
        full_bath = prop.get("fullbathrooms")
        half_bath = prop.get("halfbathrooms")
        area = prop.get("totalarea")
        lot = prop.get("lotsize")
        details = []
        if bedrooms and bedrooms > 0:
            details.append(f"{int(bedrooms)} bedrooms")
        if full_bath and full_bath > 0:
            bath_str = f"{int(full_bath)} bathrooms"
            if half_bath and half_bath > 0:
                bath_str += f" + {int(half_bath)} half bath"
            details.append(bath_str)
        if area and area > 0:
            details.append(f"{int(area)} m² built area")
        if lot and lot > 0:
            details.append(f"{int(lot)} m² lot")
        if details:
            parts.append(" | ".join(details) + ".")
    elif ad_type == "lot":
        lot = prop.get("lotsize")
        area = prop.get("totalarea")
        if lot and lot > 0:
            parts.append(f"Lot size: {int(lot)} m².")
        if area and area > 0:
            parts.append(f"Built area: {int(area)} m².")
    features = listing.get("features") or {}
    internal = features.get("internal") or ""
    external = features.get("external") or ""
    if internal:
        parts.append(f"Features: {internal.replace(';', ', ')}.")
    if external:
        parts.append(f"Exterior: {external.replace(';', ', ')}.")
    parts.append(f"MLS {listing.get('lx_mls_id', '')} The Agency Costa Rica.")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────
# XML GENERATION
# ─────────────────────────────────────────────────────────────────────

def generate_item_xml(prop, listing, ad_type, enrichment=None):
    """Generate the <item> XML block for a single listing."""
    mls = listing.get("lx_mls_id") or listing.get("id") or prop.get("id")
    enrich = enrichment or {}

    region_id   = resolve_region_id(prop, listing)
    category_id = resolve_category_id(listing, ad_type)

    # Titles: use LLM-optimized if available, else fall back to API values
    title_es = enrich.get("es_title_optimized") or get_spanish_title(prop, listing)
    title_en = enrich.get("en_title_optimized") or get_english_title(listing)

    # Descriptions: use LLM two-paragraph if available, else fallback
    descr_es = enrich.get("descr_es") or _fallback_description_es(prop, listing, ad_type)
    descr_en = enrich.get("descr_en") or _fallback_description_en(prop, listing, ad_type)

    price  = listing.get("listingprice")
    images = get_image_urls(prop)
    youtube = get_youtube_url(prop, listing)
    email, phone, contact_name = get_agent_contact(listing)
    community = listing.get("community") or ""

    lines = []
    lines.append("    <item>")

    # ── REQUIRED ──
    lines.append("      <required>")
    lines.append("        <ad>")
    lines.append(f"          <sourceid>{cdata(mls)}</sourceid>")
    lines.append(f"          <countryid>{cdata(COUNTRY_ID)}</countryid>")
    lines.append(f"          <categoryid>{cdata(str(category_id))}</categoryid>")
    lines.append(f"          <regionid>{cdata(str(region_id))}</regionid>")
    lines.append(f"          <type>{cdata(ad_type)}</type>")
    lines.append(f"          <title>{cdata(title_es)}</title>")
    lines.append(f"          <currency>{cdata('USD')}</currency>")

    if ad_type == "rent":
        lines.append(f"          <rent>{cdata(str(int(price)) if price else '0')}</rent>")
        lines.append(f"          <rooms>{cdata(format_bedrooms(prop.get('bedrooms')))}</rooms>")
        lines.append(f"          <bath>{cdata(format_bathrooms(prop.get('fullbathrooms'), prop.get('halfbathrooms')))}</bath>")
        lines.append(f"          <parking>{cdata(format_parking(prop.get('parkingspaces')))}</parking>")
    elif ad_type == "property":
        lines.append(f"          <price>{cdata(str(int(price)) if price else '0')}</price>")
        lines.append(f"          <rooms>{cdata(format_bedrooms(prop.get('bedrooms')))}</rooms>")
        lines.append(f"          <bath>{cdata(format_bathrooms(prop.get('fullbathrooms'), prop.get('halfbathrooms')))}</bath>")
        area_m2 = prop.get("totalarea")
        lines.append(f"          <square>{cdata(str(int(area_m2)) if area_m2 else '0')}</square>")
        lines.append(f"          <parking>{cdata(format_parking(prop.get('parkingspaces')))}</parking>")
    elif ad_type == "lot":
        lines.append(f"          <price>{cdata(str(int(price)) if price else '0')}</price>")
        lotsize = prop.get("lotsize")
        lines.append(f"          <lotsize>{cdata(str(int(lotsize)) if lotsize else '0')}</lotsize>")

    lines.append(f"          <advertiser>{cdata(ADVERTISER_TYPE)}</advertiser>")
    lines.append("        </ad>")

    lines.append("        <contact>")
    lines.append(f"          <email>{cdata(email)}</email>")
    lines.append(f"          <phone>{cdata(phone)}</phone>")
    lines.append(f"          <contact>{cdata(contact_name)}</contact>")
    lines.append(f"          <city>{cdata(prop.get('city') or CONTACT_CITY)}</city>")
    lines.append("        </contact>")
    lines.append("      </required>")

    # ── OPTIONAL ──
    lines.append("      <optional>")
    lines.append("        <ad>")

    if title_en:
        lines.append(f"          <title1>{cdata(title_en)}</title1>")
    if descr_es:
        lines.append(f"          <descr>{cdata(descr_es)}</descr>")
    if descr_en:
        lines.append(f"          <descr1>{cdata(descr_en)}</descr1>")

    for img_url in images:
        lines.append(f"          <picture>{cdata(img_url)}</picture>")

    if ad_type in ("property", "rent"):
        lotsize = prop.get("lotsize")
        if lotsize:
            lines.append(f"          <lotsize>{cdata(str(int(lotsize)))}</lotsize>")
        if ad_type == "rent":
            area_m2 = prop.get("totalarea")
            if area_m2:
                lines.append(f"          <square>{cdata(str(int(area_m2)))}</square>")
        if has_pool(listing):
            lines.append(f"          <swimmingpool>{cdata('si')}</swimmingpool>")
        balcony = has_balcony_terrace(listing)
        if balcony:
            lines.append(f"          <balcon>{cdata(balcony)}</balcon>")
        benefits = detect_benefits_property(listing)
        if benefits:
            lines.append(f"          <benefits>{cdata(benefits)}</benefits>")
        if community:
            lines.append(f"          <building>{cdata(community)}</building>")

    elif ad_type == "lot":
        area_m2 = prop.get("totalarea")
        if area_m2:
            lines.append(f"          <m2>{cdata(str(int(area_m2)))}</m2>")
        bedrooms = prop.get("bedrooms")
        if bedrooms and bedrooms > 0:
            lines.append(f"          <rooms>{cdata(format_bedrooms(bedrooms))}</rooms>")
        bath = format_bathrooms(prop.get("fullbathrooms"), prop.get("halfbathrooms"))
        if bath != "0":
            lines.append(f"          <bath>{cdata(bath)}</bath>")
        parking = prop.get("parkingspaces")
        if parking:
            lines.append(f"          <parking>{cdata(format_parking(parking))}</parking>")
        benefits = detect_benefits_property(listing)
        if benefits:
            lines.append(f"          <benefits>{cdata(benefits)}</benefits>")

    if youtube:
        lines.append(f"          <youtube1>{cdata(youtube)}</youtube1>")

    lines.append(f"          <uhaschat>{cdata('Quiero recibir chats')}</uhaschat>")
    lines.append(f"          <sourceid>{cdata(mls)}</sourceid>")

    lines.append("        </ad>")
    lines.append("        <contact>")
    lines.append(f"          <company>{cdata(CONTACT_COMPANY)}</company>")
    lines.append(f"          <url>{cdata(CONTACT_URL)}</url>")
    lines.append("        </contact>")
    lines.append("      </optional>")
    lines.append("    </item>")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# FEED ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────

def generate_feed(properties, filter_type="all", max_listings=MAX_LISTINGS, use_llm=True):
    """
    Generate the complete Encuentra24 XML feed.

    filter_type:  'all', 'sale', 'rent', or 'lot'
    max_listings: cap on total listings (default: MAX_LISTINGS)
    use_llm:      enable LLM enrichment for titles and descriptions

    Prioritization (3-tier system):

      TIER A — All exclusive sale listings under EXCLUSIVE_PRICE_CAP.
               Exclusive flag overrides EPP priority — all exclusives included.
               Sorted by price ascending.

      TIER B — Rental listings under RENTAL_PRICE_CAP/month.
               EPP priorities (18, 19, 20) excluded.
               Sorted by price ascending.
               Fills slots after Tier A.

      TIER C — Non-exclusive sale listings, residential only (no lots/land/farm).
               EPP priorities (18, 19, 20) excluded.
               Price cap: SALE_PRICE_CAP.
               Sorted by price ascending (cheapest first).
               Fills remaining slots after Tiers A and B.
    """

    def get_priority(prop):
        try:
            return int(prop.get("priority") or 0)
        except:
            return 0

    def is_lot_listing(prop, listing):
        st = listing.get("property_subtype") or ""
        pt = listing.get("propertytype") or ""
        return any(x in st for x in ["Lots", "Farm", "Land"]) or pt in ["Lots And Land", "Farm And Agriculture"]

    # ── Step 1: Collect all active published listings ──
    all_items = []
    skipped = 0

    for prop in properties:
        priority = get_priority(prop)
        for listing in (prop.get("listings") or []):
            if not listing.get("publish"):
                skipped += 1
                continue
            if listing.get("status") != "Active":
                skipped += 1
                continue
            price = listing.get("listingprice")
            if not price or price <= 0:
                skipped += 1
                continue
            ad_type = determine_ad_type(prop, listing)
            is_exclusive = bool(listing.get("exclusive_listing"))
            is_epp = priority in EPP_PRIORITIES
            all_items.append((prop, listing, ad_type, price, is_exclusive, is_epp, priority))

    # ── Step 2: Build Tier A — Exclusive sales ≤ EXCLUSIVE_PRICE_CAP ──
    # Exclusive flag overrides EPP — all exclusives included regardless of priority
    tier_a = [
        (prop, listing, ad_type)
        for prop, listing, ad_type, price, is_exclusive, is_epp, priority in all_items
        if ad_type in ("property", "lot")
        and is_exclusive
        and price <= EXCLUSIVE_PRICE_CAP
    ]
    tier_a.sort(key=lambda x: x[1].get("listingprice") or float("inf"))
    tier_a_mls = {listing.get("lx_mls_id") for _, listing, _ in tier_a}

    # ── Step 3: Build Tier B — Rentals ≤ RENTAL_PRICE_CAP, no EPP ──
    remaining_after_a = (max_listings or 9999) - len(tier_a)
    tier_b_pool = [
        (prop, listing, ad_type)
        for prop, listing, ad_type, price, is_exclusive, is_epp, priority in all_items
        if ad_type == "rent"
        and price <= RENTAL_PRICE_CAP
        and not is_epp
    ]
    tier_b_pool.sort(key=lambda x: x[1].get("listingprice") or float("inf"))
    tier_b = tier_b_pool[:remaining_after_a]

    # ── Step 4: Build Tier C — Non-exclusive sales, no lots, no EPP ──
    remaining_after_ab = (max_listings or 9999) - len(tier_a) - len(tier_b)
    tier_c_pool = [
        (prop, listing, ad_type)
        for prop, listing, ad_type, price, is_exclusive, is_epp, priority in all_items
        if ad_type in ("property", "lot")
        and listing.get("lx_mls_id") not in tier_a_mls  # not already in Tier A
        and not is_epp                                    # no EPP
        and not is_lot_listing(prop, listing)             # no lots/land/farm
        and price <= SALE_PRICE_CAP
    ]
    tier_c_pool.sort(key=lambda x: x[1].get("listingprice") or float("inf"))
    tier_c = tier_c_pool[:remaining_after_ab]

    final = tier_a + tier_b + tier_c

    n_tier_a = len(tier_a)
    n_tier_b = len(tier_b)
    n_tier_c = len(tier_c)
    total_eligible = len(all_items)

    print(f"  Total active published listings: {total_eligible}")
    print(f"  Tier A — Exclusives ≤ ${EXCLUSIVE_PRICE_CAP:,.0f} (excl overrides priority): {n_tier_a}")
    print(f"  Tier B — Rentals ≤ ${RENTAL_PRICE_CAP:,.0f}/mo (no EPP): {n_tier_b} of {len(tier_b_pool)}")
    print(f"  Tier C — Sale, no lots, no EPP, cheapest up: {n_tier_c} of {len(tier_c_pool)}")
    print(f"  TOTAL: {len(final)}")
    if tier_c:
        cutoff = tier_c[-1][1].get('listingprice', 0)
        print(f"  Tier C price ceiling: ${cutoff:,.0f}")
        if len(tier_c_pool) > len(tier_c):
            nxt = tier_c_pool[len(tier_c)]
            print(f"  First excluded (Tier C): ${nxt[1].get('listingprice',0):,.0f} — {nxt[1].get('name','')}")
    if skipped:
        print(f"  Skipped (inactive/no price): {skipped}")

    # ── Step 3: LLM Enrichment ──
    print(f"\nEnriching {len(final)} listings ...")
    enrichment_cache = enrich_listings(final, use_llm=use_llm)

    # ── Step 4: Generate XML ──
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append("<import>")
    lines.append("")
    lines.append("  <settings>")
    lines.append(f"    <type>{cdata('property')}</type>")
    lines.append(f"    <language>{cdata(LANGUAGE)}</language>")
    lines.append("  </settings>")
    lines.append("")
    lines.append("  <items>")

    count = 0
    for prop, listing, ad_type in final:
        mls = listing.get("lx_mls_id") or listing.get("id") or prop.get("id")
        enrich = enrichment_cache.get(str(mls)) or {}
        try:
            item_xml = generate_item_xml(prop, listing, ad_type, enrichment=enrich)
            lines.append(item_xml)
            count += 1
        except Exception as e:
            print(f"  WARNING: Skipped {mls} — {e}", file=sys.stderr)
            skipped += 1

    lines.append("  </items>")
    lines.append("")
    lines.append("</import>")

    return "\n".join(lines), count, skipped


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

API_SNAPSHOT_FILE = "api_snapshot.json"
API_SNAPSHOT_MAX_AGE = 23 * 3600  # 23 hours


def fetch_properties(force_refresh=False):
    """
    Fetch all properties from the LX Costa Rica API.
    Results are cached in api_snapshot.json for up to 23 hours to avoid
    hammering the API on every run.
    """
    if not force_refresh and os.path.exists(API_SNAPSHOT_FILE):
        age = time.time() - os.path.getmtime(API_SNAPSHOT_FILE)
        if age < API_SNAPSHOT_MAX_AGE:
            print(f"Loading properties from snapshot (age: {int(age/60)}m) ...")
            with open(API_SNAPSHOT_FILE) as f:
                data = json.load(f)
            print(f"  Loaded {len(data)} properties from cache.")
            return data

    print(f"Fetching properties from {API_URL} ...")
    req = urllib.request.Request(API_URL)
    req.add_header("User-Agent", "Encuentra24FeedGenerator/1.0")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    print(f"  Received {len(data)} properties from API.")

    with open(API_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"  Saved API snapshot to {API_SNAPSHOT_FILE}.")
    return data


def main():
    parser = argparse.ArgumentParser(
        description="Generate Encuentra24 XML feed from LX Costa Rica API"
    )
    parser.add_argument("--output", "-o", default="encuentra24_feed.xml",
                        help="Output XML file path (default: encuentra24_feed.xml)")
    parser.add_argument("--type", "-t", choices=["all", "sale", "rent", "lot"], default="all",
                        help="Filter by listing type (default: all)")
    parser.add_argument("--input", "-i", default=None,
                        help="Use a local JSON file instead of fetching from API")
    parser.add_argument("--limit", "-l", type=int, default=MAX_LISTINGS,
                        help=f"Max listings in feed (default: {MAX_LISTINGS}). Use 0 for unlimited.")
    parser.add_argument("--no-enrich", action="store_true",
                        help="Skip LLM enrichment and use fast fallback descriptions")
    parser.add_argument("--clear-cache", action="store_true",
                        help="Delete the enrichment cache before running")
    parser.add_argument("--refresh-api", action="store_true",
                        help="Force re-fetch from API even if snapshot is fresh")
    args = parser.parse_args()

    if args.clear_cache and os.path.exists(ENRICHMENT_CACHE_FILE):
        os.remove(ENRICHMENT_CACHE_FILE)
        print(f"Cleared enrichment cache: {ENRICHMENT_CACHE_FILE}")

    # Load properties
    if args.input:
        print(f"Loading properties from {args.input} ...")
        with open(args.input) as f:
            properties = json.load(f)
        print(f"  Loaded {len(properties)} properties.")
    else:
        properties = fetch_properties(force_refresh=args.refresh_api)

    limit = args.limit if args.limit > 0 else None
    use_llm = not args.no_enrich

    print(f"\nGenerating feed (type={args.type}, limit={limit or 'unlimited'}, llm={'on' if use_llm else 'off'}) ...")
    xml_content, count, skipped = generate_feed(
        properties, args.type, max_listings=limit, use_llm=use_llm
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(xml_content)

    print(f"\nDone!")
    print(f"  Listings included: {count}")
    print(f"  Listings skipped:  {skipped}")
    print(f"  Output file:       {args.output}")
    print(f"  File size:         {os.path.getsize(args.output) / 1024:.1f} KB")
    if use_llm:
        print(f"  Enrichment cache:  {ENRICHMENT_CACHE_FILE}")


if __name__ == "__main__":
    main()
