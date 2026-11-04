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
- **Backups:** every night at 04:00 UTC, `tools/backup_db.py` copies the catalog to `/var/backups/mads-bo-wacky` and keeps 30 copies. Hetzner's own backups (turned on for the server) keep copies of the whole server away from it.
- **The catalog's address:** Tailscale Funnel publishes port 8765 at the server's fixed `https://…ts.net` address. The firewall only lets SSH in.
- **Uptime:** with `HEALTHCHECK_URL` set, the bot pings Healthchecks.io every 5 minutes while it's connected to Discord, and Healthchecks.io emails when the pings stop.
- **Security updates** install themselves (unattended-upgrades).

## Setting up a new server

1. **Create the server** in the [Hetzner Cloud console](https://console.hetzner.cloud): Ubuntu 24.04, the smallest shared x86 plan, a location in Germany or Finland, the SSH key from `~/.ssh/id_ed25519_hetzner.pub`, and **Backups** turned on.
2. **Put its IP address** in `~/.ssh/config` on the PC (`HostName`), then check that `ssh bot-server` gets in.
3. **Prepare it:** `ssh bot-server 'bash -s' < deploy/setup.sh`. It prints a deploy key at the end.
4. **Let it read the repo:** add that key as a read-only deploy key: repo **Settings → Deploy keys**, or `gh api repos/madsbogaming/mads-bo-wacky/keys -f title=bot-server -f key="<the key>" -F read_only=true`.
5. **Install the bot:** `ssh bot-server 'bash -s' < deploy/install.sh`.
6. **Fixed address:** on the server, `tailscale up` (open the link it prints and sign in), then `tailscale funnel --bg 8765`. In the Tailscale admin page, turn off key expiry for the server.
7. **Move the data**, with the bot on the PC stopped so the catalog doesn't change halfway:
   - copy `.env` and `catalog.db` to `/opt/mads-bo-wacky/` (`scp .env catalog.db bot-server:/opt/mads-bo-wacky/`), then `chown bot:bot` them
   - in the server's `.env`: `PUBLIC_URL` = the server's new `https://…ts.net` address, `BACKUP_DIR=/var/backups/mads-bo-wacky`, and `HEALTHCHECK_URL` if you use it
8. **Start it:** `ssh bot-server systemctl start mads-bo-wacky`, and watch the log until it says "Logged in".
9. **Point Discord at the new address** in the Developer Portal: the Root Mapping under **Activities → URL Mappings**, and `https://<new address>/auth/callback` under **OAuth2 → Redirects**.
10. **Turn the PC off as the bot's home:** delete `Game catalog bot.vbs` from the Startup folder, stop `start-bot.bat`, and remove the **Game catalog bot backup** task from Task Scheduler.
11. **Update the privacy policy's** "Where it's stored" section to say the bot now runs on a Hetzner server in the EU.
