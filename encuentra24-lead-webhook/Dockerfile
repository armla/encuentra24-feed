# ─────────────────────────────────────────────────────────────────
# Encuentra24 Webhook Receiver — Docker Image
# The Agency Costa Rica
# ─────────────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install gws CLI (Google Workspace CLI)
RUN apt-get update && apt-get install -y curl unzip && \
    curl -sSL https://github.com/googleworkspace/cli/releases/latest/download/gws_linux_amd64.tar.gz \
    | tar -xz -C /usr/local/bin && \
    chmod +x /usr/local/bin/gws && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Mount your gws credentials at /root/.config/gws at runtime
# e.g.: docker run -v ~/.config/gws:/root/.config/gws ...

EXPOSE 5050

CMD ["gunicorn", "--bind", "0.0.0.0:5050", "--workers", "2", "--timeout", "60", "app:app"]
