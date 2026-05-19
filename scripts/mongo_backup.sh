#!/bin/bash
# MongoDB backup script — runs inside the mongo-backup container.
# Features:
#   - mongodump with --oplog for point-in-time recovery
#   - Compressed timestamped archives
#   - Local rotation (BACKUP_KEEP_DAYS, default 14)
#   - Optional rclone push to any remote (S3, B2, GDrive, etc.)
#   - Optional dead-man's switch ping (hc-ping.com)

set -euo pipefail

MONGO_HOST="${MONGO_HOST:-mongo}"
MONGO_PORT="${MONGO_PORT:-27017}"
BACKUP_DIR="/backups"
TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
DEST="${BACKUP_DIR}/${TIMESTAMP}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
RCLONE_REMOTE="${RCLONE_REMOTE:-}"       # e.g. "s3:my-bucket/miniventory" — blank = skip
HEALTHCHECK_UUID="${HEALTHCHECK_UUID:-}" # hc-ping.com UUID — blank = skip

log() { echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $*"; }

log "Starting mongodump (--oplog) -> ${DEST}.tar.gz"

mongodump \
  --host "${MONGO_HOST}:${MONGO_PORT}" \
  --oplog \
  --out "${DEST}"

tar -czf "${DEST}.tar.gz" -C "${BACKUP_DIR}" "${TIMESTAMP}"
rm -rf "${DEST}"

log "Backup written: ${DEST}.tar.gz ($(du -sh "${DEST}.tar.gz" | cut -f1))"

# ── Local rotation ────────────────────────────────────────────────────────────
find "${BACKUP_DIR}" -maxdepth 1 -name "*.tar.gz" -mtime "+${KEEP_DAYS}" -print -delete
log "Local rotation complete (keeping last ${KEEP_DAYS} days)"

# ── Off-site push via rclone ──────────────────────────────────────────────────
if [ -n "${RCLONE_REMOTE}" ]; then
  log "Pushing to rclone remote: ${RCLONE_REMOTE}"
  rclone copy "${DEST}.tar.gz" "${RCLONE_REMOTE}/" \
    --config /config/rclone/rclone.conf \
    --log-level INFO
  # Mirror local rotation on the remote
  rclone delete "${RCLONE_REMOTE}/" \
    --config /config/rclone/rclone.conf \
    --min-age "${KEEP_DAYS}d" \
    --log-level INFO
  log "Remote sync complete"
else
  log "RCLONE_REMOTE not set — skipping off-site push"
fi

# ── Dead-man's switch ping ────────────────────────────────────────────────────
if [ -n "${HEALTHCHECK_UUID}" ]; then
  curl -fsS --retry 3 "https://hc-ping.com/${HEALTHCHECK_UUID}" > /dev/null 2>&1 \
    && log "Healthcheck pinged" \
    || log "WARNING: healthcheck ping failed"
fi

log "Done."
