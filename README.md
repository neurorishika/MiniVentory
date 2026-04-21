<div align="center">

<img src="https://github.com/user-attachments/assets/efe189d7-8b67-4b2b-b00a-f399f3ed88e6" alt="MiniVentory Logo" width="480"/>

### *Lab Inventory Simplified*

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7.x-47A248?style=for-the-badge&logo=mongodb&logoColor=white)](https://www.mongodb.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License](https://img.shields.io/badge/License-Internal%20Use-lightgrey?style=for-the-badge)](LICENSE)

[🚀 Quick Start](#-quick-start) · [✨ Features](#-features) · [⚙️ Configuration](#️-environment-variables) · [🐛 Report a Bug](https://github.com/neurorishika/MiniVentory/issues) · [💡 Request Feature](https://github.com/neurorishika/MiniVentory/issues)

</div>

---

## 📋 Overview

**MiniVentory** is a lightweight, no-login **consumables checkout kiosk** built for labs and makerspaces. Designed to run on a tablet or wall-mounted screen, it lets anyone in your lab check items in and out in seconds — no account required.

The admin panel (PIN-gated) handles inventory management, low-stock alerts, usage summaries, and automatic replenishment, all backed by MongoDB and delivered via Docker Compose.

<div align="center">
<img src="https://github.com/user-attachments/assets/afbaaf92-f929-44e7-a014-de1a5f22bec9" alt="MiniVentory Screenshot" width="720"/>
<br><sub><i>The MiniVentory checkout kiosk — tablet-friendly dark UI with real-time inventory overview</i></sub>
</div>

---

## ✨ Features

| Feature | Description |
|---|---|
| 📦 **Checkout Kiosk** | Tablet-friendly form — name, item, quantity, note — with live inventory overview |
| 🔒 **Admin Panel** | PIN-gated management for items, users, logs, summaries, and settings |
| 📉 **Low-Stock Alerts** | Automatic email notifications when stock falls below threshold (rate-limited, non-blocking) |
| 📊 **Usage Summaries** | Top items & users over any time window; configurable daily/weekly email digests |
| 🔄 **Auto-Replenish** | Per-item scheduled restocking (daily/weekly/monthly) with optional max-stock cap |
| 📤 **CSV Exports** | One-click export for transaction logs and full stock snapshots |
| 🛡️ **Secure Tasks** | CRON_TOKEN-protected task endpoints; atomic, idempotent stock updates |
| 🐳 **Docker-First** | Single `docker compose up` deployment; works on any Linux server or Synology NAS |

---

## 🚀 Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & Docker Compose
- An SMTP server (optional, for email alerts)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/neurorishika/MiniVentory.git
cd MiniVentory

# 2. Configure environment
cp .env.example .env
# → Edit .env with your SECRET_KEY, ADMIN_PIN, SMTP settings, and CRON_TOKEN

# 3. Build and start
docker compose build
docker compose up -d

# 4. Open the app
open http://localhost:2152/
```

Click **Admin** in the top-right corner and enter your `ADMIN_PIN` to manage inventory.

> **Local development:** `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python app.py`

---

## 🖥️ Synology NAS Installation (DSM 7+)

<details>
<summary><b>Option 1 — Container Manager (GUI)</b></summary>

1. Open **Container Manager** → **Projects** → **Create** → **Import compose**.
2. Paste `docker-compose.yml` (Variant A for bundled Mongo, Variant B for external Mongo).
3. Add your `.env` file or set environment variables via the GUI.
4. Map `mongo_data` to a persistent shared folder for backups.
5. Deploy the project.

</details>

<details>
<summary><b>Option 2 — SSH + Compose</b></summary>

```bash
ssh admin@your-nas
cd /path/to/MiniVentory
docker compose build
docker compose up -d
```

</details>

<details>
<summary><b>Reverse Proxy + TLS (recommended)</b></summary>

Go to **Control Panel → Login Portal → Reverse Proxy**:

- Source: `https://stockroom.yourlab.local` (port 443)
- Destination: `http://127.0.0.1:2152`

Assign a certificate under **Control Panel → Security → Certificate**, and restrict external access via the Firewall allowlist.

</details>

---

## 🗓️ Scheduling (Synology Task Scheduler)

MiniVentory uses simple cron-pinged endpoints instead of fragile in-process schedulers. Add two **User-defined script** tasks in **Control Panel → Task Scheduler**:

**1. Hourly Summary Check** *(every hour, on the hour)*
```bash
curl -fsS "http://127.0.0.1:2152/tasks/summary?token=YOUR_CRON_TOKEN" >/dev/null 2>&1
```
> Sends a summary email only when the UTC hour matches your admin setting, once per period.

**2. Hourly Auto-Replenish** *(every hour, e.g. minute 5)*
```bash
curl -fsS "http://127.0.0.1:2152/tasks/replenish?token=YOUR_CRON_TOKEN" >/dev/null 2>&1
```
> Restocks only items due per their schedule; updates are atomic and idempotent.

> If using Synology Reverse Proxy, replace `127.0.0.1:2152` with your internal RP hostname.

---

## ⚙️ Environment Variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing — use a strong random value |
| `ADMIN_PIN` | Numeric PIN for the admin panel |
| `MONGO_URI` / `MONGO_DB` | MongoDB connection string and database name |
| `SMTP_*` / `ADMIN_EMAIL` | Email transport config and alert recipient |
| `CRON_TOKEN` | Shared secret for `/tasks/*` endpoint authorization |
| `SUMMARY_DEFAULT_*` | Default summary email frequency (admin can override in UI) |
| `APP_PORT` / `GUNICORN_*` | Gunicorn bind port, workers, threads, and timeouts |

---

## 💾 Backups & Data Persistence

MongoDB data is stored in the `mongo_data` Docker volume. Map it to a Synology shared folder for native snapshotting.

```bash
# Dump MongoDB
docker exec -it <mongo_container> mongodump --out /data/backup/$(date +%F)
docker cp <mongo_container>:/data/backup ./mongo-backups
```

Collections: `items` · `users` · `logs` · `settings` · `alerts`

---

## 🛡️ Security Checklist

- [ ] Run on **LAN only** — gate all external access
- [ ] Use **Reverse Proxy + TLS** for HTTPS exposure
- [ ] Restrict via **Firewall** to trusted subnets
- [ ] Set strong `SECRET_KEY`, `ADMIN_PIN`, and `CRON_TOKEN`
- [ ] Keep **MongoDB** non-public; enable auth if exposed across VLANs
- [ ] Regularly pull updated container images

---

## 🔍 Health & Troubleshooting

```bash
# Check app health
curl http://localhost:2152/health   # → { "ok": true }

# Tail logs
docker compose logs -f app
docker compose logs -f mongo
```

| Symptom | Fix |
|---|---|
| Email not sending | Check `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_SSL`, credentials, and firewall |
| Admin PIN rejected | Ensure session cookies are allowed; verify `SECRET_KEY` is set |
| Cron pings silently fail | Check Task Scheduler logs; confirm `CRON_TOKEN` and URL are correct |
| MongoDB unreachable | Set `MONGO_URI=mongodb://mongo:27017` when using the bundled Compose service |

---

## 🧑‍💻 Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # adjust for local
python app.py
```

Production (Docker Compose):

```bash
docker compose up -d --build
```

---

## 📄 License

This project is provided as-is for internal lab use. You own your data and deployment.

---

<div align="center">
<sub>Built with ❤️ for lab life · <a href="https://github.com/neurorishika">neurorishika</a></sub>
</div>
