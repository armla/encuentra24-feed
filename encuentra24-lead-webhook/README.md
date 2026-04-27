# Encuentra24 Lead Webhook Receiver
**The Agency Costa Rica**

A lightweight Python/Flask service that receives real-time lead webhooks from Encuentra24, logs them to a Google Sheet, and dispatches formatted email notifications to the team.

## How It Works

1. Encuentra24 sends a `POST` request to `/webhook/encuentra24` when a lead is generated.
2. The service parses and flattens the nested JSON payload.
3. The structured lead is appended as a new row in the configured Google Sheet.
4. An email alert is sent to all configured team recipients via Google Workspace (Gmail).

## Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Health check — returns `{"status": "ok"}` |
| `POST` | `/webhook/encuentra24` | Lead receiver endpoint |

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```env
WEBHOOK_SECRET=your_strong_random_secret
SHEET_ID=your_google_sheet_id
NOTIFY_EMAILS=broker@theagencycr.com,assistant@theagencycr.com
SENDER_EMAIL=notifications@theagencycr.com
PORT=5050
```

## Setup

### 1. Initialise the Google Sheet header row
```bash
SHEET_ID=<your_sheet_id> python3 setup_sheet.py
```

### 2. Run locally
```bash
pip install -r requirements.txt
python3 app.py
```

### 3. Run with Docker
```bash
docker build -t encuentra24-webhook .
docker run -p 5050:5050 --env-file .env \
  -v ~/.config/gws:/root/.config/gws \
  encuentra24-webhook
```

### 4. Test with a sample payload
```bash
python3 test_webhook.py --url http://localhost:5050 --secret your_strong_random_secret
```

## Lead Payload Fields Captured

| Field | Description |
| :--- | :--- |
| `id` | Unique lead ID from Encuentra24 |
| `adid` | Encuentra24 ad ID |
| `sourceid` | Your internal ad reference |
| `contact.name/email/phone` | Prospect contact details |
| `message` | The inquiry message |
| `addetails.title/category/price/currency` | Property ad details |
| `leadadditionaldata` | Custom fields (budget, financing, etc.) |

## Security

Optionally configure `WEBHOOK_SECRET`. When set, the receiver validates the `X-Webhook-Secret` header on every incoming request and returns `403` on mismatch.
