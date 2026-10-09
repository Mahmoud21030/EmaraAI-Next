#!/bin/sh
# EmaraAI Next - install on Linux / macOS (a VPS, a home server, a laptop). Run from the repository root:
#   sh next/scripts/install.sh            then:  ~/.emaraai-next/venv/bin/emaraai-next serve
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
command -v git >/dev/null || { echo "git is required"; exit 1; }
python3 -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ is required'"
VENV="$HOME/.emaraai-next/venv"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install "$ROOT"
"$VENV/bin/emaraai-next" setup ${EMARAAI_SETUP_YES:+--yes}
if command -v systemctl >/dev/null && [ "${EMARAAI_SYSTEMD:-1}" = 1 ]; then
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/emaraai-next.service" <<UNIT
[Unit]
Description=EmaraAI Next node
After=network-online.target
[Service]
WorkingDirectory=$HOME/.emaraai-next
ExecStart=$VENV/bin/emaraai-next serve
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
UNIT
  echo "To run it as a service:  systemctl --user enable --now emaraai-next"
fi
echo "Installed. Start it with:  $VENV/bin/emaraai-next serve"
