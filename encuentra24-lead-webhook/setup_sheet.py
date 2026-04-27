"""
setup_sheet.py — One-time Google Sheets initialisation
The Agency Costa Rica / Encuentra24 Lead Integration

Run once after creating your Google Sheet to add the header row.
Usage:
    SHEET_ID=<your_sheet_id> python3 setup_sheet.py
"""

import os
import json
import subprocess
import sys

SHEET_ID = os.getenv("SHEET_ID", "")

if not SHEET_ID:
    print("ERROR: Set the SHEET_ID environment variable first.")
    sys.exit(1)

HEADERS = [
    "Received At (UTC)",
    "Created At",
    "Lead ID",
    "Ad ID",
    "Source ID",
    "Lead Title",
    "Message",
    "Contact Name",
    "Contact Email",
    "Contact Phone",
    "Ad Title",
    "Ad Category",
    "Ad Price",
    "Currency",
    "Extra Data (JSON)",
    "Raw Payload (JSON)",
]

body = {"values": [HEADERS]}

cmd = [
    "gws", "sheets", "spreadsheets", "values", "update",
    "--params", json.dumps({
        "spreadsheetId": SHEET_ID,
        "range": "Leads!A1",
        "valueInputOption": "RAW",
    }),
    "--json", json.dumps(body),
]

result = subprocess.run(cmd, capture_output=True, text=True)
if result.returncode == 0:
    print(f"✓ Header row written to Sheet {SHEET_ID} → tab 'Leads'")
    print("  Rename the first tab to 'Leads' if it is still called 'Sheet1'.")
else:
    print(f"✗ Failed: {result.stderr}")
    sys.exit(1)
