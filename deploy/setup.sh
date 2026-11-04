#!/usr/bin/env bash
# Sets up a fresh Ubuntu 24.04 server for the bot. Run once, as root, from the PC:
#
#     ssh bot-server 'bash -s' < deploy/setup.sh
#
# It installs what the bot needs, makes a "bot" user, and prints a deploy key to add to the GitHub repo. Running it
# again is safe. After it: add the deploy key, then run deploy/install.sh the same way (see docs/SERVER.md).
set -euo pipefail

echo "== Updates and packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq git python3 python3-venv sqlite3 unattended-upgrades ufw
dpkg-reconfigure -f noninteractive unattended-upgrades  # security updates install themselves

echo "== Firewall: only SSH in (the catalog goes out through Tailscale Funnel, so no web port is opened)"
ufw allow OpenSSH
ufw --force enable

echo "== Logs: keep at most 500 MB"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/size.conf
systemctl restart systemd-journald

echo "== The bot's user, folder and backup folder"
id bot >/dev/null 2>&1 || useradd --create-home --shell /bin/bash bot
install -d -o bot -g bot /opt/mads-bo-wacky /var/backups/mads-bo-wacky

echo "== Tailscale (for the catalog's fixed public address)"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

echo "== Deploy key, so the server can read the private repo"
sudo -u bot mkdir -p /home/bot/.ssh
[ -f /home/bot/.ssh/id_ed25519 ] || sudo -u bot ssh-keygen -q -t ed25519 -N "" -C "bot-server deploy key" -f /home/bot/.ssh/id_ed25519
sudo -u bot sh -c 'ssh-keyscan -t ed25519 github.com >> /home/bot/.ssh/known_hosts 2>/dev/null; sort -u -o /home/bot/.ssh/known_hosts /home/bot/.ssh/known_hosts'
echo "DEPLOY KEY: $(cat /home/bot/.ssh/id_ed25519.pub)"
echo "== Done. Next: add the deploy key to the repo (read-only), then run deploy/install.sh"
