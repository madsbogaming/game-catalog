"""SQLite storage for the game catalog."""

import json
import sqlite3
import time
from collections import Counter

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    key           TEXT    NOT NULL,           -- 'steam:<appid>' or 'ig:<shortcode>'
    kind          TEXT    NOT NULL,           -- 'steam' | 'battlenet' | 'instagram'
    url           TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    description   TEXT,
    image         TEXT,
    developer     TEXT,
    genres        TEXT    NOT NULL DEFAULT '[]',  -- JSON lists
    tags          TEXT    NOT NULL DEFAULT '[]',
    custom_tags   TEXT    NOT NULL DEFAULT '[]',
    categories    TEXT    NOT NULL DEFAULT '[]',
    clips         TEXT    NOT NULL DEFAULT '[]',
    coop          INTEGER NOT NULL DEFAULT 0,
    multiplayer   INTEGER NOT NULL DEFAULT 0,
    release_date  TEXT,
    release_ts    INTEGER,
    price_cents   INTEGER,
    price_text    TEXT,
    demo_appid    INTEGER,                    -- Steam app id of the game's free demo, if it has one
    discount_pct  INTEGER NOT NULL DEFAULT 0, -- Steam sale, e.g. 50 for -50% (price_text is then the sale price)
    price_full_text TEXT,                     -- the normal price while on sale
    discount_ends INTEGER,                    -- unix time the Steam sale ends (NULL when not on sale or unknown)
    review_pct    INTEGER,
    review_desc   TEXT,
    review_count  INTEGER,
    note          TEXT,
    status        TEXT    NOT NULL DEFAULT 'want',
    added_by      INTEGER,
    added_by_name TEXT,
    added_at      INTEGER NOT NULL,
    channel_id    INTEGER,
    message_id    INTEGER,
    UNIQUE (guild_id, key)
);
CREATE TABLE IF NOT EXISTS interest (
    entry_id  INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    user_name TEXT,
    PRIMARY KEY (entry_id, user_id)
);
-- Who is playing which game right now: marked by the person in /catalog, or by the bot when Discord or Steam shows it
CREATE TABLE IF NOT EXISTS playing (
    entry_id  INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    user_name TEXT,
    since     INTEGER NOT NULL,
    seen      INTEGER,          -- marked by the bot: the last time it saw them playing (NULL = marked by the person)
    PRIMARY KEY (entry_id, user_id)
);
-- Free-to-keep Steam promotions the bot has already posted (so each one is posted once).
CREATE TABLE IF NOT EXISTS free_posts (
    guild_id  INTEGER NOT NULL,
    appid     INTEGER NOT NULL,
    posted_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, appid)
);
-- Each person's 1-5 star rating of a played game
CREATE TABLE IF NOT EXISTS ratings (
    entry_id  INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    user_name TEXT,
    stars     INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    PRIMARY KEY (entry_id, user_id)
);
-- People the bot has seen playing a game in Discord (so their activity sharing works)
CREATE TABLE IF NOT EXISTS seen_playing (
    user_id   INTEGER PRIMARY KEY,
    last_seen INTEGER NOT NULL
);
-- Who refunded which game on Steam (each person marks it themselves)
CREATE TABLE IF NOT EXISTS refunds (
    entry_id  INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    user_name TEXT,
    at        INTEGER NOT NULL,
    PRIMARY KEY (entry_id, user_id)
);
-- Which catalog game the bot has seen each person playing in Discord (shown as "played" on their rating)
CREATE TABLE IF NOT EXISTS seen_games (
    entry_id  INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (entry_id, user_id)
);
-- Steam accounts people linked to Discord (read when they sign in to /catalog), and their hours per game
CREATE TABLE IF NOT EXISTS steam_accounts (
    user_id   INTEGER PRIMARY KEY,
    steam_id  TEXT    NOT NULL
);
-- Time the bot saw each person playing each catalog game in Discord ("Playing …"), counted every few minutes:
-- hours for games Steam can't tell about (Battle.net games, or no linked Steam)
CREATE TABLE IF NOT EXISTS tracked_playtime (
    entry_id  INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    minutes   INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (entry_id, user_id)
);
-- Every game in each linked Steam library (also never-played ones): a game that disappears was likely refunded
CREATE TABLE IF NOT EXISTS steam_owned (
    user_id   INTEGER NOT NULL,
    appid     INTEGER NOT NULL,
    PRIMARY KEY (user_id, appid)
);
CREATE TABLE IF NOT EXISTS steam_playtime (
    user_id     INTEGER NOT NULL,
    appid       INTEGER NOT NULL,
    minutes     INTEGER NOT NULL,
    last_played INTEGER,
    PRIMARY KEY (user_id, appid)
);
-- What each person answered in "Have you played these?" (the catch-up for Played games): 1 played it, 0 didn't
CREATE TABLE IF NOT EXISTS played_answers (
    entry_id  INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    played    INTEGER NOT NULL,
    at        INTEGER NOT NULL,
    PRIMARY KEY (entry_id, user_id)
);
-- "Get on the game" (#GOTG): the bot's daily afternoon post (gotg.py), the games it picked and who pressed "I'm in"
CREATE TABLE IF NOT EXISTS gotg_posts (
    day        TEXT    PRIMARY KEY,           -- local date, e.g. '2026-11-03'
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    picks      TEXT    NOT NULL               -- JSON [[entry id, 'owned' | 'buy'], ...]
);
CREATE TABLE IF NOT EXISTS gotg_in (
    day       TEXT    NOT NULL,
    entry_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    at        INTEGER NOT NULL,
    PRIMARY KEY (day, entry_id, user_id)
);
-- Small bits of bot state, e.g. when it was last online (to catch up on what it missed)
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- When someone last tagged a friend from /catalog (one tag per friend every 30 minutes).
CREATE TABLE IF NOT EXISTS tags (
    sender_id INTEGER NOT NULL,
    target_id INTEGER NOT NULL,
    tagged_at INTEGER NOT NULL,
    PRIMARY KEY (sender_id, target_id)
);
-- Every Discord message that referenced an entry, so reactions on it count as interest.
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER NOT NULL,
    entry_id   INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    PRIMARY KEY (message_id, entry_id)
);
-- Achievements people earned (achievements.py), the highest tier reached; "announced" once the bot posted it
CREATE TABLE IF NOT EXISTS achievements (
    user_id   INTEGER NOT NULL,
    key       TEXT    NOT NULL,
    tier      INTEGER NOT NULL,
    at        INTEGER NOT NULL,
    announced INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, key)
);
"""

JSON_FIELDS = {"genres", "tags", "custom_tags", "categories", "clips"}
COLUMNS = {
    "kind", "key", "url", "title", "description", "image", "developer", "genres", "tags",
    "custom_tags", "categories", "clips", "coop", "multiplayer", "release_date", "release_ts",
    "price_cents", "price_text", "discount_pct", "price_full_text", "discount_ends", "demo_appid", "review_pct", "review_desc", "review_count", "note", "status",
    "added_by", "added_by_name", "added_at", "channel_id", "message_id",
}

# Group-wide status of a game. Who is playing it right now is tracked per person (the playing table).
STATUSES = {
    "want": ("📌", "Not played yet"),
    "demo": ("🧪", "Demo played"),  # only tried the demo, so still counts as not played yet
    "played": ("✅", "Played"),
}  # refunding is personal (the refunds table), not a status

# A game becomes Played once this many people have played it. Games marked Played by hand before that rule keep
# their status (nothing is ever moved out of Played automatically).
PLAYERS_NEEDED = 3
PLAYED_MIN_MINUTES = 30  # Steam hours (or time seen in Discord) below this don't count as having played it
# Who played which game without saying so: a rating (not of a demo), a refund, the bot seeing them play it in
# Discord, or Steam hours of the game itself (not its demo)
PLAYED_AUTO_SQL = f"""
    SELECT r.entry_id, r.user_id FROM ratings r JOIN entries e ON e.id = r.entry_id WHERE e.status != 'demo'
    UNION SELECT entry_id, user_id FROM refunds
    UNION SELECT entry_id, user_id FROM seen_games
    UNION SELECT entry_id, user_id FROM tracked_playtime WHERE minutes >= {PLAYED_MIN_MINUTES}
    UNION SELECT e.id, p.user_id FROM entries e JOIN steam_playtime p ON e.key = 'steam:' || p.appid
          WHERE p.minutes >= {PLAYED_MIN_MINUTES}"""
# ... plus everyone who said "I played it" themselves
PLAYED_SQL = PLAYED_AUTO_SQL + "\n    UNION SELECT entry_id, user_id FROM played_answers WHERE played = 1"

SORTS = {
    "newest": "e.added_at DESC",
    "oldest": "e.added_at ASC",
    "name": "e.title COLLATE NOCASE ASC",
    "interest": "interest DESC, e.added_at DESC",
    "rating": "e.review_pct IS NULL, e.review_pct DESC, e.review_count DESC",
    "price": "e.price_cents IS NULL, e.price_cents ASC",
    "release": "e.release_ts IS NULL, e.release_ts DESC",
}

SELECT = """
SELECT e.*, (SELECT COUNT(*) FROM interest i WHERE i.entry_id = e.id) AS interest
FROM entries e
"""


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self._quiet = 0  # rows written that open catalog pages don't show (heartbeat, presence notes, ...)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(entries)")}
        for column, kind in (("demo_appid", "INTEGER"), ("discount_pct", "INTEGER NOT NULL DEFAULT 0"),
                             ("price_full_text", "TEXT"), ("past_demo_appid", "INTEGER"), ("discount_ends", "INTEGER")):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE entries ADD COLUMN {column} {kind}")
        # Playing marks used to stay until the person took them off. Which ones the bot set wasn't kept, so all the
        # old ones count as the bot's: they go once nobody is seen playing (the bot marks real players again)
        if "seen" not in {r[1] for r in self.conn.execute("PRAGMA table_info(playing)")}:
            self.conn.execute("ALTER TABLE playing ADD COLUMN seen INTEGER")
            self.conn.execute("UPDATE playing SET seen = since")
        # The last demo a game had, kept after Steam removes it, so "Demo played" can still be picked for it
        self.conn.executescript("""
            UPDATE entries SET past_demo_appid = demo_appid WHERE demo_appid IS NOT NULL AND past_demo_appid IS NOT demo_appid;
            CREATE TRIGGER IF NOT EXISTS keep_demo_insert AFTER INSERT ON entries WHEN NEW.demo_appid IS NOT NULL
            BEGIN UPDATE entries SET past_demo_appid = NEW.demo_appid WHERE id = NEW.id; END;
            CREATE TRIGGER IF NOT EXISTS keep_demo_update AFTER UPDATE OF demo_appid ON entries WHEN NEW.demo_appid IS NOT NULL
            BEGIN UPDATE entries SET past_demo_appid = NEW.demo_appid WHERE id = NEW.id; END;
        """)
        # "Refunded" used to be a group status; refunds are per person now. Those games were bought and tried: played.
        self.conn.execute("UPDATE entries SET status = 'played' WHERE status = 'dropped'")
        # "Playing" used to be a group status; it's per person now, so those games go back to "want".
        self.conn.execute("UPDATE entries SET status = 'want' WHERE status NOT IN (%s)" % ",".join("?" * len(STATUSES)),
                          tuple(STATUSES))
        self.conn.commit()
        # The games that were Played when a game started needing PLAYERS_NEEDED players: they may stay Played
        # with fewer, and can be put back to Played by hand if someone moves them out by mistake
        if self.get_meta("played_before_rule") is None:
            ids = [r[0] for r in self.conn.execute("SELECT id FROM entries WHERE status = 'played' ORDER BY id")]
            self.set_meta("played_before_rule", json.dumps(ids))

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        d = dict(row)
        for f in JSON_FIELDS & d.keys():
            d[f] = json.loads(d[f] or "[]")
        return d

    @staticmethod
    def _encode(fields: dict) -> dict:
        unknown = fields.keys() - COLUMNS
        if unknown:
            raise ValueError(f"unknown columns: {unknown}")
        return {
            k: json.dumps(v) if k in JSON_FIELDS else int(v) if isinstance(v, bool) else v
            for k, v in fields.items()
        }

    # -- entries -----------------------------------------------------------

    def get(self, entry_id: int):
        return self._decode(self.conn.execute(SELECT + "WHERE e.id = ?", (entry_id,)).fetchone())

    def get_by_key(self, guild_id: int, key: str):
        row = self.conn.execute(SELECT + "WHERE e.guild_id = ? AND e.key = ?", (guild_id, key)).fetchone()
        return self._decode(row)

    def find_by_clip(self, guild_id: int, url: str):
        row = self.conn.execute(
            SELECT + "WHERE e.guild_id = ? AND EXISTS (SELECT 1 FROM json_each(e.clips) WHERE value = ?)",
            (guild_id, url),
        ).fetchone()
        return self._decode(row)

    def insert(self, guild_id: int, **fields) -> dict:
        fields = self._encode(fields)
        cols = ", ".join(["guild_id", *fields])
        marks = ", ".join("?" * (len(fields) + 1))
        cur = self.conn.execute(f"INSERT INTO entries ({cols}) VALUES ({marks})", (guild_id, *fields.values()))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def update(self, entry_id: int, **fields):
        if not fields:
            return
        fields = self._encode(fields)
        assignments = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE entries SET {assignments} WHERE id = ?", (*fields.values(), entry_id))
        self.conn.commit()

    def delete(self, entry_id: int):
        self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self.conn.commit()

    def add_clip(self, entry_id: int, url: str):
        entry = self.get(entry_id)
        if url not in entry["clips"]:
            self.update(entry_id, clips=entry["clips"] + [url])

    def merge(self, src_id: int, dst_id: int):
        """Fold entry `src` (e.g. an unidentified reel) into `dst` (the Steam game it shows)."""
        if src_id == dst_id:
            return
        src, dst = self.get(src_id), self.get(dst_id)
        clips = list(dict.fromkeys(dst["clips"] + [src["url"]] + src["clips"]))
        custom = list(dict.fromkeys(dst["custom_tags"] + src["custom_tags"]))
        fields = {"clips": clips, "custom_tags": custom}
        if src["added_at"] < dst["added_at"]:
            for k in ("added_at", "added_by", "added_by_name", "channel_id", "message_id"):
                fields[k] = src[k]
        if not dst["note"] and src["note"]:
            fields["note"] = src["note"]
        self.update(dst_id, **fields)
        self.conn.execute(
            "INSERT OR IGNORE INTO interest SELECT ?, user_id, user_name FROM interest WHERE entry_id = ?",
            (dst_id, src_id),
        )
        self.conn.execute(
            "INSERT OR IGNORE INTO messages SELECT message_id, ? FROM messages WHERE entry_id = ?",
            (dst_id, src_id),
        )
        self.delete(src_id)

    # -- interest & messages -----------------------------------------------

    def add_interest(self, entry_id: int, user_id: int, user_name: str | None = None):
        self.conn.execute(
            """INSERT INTO interest (entry_id, user_id, user_name) VALUES (?, ?, ?)
               ON CONFLICT (entry_id, user_id) DO UPDATE SET user_name = COALESCE(excluded.user_name, user_name)""",
            (entry_id, user_id, user_name),
        )
        self.conn.commit()

    def remove_interest(self, entry_id: int, user_id: int):
        self.conn.execute("DELETE FROM interest WHERE entry_id = ? AND user_id = ?", (entry_id, user_id))
        self.conn.commit()

    def interested(self, entry_id: int) -> list[tuple[int, str | None]]:
        rows = self.conn.execute("SELECT user_id, user_name FROM interest WHERE entry_id = ?", (entry_id,))
        return [tuple(r) for r in rows]

    def set_playing(self, entry_id: int, user_id: int, user_name: str | None, playing: bool, when: int,
                    by_bot: bool = False):
        """Mark someone as playing (or not). A mark the bot sets goes again once it stops seeing them play."""
        if playing:
            self.conn.execute("INSERT OR REPLACE INTO playing VALUES (?, ?, ?, ?, ?)",
                              (entry_id, user_id, user_name, when, when if by_bot else None))
        else:
            self.conn.execute("DELETE FROM playing WHERE entry_id = ? AND user_id = ?", (entry_id, user_id))
        self.conn.commit()

    def still_playing(self, entry_id: int, user_id: int, when: int):
        """The bot saw them playing it again: keeps its mark (marks people set themselves aren't touched)."""
        self._write_quietly("UPDATE playing SET seen = ? WHERE entry_id = ? AND user_id = ? AND seen IS NOT NULL",
                            (when, entry_id, user_id))

    def end_stale_playing(self, before: int) -> list[sqlite3.Row]:
        """Take off the bot's marks of people it hasn't seen playing since `before`; returns them."""
        rows = self.conn.execute("SELECT p.user_name, e.title FROM playing p JOIN entries e ON e.id = p.entry_id "
                                 "WHERE p.seen < ?", (before,)).fetchall()
        if rows:
            self.conn.execute("DELETE FROM playing WHERE seen < ?", (before,))
            self.conn.commit()
        return rows

    def set_rating(self, entry_id: int, user_id: int, user_name: str | None, stars: int):
        """stars 1-5 rates the game; 0 removes the rating."""
        if stars:
            self.conn.execute("INSERT OR REPLACE INTO ratings VALUES (?, ?, ?, ?)", (entry_id, user_id, user_name, stars))
        else:
            self.conn.execute("DELETE FROM ratings WHERE entry_id = ? AND user_id = ?", (entry_id, user_id))
        self.conn.commit()

    def set_refund(self, entry_id: int, user_id: int, user_name: str | None, refunded: bool, when: int):
        if refunded:
            self.conn.execute("INSERT OR REPLACE INTO refunds VALUES (?, ?, ?, ?)", (entry_id, user_id, user_name, when))
        else:
            self.conn.execute("DELETE FROM refunds WHERE entry_id = ? AND user_id = ?", (entry_id, user_id))
        self.conn.commit()

    def refunds(self, entry_id: int) -> list[tuple[int, str | None]]:
        rows = self.conn.execute("SELECT user_id, user_name FROM refunds WHERE entry_id = ? ORDER BY at", (entry_id,))
        return [tuple(r) for r in rows]

    def ratings(self, entry_id: int) -> list[tuple[int, int]]:
        return [tuple(r) for r in self.conn.execute("SELECT user_id, stars FROM ratings WHERE entry_id = ?", (entry_id,))]

    # -- who played what (a game is Played once PLAYERS_NEEDED people have) --

    def set_played_answer(self, entry_id: int, user_id: int, played: bool | None, when: int):
        """`user_id` says they played the game (True), didn't (False), or takes their answer back (None)."""
        if played is None:
            self.conn.execute("DELETE FROM played_answers WHERE entry_id = ? AND user_id = ?", (entry_id, user_id))
        else:
            self.conn.execute("INSERT OR REPLACE INTO played_answers VALUES (?, ?, ?, ?)",
                              (entry_id, user_id, int(played), when))
        self.conn.commit()

    def played_before_rule(self) -> set[int]:
        return set(json.loads(self.get_meta("played_before_rule") or "[]"))

    def played_by(self, entry_id: int) -> list[int]:
        return [r[0] for r in self.conn.execute(f"SELECT user_id FROM ({PLAYED_SQL}) WHERE entry_id = ?", (entry_id,))]

    def played_map(self) -> dict[int, dict[str, set[int]]]:
        """Entry id -> {"by": everyone who played it, "auto": those who didn't have to say so themselves,
        "not": those who said they didn't}. Only games anyone has played or answered for are in it."""
        out: dict[int, dict[str, set[int]]] = {}
        get = lambda eid: out.setdefault(eid, {"by": set(), "auto": set(), "not": set()})
        for eid, uid in self.conn.execute(PLAYED_SQL):
            get(eid)["by"].add(uid)
        for eid, uid in self.conn.execute(PLAYED_AUTO_SQL):
            get(eid)["auto"].add(uid)
        for eid, uid in self.conn.execute("SELECT entry_id, user_id FROM played_answers WHERE played = 0"):
            get(eid)["not"].add(uid)
        return out

    def promote_played(self) -> list[dict]:
        """Mark every game PLAYERS_NEEDED or more people have played as Played. -> the games that just became Played."""
        rows = self.conn.execute(f"""SELECT e.id, e.title FROM entries e WHERE e.status != 'played'
            AND (SELECT COUNT(*) FROM ({PLAYED_SQL}) p WHERE p.entry_id = e.id) >= ?""", (PLAYERS_NEEDED,)).fetchall()
        if rows:
            self.conn.executemany("UPDATE entries SET status = 'played' WHERE id = ?", [(r["id"],) for r in rows])
            self.conn.commit()
        return [dict(r) for r in rows]

    def catch_up_games(self, guild_id: int, user_id: int) -> list[dict]:
        """Played games with fewer than PLAYERS_NEEDED players that `user_id` hasn't played (as far as we know)
        and hasn't answered for yet: what "Have you played these?" asks them. Closest to the rule first."""
        played = self.played_map()
        games = []
        for e in self.query(guild_id, status="played", sort="name"):
            p = played.get(e["id"], {"by": set(), "not": set()})
            if len(p["by"]) < PLAYERS_NEEDED and user_id not in p["by"] and user_id not in p["not"]:
                games.append({**e, "played_by": sorted(p["by"])})
        return sorted(games, key=lambda e: -len(e["played_by"]))

    def played_progress(self, guild_id: int) -> tuple[int, int]:
        """(Played games with PLAYERS_NEEDED or more players, all Played games)."""
        played = self.played_map()
        games = [e["id"] for e in self.query(guild_id, status="played")]
        return sum(len(played.get(eid, {}).get("by", ())) >= PLAYERS_NEEDED for eid in games), len(games)

    @property
    def version(self) -> int:
        """Changes whenever something an open catalog page shows changes (it reloads its data then)."""
        return self.conn.total_changes - self._quiet

    def _write_quietly(self, sql: str, params=()):
        """A write that doesn't change what the catalog page shows, so open pages don't reload for it."""
        before = self.conn.total_changes
        self.conn.execute(sql, params)
        self.conn.commit()
        self._quiet += self.conn.total_changes - before

    # -- achievements --

    def achievement_tier(self, user_id: int, key: str) -> int:
        row = self.conn.execute("SELECT tier FROM achievements WHERE user_id = ? AND key = ?", (user_id, key)).fetchone()
        return row[0] if row else 0

    def save_achievement(self, user_id: int, key: str, tier: int, at: int, *, announced: bool):
        self.conn.execute("INSERT OR REPLACE INTO achievements VALUES (?, ?, ?, ?, ?)",
                          (user_id, key, tier, at, int(announced)))
        self.conn.commit()

    def move_title(self, old: int | None, new: int | None, at: int, *, announced: bool):
        """The Biggest refunder title goes from `old` to `new` (either can be None)."""
        self.conn.execute("DELETE FROM achievements WHERE key = 'biggest_refunder'")
        if new:
            self.conn.execute("INSERT INTO achievements VALUES (?, 'biggest_refunder', 1, ?, ?)", (new, at, int(announced)))
        self.conn.commit()

    def achievements(self, user_id: int) -> list[tuple[str, int, int]]:
        """(key, tier, earned at) of everything `user_id` has earned."""
        return [tuple(r) for r in self.conn.execute("SELECT key, tier, at FROM achievements WHERE user_id = ?", (user_id,))]

    def unannounced(self) -> dict[int, list[tuple[str, int]]]:
        """Earned but not posted yet: user id -> [(key, tier)], oldest first."""
        out: dict[int, list[tuple[str, int]]] = {}
        for uid, key, tier in self.conn.execute("SELECT user_id, key, tier FROM achievements WHERE announced = 0 ORDER BY at"):
            out.setdefault(uid, []).append((key, tier))
        return out

    def mark_announced(self, user_id: int, keys: list[str]):
        self._write_quietly(f"UPDATE achievements SET announced = 1 WHERE user_id = ? AND key IN ({','.join('?' * len(keys))})",
                            (user_id, *keys))

    def saw_playing(self, user_id: int, when: int):
        self._write_quietly("INSERT OR REPLACE INTO seen_playing VALUES (?, ?)", (user_id, when))

    def seen_playing(self) -> list[int]:
        return [r[0] for r in self.conn.execute("SELECT user_id FROM seen_playing")]

    def saw_game(self, entry_id: int, user_id: int, when: int):
        if user_id in self.seen_in_game(entry_id):  # already has the badge: only the time changes
            self._write_quietly("UPDATE seen_games SET last_seen = ? WHERE entry_id = ? AND user_id = ?",
                                (when, entry_id, user_id))
            return
        self.conn.execute("INSERT INTO seen_games VALUES (?, ?, ?)", (entry_id, user_id, when))
        self.conn.commit()

    def seen_in_game(self, entry_id: int) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT user_id FROM seen_games WHERE entry_id = ?", (entry_id,))}

    def set_steam_account(self, user_id: int, steam_id: str):
        self._write_quietly("INSERT OR REPLACE INTO steam_accounts VALUES (?, ?)", (user_id, steam_id))

    def steam_accounts(self) -> dict[int, str]:
        return dict(self.conn.execute("SELECT user_id, steam_id FROM steam_accounts").fetchall())

    def set_playtime(self, user_id: int, games: dict[int, tuple[int, int | None]]):
        """Replace `user_id`'s Steam hours with `games` (appid -> (minutes played, last played))."""
        old = dict(self.conn.execute("SELECT appid, minutes FROM steam_playtime WHERE user_id = ?", (user_id,)).fetchall())
        if old == {appid: m for appid, (m, _) in games.items() if m}:
            return  # no new hours: don't make open pages reload
        self.conn.execute("DELETE FROM steam_playtime WHERE user_id = ?", (user_id,))
        self.conn.executemany("INSERT INTO steam_playtime VALUES (?, ?, ?, ?)",
                              [(user_id, appid, m, last) for appid, (m, last) in games.items() if m])
        self.conn.commit()

    def owners(self) -> dict[int, set[int]]:
        """Steam app id -> who has it in their linked Steam library."""
        owners: dict[int, set[int]] = {}
        for uid, appid in self.conn.execute("SELECT user_id, appid FROM steam_owned"):
            owners.setdefault(appid, set()).add(uid)
        return owners

    def last_played(self) -> dict[int, int]:
        """Entry id -> the last time anyone was seen playing it (Discord) or played it (Steam)."""
        rows = self.conn.execute("""
            SELECT entry_id, MAX(at) FROM (
                SELECT entry_id, last_seen AS at FROM seen_games
                UNION ALL SELECT entry_id, last_seen FROM tracked_playtime
                UNION ALL SELECT e.id, p.last_played FROM entries e JOIN steam_playtime p ON e.key = 'steam:' || p.appid)
            WHERE at IS NOT NULL GROUP BY entry_id""")
        return dict(rows.fetchall())

    def owned(self, user_id: int) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT appid FROM steam_owned WHERE user_id = ?", (user_id,))}

    def set_owned(self, user_id: int, appids: set[int]):
        before = self.conn.total_changes  # the page doesn't show libraries: open pages needn't reload for this
        self.conn.execute("DELETE FROM steam_owned WHERE user_id = ?", (user_id,))
        self.conn.executemany("INSERT INTO steam_owned VALUES (?, ?)", [(user_id, a) for a in appids])
        self.conn.commit()
        self._quiet += self.conn.total_changes - before

    def user_playtime(self, user_id: int) -> dict[int, int]:
        """Minutes `user_id` has in each game, as of the last check."""
        return dict(self.conn.execute("SELECT appid, minutes FROM steam_playtime WHERE user_id = ?", (user_id,)).fetchall())

    def steam_hours_users(self) -> list[int]:
        """People whose Steam hours the bot could load."""
        return [r[0] for r in self.conn.execute("SELECT DISTINCT user_id FROM steam_playtime")]

    def playtime(self, appids: list[int]) -> dict[int, int]:
        """Minutes on Steam per person, summed over `appids` (a game and its demo)."""
        marks = ",".join("?" * len(appids))
        rows = self.conn.execute(f"SELECT user_id, SUM(minutes) FROM steam_playtime WHERE appid IN ({marks}) "
                                 "GROUP BY user_id", appids) if appids else []
        return {uid: m for uid, m in rows}

    def add_tracked_playtime(self, entry_id: int, user_id: int, minutes: int, now: int):
        self._write_quietly(
            "INSERT INTO tracked_playtime VALUES (?, ?, ?, ?) ON CONFLICT (entry_id, user_id) DO UPDATE "
            "SET minutes = minutes + excluded.minutes, last_seen = excluded.last_seen", (entry_id, user_id, minutes, now))

    def tracked_playtime(self, entry_id: int) -> dict[int, int]:
        """Minutes the bot saw each person playing this game in Discord."""
        return dict(self.conn.execute("SELECT user_id, minutes FROM tracked_playtime WHERE entry_id = ?",
                                      (entry_id,)).fetchall())

    def recent_players(self, since: int) -> dict[int, list[tuple[int, int]]]:
        """Who played each game since `since`, as seen on Discord or Steam or marked playing:
        entry id -> [(user id, last time)], most recent first."""
        rows = self.conn.execute("""
            SELECT entry_id, user_id, MAX(at) FROM (
                SELECT entry_id, user_id, last_seen AS at FROM seen_games
                UNION ALL SELECT entry_id, user_id, last_seen FROM tracked_playtime
                UNION ALL SELECT entry_id, user_id, COALESCE(seen, ?) FROM playing)
            WHERE at >= ? GROUP BY entry_id, user_id ORDER BY 3 DESC""", (int(time.time()), since))
        recent = {}
        for entry_id, user_id, at in rows:
            recent.setdefault(entry_id, []).append((user_id, at))
        return recent

    def players(self, entry_id: int) -> list[tuple[int, str | None]]:
        rows = self.conn.execute("SELECT user_id, user_name FROM playing WHERE entry_id = ? ORDER BY since",
                                 (entry_id,))
        return [tuple(r) for r in rows]

    def link_message(self, message_id: int, entry_id: int):
        self.conn.execute("INSERT OR IGNORE INTO messages VALUES (?, ?)", (message_id, entry_id))
        self.conn.commit()

    def entries_for_message(self, message_id: int) -> list[int]:
        rows = self.conn.execute("SELECT entry_id FROM messages WHERE message_id = ?", (message_id,))
        return [r[0] for r in rows]

    # -- free game posts ---------------------------------------------------

    def free_posted_since(self, guild_id: int, appid: int, since: int) -> bool:
        row = self.conn.execute("SELECT 1 FROM free_posts WHERE guild_id = ? AND appid = ? AND posted_at >= ?",
                                (guild_id, appid, since)).fetchone()
        return row is not None

    def mark_free_posted(self, guild_id: int, appid: int, when: int):
        self.conn.execute("INSERT OR REPLACE INTO free_posts VALUES (?, ?, ?)", (guild_id, appid, when))
        self.conn.commit()

    # -- Get on the game (#GOTG) ------------------------------------------

    def gotg_post(self, day: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM gotg_posts WHERE day = ?", (day,)).fetchone()
        return {**dict(row), "picks": json.loads(row["picks"])} if row else None

    def save_gotg_post(self, day: str, guild_id: int, picks: list[tuple[int, str]],
                       channel_id: int | None = None, message_id: int | None = None):
        self._write_quietly("INSERT OR REPLACE INTO gotg_posts VALUES (?, ?, ?, ?, ?)",
                            (day, guild_id, channel_id, message_id, json.dumps(picks)))

    def gotg_posted_since(self, day: str) -> set[int]:
        """Entry ids picked on `day` or later (they rest a while before they're picked again)."""
        rows = self.conn.execute("SELECT picks FROM gotg_posts WHERE day >= ?", (day,))
        return {entry_id for (picks,) in rows for entry_id, _ in json.loads(picks)}

    def gotg_in(self, day: str, entry_id: int) -> list[int]:
        """Who pressed "I'm in" for this game on `day`, first first."""
        rows = self.conn.execute("SELECT user_id FROM gotg_in WHERE day = ? AND entry_id = ? ORDER BY at, rowid", (day, entry_id))
        return [r[0] for r in rows]

    def set_gotg_in(self, day: str, entry_id: int, user_id: int, on: bool, when: int):
        if on:
            self._write_quietly("INSERT OR IGNORE INTO gotg_in VALUES (?, ?, ?, ?)", (day, entry_id, user_id, when))
        else:
            self._write_quietly("DELETE FROM gotg_in WHERE day = ? AND entry_id = ? AND user_id = ?",
                                (day, entry_id, user_id))

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value):
        self._write_quietly("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))

    def last_tagged(self, sender_id: int, target_id: int) -> int | None:
        row = self.conn.execute("SELECT tagged_at FROM tags WHERE sender_id = ? AND target_id = ?",
                                (sender_id, target_id)).fetchone()
        return row[0] if row else None

    def mark_tagged(self, sender_id: int, target_id: int, when: int):
        self._write_quietly("INSERT OR REPLACE INTO tags VALUES (?, ?, ?)", (sender_id, target_id, when))

    # -- browsing ----------------------------------------------------------

    def query(self, guild_id: int, *, sort="newest", genre=None, source="all", status=None,
              added_by=None, coop=False, text=None) -> list[dict]:
        where, args = ["e.guild_id = ?"], [guild_id]
        if genre:
            where.append("""EXISTS (SELECT 1 FROM (
                SELECT value FROM json_each(e.genres) UNION ALL
                SELECT value FROM json_each(e.tags) UNION ALL
                SELECT value FROM json_each(e.custom_tags)) WHERE lower(value) = lower(?))""")
            args.append(genre)
        if source == "steam":
            where.append("e.kind = 'steam'")
        elif source == "games":  # Steam and Battle.net games, not reels
            where.append("e.kind != 'instagram'")
        elif source == "reels":
            where.append("e.kind = 'instagram'")
        elif source == "clips":
            where.append("e.clips != '[]'")
        if status:  # one status or several
            statuses = (status,) if isinstance(status, str) else tuple(status)
            where.append(f"e.status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        if added_by:
            where.append("e.added_by = ?")
            args.append(added_by)
        if coop:
            where.append("e.coop = 1")
        if text:
            where.append("(e.title LIKE ? OR e.note LIKE ? OR e.description LIKE ?)")
            args += [f"%{text}%"] * 3
        order = SORTS.get(sort, SORTS["newest"])
        sql = SELECT + "WHERE " + " AND ".join(where) + f" ORDER BY {order}"
        return [self._decode(r) for r in self.conn.execute(sql, args)]

    def search(self, guild_id: int, text: str, kind: str | None = None, limit: int = 25) -> list[dict]:
        sql = SELECT + "WHERE e.guild_id = ? AND e.title LIKE ?"
        args = [guild_id, f"%{text}%"]
        if kind:
            sql += " AND e.kind = ?"
            args.append(kind)
        sql += " ORDER BY e.added_at DESC LIMIT ?"
        return [self._decode(r) for r in self.conn.execute(sql, (*args, limit))]

    def genre_counts(self, guild_id: int) -> tuple[Counter, Counter]:
        """(Steam genres + custom tags, Steam user tags) -> how many entries carry each."""
        genres, tags = Counter(), Counter()
        for e in self.query(guild_id):
            genres.update(set(e["genres"]) | set(e["custom_tags"]))
            tags.update(e["tags"])
        return genres, tags
