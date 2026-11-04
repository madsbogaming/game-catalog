#!/usr/bin/env bash
# Gets the code onto the server and installs the bot's services. Run as root, after setup.sh and the deploy key:
#
#     ssh bot-server 'bash -s' < deploy/install.sh
#
# The bot isn't started: .env and catalog.db are copied over first (see docs/SERVER.md). Running it again is safe.
set -euo pipefail
cd /opt/mads-bo-wacky

if [ ! -d .git ]; then
    sudo -u bot git clone git@github.com:madsbogaming/game-catalog.git .
fi
sudo -u bot python3 -m venv .venv
sudo -u bot .venv/bin/pip install -q -r requirements.txt

cp deploy/mads-bo-wacky.service deploy/mads-bo-wacky-backup.service deploy/mads-bo-wacky-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable mads-bo-wacky mads-bo-wacky-backup.timer
systemctl start mads-bo-wacky-backup.timer
echo "== Installed. Copy .env and catalog.db over, then: systemctl start mads-bo-wacky"
