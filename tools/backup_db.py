r"""Copy catalog.db to a backup folder (OneDrive by default) and keep the newest 30 copies.

    .venv\Scripts\python.exe tools\backup_db.py

Run every night by deploy/mads-bo-wacky-backup.timer on the server (or a Task Scheduler task on Windows). Safe
while the bot is running: SQLite's backup API copies a consistent snapshot. Set BACKUP_DIR in .env to back up
somewhere else.
"""

import os
import sqlite3
import sys
from datetime import datetime

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

DB_PATH = os.path.join(ROOT, os.getenv("DB_PATH") or "catalog.db")
BACKUP_DIR = os.getenv("BACKUP_DIR") or os.path.join(os.getenv("OneDrive") or ROOT, "Mads-bo-wacky backups")
KEEP = 30


def main():
    if not os.path.exists(DB_PATH):
        sys.exit(f"{DB_PATH} doesn't exist")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(BACKUP_DIR, f"catalog-{datetime.now():%Y%m%d-%H%M}.db")
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    print(f"Backed up to {target}")
    # Oldest first by name (the date is in it), so everything before the newest KEEP goes
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("catalog-") and f.endswith(".db"))[:-KEEP]
    for f in old:
        os.remove(os.path.join(BACKUP_DIR, f))
        print(f"Removed {f}")


if __name__ == "__main__":
    main()
