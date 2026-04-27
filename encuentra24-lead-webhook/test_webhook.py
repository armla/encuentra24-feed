"""
test_webhook.py — Send a sample Encuentra24 lead payload to the local webhook
Usage:
    python3 test_webhook.py [--url http://localhost:5050] [--secret your_secret]
"""

import argparse
import json
import requests
from datetime import datetime

SAMPLE_PAYLOAD = {
    "createdat": datetime.utcnow().isoformat() + "Z",
    "sourceid": "CR-2024-001",
    "adid": "E24-98765",
    "id": "LEAD-TEST-001",
    "title": "Inquiry about beachfront property in Guanacaste",
    "message": "Hello, I am interested in this property. Could you please send me more details and schedule a visit?",
    "contact": {
        "name": "John Doe",
        "email": "john.doe@example.com",
        "phone": "+1 305 555 0199"
    },
    "leadadditionaldata": {
        "budget": "USD 1,500,000",
        "timeline": "3 months",
        "financing": "cash"
    },
    "addetails": {
        "title": "Luxury Beachfront Villa — Playa Flamingo",
        "category": "Real Estate / Residential / For Sale",
        "price": 1450000,
        "currency": "USD"
    }
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:5050/webhook/encuentra24")
    parser.add_argument("--secret", default="")
    args = parser.parse_args()

    headers = {"Content-Type": "application/json"}
    if args.secret:
        headers["X-Webhook-Secret"] = args.secret

    print(f"Sending test payload to {args.url} …")
    resp = requests.post(args.url, json=SAMPLE_PAYLOAD, headers=headers, timeout=15)
    print(f"Status : {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2)}")

if __name__ == "__main__":
    main()
