#!/usr/bin/env bash
# ----------------------------------------------------------------------
#  J-Dawg Bot — VM-side installer.
#  Run this ONCE on the GCE VM (not in Cloud Shell).
#  It installs deps, sets up the venv, and registers a systemd service
#  that auto-restarts the bot and starts on boot.
# ----------------------------------------------------------------------
set -euo pipefail

REPO_URL="https://github.com/natehag12-stack/j-dawg-bot.git"
INSTALL_DIR="${INSTALL_DIR:-$HOME/j-dawg-bot}"
SERVICE_NAME="jdawgbot"
USER_NAME="$(whoami)"

echo "==> Installing system packages…"
sudo apt-get update -qq
sudo apt-get install -yqq python3 python3-venv python3-pip git tzdata

echo "==> Syncing repo to ${INSTALL_DIR}…"
if [ ! -d "${INSTALL_DIR}/.git" ]; then
    git clone "${REPO_URL}" "${INSTALL_DIR}"
else
    git -C "${INSTALL_DIR}" pull --ff-only
fi

cd "${INSTALL_DIR}"
mkdir -p logs

echo "==> Setting up virtual environment…"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
    cat <<MSG

================================================================
 .env not found at ${INSTALL_DIR}/.env

 Create it now:
     cp .env.example .env
     nano .env            # paste TELEGRAM_TOKEN + TELEGRAM_CHAT_ID

 Then re-run:
     bash deploy/setup.sh
================================================================
MSG
    exit 1
fi

echo "==> Writing systemd unit /etc/systemd/system/${SERVICE_NAME}.service…"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null <<EOF
[Unit]
Description=J-Dawg Bot (paper trading + Telegram alerts)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python -u main.py
Restart=always
RestartSec=10
StandardOutput=append:${INSTALL_DIR}/logs/bot.log
StandardError=append:${INSTALL_DIR}/logs/bot.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

cat <<MSG

================================================================
 J-Dawg Bot is live as systemd service '${SERVICE_NAME}'.

   Status:    sudo systemctl status ${SERVICE_NAME}
   Logs:      tail -f ${INSTALL_DIR}/logs/bot.log
   Restart:   sudo systemctl restart ${SERVICE_NAME}
   Stop:      sudo systemctl stop ${SERVICE_NAME}
   Update:    cd ${INSTALL_DIR} && git pull && sudo systemctl restart ${SERVICE_NAME}

 Send /status to your Telegram bot to confirm it is online.
================================================================
MSG
