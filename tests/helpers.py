"""Small helpers shared by the tests: a fresh in-memory catalog and quick ways to add games to it."""

import atexit

from db import Database

GUILD_A, GUILD_B = 111, 222
_opened: list[Database] = []
atexit.register(lambda: [db.conn.close() for db in _opened])  # closed, not just dropped, so Python doesn't warn


def new_db() -> Database:
    db = Database(":memory:")
    _opened.append(db)
    return db


def add_steam(db: Database, guild_id: int, appid: int, title: str | None = None, **fields) -> dict:
    """A Steam game in `guild_id`'s catalog (co-op and already released unless said otherwise)."""
    fields.setdefault("coop", True)
    fields.setdefault("added_at", 1_700_000_000)
    return db.insert(guild_id, key=f"steam:{appid}", kind="steam", url=f"https://store.steampowered.com/app/{appid}/",
                     title=title or f"Game {appid}", **fields)
