#!/bin/bash
# Interactive helper to generate scripts/rclone.conf for the mongo-backup service.
# Run this ONCE on the host before starting the stack.
# Requires rclone to be installed on the host (brew install rclone / apt install rclone).
#
# Supported remotes (choose one):
#   s3        — Amazon S3 or any S3-compatible store (Backblaze B2 S3, MinIO, Wasabi, etc.)
#   b2        — Backblaze B2 native API
#   sftp      — Another NAS or server via SFTP (SSH key auth)
#   drive     — Google Drive
#   custom    — Skip and write rclone.conf manually

set -euo pipefail

CONF_PATH="$(dirname "$0")/rclone.conf"

if [ -f "$CONF_PATH" ]; then
  echo "rclone.conf already exists at $CONF_PATH"
  read -rp "Overwrite? [y/N] " yn
  [[ "$yn" =~ ^[Yy]$ ]] || exit 0
fi

echo ""
echo "Choose remote type:"
echo "  1) Amazon S3 (or S3-compatible: Backblaze B2 S3, MinIO, Wasabi…)"
echo "  2) Backblaze B2 (native API)"
echo "  3) SFTP — another NAS or server (SSH key auth)"
echo "  4) Google Drive"
echo "  5) Other — open rclone interactive config"
read -rp "Choice [1-5]: " choice

case "$choice" in
  1)
    read -rp "Access Key ID:     " AWS_ACCESS_KEY_ID
    read -rsp "Secret Access Key: " AWS_SECRET_ACCESS_KEY; echo
    read -rp "Region [us-east-1]: " REGION; REGION="${REGION:-us-east-1}"
    read -rp "Endpoint (leave blank for AWS S3): " ENDPOINT
    PROVIDER="${ENDPOINT:+Other}"; PROVIDER="${PROVIDER:-AWS}"
    EP_LINE="${ENDPOINT:+endpoint = $ENDPOINT}"
    cat > "$CONF_PATH" <<EOF
[remote]
type = s3
provider = ${PROVIDER}
access_key_id = ${AWS_ACCESS_KEY_ID}
secret_access_key = ${AWS_SECRET_ACCESS_KEY}
region = ${REGION}
${EP_LINE}
EOF
    echo ""
    echo "Set RCLONE_REMOTE=remote:<your-bucket>/miniventory-backups in .env"
    ;;
  2)
    read -rp "Account ID:      " B2_ACCOUNT
    read -rsp "Application Key: " B2_KEY; echo
    cat > "$CONF_PATH" <<EOF
[remote]
type = b2
account = ${B2_ACCOUNT}
key = ${B2_KEY}
EOF
    echo ""
    echo "Set RCLONE_REMOTE=remote:<your-bucket>/miniventory-backups in .env"
    ;;
  3)
    read -rp "Destination host (IP or hostname): " SFTP_HOST
    read -rp "SSH user [backup-writer]: " SFTP_USER; SFTP_USER="${SFTP_USER:-backup-writer}"
    read -rp "SSH port [22]: " SFTP_PORT; SFTP_PORT="${SFTP_PORT:-22}"
    KEY_PATH="$(dirname "$0")/backup_id_ed25519"
    if [ ! -f "$KEY_PATH" ]; then
      echo "Generating SSH key pair at ${KEY_PATH} ..."
      ssh-keygen -t ed25519 -f "$KEY_PATH" -N ""
      echo ""
      echo "Copy the public key to the destination host:"
      echo "  ssh-copy-id -i ${KEY_PATH}.pub -p ${SFTP_PORT} ${SFTP_USER}@${SFTP_HOST}"
      echo "Then re-run this script, or set up the config manually."
    fi
    cat > "$CONF_PATH" <<EOF
[remote]
type = sftp
host = ${SFTP_HOST}
user = ${SFTP_USER}
port = ${SFTP_PORT}
key_file = /config/rclone/backup_id_ed25519
EOF
    echo ""
    echo "Mount the key in docker-compose.yml under mongo-backup → volumes:"
    echo "  - ./scripts/backup_id_ed25519:/config/rclone/backup_id_ed25519:ro"
    echo ""
    echo "Set RCLONE_REMOTE=remote:/path/on/destination/nas in .env"
    echo "Add scripts/backup_id_ed25519 and scripts/backup_id_ed25519.pub to .gitignore!"
    ;;
  4)
    echo "Opening rclone interactive config for Google Drive..."
    rclone config --config "$CONF_PATH"
    echo ""
    echo "Set RCLONE_REMOTE=remote:<folder-path> in .env (use the remote name you chose above)"
    ;;
  5)
    echo "Opening rclone interactive config..."
    rclone config --config "$CONF_PATH"
    echo ""
    echo "Set RCLONE_REMOTE=remote:<path> in .env (use the remote name you chose above)"
    ;;
  *)
    echo "Invalid choice."; exit 1 ;;
esac

echo ""
echo "Config written to: $CONF_PATH"
echo "Test with: rclone ls remote: --config $CONF_PATH"
