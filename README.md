# MiniVentory

A lightweight **consumables checkout kiosk** for labs and makerspaces. Users check out items from a tablet-friendly form; admins manage inventory, users, and settings behind a PIN gate. Everything runs in Docker — one compose file brings up the app, a two-node MongoDB replica set, and an automated backup service.

**What it does**
- Tablet-friendly checkout: pick your name, item, quantity, and an optional note
- Atomic stock updates with full before/after audit log
- Low-stock email alerts (rate-limited, non-blocking)
- Scheduled summary emails (daily/weekly), configurable from the admin UI
- Per-item auto-replenish (daily/weekly/monthly), idempotent
- CSV export for logs and stock snapshots
- Admin panel: items, users, logs, summary dashboard, settings

**How scheduled tasks work** — the app deliberately has no internal scheduler. Instead, two HTTP endpoints (`/tasks/summary` and `/tasks/replenish`) are secured with a `CRON_TOKEN` and pinged on a schedule from the host (crontab, Synology Task Scheduler, Portainer Schedules, or Cloud Scheduler). This keeps the app stateless and easy to reason about.

---

## Architecture

The production stack (`docker-compose.yml`) contains five services:

| Service | Role |
|---------|------|
| `mongo` | Primary MongoDB node (replica set `rs0`) |
| `mongo2` | Secondary MongoDB node — real-time replica of every write |
| `mongo-init` | One-shot init container — calls `rs.initiate()` then exits |
| `mongo-backup` | Runs `mongodump --oplog` every 6 hours, compresses, rotates, and optionally pushes off-site via rclone |
| `app` | Flask app served by Gunicorn |

Three named Docker volumes are created: `miniventory_mongo_data` (primary), `miniventory_mongo_data2` (secondary), and `miniventory_mongo_backups` (compressed dumps). Named volumes survive container removal.

---

## Step 1 — Configure

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Required values:

| Key | Purpose |
|-----|---------|
| `SECRET_KEY` | Flask session key — generate a long random string |
| `ADMIN_PIN` | Numeric PIN for the admin UI |
| `MONGO_DB` | Database name, e.g. `lab_inventory` |
| `CRON_TOKEN` | Shared secret for `/tasks/*` endpoints |

Optional but recommended:

| Key | Purpose |
|-----|---------|
| `SMTP_HOST/PORT/USERNAME/PASSWORD` | Outbound email for alerts and summaries |
| `SMTP_USE_SSL` | `true` or `false` |
| `SMTP_FROM`, `ADMIN_EMAIL` | Sender address and alert recipient |
| `SUMMARY_DEFAULT_FREQUENCY` | `never` / `daily` / `weekly` |
| `RCLONE_REMOTE` | Off-site backup destination, e.g. `remote:bucket/miniventory-backups` |
| `HEALTHCHECK_UUID` | [healthchecks.io](https://healthchecks.io) UUID — get alerted if backups stop |
| `BACKUP_KEEP_DAYS` | Days of local backups to retain (default `14`) |

---

## Step 2 — Build and push the image

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

## Step 3 — Set up off-site backups (run once on the server)

The backup service uses rclone to push archives off-server. Run the interactive setup script on the host to generate `scripts/rclone.conf` (this file is gitignored — credentials never go into source control):

```bash
# Install rclone on the host if not already present
curl https://rclone.org/install.sh | sudo bash   # Linux / Synology via SSH
# or: brew install rclone  (macOS)

bash scripts/setup_rclone.sh   # choose S3, Backblaze B2, Google Drive, or other

# Verify the connection
rclone ls remote: --config scripts/rclone.conf

# Add the remote path to .env
echo 'RCLONE_REMOTE=remote:your-bucket/miniventory-backups' >> .env
echo 'HEALTHCHECK_UUID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' >> .env
```

If you skip this step, backups still run locally — `RCLONE_REMOTE` is optional.

---

## Step 4 — Deploy

### Portainer (recommended)

Portainer's **Stacks** feature is the recommended way to manage the stack — it gives you a GUI for deployment, log viewing, container management, and updates without needing repeated SSH access.

**Install Portainer** (skip if already running):

```bash
docker run -d \
  --name portainer --restart always \
  -p 9443:9443 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v portainer_data:/data \
  portainer/portainer-ce:latest
```

Access at `https://<host-ip>:9443`.

**Create the stack:**

1. Portainer → **Stacks** → **+ Add stack** → name it `miniventory`
2. Select **Web editor** and paste the full contents of `docker-compose.yml`
3. Because Portainer resolves paths relative to the host (not the repo directory), replace the two relative bind-mount paths in the editor:
   ```yaml
   # in the mongo-backup volumes section, change:
   - ./scripts/mongo_backup.sh:/usr/local/bin/mongo_backup.sh:ro
   - ./scripts/rclone.conf:/config/rclone/rclone.conf:ro
   # to absolute paths, e.g. on Synology:
   - /volume1/docker/miniventory/scripts/mongo_backup.sh:/usr/local/bin/mongo_backup.sh:ro
   - /volume1/docker/miniventory/scripts/rclone.conf:/config/rclone/rclone.conf:ro
   ```
4. Scroll to **Environment variables** and add every key from your `.env` file
5. Click **Deploy the stack**

The `mongo-init` service runs once, initialises the replica set, and exits. Verify it worked: **Containers** → `miniventory_mongo_1` → **Console** → run `mongosh --eval "rs.status()"` — you should see one `PRIMARY` and one `SECONDARY`.

**Set up cron pings** via Portainer → **Schedules** (CE 2.19+), or on the host:

```cron
0  * * * * curl -fsS "http://127.0.0.1:2152/tasks/summary?token=YOUR_CRON_TOKEN"  >/dev/null 2>&1
5  * * * * curl -fsS "http://127.0.0.1:2152/tasks/replenish?token=YOUR_CRON_TOKEN" >/dev/null 2>&1
```

**Update the app:** push a new image, then Portainer → **Stacks** → `miniventory` → **Update the stack**, or use the **Recreate** button on the `app` container with **Pull latest image** checked.

---

### Synology NAS (SSH + Compose)

```bash
ssh admin@<nas-ip>
cd /volume1/docker
git clone https://github.com/neurorishika/MiniVentory.git miniventory
cd miniventory
cp .env.example .env && nano .env
bash scripts/setup_rclone.sh          # off-site backup config (Step 3)
docker compose -p miniventory up -d --build
```

**Reverse proxy + TLS** — Control Panel → Login Portal → Reverse Proxy:
- Source: `https://inventory.yourlab.local:443`
- Destination: `http://127.0.0.1:2152`
- Assign a certificate under Control Panel → Security → Certificate

**Firewall** — restrict port 2152 to LAN; expose only 443 via the reverse proxy.

**Cron** — Control Panel → Task Scheduler → Create → User-defined script:

| Task | Schedule | Command |
|------|----------|---------|
| Summary | Every hour | `curl -fsS "http://127.0.0.1:2152/tasks/summary?token=YOUR_CRON_TOKEN" \|\| true` |
| Replenish | Every hour (min 5) | `curl -fsS "http://127.0.0.1:2152/tasks/replenish?token=YOUR_CRON_TOKEN" \|\| true` |

**Update:**
```bash
cd /volume1/docker/miniventory && git pull
docker compose -p miniventory up -d --build
```

---

### AWS (EC2 + EBS)

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
docker compose -p miniventory up -d --build
```

**HTTPS** — install nginx + Certbot: `sudo certbot --nginx -d inventory.yourlab.com`

**Cron** — `crontab -e`:
```cron
0  * * * * curl -fsS "http://127.0.0.1:2152/tasks/summary?token=YOUR_CRON_TOKEN"  >/dev/null 2>&1
5  * * * * curl -fsS "http://127.0.0.1:2152/tasks/replenish?token=YOUR_CRON_TOKEN" >/dev/null 2>&1
```

**Secrets** — store `.env` values in AWS Secrets Manager instead of a plain file on disk:
```bash
aws secretsmanager create-secret --name miniventory/prod --secret-string file://.env
```

---

### Google Cloud

**Compute Engine (same pattern as EC2):**

```bash
gcloud compute instances create miniventory --zone=us-central1-a \
  --machine-type=e2-small --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=20GB

gcloud compute ssh miniventory --zone=us-central1-a
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
# re-login, then follow the same clone/configure/deploy steps as EC2 above
```

**Cloud Run (serverless, requires external MongoDB Atlas):**

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

# Cron via Cloud Scheduler
gcloud scheduler jobs create http miniventory-summary \
  --schedule="0 * * * *" --location=us-central1 \
  --uri="https://YOUR_CLOUD_RUN_URL/tasks/summary?token=YOUR_CRON_TOKEN"
gcloud scheduler jobs create http miniventory-replenish \
  --schedule="5 * * * *" --location=us-central1 \
  --uri="https://YOUR_CLOUD_RUN_URL/tasks/replenish?token=YOUR_CRON_TOKEN"
```

> Use `--min-instances 1` to avoid cold-start delays on the first request.

---

## Step 5 — Backups and recovery

The `mongo-backup` service runs automatically every 6 hours. Each run:
1. Calls `mongodump --oplog` — captures a point-in-time snapshot of all databases plus all writes that occurred mid-dump, enabling restore to any moment between windows
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

**Check replica set health at any time:**
```bash
docker exec -it miniventory-mongo-1 mongosh --eval "rs.status()"
```

---

## Security checklist

- Run on LAN only; gate external access with a firewall
- Use a reverse proxy (nginx / Synology Login Portal) with TLS — never expose port 2152 directly to the internet
- Set a strong random `SECRET_KEY`, a non-trivial `ADMIN_PIN`, and a long `CRON_TOKEN`
- Store secrets in AWS Secrets Manager / GCP Secret Manager / Portainer Secrets — not in a plain `.env` on disk in production
- Keep Mongo non-public; do not expose port 27017 outside the Docker network
- Regularly update container images: `docker compose pull && docker compose up -d`

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set MONGO_URI to a local Mongo instance
python app.py
```

Health endpoint: `GET /health` returns `{ "ok": true }`

Logs in production:
```bash
docker compose -p miniventory logs -f app
docker compose -p miniventory logs -f mongo-backup
```

Common issues:
- **Mongo connectivity on startup** — if Mongo is temporarily unreachable at boot, index creation is skipped with a warning and retried on the next deployment; the app will still serve requests
- **Email not sending** — verify `SMTP_HOST`, port, `SMTP_USE_SSL`, credentials, and that your firewall allows outbound SMTP
- **Cron pings doing nothing** — confirm `CRON_TOKEN` in `.env` matches the token in the curl command; check the scheduler logs on your platform
- **Replica set not initialising** — check `mongo-init` container logs: `docker logs miniventory-mongo-init-1`

---

## License

Provided as-is for internal lab use. You own your data and deployment.
