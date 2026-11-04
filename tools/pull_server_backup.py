r"""Copy the server's newest catalog backup to this PC (OneDrive by default) and keep the newest 30 copies here.

    .venv\Scripts\python.exe tools\pull_server_backup.py

Run every morning by the "Game catalog bot backup" task in Windows Task Scheduler, after the server's own 04:00 UTC
backup. It keeps a copy of the catalog away from the server, and it's the catalog to start from when the bot moves
back to this PC (docs/SERVER.md). Uses the `bot-server` entry in ~/.ssh/config; set BACKUP_SERVER and PULL_BACKUP_DIR
in .env to change where it copies from and to.
"""

import os
import subprocess
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

SERVER = os.getenv("BACKUP_SERVER") or "bot-server"
SERVER_DIR = "/var/backups/mads-bo-wacky"
TARGET_DIR = os.getenv("PULL_BACKUP_DIR") or os.path.join(os.getenv("OneDrive") or ROOT, "Mads-bo-wacky backups",
                                                          "from the server")
KEEP = 30


def ssh(*args: str) -> str:
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", SERVER, *args],
                          capture_output=True, text=True, check=True).stdout


def main():
    newest = sorted(f for f in ssh("ls", SERVER_DIR).split() if f.startswith("catalog-") and f.endswith(".db"))
    if not newest:
        sys.exit(f"No backups on {SERVER} yet")
    name = newest[-1]
    os.makedirs(TARGET_DIR, exist_ok=True)
    target = os.path.join(TARGET_DIR, name)
    if os.path.exists(target):
        print(f"Already have {name}")
    else:
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{SERVER}:{SERVER_DIR}/{name}", target], check=True)
        print(f"Copied {name} to {TARGET_DIR}")
    for f in sorted(f for f in os.listdir(TARGET_DIR) if f.startswith("catalog-") and f.endswith(".db"))[:-KEEP]:
        os.remove(os.path.join(TARGET_DIR, f))
        print(f"Removed {f}")


if __name__ == "__main__":
    main()
