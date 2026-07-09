#!/usr/bin/env bash
# Optional: log into ChatGPT (Codex CLI) so Claude Design can generate GPT images on a
# ChatGPT subscription (no OpenAI API key). Runs ON the server. This is the ONE fiddly
# step: ChatGPT's login redirects to a localhost port on the machine running the
# browser, so a headless server needs an SSH port-forward.
#
# HOW TO (the agent does the tunnel; the human only opens a link):
#   1) The agent (Claude) opens the callback tunnel from its OWN shell on the user's
#      computer, in the background:
#          ssh -f -N -L 1455:localhost:1455 root@<SERVER_IP>
#   2) The agent runs this script on the server:
#          ssh root@<SERVER_IP> 'bash /root/claudecode-kit-src/login-codex.sh'
#      It runs `codex login` and prints a URL.
#   3) The HUMAN opens that URL in their browser (with VPN if ChatGPT is blocked in
#      their region) and signs into THEIR ChatGPT. The redirect to localhost:1455 is
#      tunneled back to the server, and codex saves the login.
#   4) The script moves the login into Claude Design's isolated home and recreates the
#      container. The agent then closes the tunnel (pkill -f '1455:localhost:1455').
set -uo pipefail
export CODEX_HOME=/opt/od-codex-home
mkdir -p "$CODEX_HOME"

command -v codex >/dev/null || { echo "codex not installed. Run setup-open-design.sh with ENABLE_CODEX=yes first."; exit 1; }

echo "==> Starting 'codex login' (open the printed URL in your laptop browser)…"
echo "    If it hangs waiting for the browser, that means the localhost:1455 tunnel"
echo "    is not set up — see the header of this script."
codex login || { echo "codex login did not complete."; exit 1; }

# Hand the login to the isolated Claude Design copy and refresh the container.
chown -R 1001:1001 "$CODEX_HOME" 2>/dev/null || true
chmod 700 "$CODEX_HOME" 2>/dev/null || true
echo "==> Recreating Claude Design container to pick up the ChatGPT login…"
DEP=/opt/open-design/deploy
if [ -d "$DEP" ]; then
  cd "$DEP" && HOME=/root docker compose -f docker-compose.yml -f docker-compose.linux.yml up -d --force-recreate
fi
echo "DONE. Test a GPT image inside Claude Design."
