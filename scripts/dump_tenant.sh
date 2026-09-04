#!/usr/bin/env bash
set -euo pipefail
TS=$(date -u +%Y%m%d)
DEST="s3://${BACKUP_BUCKET:-recoup-backups}/weekly/${TS}/recoup_tenant.dump"
pg_dump -d "$DATABASE_URL" -n public -F c -f /tmp/recoup_${TS}.dump --no-owner
aws s3 cp /tmp/recoup_${TS}.dump "$DEST" --storage-class STANDARD_IA
rm /tmp/recoup_${TS}.dump
echo "[OK] dumped to $DEST"
