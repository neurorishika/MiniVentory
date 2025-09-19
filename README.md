# Stockroom Kiosk (Flask + Mongo)

A lightweight, no-login **consumables checkout** app for labs and makerspaces.
- Tablet-friendly checkout UI (users pick their name, item, quantity, note)
- Admin PIN to access management screens
- **Low-stock alerts** (email), **CSV exports** (logs + stock)
- **Usage summary** dashboard + **scheduled summary emails** (daily/weekly)
- **Per-item automatic replenish** (daily/weekly/monthly), robust + idempotent
- Atomic stock updates, audit logs, and minimal dependencies

## Features

- **Checkout**: simple form; reduces stock; writes a log (with before/after)
- **Inventory overview** on checkout page so users see remaining stock
- **Admin**:
  - Items: create, stock adjust, low-stock thresholds, auto-replenish schedule
  - Users: create/enable/disable/delete
  - Logs: filter by item/user, export CSV
  - Summary: top items/users over time window; low-stock list
  - Settings: summary email frequency (never/daily/weekly), UTC hour/weekday, manual send
- **Email**:
  - Low-stock alerts (rate-limited), robust retries, non-blocking
  - Summary email (daily/weekly), triggered by **safe cron ping**
- **Replenish**:
  - Per-item schedule, quantity, hour (UTC),
  - weekly weekday (0–6), monthly DOM (1–28), optional **max stock cap**
  - Logged as `user="SYSTEM", note="auto-replenish"`
- **Security**: Admin PIN session gate, **CRON_TOKEN** for task endpoints, non-root container

---

## Quick Start (Docker Compose)

1. Clone or copy this repo into your server/NAS.
2. Copy the env file:
```bash
   cp .env.example .env
```

Edit **.env**:

* `SECRET_KEY` — generate a strong random
* `ADMIN_PIN` — numeric PIN for admin UI
* `MONGO_URI` — either your lab Mongo or the Compose `mongo` service
* SMTP settings for email (optional but recommended)
* `CRON_TOKEN` — long random string for secure task pings

3. Choose a compose variant:

   * **App + Mongo**: use the provided `docker-compose.yml` (Variant A)
   * **App only**: swap to Variant B and keep your own Mongo
4. Build & run:

   ```bash
   docker compose build
   docker compose up -d
   ```
5. Visit the app at `http://<host>:8000/`
6. Click **Admin** → enter your `ADMIN_PIN`.

> Development mode: you can still run `python app.py` locally if you want.

---

## Synology NAS Installation (DSM 7+)

You can use **Container Manager** (Docker GUI) or **docker compose** via SSH.

### Option 1: Container Manager (GUI)

1. Open **Container Manager** → **Projects** → **Create** → **Import compose**.
2. Paste the provided `docker-compose.yml` (pick Variant A or B) and save as a project.
3. Add a **.env** file to the project (Container Manager supports env files) or set env vars via the GUI.
4. Create a **volume** (if using Mongo service): map `mongo_data` to a persistent shared folder.
5. Deploy the project.

### Option 2: SSH + Compose

1. SSH into your NAS.
2. `cd` into the project folder with `Dockerfile`, `docker-compose.yml`, `.env`.
3. Run:

   ```bash
   docker compose build
   docker compose up -d
   ```

### Reverse Proxy + TLS (recommended)

Use **Control Panel → Login Portal → Reverse Proxy**:

* **Create**:

  * Source: `https://stockroom.yourlab.local` → Port 443
  * Destination: `http://127.0.0.1:8000` (or the container IP\:port)
* **Certificates**: Control Panel → Security → Certificate → add/assign cert to the RP host.
* Lock **external** access to trusted subnets only (Firewall → Allowlist).

---

## Scheduling (Synology Task Scheduler)

This app avoids fragile in-process schedulers. Instead, you **ping** endpoints on a schedule, secured with `CRON_TOKEN`.

Open **Control Panel → Task Scheduler** and add two **User-defined script** tasks:

1. **Hourly Summary Check**

* **Schedule**: Every hour, on the hour.
* **Run command**:

  ```bash
  curl -fsS "http://127.0.0.1:8000/tasks/summary?token=YOUR_CRON_TOKEN" >/dev/null 2>&1
  ```

  > It only sends an email when the current UTC hour matches your admin setting and it hasn’t been sent for the period (day/week).

2. **Hourly Auto-Replenish**

* **Schedule**: Every hour (e.g., minute 5).
* **Run command**:

  ```bash
  curl -fsS "http://127.0.0.1:8000/tasks/replenish?token=YOUR_CRON_TOKEN" >/dev/null 2>&1
  ```

  > It replenishes **only** items that are due per their schedule; updates are atomic and idempotent.

> If your app is behind Synology Reverse Proxy, replace `127.0.0.1:8000` with your internal RP hostname.

---

## Environment Variables

| Key                      | Purpose                                                               |
| ------------------------ | --------------------------------------------------------------------- |
| `SECRET_KEY`             | Flask sessions & CSRF; set a strong random value.                     |
| `ADMIN_PIN`              | Simple admin gate (no per-user login).                                |
| `MONGO_URI`, `MONGO_DB`  | Mongo connection and DB name.                                         |
| `SMTP_*`, `ADMIN_EMAIL`  | Email transport and recipient for alerts/summaries.                   |
| `CRON_TOKEN`             | Shared secret to authorize `/tasks/*` endpoints.                      |
| `SUMMARY_DEFAULT_*`      | Initial defaults for summary email settings (admin can change in UI). |
| `APP_PORT`, `GUNICORN_*` | Gunicorn bind/worker/thread/timeouts inside the container.            |

---

## Backups & Data Persistence

* **Mongo data** (if using compose Mongo) is persisted in the `mongo_data` Docker volume. Map it to a Synology shared folder for easy snapshot/backup.
* App state is in Mongo collections: `items`, `users`, `logs`, `settings`, `alerts`.
* To back up: dump Mongo:

  ```bash
  docker exec -it <mongo_container> mongodump --out /data/backup/$(date +%F)
  docker cp <mongo_container>:/data/backup ./mongo-backups
  ```

---

## Security Checklist

* Run on **LAN only**; gate external access.
* Use **Reverse Proxy** with TLS certificates if exposing over HTTPS.
* Restrict **Firewall** to trusted subnets.
* Set strong `SECRET_KEY`, `ADMIN_PIN`, and `CRON_TOKEN`.
* Keep **Mongo** non-public (bind to LAN; auth if exposed across VLANs).
* Regularly update container images (Mongo, base Python).

---

## Usage Notes

* **Low-stock emails**: sent when post-checkout stock <= threshold. Rate-limited 1/hr per item; retries on failure; never blocks checkout.
* **Summary emails**: admin-configurable (Never/Daily/Weekly) + UTC hour/weekday. Triggered by hourly cron ping.
* **Auto-replenish**: per-item; daily/weekly/monthly; UTC hour; optional weekday/DOM and max cap. Triggered by hourly cron ping; idempotent & atomic.
* **CSV exports**:

  * Logs: `/admin/logs/export`
  * Stock snapshot: `/admin/stock/export`.

---

## Health & Troubleshooting

* Health endpoint: `GET /health` returns `{ ok: true }`
* Logs:

  ```bash
  docker compose logs -f app
  docker compose logs -f mongo
  ```
* Common issues:

  * **Email not sending** → verify SMTP host, port, TLS (`SMTP_USE_SSL`), creds, firewall rules.
  * **Admin PIN not working** → ensure session cookies allowed; check `SECRET_KEY`.
  * **Cron pings not doing anything** → check Synology Task Scheduler logs; ensure `CRON_TOKEN` matches and you’re calling correct URL.
  * **Mongo connectivity** → ensure `MONGO_URI` points to reachable host; if using compose `mongo`, set `MONGO_URI=mongodb://mongo:27017`.

---

## Development

* Local run:

  ```bash
  python3 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  cp .env.example .env   # adjust for local
  python app.py
  ```
* Production run (compose):

  ```bash
  docker compose up -d --build
  ```

---

## License

This project is provided as-is for internal lab use. You own your data and deployment.