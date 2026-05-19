#!/bin/bash
# Interactive helper to generate scripts/rclone.conf for the mongo-backup service.
# Run this ONCE on the host before starting the stack.
# Requires rclone to be installed on the host (brew install rclone / apt install rclone).
#
# Supported remotes (choose one):
#   s3        — Amazon S3 or any S3-compatible store (Backblaze B2 S3, MinIO, etc.)
#   b2        — Backblaze B2 native API
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
echo "  3) Google Drive"
echo "  4) Other — open rclone interactive config"
read -rp "Choice [1-4]: " choice

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
    echo "Opening rclone interactive config for Google Drive..."
    rclone config --config "$CONF_PATH"
    echo ""
    echo "Set RCLONE_REMOTE=remote:<folder-path> in .env (use the remote name you chose above)"
    ;;
  4)
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
