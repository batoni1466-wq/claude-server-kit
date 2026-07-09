#!/usr/bin/env bash
# Optional: install Claude Design (nexu-io/open-design) as a Docker service next to the
# panel, running on the SAME Claude subscription (no API key). Runs ON the server, AFTER
# install.sh and AFTER you have logged into Claude (login-claude.sh) — it needs a valid
# /root/.claude/.credentials.json to copy into the container's isolated home.
#
# The image is built from source (the prebuilt image is not anonymously pullable). The
# build is heavy (a pnpm monorepo + Next.js) and can take 10-20 minutes on 2 vCPU.
#
# Usage:  bash setup-open-design.sh
# Reads:  config.env (ENABLE_CODEX, SERVER_IP)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/config.env"
[ -f "$CFG" ] || { echo "ERROR: config.env not found (run install.sh first)."; exit 1; }
set -a; . "$CFG"; set +a
: "${SERVER_IP:?Set SERVER_IP in config.env}"
ENABLE_CODEX="${ENABLE_CODEX:-no}"
HOST="$(echo "$SERVER_IP" | tr '.' '-').sslip.io"
DESIGN_HOST="design.$HOST"
OD=/opt/open-design

[ -f /root/.claude/.credentials.json ] || {
  echo "ERROR: Claude is not logged in yet. Run 'bash login-claude.sh' first."; exit 1; }

echo "==> [1/7] Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh >/dev/null 2>&1
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
echo "    docker $(docker --version | head -c 25)"

echo "==> [2/7] Clone open-design"
command -v git >/dev/null || { apt-get update >/dev/null 2>&1; apt-get install -y git >/dev/null 2>&1; }
[ -d "$OD" ] || git clone --depth 1 https://github.com/nexu-io/open-design "$OD"

echo "==> [3/7] Complete the Russian translation (best-effort)"
RU="$OD/apps/web/src/i18n/locales/ru.ts"
if [ -f "$RU" ]; then
  python3 "$HERE/open-design/apply-ru.py" "$HERE/open-design/ru_translations.json" "$RU" || echo "    (ru apply skipped)"
else
  echo "    ru.ts not found (upstream layout changed) — Russian still switchable in-app via the 文A toggle"
fi

echo "==> [4/7] Build image from source (heavy — be patient)"
cd "$OD"
docker build -t open-design-base:latest -f deploy/Dockerfile .
# glibc layer: the npm claude binary is glibc-linked, base image is Alpine (musl).
docker build -t open-design-local:latest - <<'DOCKER'
FROM open-design-base:latest
USER root
RUN apk add --no-cache libc6-compat || true
USER 1001
DOCKER

echo "==> [5/7] Host helpers (claude launcher + isolated creds copy)"
mkdir -p /opt/od-claude-bin /opt/od-claude-home
ln -sf /usr/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /opt/od-claude-bin/claude
cp -f /root/.claude/.credentials.json /opt/od-claude-home/.credentials.json
[ -f /root/.claude/settings.json ] && cp -f /root/.claude/settings.json /opt/od-claude-home/settings.json || true
chown -R 1001:1001 /opt/od-claude-home
chmod 700 /opt/od-claude-home
chmod 600 /opt/od-claude-home/.credentials.json

WITH_CODEX=0
if [ "$ENABLE_CODEX" = "yes" ]; then
  echo "    Codex (GPT image via ChatGPT): installing @openai/codex…"
  command -v codex >/dev/null || npm install -g @openai/codex >/dev/null 2>&1 || echo "    (codex npm install failed — GPT image will be off)"
  if command -v codex >/dev/null; then
    mkdir -p /opt/od-codex-home && chown -R 1001:1001 /opt/od-codex-home && chmod 700 /opt/od-codex-home
    WITH_CODEX=1
  fi
fi

echo "==> [6/7] Config + compose"
DEP="$OD/deploy"
sed "s#__DESIGN_DOMAIN__#$DESIGN_HOST#g" "$HERE/open-design/.env.example" > "$DEP/.env"
cp "$HERE/open-design/docker-compose.linux.yml" "$DEP/docker-compose.linux.yml"
if [ "$WITH_CODEX" = "0" ]; then
  # Strip the two Codex bind-mount lines so docker does not create empty phantom mounts.
  sed -i '/host-codex-bin:ro/d; /od-codex-home:\/home\/open-design\/.codex:rw/d' "$DEP/docker-compose.linux.yml"
fi

echo "==> [7/7] Start"
cd "$DEP"
HOME=/root docker compose -f docker-compose.yml -f docker-compose.linux.yml up -d --no-build
sleep 8
if curl -sf "http://127.0.0.1:7456/api/health" >/dev/null 2>&1; then
  echo "    open-design: healthy on 127.0.0.1:7456"
else
  echo "    open-design: not healthy yet — check: HOME=/root docker compose -f docker-compose.yml -f docker-compose.linux.yml logs --tail=40"
fi

cat <<EOF

==================================================================
  CLAUDE DESIGN DONE.
  Opens from the "Claude Design" button in the panel's left menu.
  URL (has the access key — keep private): $(cat /root/claudecode-kit/design-url.txt 2>/dev/null)
$([ "$ENABLE_CODEX" = yes ] && echo "
  For GPT image generation, log into ChatGPT next:
      bash $HERE/login-codex.sh
  (then re-create the container so it picks up the login:
      cd $DEP && HOME=/root docker compose -f docker-compose.yml -f docker-compose.linux.yml up -d --force-recreate)")
==================================================================
EOF
