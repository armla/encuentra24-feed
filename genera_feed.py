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
from datetime import datetime, date, timedelta
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
MAX_LISTINGS = 250

# ── Five-Tier Structure ────────────────────────────────────────────────────────
# Tier A: Exclusive residential sales (no lots) at or below this price
EXCLUSIVE_PRICE_CAP = 1_250_000  # USD

# Tier B: Rentals at or below this monthly price (no EPP)
RENTAL_PRICE_CAP = 4_950  # USD/month

# Tier C: ALL exclusive lots, farms & land (no price cap — exclusive flag overrides)
# (no constant needed — all exclusive lots included regardless of price)

# Tier D: Non-exclusive residential sales (no lots) up to this price (no EPP)
SALE_PRICE_CAP = 980_000  # USD  — Tier D ceiling

# Tier E: Non-exclusive lots, farms & land, cheapest first (no EPP, no price cap)
# (fills remaining slots after Tiers A–D)

# EPP priority numbers — excluded from ALL non-exclusive listings
# 18 = EPP Casas High-end, 19 = EPP Casas normales, 20 = EPP Lotes
EPP_PRIORITIES = {18, 19, 20}

# LLM model for enrichment
LLM_MODEL = "gpt-4.1-mini"

# Cache file for LLM enrichment results (avoids re-generating on every run)
ENRICHMENT_CACHE_FILE = "enrichment_cache.json"

# State file for photo rotation (tracks last swap date per MLS ID)
PHOTO_ROTATION_STATE_FILE = "photo_rotation_state.json"

# Zapier webhook URL — fires once per NEW listing added to the feed
# Set to empty string to disable. Override via ZAPIER_WEBHOOK_URL env var.
ZAPIER_WEBHOOK_URL = os.environ.get(
    "ZAPIER_WEBHOOK_URL",
    "https://hooks.zapier.com/hooks/catch/3798504/uj79jgd/"
)

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
    # ── San José provincia (ID: 116) ──────────────────────────────────
    # Canton-level
    "san jose":              139,   # San José Capital canton
    "san josé":              139,
    "escazú":                117,
    "escazu":                117,
    "desamparados":          118,
    "puriscal":              1474,
    "tarrazú":               1473,
    "tarrazu":               1473,
    "aserrí":                121,
    "aserri":                121,
    "mora":                  1472,
    "goicoechea":            1471,
    "santa ana":             124,
    "alajuelita":            1475,
    "vásquez de coronado":   1470,
    "vasquez de coronado":   1470,
    "acosta":                1469,
    "tibás":                 1468,
    "tibas":                 1468,
    "moravia":               1466,
    "montes de oca":         1467,
    "turrubares":            1465,
    "dota":                  1464,
    "curridabat":            132,
    "pérez zeledón":         133,
    "perez zeledon":         133,
    "san isidro del general": 1463,
    "león cortés":           138,
    "leon cortes":           138,
    # San José Capital districts
    "nunciatura":            139,
    "barrio escalante":      5211,
    "carmen":                140,
    "catedral":              143,
    "hatillo":               149,
    "hospital":              142,
    "mata redonda":          147,
    "merced":                141,
    "pavas":                 148,
    "rohrmoser":             2155,
    "sabana":                2155,   # Sabana is in Rohrmoser district
    "san francisco de dos rios": 145,
    "san sebastian":         150,
    "uruca":                 146,
    "la uruca":              146,
    "zapote":                144,
    # Escazú districts
    "escazú centro":         2140,
    "san antonio de escazu": 2139,
    "san antonio":           2139,
    "san rafael de escazu":  2138,
    "guachipelín":           117,    # Guachipelín is in Escazú canton
    "guachipelin":           117,
    # Santa Ana districts
    "lindora":               5623,
    "pozos":                 5179,
    "santa ana centro":      5176,
    "brasil santa ana":      5181,
    "piedades":              5180,
    "rio oro":               5213,
    "río oro":               5213,
    "ciudad colon":          1472,   # Colón district is in Mora canton
    "ciudad colón":          1472,
    # Curridabat districts
    "ciudad curridabat":     1478,
    "granadilla":            1477,
    "sanchez":               1479,
    # Montes de Oca districts
    "san pedro":             129,
    "sabanilla":             1488,
    # Moravia districts
    "san vicente":           128,
    # Goicoechea districts
    "guadalupe":             123,
    # Heredia province districts used as San José refs (correct below)
    # ── Alajuela provincia (ID: 3) ────────────────────────────────────
    "alajuela":              4,
    "san ramón":             5,
    "san ramon":             5,
    "grecia":                6,
    "san mateo":             7,
    "atenas":                8,
    "naranjo":               9,
    "palmares":              10,
    "poás":                  2119,
    "poas":                  2119,
    "orotina":               12,
    "san carlos":            19,
    "la fortuna":            13,
    "zarcero":               14,
    "sarchí":                15,
    "sarchi":                15,
    "upala":                 16,
    "los chiles":            17,
    "guatuso":               5230,
    "san miguel":            20,
    "san pedro alajuela":    11,
    "tambor alajuela":       2093,
    "coyol":                 2080,
    "carrizal":              2095,
    "san rafael alajuela":   2113,   # San Rafael district, Alajuela canton
    "san rafael de alajuela": 2113,
    # ── Heredia provincia (ID: 35) ────────────────────────────────────
    "heredia":               36,
    "barva":                 37,
    "santo domingo":         38,
    "santa bárbara de heredia": 39,
    "santa barbara de heredia": 39,
    "santa bárbara":         39,
    "santa barbara":         39,
    "san pablo heredia":     44,
    "san isidro heredia":    41,
    "san isidro":            41,
    "belén heredia":         68,
    "belen heredia":         68,
    "belén":                 68,
    "belen":                 68,
    "flores heredia":        1459,
    "flores":                1459,
    "san rafael de heredia": 40,
    "san rafael":            40,
    "sarapiquí":             1460,
    "sarapiqui":             1460,
    "ulloa":                 1543,
    "mercedes heredia":      24,
    "san antonio heredia":   42,
    "san antonio":           42,
    "san pablo":             44,
    # ── Guanacaste provincia (ID: 47) ─────────────────────────────────
    "guanacaste":            47,
    "liberia":               48,
    "nicoya":                49,
    "santa cruz":            50,
    "bagaces":               51,
    "carrillo":              1457,
    "cañas":                 53,
    "canas":                 53,
    "abangares":             54,
    "tilarán":               55,
    "tilaran":               55,
    "nandayure":             1456,
    "la cruz":               57,
    "hojancha":              58,
    # Guanacaste beach/district areas
    "tamarindo":             62,
    "nosara":                1494,
    "sámara":                64,
    "samara":                64,
    "playa flamingo":        63,
    "flamingo":              63,
    "playa potrero":         1496,
    "potrero":               1496,
    "playa hermosa guanacaste": 61,
    "playa hermosa":         61,
    "playa del coco":        66,
    "playas del coco":       66,
    "coco":                  66,
    "el coco":               66,
    "playa conchal":         67,
    "conchal":               67,
    "brasilito":             69,
    "playa brasilito":       69,
    "playa avellanas":       50,    # Santa Cruz canton
    "avellanas":             50,
    "playa negra guanacaste": 50,
    "playa grande":          71,
    "playa junquillal":      50,
    "junquillal":            50,
    "playa langosta":        50,
    "langosta":              50,
    "peninsula papagayo":    65,
    "papagayo":              65,
    "golfo de papagayo":     65,
    "sardinal":              1577,
    "huacas":                1457,
    "villareal":             50,
    "villa real":            50,
    "27 de abril":           1570,
    "veintisiete de abril":  1570,
    "tempate":               1571,
    "peninsula de nicoya":   49,
    "nicoya peninsula":      49,
    # ── Puntarenas provincia (ID: 73) ─────────────────────────────────
    "puntarenas":            74,    # Puntarenas canton
    "esparza":               75,
    "buenos aires":          76,
    "montes de oro":         1481,
    "miramar":               77,
    "osa":                   91,
    "quepos":                79,
    "manuel antonio":        79,
    "golfito":               85,
    "coto brus":             1483,
    "parrita":               87,
    "corredores":            1484,
    "garabito":              1480,
    "jaco":                  89,
    "jacó":                  89,
    "playa jaco":            89,
    "herradura":             2081,
    "dominical":             92,
    "uvita":                 101,
    "ojochal":               91,
    "bahia ballena":         98,
    "bahía ballena":         98,
    "playa hermosa puntarenas": 1480,
    "playa bejuco":          5612,
    "playa esterillos":      90,
    "esterillos":            90,
    "santa teresa":          2075,  # Cóbano district, Puntarenas canton
    "mal pais":              2075,
    "mal país":              2075,
    "montezuma":             2075,
    "cobano":                2075,
    "cóbano":                2075,
    "tambor puntarenas":     2072,
    "paquera":               2072,
    "lepanto":               2069,
    "monte verde":           2074,
    "monteverde":            2074,
    # ── Limón provincia (ID: 104) ─────────────────────────────────────
    "limon":                 105,   # Limón canton
    "limón":                 105,
    "pococi":                1458,
    "guápiles":              106,
    "guapiles":              106,
    "siquirres":             107,
    "talamanca":             1461,
    "matina":                109,
    "guácimo":               110,
    "guacimo":               110,
    "puerto viejo":          111,
    "puerto viejo de talamanca": 111,
    "cahuita":               112,
    "manzanillo":            113,
    "punta cocles":          114,
    "playa negra limon":     111,
    "playa negra":           111,
    # ── Cartago provincia (ID: 25) ────────────────────────────────────
    "cartago":               26,    # Cartago canton
    "paraíso":               27,
    "paraiso":               27,
    "la unión":              28,
    "la union":              28,
    "tres ríos":             5172,
    "tres rios":             5172,
    "jiménez":               5200,
    "jimenez":               5200,
    "turrialba":             30,
    "alvarado":              5204,
    "oreamuno":              5207,
    "el guarco":             5209,
    "valle de orosi":        34,
    "orosi":                 34,
    # ── Province-level fallbacks ──────────────────────────────────────
    "san jose provincia":    116,
    "alajuela provincia":    3,
    "heredia provincia":     35,
    "guanacaste provincia":  47,
    "puntarenas provincia":  73,
    "limon provincia":       104,
    "limón provincia":       104,
    "cartago provincia":     25,
    # Default fallback
    "costa rica":            116,   # San José provincia
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

Su objetivo es crear un título que un comprador real escribiría en el buscador de Encuentra24 — palabras clave concretas que generan clics.

Cree UN título optimizado en español siguiendo esta estructura:
[Tipo] [X] habs en [Ubicación] - [Comunidad o Rasgo clave] - [Gancho de búsqueda]

REGLAS:
- MÁXIMO 70 caracteres (límite estricto, cuente con cuidado)
- [Tipo]: Casa, Villa, Apartamento, Lote, Finca, etc. — use el término que los compradores buscan
- [X] habs: número de habitaciones (omita para lotes/fincas)
- [Ubicación]: ciudad o distrito más buscado (ej: Escazú, Santa Teresa, Tamarindo, Uvita)
- [Comunidad o Rasgo clave]: nombre del condominio si es conocido, o el rasgo más buscado (ej: frente al mar, zona residencial, acceso pavimentado)
- [Gancho de búsqueda]: beneficio concreto que un comprador buscaría (ej: piscina, vista al mar, renta vacacional, playa a 5 min, casa de huéspedes, inversión, llave en mano)
- Priorice términos de búsqueda reales sobre lenguaje de marketing
- Use acentos correctos (á, é, í, ó, ú, ñ)
- Nunca use signos de exclamación
- Nunca use guiones dentro de palabras
- Si la comunidad no es conocida, omítala y use el espacio para más palabras clave

Devuelva SOLO el título, nada más. Sin comillas, sin explicación."""

    SYSTEM_EN = """You are a title optimizer for a luxury real estate classified portal in Costa Rica (Encuentra24).

Your goal is to write a title that a real buyer would TYPE into the Encuentra24 search bar — concrete keywords that generate clicks.

Create ONE optimized English title following this structure:
[Type] [X]BR in [Location] - [Key Feature] - [Search Hook]

RULES:
- MAXIMUM 70 characters (strict limit, count carefully)
- [Type]: Home, Villa, Condo, Lot, Farm, etc. — use the term buyers actually search
- [X]BR: bedroom count using BR abbreviation (omit for lots/farms)
- [Location]: most-searched city or district (e.g., Escazu, Santa Teresa, Tamarindo, Uvita, Manuel Antonio)
- [Key Feature]: well-known community name OR most-searched attribute (e.g., beachfront, gated community, mountain view, ocean view)
- [Search Hook]: concrete benefit a buyer would search for (e.g., Pool, Ocean View, Rental Income, Walk to Beach, Guest House, Turnkey, Investment)
- Prioritize real search terms over marketing language
- Never use exclamation marks
- Never use hyphens within words
- If community is not well known, drop it and use the space for more keywords

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

    # ── SEO Short Description (150-200 chars, keyword-dense, buyer search terms) ──
    SEO_EN = """You are an SEO copywriter for a Costa Rica real estate classified portal.

Write a SHORT search-optimized property summary in English (150-200 characters).

RULES:
- Pack in the most-searched buyer keywords: property type, bedrooms, location, price range, key features
- Write as a natural sentence buyers would search for, not marketing copy
- Include: property type + bedrooms + city/area + 2-3 key features (pool, ocean view, gated, beachfront, etc.)
- End with: Costa Rica
- No exclamation marks. No hype.
- Output ONLY the summary text, nothing else."""

    SEO_ES = """Usted es un redactor SEO para un portal de clasificados de bienes raíces en Costa Rica.

Escriba un RESUMEN CORTO optimizado para búsqueda en español (150-200 caracteres).

REGLAS:
- Incluya las palabras clave más buscadas: tipo de propiedad, habitaciones, ubicación, características clave
- Escriba como una oración natural que un comprador buscaría, no lenguaje de marketing
- Incluya: tipo + habitaciones + ciudad/zona + 2-3 características (piscina, vista al mar, condominio, frente al mar, etc.)
- Termine con: Costa Rica
- Sin signos de exclamación. Sin exageraciones.
- Devuelva SOLO el resumen, nada más."""

    seo_en = ""
    seo_es = ""
    try:
        seo_en_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SEO_EN},
                {"role": "user", "content": f"Write a short SEO summary.\n\n{context}"}
            ],
            temperature=0.3,
            max_tokens=80,
        )
        seo_en = seo_en_resp.choices[0].message.content.strip().strip('"')
        if len(seo_en) > 200:
            seo_en = seo_en[:200].rsplit(' ', 1)[0]
    except Exception as e:
        print(f"    WARNING: EN SEO summary LLM failed for {mls}: {e}", file=sys.stderr)

    time.sleep(0.3)
    try:
        seo_es_resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SEO_ES},
                {"role": "user", "content": f"Escriba un resumen SEO corto.\n\n{context}"}
            ],
            temperature=0.3,
            max_tokens=80,
        )
        seo_es = seo_es_resp.choices[0].message.content.strip().strip('"')
        if len(seo_es) > 200:
            seo_es = seo_es[:200].rsplit(' ', 1)[0]
    except Exception as e:
        print(f"    WARNING: ES SEO summary LLM failed for {mls}: {e}", file=sys.stderr)

    time.sleep(0.3)
    return descr_en, descr_es, seo_en, seo_es


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
        listing_id = listing.get("id")  # Use listing ID (not property ID) for the detail endpoint

        # Build a fingerprint from fields that should trigger re-enrichment when changed.
        # Includes: listing name (catches renames) and lastmodifieddate (catches content edits).
        listing_name = (listing.get("name") or "").strip()
        last_modified = (prop.get("lastmodifieddate") or "").strip()
        current_fingerprint = f"{listing_name}|{last_modified}"

        # Skip if already cached AND fingerprint matches (nothing changed)
        cached_entry = cache.get(mls)
        if cached_entry and cached_entry.get("_fingerprint") == current_fingerprint:
            continue
        if cached_entry:
            print(f"  Cache invalidated for {mls} (name or date changed) — re-enriching ...", end=" ", flush=True)

        print(f"  Enriching [{i+1}/{total}] {mls} ...", end=" ", flush=True)

        # Step 1: Fetch detail
        en_description = ""
        es_description = ""
        highlights = []

        if listing_id:
            detail_prop, detail_listing = fetch_listing_detail(listing_id)
            if detail_prop and detail_listing:
                en_description, es_description, highlights = extract_detail_fields(detail_prop, detail_listing)

        # Step 2, 3 & 4: LLM enrichment
        es_title_opt = ""
        en_title_opt = ""
        descr_en = ""
        descr_es = ""
        seo_en = ""
        seo_es = ""

        if use_llm and client:
            es_title_opt, en_title_opt = generate_llm_title(
                client, prop, listing, en_description, es_description, highlights, mls
            )
            descr_en, descr_es, seo_en, seo_es = generate_llm_descriptions(
                client, prop, listing, en_description, es_description, highlights, mls
            )

        cache[mls] = {
            "_fingerprint": current_fingerprint,
            "en_description_full": en_description,
            "es_description_full": es_description,
            "highlights": highlights,
            "es_title_optimized": es_title_opt,
            "en_title_optimized": en_title_opt,
            "descr_en": descr_en,
            "descr_es": descr_es,
            "seo_en": seo_en,
            "seo_es": seo_es,
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


# Region IDs that belong exclusively to Heredia province — never assign these
# to a listing whose state is San Jose, Alajuela, Guanacaste, etc.
_HEREDIA_REGION_IDS = {
    35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 68,
    1459, 1460, 1543, 24,
}

# Province name → set of region IDs that are valid for that province
# Used to filter out cross-province mismatches
_PROVINCE_WHITELIST = {
    "san jose":   None,   # No restriction — San José has many districts
    "alajuela":   None,
    "cartago":    None,
    "guanacaste": None,
    "puntarenas": None,
    "limon":      None,
    "limón":      None,
    "heredia":    _HEREDIA_REGION_IDS,  # Only allow Heredia IDs when province=Heredia
}


def resolve_region_id(prop, listing):
    """
    Resolve the Encuentra24 region ID from property location fields.

    Strategy:
    1. Build a province context from prop['state'] to avoid cross-province mismatches.
    2. Try candidates in specificity order: address → community → city → state.
    3. For each candidate, first try exact match, then partial match.
    4. If a candidate resolves to a Heredia region ID but the province is NOT Heredia,
       skip that match and continue to the next candidate.
    5. Special case: if address/community contains 'san rafael' and city/state
       indicates Escazú or San Jose province, return Escazú canton ID (117) instead
       of San Rafael de Heredia (40).
    """
    state_raw  = (prop.get("state") or "").strip().lower()
    city_raw   = (prop.get("city") or "").strip().lower()
    is_heredia = (state_raw == "heredia")

    # Detect Escazú context: city says Escazú, or state is San Jose with address San Rafael
    is_escazu_context = (
        "escaz" in city_raw or
        (state_raw in ("san jose", "san josé") and "escaz" not in city_raw and city_raw == "")
    )

    candidates = [
        prop.get("address") or "",
        listing.get("community") or "",
        prop.get("city") or "",
        prop.get("state") or "",
    ]

    def is_valid(region_id):
        """Return True if this region_id is compatible with the listing's province."""
        if region_id in _HEREDIA_REGION_IDS and not is_heredia:
            return False
        return True

    for candidate in candidates:
        key = candidate.strip().lower()
        if not key:
            continue

        # Special case: 'san rafael' disambiguation by province
        if "san rafael" in key and not is_heredia:
            if state_raw in ("alajuela",):
                return 2113  # San Rafael de Alajuela
            else:
                return 2138  # San Rafael de Escazú (San Jose province default)

        # Exact match
        if key in REGION_MAP:
            rid = REGION_MAP[key]
            if is_valid(rid):
                return rid

        # Partial match
        for region_key, rid in REGION_MAP.items():
            if region_key in key and len(region_key) > 4:
                if is_valid(rid):
                    return rid

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
    """Convert fullbathrooms + halfbathrooms to Encuentra24 bath value.
    Encuentra24 requires bath as a whole integer. The LX API can return
    fractional values (e.g. 3.25 from quarter-bath counting), which are
    rejected by Encuentra24 with a field validation error. We round to
    the nearest integer to avoid this.
    """
    full = full or 0
    half = half or 0
    total = full + (0.5 * half)
    if total <= 0:
        return "0"
    if total > 20:
        return "20+"
    return str(int(round(total)))


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


# ───────────────────────────────────────────────────────────────────
# PHOTO ROTATION
# ───────────────────────────────────────────────────────────────────

def load_rotation_state():
    """Load the photo rotation state from disk. Returns a dict keyed by MLS ID."""
    if os.path.exists(PHOTO_ROTATION_STATE_FILE):
        try:
            with open(PHOTO_ROTATION_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_rotation_state(state):
    """Persist the photo rotation state to disk."""
    with open(PHOTO_ROTATION_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def should_rotate_photos(mls, last_modified_str, rotation_state, today):
    """
    Return True if photos 1 and 2 should be swapped for this listing.

    Rules:
    - Only runs on Fridays (weekday=4) and Sundays (weekday=6)
    - Listing must not have changed in the last 5 days (based on lastmodifieddate)
    - Must not have already been swapped within the last 5 days (tracked in rotation_state)
    """
    # Only act on Fridays and Sundays
    if today.weekday() not in (4, 6):  # 4=Friday, 6=Sunday
        return False

    cutoff = today - timedelta(days=5)

    # Check if the listing itself was modified recently
    if last_modified_str:
        try:
            # Handle ISO format with timezone offset: 2026-06-30T01:44:05.000+0000
            mod_str = last_modified_str.replace("+0000", "+00:00").replace("Z", "+00:00")
            # Strip microseconds if present beyond 6 digits
            import re as _re
            mod_str = _re.sub(r'(\.\d{6})\d+', r'\1', mod_str)
            mod_date = datetime.fromisoformat(mod_str).date()
            if mod_date >= cutoff:
                return False  # Modified recently — skip
        except Exception:
            pass  # If we can’t parse, don’t block rotation

    # Check if we already swapped this listing within the last 5 days
    last_swap_str = rotation_state.get(mls)
    if last_swap_str:
        try:
            last_swap = date.fromisoformat(last_swap_str)
            if last_swap >= cutoff:
                return False  # Swapped recently — skip
        except Exception:
            pass

    return True


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

def generate_item_xml(prop, listing, ad_type, enrichment=None, rotation_state=None, today=None):
    """Generate the <item> XML block for a single listing."""
    mls = listing.get("lx_mls_id") or listing.get("id") or prop.get("id")
    enrich = enrichment or {}

    region_id   = resolve_region_id(prop, listing)
    category_id = resolve_category_id(listing, ad_type)

    # Titles: use LLM-optimized if available, else fall back to API values
    title_es = enrich.get("es_title_optimized") or get_spanish_title(prop, listing)
    title_en = enrich.get("en_title_optimized") or get_english_title(listing)

    # Descriptions: prepend SEO short summary to the two-paragraph narrative
    seo_es = enrich.get("seo_es") or ""
    seo_en = enrich.get("seo_en") or ""
    body_es = enrich.get("descr_es") or _fallback_description_es(prop, listing, ad_type)
    body_en = enrich.get("descr_en") or _fallback_description_en(prop, listing, ad_type)
    descr_es = (seo_es + "\n\n" + body_es).strip() if seo_es else body_es
    descr_en = (seo_en + "\n\n" + body_en).strip() if seo_en else body_en

    # GPS coordinates for location pin
    lat = prop.get("latitude") or prop.get("lat") or ""
    lon = prop.get("longitude") or prop.get("lng") or prop.get("lon") or ""

    price  = listing.get("listingprice")
    images = get_image_urls(prop)

    # Apply Friday/Sunday photo rotation if eligible
    if rotation_state is not None and today is not None and len(images) >= 2:
        last_modified_str = prop.get("lastmodifieddate") or ""
        if should_rotate_photos(str(mls), last_modified_str, rotation_state, today):
            images = [images[1], images[0]] + images[2:]  # swap photo 1 and 2
            rotation_state[str(mls)] = today.isoformat()  # record swap date

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

    # GPS coordinates — location pin on Encuentra24 map
    if lat and lon:
        lines.append(f"          <location-lat>{cdata(str(lat))}</location-lat>")
        lines.append(f"          <location-long>{cdata(str(lon))}</location-long>")
        lines.append(f"          <location-zoom>{cdata('15')}</location-zoom>")

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

    Prioritization (5-tier system):

      TIER A — Exclusive residential sales (no lots) ≤ EXCLUSIVE_PRICE_CAP ($1,250,000).
               Exclusive flag overrides EPP priority.
               Sorted by price ascending.

      TIER B — Rentals ≤ RENTAL_PRICE_CAP/month ($4,950). No EPP.
               Sorted by price ascending. Fills slots after Tier A.

      TIER C — ALL exclusive lots, farms & land (any price).
               Exclusive flag overrides price cap.
               Sorted by price ascending. Fills slots after Tiers A+B.

      TIER D — Non-exclusive residential sales (no lots) ≤ SALE_PRICE_CAP ($980,000). No EPP.
               Sorted by price ascending (cheapest first).
               Fills slots after Tiers A+B+C.

      TIER E — Non-exclusive lots, farms & land. No EPP. No price cap.
               Sorted by price ascending (cheapest first).
               Fills remaining slots after Tiers A+B+C+D.
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

    # ── Step 2: Build Tier A — Exclusive residential sales (no lots) ≤ EXCLUSIVE_PRICE_CAP ──
    tier_a = [
        (prop, listing, ad_type)
        for prop, listing, ad_type, price, is_exclusive, is_epp, priority in all_items
        if ad_type in ("property", "lot")
        and is_exclusive
        and not is_lot_listing(prop, listing)
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
    tier_b_mls = {listing.get("lx_mls_id") for _, listing, _ in tier_b}

    # ── Step 4: Build Tier C — ALL exclusive lots, farms & land (any price) ──
    remaining_after_ab = (max_listings or 9999) - len(tier_a) - len(tier_b)
    tier_c_pool = [
        (prop, listing, ad_type)
        for prop, listing, ad_type, price, is_exclusive, is_epp, priority in all_items
        if ad_type in ("property", "lot")
        and is_exclusive
        and is_lot_listing(prop, listing)
        and listing.get("lx_mls_id") not in tier_a_mls
    ]
    tier_c_pool.sort(key=lambda x: x[1].get("listingprice") or float("inf"))
    tier_c = tier_c_pool[:remaining_after_ab]
    tier_c_mls = {listing.get("lx_mls_id") for _, listing, _ in tier_c}

    # ── Step 5: Build Tier D — Non-exclusive residential sales ≤ SALE_PRICE_CAP, no EPP ──
    remaining_after_abc = (max_listings or 9999) - len(tier_a) - len(tier_b) - len(tier_c)
    all_exclusive_mls = tier_a_mls | tier_c_mls
    tier_d_pool = [
        (prop, listing, ad_type)
        for prop, listing, ad_type, price, is_exclusive, is_epp, priority in all_items
        if ad_type in ("property", "lot")
        and not is_exclusive
        and not is_epp
        and not is_lot_listing(prop, listing)
        and price <= SALE_PRICE_CAP
        and listing.get("lx_mls_id") not in all_exclusive_mls
    ]
    tier_d_pool.sort(key=lambda x: x[1].get("listingprice") or float("inf"))
    tier_d = tier_d_pool[:remaining_after_abc]
    tier_d_mls = {listing.get("lx_mls_id") for _, listing, _ in tier_d}

    # ── Step 6: Build Tier E — Non-exclusive lots/farms/land, cheapest first, no EPP ──
    remaining_after_abcd = (max_listings or 9999) - len(tier_a) - len(tier_b) - len(tier_c) - len(tier_d)
    used_mls = all_exclusive_mls | tier_d_mls | tier_b_mls
    tier_e_pool = [
        (prop, listing, ad_type)
        for prop, listing, ad_type, price, is_exclusive, is_epp, priority in all_items
        if ad_type in ("property", "lot")
        and not is_exclusive
        and not is_epp
        and is_lot_listing(prop, listing)
        and listing.get("lx_mls_id") not in used_mls
    ]
    tier_e_pool.sort(key=lambda x: x[1].get("listingprice") or float("inf"))
    tier_e = tier_e_pool[:remaining_after_abcd]

    final = tier_a + tier_b + tier_c + tier_d + tier_e

    n_tier_a = len(tier_a)
    n_tier_b = len(tier_b)
    n_tier_c = len(tier_c)
    n_tier_d = len(tier_d)
    n_tier_e = len(tier_e)
    total_eligible = len(all_items)

    print(f"  Total active published listings: {total_eligible}")
    print(f"  Tier A — Exclusive residential ≤ ${EXCLUSIVE_PRICE_CAP:,.0f}: {n_tier_a}")
    print(f"  Tier B — Rentals ≤ ${RENTAL_PRICE_CAP:,.0f}/mo (no EPP): {n_tier_b} of {len(tier_b_pool)}")
    print(f"  Tier C — Exclusive lots/farms/land (all prices): {n_tier_c} of {len(tier_c_pool)}")
    print(f"  Tier D — Non-excl residential ≤ ${SALE_PRICE_CAP:,.0f} (no EPP): {n_tier_d} of {len(tier_d_pool)}")
    print(f"  Tier E — Non-excl lots/farms/land, cheapest first (no EPP): {n_tier_e} of {len(tier_e_pool)}")
    print(f"  TOTAL: {len(final)}")
    if tier_d:
        cutoff = tier_d[-1][1].get('listingprice', 0)
        print(f"  Tier D price ceiling: ${cutoff:,.0f}")
    if tier_e:
        cutoff_e = tier_e[-1][1].get('listingprice', 0)
        print(f"  Tier E price ceiling: ${cutoff_e:,.0f}")
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

    # Load photo rotation state and determine today's date
    rotation_state = load_rotation_state()
    today = datetime.utcnow().date()
    rotated_count = 0

    count = 0
    for prop, listing, ad_type in final:
        mls = listing.get("lx_mls_id") or listing.get("id") or prop.get("id")
        enrich = enrichment_cache.get(str(mls)) or {}
        prev_rotation_size = len(rotation_state)
        try:
            item_xml = generate_item_xml(
                prop, listing, ad_type,
                enrichment=enrich,
                rotation_state=rotation_state,
                today=today
            )
            lines.append(item_xml)
            count += 1
            if len(rotation_state) > prev_rotation_size:
                rotated_count += 1
        except Exception as e:
            print(f"  WARNING: Skipped {mls} — {e}", file=sys.stderr)
            skipped += 1

    # Save updated rotation state
    save_rotation_state(rotation_state)
    if rotated_count:
        print(f"  Photo rotation: swapped photos 1↔2 for {rotated_count} listings (Friday/Sunday rule).")

    lines.append("  </items>")
    lines.append("")
    lines.append("</import>")

    return "\n".join(lines), count, skipped, final


# ─────────────────────────────────────────────────────────────────────
# ZAPIER WEBHOOK — NEW LISTING NOTIFICATION
# ─────────────────────────────────────────────────────────────────────

def get_published_ids(xml_path):
    """
    Parse the existing XML feed file and return the set of sourceid values
    (LX MLS IDs) currently published. Used to detect newly added listings.
    Returns an empty set if the file does not exist or cannot be parsed.
    """
    if not os.path.exists(xml_path):
        return set()
    try:
        import re
        with open(xml_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Extract all sourceid values from CDATA blocks
        ids = re.findall(r'<sourceid><!\[CDATA\[(.*?)\]\]></sourceid>', content)
        return set(ids)
    except Exception as e:
        print(f"  WARNING: Could not parse existing feed for new-listing detection: {e}", file=sys.stderr)
        return set()


def notify_zapier_new_listings(new_listings, webhook_url):
    """
    Fire a POST to the Zapier webhook for each newly added listing.
    Payload includes all available geographic fields from the LX API.
    Failures are logged as warnings but do not abort the feed generation.
    """
    if not webhook_url:
        return
    today = datetime.utcnow().strftime("%Y-%m-%d")
    success = 0
    for item in new_listings:
        prop, listing, ad_type = item
        mls = listing.get("lx_mls_id") or listing.get("id") or prop.get("id") or ""
        name = listing.get("name") or prop.get("address") or ""
        price = listing.get("listingprice") or 0
        prop_type = listing.get("propertytype") or ""
        prop_subtype = listing.get("property_subtype") or ""
        permalink = listing.get("permalink") or ""
        listing_url = f"https://theagency.cr/listings/{permalink}" if permalink else ""
        # Geographic fields
        address     = prop.get("address") or ""
        community   = listing.get("community") or prop.get("address") or ""
        city        = prop.get("city") or ""
        state       = prop.get("state") or ""          # e.g. "San Jose"
        country     = prop.get("country") or "Costa Rica"
        region      = listing.get("region") or prop.get("region") or ""  # e.g. "CENTRAL VALLEY EAST"
        region_desc = listing.get("region_description") or prop.get("region_description") or ""  # e.g. "Curridabat, Tres Rios"
        latitude    = prop.get("latitude") or prop.get("lat") or ""
        longitude   = prop.get("longitude") or prop.get("lng") or ""
        payload = json.dumps({
            "date": today,
            "listing_id": mls,
            "name": name,
            "price_usd": price,
            "listing_type": ad_type,
            "property_type": prop_type,
            "property_subtype": prop_subtype,
            "address": address,
            "community": community,
            "city": city,
            "state": state,
            "country": country,
            "region": region,
            "region_description": region_desc,
            "latitude": latitude,
            "longitude": longitude,
            "url": listing_url,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
            print(f"  Zapier notified: {mls} — HTTP {status}")
            success += 1
        except Exception as e:
            print(f"  WARNING: Zapier notification failed for {mls}: {e}", file=sys.stderr)
    if new_listings:
        print(f"  Zapier: {success}/{len(new_listings)} notifications sent.")


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

    # Capture the set of MLS IDs already in the feed BEFORE regenerating
    # so we can detect which listings are newly added in this run.
    previously_published = get_published_ids(args.output)
    if previously_published:
        print(f"  Previously published listings: {len(previously_published)}")

    print(f"\nGenerating feed (type={args.type}, limit={limit or 'unlimited'}, llm={'on' if use_llm else 'off'}) ...")
    xml_content, count, skipped, final_listings = generate_feed(
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

    # Detect new listings and notify Zapier
    if ZAPIER_WEBHOOK_URL and previously_published:
        new_listings = [
            (prop, listing, ad_type)
            for prop, listing, ad_type in final_listings
            if (listing.get("lx_mls_id") or listing.get("id") or prop.get("id")) not in previously_published
        ]
        if new_listings:
            print(f"\nNew listings detected: {len(new_listings)} — notifying Zapier ...")
            notify_zapier_new_listings(new_listings, ZAPIER_WEBHOOK_URL)
        else:
            print("\nNo new listings detected — Zapier not triggered.")


if __name__ == "__main__":
    main()
