#!/usr/bin/env bash
# Read-only verification for a CEI Labs platform backup bundle.
set -Eeuo pipefail
umask 077

BACKUP_DIR="${1:-}"
KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-}"
[[ -d "$BACKUP_DIR" ]] || { echo "usage: $0 <backup-directory>" >&2; exit 1; }
[[ -f "$KEY_FILE" ]] || { echo "BACKUP_ENCRYPTION_KEY_FILE is required" >&2; exit 1; }

required=(manifest.json SHA256SUMS ctfd.sql ctfd-uploads.tar orchestrator-data.tar protected-config.tar.enc services.json resolved-stack.yml)
for name in "${required[@]}"; do
  [[ -s "$BACKUP_DIR/$name" ]] || { echo "missing or empty: $name" >&2; exit 1; }
done

(
  cd "$BACKUP_DIR"
  sha256sum -c SHA256SUMS
)
python3 - "$BACKUP_DIR/manifest.json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    manifest = json.load(f)
if manifest.get("format") != "cei-labs-platform-backup-v1":
    raise SystemExit("unsupported backup format")
print(f"manifest OK: {manifest['run_id']} ({manifest['created_utc']})")
PY
openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$KEY_FILE" \
  -in "$BACKUP_DIR/protected-config.tar.enc" | tar -tf - >/dev/null
tar -tf "$BACKUP_DIR/ctfd-uploads.tar" >/dev/null
tar -tf "$BACKUP_DIR/orchestrator-data.tar" >/dev/null
grep -qE '^(-- MariaDB dump|/\*!999999)' "$BACKUP_DIR/ctfd.sql" || {
  echo "ctfd.sql does not look like a MariaDB logical dump" >&2
  exit 1
}
echo "backup verification passed"
