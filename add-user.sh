#!/usr/bin/env bash
# Add a panel user. Runs ON the server. Each user gets a private folder (0700) + a ready
# default project on first login (no path to type). Re-running with the same login
# is safe (the panel returns "user already exists").
#
# Usage:  bash add-user.sh <login> <password> "<Display Name>"
#   login     : 3-32 chars, latin letters/digits/underscore/hyphen, e.g. anna_ivanova
#   password  : 6+ characters
#   Display   : human name shown as the git author, e.g. "Анна Иванова"
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LOGIN="${1:?Usage: bash add-user.sh <login> <password> \"<Display Name>\"}"
PASS="${2:?password required (6+ chars)}"
NAME="${3:-$1}"

resp=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:3001/api/auth/register \
  -H 'Content-Type: application/json' \
  --data "$(python3 - "$LOGIN" "$PASS" "$NAME" <<'PY'
import json,sys
print(json.dumps({"username":sys.argv[1],"password":sys.argv[2],"displayName":sys.argv[3]}))
PY
)")

# The panel makes the folder itself, already private on a patched panel. Closing it here too
# means a person's files are private from their first minute even on a panel that has not
# been re-patched yet, and it reports loudly if something is still open.
# Runs before the HTTP verdict on purpose: a panel that refuses registration (503 behind a
# hardening layer) still leaves folders created by other means, and those must not stay open
# just because this script exited early.
bash "$HERE/secure-workspaces.sh"

case "$resp" in
  2??) echo "OK — user '$LOGIN' created. They log in at the panel with this login+password." ;;
  403) echo "User '$LOGIN' already exists (nothing changed)." ;;
  *)   echo "Unexpected HTTP $resp. Is the panel running?  systemctl status cloudcli"; exit 1 ;;
esac
