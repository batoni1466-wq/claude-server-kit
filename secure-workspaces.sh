#!/usr/bin/env bash
# Make every panel user's work folder private, and check that it stayed private.
# Runs ON the server (as root for the fixing mode).
#
# Usage:
#   bash secure-workspaces.sh --check    read-only audit. Changes nothing, prints every
#                                        open folder, exits 1 if at least one is open.
#   bash secure-workspaces.sh            closes them, then runs the same audit.
#
# The rule it enforces, one line per path:
#   <WORKSPACES_ROOT>          mode 0711, owner root  (you may walk through it, you may not
#                                                      list who works on this server)
#   <WORKSPACES_ROOT>/<login>  mode 0700, owner ccuser_<login> when that system account
#                              exists (the person's session runs as it), otherwise root
#
# Only the top folder of each user is touched. Files deeper inside keep the modes they have:
# an outsider cannot walk past a 0700 parent anyway, a recursive chmod would hit the
# read-only bind mounts a setup may have inside a jail, and it would also cut the person off from the
# panel's own .tmp/images folders, which the panel writes as root.
#
# Idempotent: install.sh calls it on every run, and once every folder is closed a repeat run
# changes nothing. New folders are already created closed by patches/patch-multiuser.py —
# this script is what closes the ones made before that patch existed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/config.env"
if [ -z "${WORKSPACES_ROOT:-}" ] && [ -f "$CFG" ]; then
  # shellcheck disable=SC1090
  set -a; . "$CFG"; set +a
fi
WORKSPACES_ROOT="${WORKSPACES_ROOT:-/srv/cloudcli-users}"
ROOT_MODE=711
USER_MODE=700

MODE=fix
case "${1:-}" in
  '')      MODE=fix ;;
  --check) MODE=check ;;
  *) echo "ERROR: unknown argument '$1'. Usage: bash secure-workspaces.sh [--check]"; exit 1 ;;
esac

[ -d "$WORKSPACES_ROOT" ] || { echo "ERROR: $WORKSPACES_ROOT does not exist. Run install.sh first."; exit 1; }
# Root is required for BOTH modes. A closed root folder (0711) hides its listing from
# everyone else, so a non-root audit would walk an empty list and print a green "0 open" —
# the exact false answer this script exists to prevent.
if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: run as root. A non-root run cannot list $WORKSPACES_ROOT and would report a false OK."
  exit 1
fi

# Who must own <login>'s folder. With the per-person system accounts in place the session
# itself runs as ccuser_<login>, so the folder is handed to it; without them nobody but root
# runs anything, so root keeps it.
want_owner() {
  if getent passwd "ccuser_$1" >/dev/null 2>&1; then echo "ccuser_$1"; else echo root; fi
}

open_count=0
total=0

audit() {
  local mode owner want dir login
  open_count=0
  total=0
  mode="$(stat -c '%a' "$WORKSPACES_ROOT")"
  owner="$(stat -c '%U' "$WORKSPACES_ROOT")"
  if [ "$mode" != "$ROOT_MODE" ] || [ "$owner" != root ]; then
    printf '    OPEN  %-24s mode %s (want %s)  owner %s (want root)\n' \
      "$(basename "$WORKSPACES_ROOT")/" "$mode" "$ROOT_MODE" "$owner"
    open_count=$((open_count + 1))
  fi
  for dir in "$WORKSPACES_ROOT"/*; do
    [ -d "$dir" ] || continue
    login="${dir##*/}"
    total=$((total + 1))
    mode="$(stat -c '%a' "$dir")"
    owner="$(stat -c '%U' "$dir")"
    want="$(want_owner "$login")"
    if [ "$mode" != "$USER_MODE" ] || [ "$owner" != "$want" ]; then
      printf '    OPEN  %-24s mode %s (want %s)  owner %s (want %s)\n' \
        "$login" "$mode" "$USER_MODE" "$owner" "$want"
      open_count=$((open_count + 1))
    fi
  done
}

close_all() {
  local mode owner want dir login changed=0
  mode="$(stat -c '%a' "$WORKSPACES_ROOT")"
  owner="$(stat -c '%U' "$WORKSPACES_ROOT")"
  if [ "$mode" != "$ROOT_MODE" ]; then
    chmod "$ROOT_MODE" "$WORKSPACES_ROOT"
    echo "    $WORKSPACES_ROOT: mode $mode -> $ROOT_MODE"
    changed=$((changed + 1))
  fi
  if [ "$owner" != root ]; then
    chown root "$WORKSPACES_ROOT"
    echo "    $WORKSPACES_ROOT: owner $owner -> root"
    changed=$((changed + 1))
  fi
  for dir in "$WORKSPACES_ROOT"/*; do
    [ -d "$dir" ] || continue
    login="${dir##*/}"
    mode="$(stat -c '%a' "$dir")"
    owner="$(stat -c '%U' "$dir")"
    want="$(want_owner "$login")"
    if [ "$mode" != "$USER_MODE" ]; then
      chmod "$USER_MODE" "$dir"
      echo "    $login: mode $mode -> $USER_MODE"
      changed=$((changed + 1))
    fi
    if [ "$owner" != "$want" ]; then
      chown "$want" "$dir"
      echo "    $login: owner $owner -> $want"
      changed=$((changed + 1))
    fi
  done
  echo "    changed $changed path(s)"
}

if [ "$MODE" = check ]; then
  echo "==> Work folders: check ($WORKSPACES_ROOT)"
  audit
  if [ "$open_count" -gt 0 ]; then
    echo "    $open_count path(s) open — anyone with an account on this server can read them."
    echo "    Close them: bash secure-workspaces.sh"
    exit 1
  fi
  if [ "$total" -eq 0 ]; then
    echo "    OK — root is 0$ROOT_MODE, but there are NO work folders in $WORKSPACES_ROOT yet."
    echo "    That is normal right after install; on a live panel it means WORKSPACES_ROOT points elsewhere."
    exit 0
  fi
  echo "    OK — $total work folder(s), each 0$USER_MODE and owned by its user; root 0$ROOT_MODE"
  exit 0
fi

echo "==> Work folders: closing ($WORKSPACES_ROOT)"
close_all
audit
if [ "$open_count" -gt 0 ]; then
  echo "FAIL: $open_count path(s) still open after the fix (see the OPEN lines above)."
  exit 1
fi
echo "    $total work folder(s) private: 0$USER_MODE + own owner, root 0$ROOT_MODE"
