#!/usr/bin/env bash
# Puts the newest code from GitHub's main branch live: pull, install packages, restart (about 30 seconds offline).
# Run as root from the PC:
#
#     ssh bot-server 'bash -s' < deploy/update.sh
set -euo pipefail
cd /opt/mads-bo-wacky
sudo -u bot git pull --ff-only
sudo -u bot .venv/bin/pip install -q -r requirements.txt
cp deploy/mads-bo-wacky.service deploy/mads-bo-wacky-backup.service deploy/mads-bo-wacky-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl restart mads-bo-wacky
sleep 5
systemctl --no-pager --lines=5 status mads-bo-wacky
echo "== Now running $(sudo -u bot git log --oneline -1)"
