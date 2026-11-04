# Running the bot on a server

How the bot runs around the clock on a small Hetzner server (Ubuntu 24.04), and how to put new code live. The scripts are in [`deploy/`](../deploy).

## Everyday

| To | Run from the PC |
|---|---|
| Put the newest `main` live (about 30 seconds offline) | `ssh bot-server 'bash -s' < deploy/update.sh` |
| Watch the log | `ssh bot-server journalctl -u mads-bo-wacky -f` |
| See if it's running | `ssh bot-server systemctl status mads-bo-wacky` |
| Restart it | `ssh bot-server systemctl restart mads-bo-wacky` |
| List the backups | `ssh bot-server ls /var/backups/mads-bo-wacky` |

`bot-server` is the name in `~/.ssh/config` on the PC, with the server's IP address and the SSH key `~/.ssh/id_ed25519_hetzner`.

## What runs where

- **The bot:** the `mads-bo-wacky` service, as the user `bot`, from `/opt/mads-bo-wacky`. It starts at boot and again 30 seconds after it stops.
- **Its settings and catalog:** `/opt/mads-bo-wacky/.env` and `catalog.db`. Neither is in git.
- **Backups:** every night at 04:00 UTC, `tools/backup_db.py` copies the catalog to `/var/backups/mads-bo-wacky` and keeps 30 copies. Hetzner's own backups (turned on for the server) keep copies of the whole server away from it, and every morning at 07:00 the PC copies the newest catalog backup to OneDrive (`tools/pull_server_backup.py`), if it's on.
- **The catalog's address:** Tailscale Funnel publishes port 8765 at the server's fixed `https://…ts.net` address. The firewall only lets SSH in.
- **Uptime:** with `HEALTHCHECK_URL` set, the bot pings Healthchecks.io every 5 minutes while it's connected to Discord, and Healthchecks.io emails when the pings stop.
- **Security updates** install themselves (unattended-upgrades).

## Setting up a new server

1. **Create the server** in the [Hetzner Cloud console](https://console.hetzner.cloud): Ubuntu 24.04, the smallest shared x86 plan, a location in Germany or Finland, the SSH key from `~/.ssh/id_ed25519_hetzner.pub`, and **Backups** turned on.
2. **Put its IP address** in `~/.ssh/config` on the PC (`HostName`), then check that `ssh bot-server` gets in.
3. **Prepare it:** `ssh bot-server 'bash -s' < deploy/setup.sh`. It prints a deploy key at the end.
4. **Let it read the repo:** add that key as a read-only deploy key: repo **Settings → Deploy keys**, or `gh api repos/madsbogaming/game-catalog/keys -f title=bot-server -f key="<the key>" -F read_only=true`.
5. **Install the bot:** `ssh bot-server 'bash -s' < deploy/install.sh`.
6. **Fixed address:** on the server, `tailscale up` (open the link it prints and sign in), then `tailscale funnel --bg 8765`. In the Tailscale admin page, turn off key expiry for the server.
7. **Move the data**, with the bot on the PC stopped so the catalog doesn't change halfway:
   - copy `.env` and `catalog.db` to `/opt/mads-bo-wacky/` (`scp .env catalog.db bot-server:/opt/mads-bo-wacky/`), then `chown bot:bot` them
   - in the server's `.env`: `PUBLIC_URL` = the server's new `https://…ts.net` address, `BACKUP_DIR=/var/backups/mads-bo-wacky`, and `HEALTHCHECK_URL` if you use it
8. **Start it:** `ssh bot-server systemctl start mads-bo-wacky`, and watch the log until it says "Logged in".
9. **Point Discord at the new address** in the Developer Portal: the Root Mapping under **Activities → URL Mappings**, and `https://<new address>/auth/callback` under **OAuth2 → Redirects**.
10. **Turn the PC off as the bot's home:** stop `start-bot.bat`, rename `Game catalog bot.vbs` in the Startup folder to `Game catalog bot.vbs.disabled` (kept for moving back), and change the **Game catalog bot backup** task in Task Scheduler to run `tools\pull_server_backup.py` every morning.
11. **Update the privacy policy's** "Where it's stored" section to say the bot now runs on a Hetzner server in the EU.

## Moving back to the PC

For when the server subscription ends, or the server breaks. The PC keeps everything it needs: the code (`git pull`), its own `.env` (still with the PC's address), and every morning a copy of the server's catalog in `OneDrive\Mads-bo-wacky backups\from the server`. Do the steps in this order, so the two bots never run at the same time.

1. **Stop the bot on the server for good:** `ssh bot-server systemctl disable --now mads-bo-wacky`.
2. **Bring the catalog home.** If the server still answers, take the live one: `scp bot-server:/opt/mads-bo-wacky/catalog.db .` in the project folder. If it's already gone, copy the newest file from `OneDrive\Mads-bo-wacky backups\from the server` to the project folder and rename it `catalog.db`. That one is at most a day old.
3. **Get the newest code and packages:** `git pull`, then `.venv\Scripts\pip install -r requirements.txt`.
4. **Start the bot on the PC:** rename `Game catalog bot.vbs.disabled` in the Startup folder (`shell:startup`) back to `Game catalog bot.vbs` and double-click it. `bot.log` should say "Logged in" within a minute.
5. **The PC's fixed address:** check that `tailscale funnel status` on the PC shows port 8765. If not, run `tailscale funnel --bg 8765`.
6. **Point Discord back at the PC** in the Developer Portal: the Root Mapping under **Activities → URL Mappings** to the PC's `…ts.net` address (it's `PUBLIC_URL` in the PC's `.env`), and its `/auth/callback` under **OAuth2 → Redirects**.
7. **Back up on the PC again:** in Task Scheduler, change the **Game catalog bot backup** task to run `tools\backup_db.py` instead of `tools\pull_server_backup.py`.
8. **Update the privacy policy's** "Where it's stored" section to say the bot runs on the operator's own computer again.
9. **Cancel the server** in the Hetzner console (**Delete** on the server). Its Hetzner backups go with it, so only do this once step 2 is done.
