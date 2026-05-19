<div align="center">

<img src="https://github.com/user-attachments/assets/efe189d7-8b67-4b2b-b00a-f399f3ed88e6" alt="MiniVentory Logo" width="480"/>

### *Lab Inventory Simplified*

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![MongoDB](https://img.shields.io/badge/MongoDB-7.x-47A248?style=for-the-badge&logo=mongodb&logoColor=white)](https://www.mongodb.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![License](https://img.shields.io/badge/License-Internal%20Use-lightgrey?style=for-the-badge)](LICENSE)

[🚀 Quick Start](#-step-4--deploy) · [🏗️ Architecture](#️-architecture) · [⚙️ Configuration](#️-step-1--configure) · [🐛 Report a Bug](https://github.com/neurorishika/MiniVentory/issues) · [💡 Request Feature](https://github.com/neurorishika/MiniVentory/issues)

</div>

---

## 📋 Overview

**MiniVentory** is a lightweight **consumables checkout kiosk** for labs and makerspaces. Users check out items from a tablet-friendly form; admins manage inventory, users, and settings behind a PIN gate. Everything runs in Docker — one `docker compose up` brings up the app, a two-node MongoDB replica set, and an automated backup service.

<div align="center">
<img src="https://github.com/user-attachments/assets/afbaaf92-f929-44e7-a014-de1a5f22bec9" alt="MiniVentory Screenshot" width="720"/>
<br><sub><i>The MiniVentory checkout kiosk — tablet-friendly dark UI with real-time inventory overview</i></sub>
</div>

**What it does**

| Feature | Description |
|---------|-------------|
| 📦 **Checkout Kiosk** | Tablet-friendly form — name, item, quantity, optional note — with live inventory overview |
| 🔒 **Admin Panel** | PIN-gated management for items, users, logs, summaries, and settings |
| 📉 **Low-Stock Alerts** | Automatic email notifications when stock falls below threshold (rate-limited, non-blocking) |
| 📊 **Usage Summaries** | Configurable daily/weekly email digests; top items & users over any time window |
| 🔄 **Auto-Replenish** | Per-item scheduled restocking (daily/weekly/monthly) with optional max-stock cap |
| 📤 **CSV Exports** | One-click export for transaction logs and full stock snapshots |
| � **Internal Scheduler** | APScheduler fires summary and replenish tasks every hour inside the container — no external cron needed |
| 🛡️ **Secure Tasks** | `CRON_TOKEN`-protected HTTP endpoints for manual or external triggers; atomic, idempotent stock updates |
| 🐳 **Docker-First** | Single compose file — app, replica-set Mongo, and automated backups |

**How scheduled tasks work** — the app runs an internal `APScheduler` background scheduler that fires the summary and auto-replenish tasks every hour. A MongoDB TTL collection acts as a distributed mutex so only one Gunicorn worker executes each job tick, preventing duplicate emails or stock changes. The HTTP endpoints `/tasks/summary` and `/tasks/replenish` (secured with `CRON_TOKEN`) remain available as a manual trigger or belt-and-suspenders fallback, but no external cron is required.

---

## 🏗️ Architecture

The production stack (`docker-compose.yml`) contains five services:

| Service | Role |
|---------|------|
| `mongo` | Primary MongoDB node (replica set `rs0`) |
| `mongo2` | Secondary MongoDB node — real-time replica of every write |
| `mongo-init` | One-shot init container — calls `rs.initiate()` then exits |
| `mongo-backup` | Runs `mongodump --oplog` every 6 hours, compresses, rotates, and optionally pushes off-site via rclone |
| `app` | Flask app served by Gunicorn |

Three named Docker volumes survive container removal: `miniventory_mongo_data` (primary), `miniventory_mongo_data2` (secondary), and `miniventory_mongo_backups` (compressed dumps).

---

## ⚙️ Step 1 — Configure

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

**Required:**

| Key | Purpose |
|-----|---------|
| `SECRET_KEY` | Flask session key — generate a long random string |
| `ADMIN_PIN` | Numeric PIN for the admin UI |
| `MONGO_DB` | Database name, e.g. `lab_inventory` |

**Optional but recommended:**

| Key | Purpose |
|-----|---------|
| `CRON_TOKEN` | Shared secret protecting the `/tasks/*` HTTP endpoints — set this if you want to trigger tasks manually or use an external ping as a fallback |
| `SMTP_HOST/PORT/USERNAME/PASSWORD` | Outbound email for alerts and summaries |
| `SMTP_USE_SSL` | `true` or `false` |
| `SMTP_FROM`, `ADMIN_EMAIL` | Sender address and alert recipient |
| `SUMMARY_DEFAULT_FREQUENCY` | `never` / `daily` / `weekly` |
| `RCLONE_REMOTE` | Off-site backup destination, e.g. `remote:bucket/miniventory-backups` |
| `HEALTHCHECK_UUID` | [healthchecks.io](https://healthchecks.io) UUID — get alerted if backups stop |
| `BACKUP_KEEP_DAYS` | Days of local backups to retain (default `14`) |

---

## 🔨 Step 2 — Build and push the image

The compose file references a Docker Hub image. Use `docker buildx` to build a multi-platform image so the same tag works on `amd64` servers (most cloud VMs, Synology x86) and `arm64` servers (AWS Graviton, Synology ARM, Apple Silicon):

```bash
# One-time setup per machine
docker buildx create --name multiarch --use
docker buildx inspect --bootstrap

# Build both platforms and push directly to Docker Hub
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t YOUR_DOCKERHUB_USERNAME/miniventory:latest \
  --push .
```

> `--push` is required for multi-platform builds — the image goes straight to the registry and Docker Hub serves each host the correct architecture automatically.

If you only target one architecture, use `--platform linux/amd64` (most VMs) or `--platform linux/arm64` (Graviton / Synology ARM) and omit the other.

Update the `image:` line in `docker-compose.yml` to match your Docker Hub username.

---

## ☁️ Step 3 — Set up off-site backups (run once on the server)

The `mongo-backup` container dumps MongoDB every 6 hours, compresses the archive, and can push it to any rclone-supported destination. All configuration lives in two env vars: `RCLONE_REMOTE` (where to push) and `HEALTHCHECK_UUID` (dead-man's switch). Both are optional — if omitted, backups still run and rotate locally.

> **This step is required regardless of how you deploy** (Portainer, SSH, AWS, etc.). The backup container always needs `scripts/mongo_backup.sh` and `scripts/rclone.conf` present on the host at the paths bound into the container. For Portainer deployments see the [host prep instructions](#portainer-host-prep) below before running the stack.

### 3a — Install rclone on the host

```bash
curl https://rclone.org/install.sh | sudo bash   # Linux / Synology via SSH
# or: brew install rclone  (macOS)
```

### 3b — Configure a remote destination

Run the interactive helper — it writes `scripts/rclone.conf` (gitignored, never committed):

```bash
bash scripts/setup_rclone.sh
```

Or configure manually using one of the recipes below.

---

<details>
<summary><b>Amazon S3</b></summary>

Create an IAM user with `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, and `s3:ListBucket` on your target bucket. Generate an access key for that user.

```bash
cat > scripts/rclone.conf <<EOF
[remote]
type = s3
provider = AWS
access_key_id = AKIAIOSFODNN7EXAMPLE
secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
region = us-east-1
EOF
```

```bash
# .env
RCLONE_REMOTE=remote:your-bucket-name/miniventory-backups
```

</details>

<details>
<summary><b>Backblaze B2</b></summary>

**Option A — B2 native API** (cheaper egress within Cloudflare network):

Go to Backblaze → **App Keys** → **Add a New Application Key**. Grant read/write access to your bucket.

```bash
cat > scripts/rclone.conf <<EOF
[remote]
type = b2
account = YOUR_ACCOUNT_ID
key = YOUR_APPLICATION_KEY
EOF
```

```bash
# .env
RCLONE_REMOTE=remote:your-bucket-name/miniventory-backups
```

**Option B — B2 S3-compatible API** (works with S3 tooling):

Enable the S3-compatible endpoint in your Backblaze bucket settings. Use the S3 endpoint `s3.us-west-004.backblazeb2.com` (region varies by bucket location).

```bash
cat > scripts/rclone.conf <<EOF
[remote]
type = s3
provider = Other
access_key_id = YOUR_KEY_ID
secret_access_key = YOUR_APPLICATION_KEY
endpoint = s3.us-west-004.backblazeb2.com
EOF
```

```bash
# .env
RCLONE_REMOTE=remote:your-bucket-name/miniventory-backups
```

</details>

<details>
<summary><b>Another Synology NAS (SFTP)</b></summary>

On the **destination** NAS:
1. Control Panel → **File Services → SFTP** → enable SFTP
2. Create a dedicated user (e.g. `backup-writer`) with write access to a shared folder, e.g. `/volume1/backups/miniventory`
3. Note the NAS IP and SFTP port (default 22)

On the **source** machine (where the stack runs):

```bash
# Generate an SSH key pair for passwordless auth
ssh-keygen -t ed25519 -f scripts/backup_id_ed25519 -N ""
# Copy the public key to the destination NAS
ssh-copy-id -i scripts/backup_id_ed25519.pub -p 22 backup-writer@<destination-nas-ip>
```

> Add `scripts/backup_id_ed25519` and `scripts/backup_id_ed25519.pub` to `.gitignore` — they are credentials.

```bash
cat > scripts/rclone.conf <<EOF
[remote]
type = sftp
host = <destination-nas-ip>
user = backup-writer
port = 22
key_file = /config/rclone/backup_id_ed25519
EOF
```

Mount the key into the backup container by adding to `docker-compose.yml` under `mongo-backup → volumes`:

```yaml
- ./scripts/backup_id_ed25519:/config/rclone/backup_id_ed25519:ro
```

```bash
# .env
RCLONE_REMOTE=remote:/volume1/backups/miniventory
```

</details>

<details>
<summary><b>Wasabi / MinIO / other S3-compatible stores</b></summary>

Any S3-compatible store works by setting `provider = Other` and supplying the endpoint URL.

**Wasabi example** (no egress fees):

```bash
cat > scripts/rclone.conf <<EOF
[remote]
type = s3
provider = Wasabi
access_key_id = YOUR_WASABI_KEY
secret_access_key = YOUR_WASABI_SECRET
endpoint = s3.wasabisys.com
EOF
```

**MinIO example** (self-hosted):

```bash
cat > scripts/rclone.conf <<EOF
[remote]
type = s3
provider = Minio
access_key_id = YOUR_MINIO_ACCESS_KEY
secret_access_key = YOUR_MINIO_SECRET_KEY
endpoint = http://<minio-host>:9000
EOF
```

```bash
# .env — bucket must already exist
RCLONE_REMOTE=remote:your-bucket/miniventory-backups
```

</details>

<details>
<summary><b>Google Drive</b></summary>

Run the interactive rclone config — it opens a browser for OAuth:

```bash
rclone config --config scripts/rclone.conf
# Choose: New remote → name it "remote" → type "drive" → follow OAuth prompts
```

```bash
# .env — use the folder path inside your Drive
RCLONE_REMOTE=remote:miniventory-backups
```

</details>

---

**Verify the connection before starting the stack:**

```bash
rclone ls remote: --config scripts/rclone.conf
# Should list bucket/folder contents without error
```

Add the remote path to `.env`:

```bash
echo 'RCLONE_REMOTE=remote:your-bucket/miniventory-backups' >> .env
```

### 3c — Set up healthchecks.io monitoring (optional but recommended)

healthchecks.io sends you an alert if a backup run is ever missed or takes too long. It's free for up to 20 checks.

1. Sign up at [healthchecks.io](https://healthchecks.io) (or self-host)
2. Click **+ New Check** → set the name to `MiniVentory Backups`
3. Set **Period** to `6 hours` and **Grace** to `1 hour`
4. Under **Integrations**, add your email (or Slack/PagerDuty/etc.)
5. Copy the ping URL — it looks like `https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
6. The UUID is the last path segment:

```bash
echo 'HEALTHCHECK_UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' >> .env
```

The backup script pings that URL after every successful run. If no ping arrives within `Period + Grace`, healthchecks.io fires your alert.

> **Self-hosted alternative:** if you run your own [Healthchecks](https://github.com/healthchecks/healthchecks) instance, replace the ping URL base in `scripts/mongo_backup.sh` — change `https://hc-ping.com/` to your instance's URL.

---

## 🚀 Step 4 — Deploy

### Portainer (recommended)

Portainer's **Stacks** feature is the recommended deployment method — it gives you a GUI for deployment, log viewing, container management, and updates without repeated SSH access.

<details>
<summary><b>Install Portainer (skip if already running)</b></summary>

```bash
docker run -d \
  --name portainer --restart always \
  -p 9443:9443 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v portainer_data:/data \
  portainer/portainer-ce:latest
```

Access at `https://<host-ip>:9443`.

</details>

<details>
<summary id="portainer-host-prep"><b>Prepare the host before deploying (SSH — run once)</b></summary>

Portainer's web editor only manages the compose YAML — it does not clone the repo. The backup script and rclone config must be placed on the host manually via SSH before the stack starts.

```bash
ssh admin@<host-ip>

# 1. Create the scripts directory at a stable absolute path
mkdir -p /volume1/docker/miniventory/scripts
cd /volume1/docker/miniventory

# 2. Download the backup script from the repo
curl -fsSL https://raw.githubusercontent.com/neurorishika/MiniVentory/main/scripts/mongo_backup.sh \
  -o scripts/mongo_backup.sh
chmod +x scripts/mongo_backup.sh

# 3. Install rclone (if not already present)
curl https://rclone.org/install.sh | sudo bash

# 4. Download and run the interactive rclone setup helper
curl -fsSL https://raw.githubusercontent.com/neurorishika/MiniVentory/main/scripts/setup_rclone.sh \
  -o scripts/setup_rclone.sh
chmod +x scripts/setup_rclone.sh
bash scripts/setup_rclone.sh   # writes scripts/rclone.conf

# 5. Verify the connection
rclone ls remote: --config scripts/rclone.conf
```

If you chose **SFTP (another NAS)** in the setup helper, also download the generated key:
```bash
# The key was generated at scripts/backup_id_ed25519 — it stays on this host,
# never upload it anywhere. The setup helper already told you to run ssh-copy-id.
```

Once the scripts directory is ready, proceed to deploy the stack below.

</details>

**Create the stack:**

1. Portainer → **Stacks** → **+ Add stack** → name it `miniventory`
2. Select **Web editor** and paste the full contents of `docker-compose.yml`, then make these two edits before deploying:

   **a) Remove the `build:` line** from the `app` service — Portainer's Web editor has no build context, so building will fail. The published image is pulled directly:
   ```yaml
   # remove this line:
   build: .
   # keep:
   image: docker.io/neurorishika/miniventory:latest
   ```

   **b) Replace `env_file: - .env` with `env_file: - stack.env`** in the `app` service — Portainer creates the env file as `stack.env`, not `.env`:
   ```yaml
   # change:
   env_file:
     - .env
   # to:
   env_file:
     - stack.env
   ```

   **c) Replace the two relative bind-mount paths** with the absolute paths you created above:
   ```yaml
   # change:
   - ./scripts/mongo_backup.sh:/usr/local/bin/mongo_backup.sh:ro
   - ./scripts/rclone.conf:/config/rclone/rclone.conf:ro
   # to (adjust base path if you used a different location):
   - /volume1/docker/miniventory/scripts/mongo_backup.sh:/usr/local/bin/mongo_backup.sh:ro
   - /volume1/docker/miniventory/scripts/rclone.conf:/config/rclone/rclone.conf:ro
   ```
   If you configured SFTP backups, also add:
   ```yaml
   - /volume1/docker/miniventory/scripts/backup_id_ed25519:/config/rclone/backup_id_ed25519:ro
   ```
3. Scroll to **Environment variables** and add every key from your `.env` — at minimum `SECRET_KEY`, `ADMIN_PIN`, `MONGO_DB`; add `RCLONE_REMOTE` and `HEALTHCHECK_UUID` if configured in Step 3
4. Click **Deploy the stack**

The `mongo-init` service runs once, initialises the replica set, and exits. Verify: **Containers** → `miniventory_mongo_1` → **Console** → `mongosh --eval "rs.status()"` — you should see one `PRIMARY` and one `SECONDARY`.

> **No external cron needed.** The app schedules summary and replenish tasks internally. The `/tasks/*` endpoints are still available as a manual trigger — if you want an additional external ping via Portainer → **Schedules** (CE 2.19+) you can add one, but it is not required.

**Update:** push a new image, then Portainer → **Stacks** → `miniventory` → **Update the stack**, or use **Recreate** on the `app` container with **Pull latest image** checked.

---

### Synology NAS (SSH + Compose)

<details>
<summary><b>Install via SSH</b></summary>

```bash
ssh admin@<nas-ip>
cd /volume1/docker
git clone https://github.com/neurorishika/MiniVentory.git miniventory
cd miniventory
cp .env.example .env && nano .env
bash scripts/setup_rclone.sh          # off-site backup config (Step 3)
docker compose -p miniventory up -d
```

</details>

<details>
<summary><b>Reverse proxy + TLS</b></summary>

Control Panel → Login Portal → Reverse Proxy:
- Source: `https://inventory.yourlab.local:443`
- Destination: `http://127.0.0.1:2152`

Assign a certificate under Control Panel → Security → Certificate.
Restrict port 2152 to LAN; expose only 443 via the reverse proxy.

</details>

> **No external cron needed.** Scheduled tasks run inside the container. If you previously set up Task Scheduler entries for the `/tasks/*` endpoints you can remove them — or leave them as a fallback (the endpoints are idempotent).

**Update:**
```bash
cd /volume1/docker/miniventory && git pull
docker compose -p miniventory up -d
```

---

### AWS (EC2 + EBS)

<details>
<summary><b>Launch and configure</b></summary>

```bash
# Launch EC2 (t3.small+), attach a gp3 EBS volume (>= 20 GB)
aws ec2 run-instances --image-id ami-0c02fb55956c7d316 --instance-type t3.small \
  --key-name YOUR_KEY --security-group-ids sg-XXXXXXXX

aws ec2 create-volume --size 20 --volume-type gp3 --availability-zone us-east-1a
aws ec2 attach-volume --volume-id vol-XXXX --instance-id i-XXXX --device /dev/sdf
```

```bash
ssh ec2-user@<instance-ip>
sudo dnf install -y docker git && sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user   # re-login after this

# Mount EBS and point Docker at it
sudo mkfs.xfs /dev/xvdf
sudo mkdir -p /mnt/data
echo '/dev/xvdf /mnt/data xfs defaults,nofail 0 2' | sudo tee -a /etc/fstab && sudo mount -a
echo '{"data-root":"/mnt/data/docker"}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker

git clone https://github.com/neurorishika/MiniVentory.git ~/miniventory
cd ~/miniventory && cp .env.example .env && nano .env
bash scripts/setup_rclone.sh
docker compose -p miniventory up -d
```

</details>

**HTTPS** — `sudo certbot --nginx -d inventory.yourlab.com`

> **No external cron needed.** Scheduled tasks run inside the container. If you want an additional external ping as a fallback, add to `crontab -e`:
> ```cron
> 0  * * * * curl -fsS "http://127.0.0.1:2152/tasks/summary?token=YOUR_CRON_TOKEN"  >/dev/null 2>&1
> 5  * * * * curl -fsS "http://127.0.0.1:2152/tasks/replenish?token=YOUR_CRON_TOKEN" >/dev/null 2>&1
> ```

**Secrets** — store `.env` values in AWS Secrets Manager instead of a plain file:
```bash
aws secretsmanager create-secret --name miniventory/prod --secret-string file://.env
```

---

### Google Cloud

<details>
<summary><b>Compute Engine (same pattern as AWS)</b></summary>

```bash
gcloud compute instances create miniventory --zone=us-central1-a \
  --machine-type=e2-small --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=20GB

gcloud compute ssh miniventory --zone=us-central1-a
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
# re-login, then follow the same clone/configure/deploy steps as AWS above
```

</details>

<details>
<summary><b>Cloud Run (serverless, requires MongoDB Atlas)</b></summary>

```bash
# Push to Artifact Registry
gcloud artifacts repositories create miniventory --repository-format=docker --location=us-central1
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build -t us-central1-docker.pkg.dev/YOUR_PROJECT/miniventory/app:latest . && docker push $_

# Deploy
gcloud run deploy miniventory \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT/miniventory/app:latest \
  --region us-central1 --port 2152 --min-instances 1 --allow-unauthenticated \
  --set-env-vars MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/,MONGO_DB=lab_inventory \
  --set-secrets SECRET_KEY=miniventory-secret-key:latest,ADMIN_PIN=miniventory-admin-pin:latest,CRON_TOKEN=miniventory-cron-token:latest

```

> Use `--min-instances 1` to avoid cold-start delays on the first request.
>
> **No external scheduler needed.** The app's internal APScheduler handles summary and replenish tasks. If you want an optional external fallback via Cloud Scheduler:
> ```bash
> gcloud scheduler jobs create http miniventory-summary \
>   --schedule="0 * * * *" --location=us-central1 \
>   --uri="https://YOUR_CLOUD_RUN_URL/tasks/summary?token=YOUR_CRON_TOKEN"
> gcloud scheduler jobs create http miniventory-replenish \
>   --schedule="5 * * * *" --location=us-central1 \
>   --uri="https://YOUR_CLOUD_RUN_URL/tasks/replenish?token=YOUR_CRON_TOKEN"
> ```

</details>

---

## 💾 Step 5 — Backups and recovery

The `mongo-backup` service runs automatically every 6 hours. Each run:
1. Calls `mongodump --oplog` — captures a point-in-time snapshot plus all writes mid-dump, enabling restore to any moment between windows
2. Compresses the dump to a timestamped `.tar.gz` in the `miniventory_mongo_backups` volume
3. Rotates archives older than `BACKUP_KEEP_DAYS` days (default 14), both locally and on the remote
4. Pushes the new archive to `RCLONE_REMOTE` if configured
5. Pings `https://hc-ping.com/HEALTHCHECK_UUID` so you get alerted if a run is ever missed

**List available backups:**
```bash
docker run --rm -v miniventory_mongo_backups:/backups alpine ls -lht /backups
```

**Restore from a local backup:**
```bash
BACKUP=20260519T060000Z   # replace with the timestamp you want

docker compose -p miniventory stop app

docker run --rm \
  -v miniventory_mongo_backups:/backups:ro \
  --network miniventory_default \
  mongo:7 bash -c "
    cd /tmp
    tar -xzf /backups/${BACKUP}.tar.gz
    mongorestore --host mongo:27017 --drop --oplogReplay /tmp/${BACKUP}
  "

docker compose -p miniventory start app
```

**Restore from a remote backup:**
```bash
rclone copy "${RCLONE_REMOTE}/${BACKUP}.tar.gz" /tmp/ --config scripts/rclone.conf
docker cp /tmp/${BACKUP}.tar.gz \
  $(docker compose -p miniventory ps -q mongo-backup):/backups/
# then run the restore block above
```

**Check replica set health:**
```bash
docker exec -it miniventory-mongo-1 mongosh --eval "rs.status()"
```

---

## 🛡️ Security checklist

- [ ] Run on LAN only; gate external access with a firewall
- [ ] Use a reverse proxy (nginx / Synology Login Portal) with TLS — never expose port 2152 directly to the internet
- [ ] Set a strong random `SECRET_KEY` and a non-trivial `ADMIN_PIN`; if you expose the `/tasks/*` endpoints set a long `CRON_TOKEN`
- [ ] Store secrets in AWS Secrets Manager / GCP Secret Manager / Portainer Secrets — not in a plain `.env` on disk in production
- [ ] Keep Mongo non-public; do not expose port 27017 outside the Docker network
- [ ] Regularly update container images: `docker compose pull && docker compose up -d`

---

## 🧑‍💻 Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set MONGO_URI to a local Mongo instance
python app.py
```

Health endpoint: `GET /health` → `{ "ok": true }`

**Logs in production:**
```bash
docker compose -p miniventory logs -f app
docker compose -p miniventory logs -f mongo-backup
```

**Common issues:**

| Symptom | Fix |
|---------|-----|
| Mongo unreachable at startup | Index creation is skipped with a warning and retried on next deploy; app still serves requests |
| Email not sending | Verify `SMTP_HOST`, port, `SMTP_USE_SSL`, credentials, and outbound firewall |
| Scheduled tasks not running | Check app logs for `miniventory.scheduler` entries: `docker compose -p miniventory logs app | grep scheduler`. Confirm MongoDB is reachable (the lock collection requires a write). |
| Manual task endpoint does nothing | Confirm `CRON_TOKEN` in `.env` matches the token in the curl command |
| Replica set not initialising | Check init container logs: `docker logs miniventory-mongo-init-1` |

---

## 📄 License

This project is provided as-is for internal lab use. You own your data and deployment.

---

<div align="center">
<sub>Built with ❤️ for lab life · <a href="https://github.com/neurorishika">neurorishika</a></sub>
</div>
