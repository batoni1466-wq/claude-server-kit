#!/usr/bin/env bash
# ClaudeCode server — one-command fresh install.
#
# Runs ON a fresh Ubuntu 24.04 server as root. Installs the full stack:
#   Node 22 + Claude Code CLI + CloudCLI panel + Caddy (HTTPS) + firewall
#   + all our patches (any-file attachments, terracotta theme, Inter font,
#     ClaudeCode rebrand, multi-user path-jail isolation, logout button)
#   + keepalive / creds-sync timers + auto-update lock.
# It does NOT log into Claude — that is a separate interactive step printed at the
# end (needs a browser + VPN once). No personal data, no snapshot: every install
# gets its own fresh login, its own users, its own generated secrets.
#
# Usage (on the server, from the kit folder):
#   cp config.example.env config.env    # then edit config.env (set SERVER_IP)
#   bash install.sh
#
# Idempotent: safe to re-run (e.g. after `cloudcli update` wiped the patches).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/config.env"
[ -f "$CFG" ] || { echo "ERROR: config.env not found. Run: cp config.example.env config.env  then edit it."; exit 1; }
# shellcheck disable=SC1090
set -a; . "$CFG"; set +a

: "${SERVER_IP:?Set SERVER_IP in config.env}"
BRAND="${BRAND:-ClaudeCode}"
ENABLE_DESIGN="${ENABLE_DESIGN:-yes}"
WORKSPACES_ROOT="${WORKSPACES_ROOT:-/srv/cloudcli-users}"
BASE=/usr/lib/node_modules/@cloudcli-ai/cloudcli
STATE=/root/claudecode-kit
mkdir -p "$STATE"

HOST="$(echo "$SERVER_IP" | tr '.' '-').sslip.io"
DESIGN_HOST="design.$HOST"

# Persist / reuse the Claude Design access secret so re-runs keep the same URL.
if [ "$ENABLE_DESIGN" = "yes" ]; then
  if [ -f "$STATE/design-secret.txt" ]; then
    DESIGN_SECRET="$(cat "$STATE/design-secret.txt")"
  else
    DESIGN_SECRET="$(openssl rand -hex 24)"
    echo "$DESIGN_SECRET" > "$STATE/design-secret.txt"; chmod 600 "$STATE/design-secret.txt"
  fi
  DESIGN_URL="https://$DESIGN_HOST/?k=$DESIGN_SECRET"
  echo "$DESIGN_URL" > "$STATE/design-url.txt"; chmod 600 "$STATE/design-url.txt"
else
  DESIGN_URL=""
fi

echo "==> ClaudeCode install"
echo "    brand=$BRAND  panel=https://$HOST  design=$([ "$ENABLE_DESIGN" = yes ] && echo "https://$DESIGN_HOST" || echo off)"

echo "==> [1/7] Swap + base packages (Node 22, Claude Code, CloudCLI, Caddy, python3)"
export DEBIAN_FRONTEND=noninteractive
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
  sysctl -w vm.swappiness=10 >/dev/null
fi
command -v python3 >/dev/null || { apt-get update >/dev/null 2>&1; apt-get install -y python3 >/dev/null 2>&1; }
command -v node    >/dev/null || { curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1; apt-get install -y nodejs >/dev/null 2>&1; }
command -v claude  >/dev/null || npm install -g @anthropic-ai/claude-code >/dev/null 2>&1
# Pin the panel to 1.36.0: the patches (patch-attachments.py / patch-multiuser.py)
# match exact source strings from this version and abort loudly on any drift. If you
# ever bump this, re-check the patch anchors (see docs/06-troubleshooting.md).
CLOUDCLI_VERSION="${CLOUDCLI_VERSION:-1.36.0}"
command -v cloudcli>/dev/null || npm install -g "@cloudcli-ai/cloudcli@${CLOUDCLI_VERSION}" >/dev/null 2>&1
if ! command -v caddy >/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg >/dev/null 2>&1
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' >/etc/apt/sources.list.d/caddy-stable.list
  apt-get update >/dev/null 2>&1; apt-get install -y caddy >/dev/null 2>&1
fi
echo "    node $(node -v) | claude $(claude --version 2>&1 | head -c 14) | caddy $(caddy version 2>/dev/null | head -c 10)"

echo "==> [2/7] Patch attachments (any file type, up to 20 / 25MB), trim tabs, remove in-UI update"
python3 "$HERE/patches/patch-attachments.py" "$BASE" >/dev/null && echo "    attachments patched"

echo "==> [3/7] Inter font + terracotta theme + $BRAND rebrand + logout + Design button"
cp "$HERE/patches/InterVariable.woff2" /root/InterVariable.woff2
cp "$HERE/patches/cc-theme.css"        /root/cc-theme.css
BRAND="$BRAND" DESIGN_URL="$DESIGN_URL" bash "$HERE/patches/inject-font.sh" "$BASE" /root/InterVariable.woff2 /root/cc-theme.css

echo "==> [4/7] Multi-user path-jail isolation (each login -> own $WORKSPACES_ROOT/<user>)"
mkdir -p "$WORKSPACES_ROOT"
python3 "$HERE/patches/patch-multiuser.py" "$BASE" >/dev/null && echo "    multiuser patched"

echo "==> [5/7] Panel service (systemd, root-permission fix IS_SANDBOX=1)"
JWT=$(openssl rand -hex 32)
CLAUDE_BIN=$(command -v claude)
cat > /etc/systemd/system/cloudcli.service <<EOF
[Unit]
Description=CloudCLI web panel (Claude Code UI)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=root
WorkingDirectory=/root
Environment=HOME=/root
Environment=IS_SANDBOX=1
Environment=WORKSPACES_ROOT=$WORKSPACES_ROOT
Environment=NODE_ENV=production
Environment=HOST=127.0.0.1
Environment=SERVER_PORT=3001
Environment=PORT=3001
Environment=JWT_SECRET=$JWT
Environment=CLAUDE_CLI_PATH=$CLAUDE_BIN
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/bin/cloudcli start
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
mkdir -p /etc/systemd/system/cloudcli.service.d
cat > /etc/systemd/system/cloudcli.service.d/no-autoupdate.conf <<'EOF'
[Service]
Environment=DISABLE_AUTOUPDATER=1
EOF
systemctl daemon-reload
systemctl enable --now cloudcli >/dev/null 2>&1
echo "    panel: $(systemctl is-active cloudcli)"

echo "==> [6/7] HTTPS via Caddy"
{
  printf '%s {\n\treverse_proxy 127.0.0.1:3001\n}\n' "$HOST"
  if [ "$ENABLE_DESIGN" = "yes" ]; then
    # Cookie-gate for Claude Design: ?k=<secret> sets a 1-year cookie and proxies;
    # a request already carrying the cookie proxies; anything else -> 403. The panel
    # "Claude Design" button carries ?k=<secret>, so the team never sees a password.
    cat <<EOF
$DESIGN_HOST {
	@haskey query k=$DESIGN_SECRET
	@hascookie header Cookie *od_key=$DESIGN_SECRET*
	handle @haskey {
		header +Set-Cookie "od_key=$DESIGN_SECRET; Path=/; Max-Age=31536000; Secure; HttpOnly; SameSite=Lax"
		reverse_proxy 127.0.0.1:7456
	}
	handle @hascookie {
		reverse_proxy 127.0.0.1:7456
	}
	handle {
		respond "Forbidden" 403
	}
}
EOF
  fi
} > /etc/caddy/Caddyfile
systemctl restart caddy && echo "    caddy: $(systemctl is-active caddy)"

echo "==> [7/7] Firewall (22/80/443 only) + keepalive/creds-sync timers + auto-update lock"
ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null 2>&1
grep -q 'DISABLE_AUTOUPDATER' /etc/environment || echo 'DISABLE_AUTOUPDATER=1' >> /etc/environment

# Keepalive: refresh the shared login every 3h so it never dies while idle.
cat > /usr/local/bin/claude-keepalive.sh <<'EOF'
#!/usr/bin/env bash
IS_SANDBOX=1 HOME=/root /usr/bin/claude -p "ok" >/dev/null 2>&1 || true
EOF
chmod +x /usr/local/bin/claude-keepalive.sh
cat > /etc/systemd/system/claude-keepalive.service <<'EOF'
[Unit]
Description=Refresh Claude subscription token
[Service]
Type=oneshot
ExecStart=/usr/local/bin/claude-keepalive.sh
EOF
cat > /etc/systemd/system/claude-keepalive.timer <<'EOF'
[Unit]
Description=Run Claude keepalive every 3h
[Timer]
OnBootSec=10min
OnUnitActiveSec=3h
Persistent=true
[Install]
WantedBy=timers.target
EOF

if [ "$ENABLE_DESIGN" = "yes" ]; then
  # Sync the shared login into Claude Design's isolated copy whenever it changes.
  cat > /usr/local/bin/od-creds-sync.sh <<'EOF'
#!/usr/bin/env bash
src=/root/.claude/.credentials.json
dst=/opt/od-claude-home/.credentials.json
[ -f "$src" ] || exit 0
mkdir -p /opt/od-claude-home
cp -f "$src" "$dst" 2>/dev/null || exit 0
chown 1001:1001 "$dst" 2>/dev/null || true
chmod 600 "$dst" 2>/dev/null || true
EOF
  chmod +x /usr/local/bin/od-creds-sync.sh
  cat > /etc/systemd/system/od-creds-sync.service <<'EOF'
[Unit]
Description=Copy Claude login into Claude Design isolated home
[Service]
Type=oneshot
ExecStart=/usr/local/bin/od-creds-sync.sh
EOF
  cat > /etc/systemd/system/od-creds-sync.path <<'EOF'
[Unit]
Description=Watch Claude credentials for Claude Design sync
[Path]
PathModified=/root/.claude/.credentials.json
[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/systemd/system/od-creds-sync.timer <<'EOF'
[Unit]
Description=Claude Design creds sync safety net (15 min)
[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Persistent=true
[Install]
WantedBy=timers.target
EOF
fi
systemctl daemon-reload
systemctl enable --now claude-keepalive.timer >/dev/null 2>&1 || true
if [ "$ENABLE_DESIGN" = "yes" ]; then
  systemctl enable --now od-creds-sync.path od-creds-sync.timer >/dev/null 2>&1 || true
fi
echo "    firewall: $(ufw status | head -1 | cut -d' ' -f2)  timers: on"

echo "==> Restarting panel to pick up all patches"
systemctl restart cloudcli && echo "    panel: $(systemctl is-active cloudcli)"
# Note: this build trims the top tabs to Chat + Files (Shell/Git/Browser removed),
# so no in-panel browser is installed. Claude Design (optional) is a separate service.

cat <<EOF

==================================================================
  BASE INSTALL DONE.

  Panel:  https://$HOST
  (HTTPS certificate can take ~20 seconds on first open.)

  NEXT — two manual steps (the installer cannot do these for you):

  1) LOG INTO CLAUDE (once, needs a browser + VPN for 1 minute):
        bash $HERE/login-claude.sh
     It shows a link. Open it in a browser with VPN, sign into YOUR
     Claude account, copy the code, paste it back. Done — the login
     then lives on the server for months.

  2) CREATE YOUR FIRST PANEL USER (repeat per team member):
        bash $HERE/add-user.sh <login> <password> "<Display Name>"

$([ "$ENABLE_DESIGN" = yes ] && cat <<EOD
  3) (optional) CLAUDE DESIGN — the AI design tool:
        bash $HERE/setup-open-design.sh
     Then log into ChatGPT for GPT-image (if ENABLE_CODEX=yes):
        bash $HERE/login-codex.sh
     Design URL (has the access key — keep private):
        $(cat "$STATE/design-url.txt")
EOD
)
==================================================================
EOF
