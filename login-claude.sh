#!/usr/bin/env bash
# Interactive Claude subscription login, driven over a tmux session so an agent (or a
# person over SSH) can do it step by step. Runs ON the server.
#
#   Step A:  bash login-claude.sh
#            -> starts the login, prints the OAuth URL. Open it in a browser WITH VPN,
#               sign into YOUR Claude account, copy the code shown after you approve.
#   Step B:  bash login-claude.sh <CODE>
#            -> pastes the code, finishes the login, verifies.
#
# Verify any time:  IS_SANDBOX=1 HOME=/root /usr/bin/claude -p "ok"   (should print ok)
set -uo pipefail
SESSION=login
CODE="${1:-}"

cap() { tmux capture-pane -t "$SESSION" -p 2>/dev/null; }

if [ -z "$CODE" ]; then
  # Fresh start: (re)create the tmux session running claude and open /login.
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  tmux new-session -d -s "$SESSION" -x 220 -y 50
  tmux send-keys -t "$SESSION" 'IS_SANDBOX=1 HOME=/root /usr/bin/claude' Enter
  sleep 6
  # Dismiss a possible "trust this folder?" prompt.
  tmux send-keys -t "$SESSION" Enter
  sleep 2
  tmux send-keys -t "$SESSION" '/login' Enter
  sleep 3
  # Choose option 1 = "Claude account with subscription".
  tmux send-keys -t "$SESSION" '1' Enter
  sleep 6
  echo "----------------------------------------------------------------"
  echo "Open this link in a browser WITH VPN, sign into YOUR Claude, then"
  echo "copy the code it gives you and run:  bash login-claude.sh <CODE>"
  echo "----------------------------------------------------------------"
  cap | grep -Eo 'https://[a-zA-Z0-9./?=&_%-]+' | grep -Ei 'anthropic|claude|oauth' | head -1
  echo ""
  echo "(If no link appeared above, show the raw screen with:"
  echo "   tmux capture-pane -t $SESSION -p )"
else
  # Paste the code into the running session.
  tmux has-session -t "$SESSION" 2>/dev/null || { echo "No '$SESSION' session. Run 'bash login-claude.sh' first."; exit 1; }
  tmux send-keys -t "$SESSION" -l "$CODE"
  tmux send-keys -t "$SESSION" Enter
  sleep 6
  tmux kill-session -t "$SESSION" 2>/dev/null || true
  echo "==> Verifying login (headless)…"
  if OUT=$(IS_SANDBOX=1 HOME=/root /usr/bin/claude -p "ok" 2>&1) && echo "$OUT" | grep -qi ok; then
    echo "LOGIN OK — Claude answered: $OUT"
    echo "If Claude Design is installed, refresh its copy of the login now:"
    echo "  systemctl start od-creds-sync.service 2>/dev/null || true"
    systemctl start od-creds-sync.service 2>/dev/null || true
  else
    echo "LOGIN NOT CONFIRMED. Screen said:"; echo "$OUT"
    echo "Re-run 'bash login-claude.sh' and try again."
    exit 1
  fi
fi
