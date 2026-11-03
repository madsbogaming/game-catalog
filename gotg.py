"""Get on the game (#GOTG): which games the bot's daily afternoon post suggests playing together tonight.

Two groups: games enough of you already own (any co-op or multiplayer game in the catalog, Played ones too, going by
the Steam libraries linked to Discord), and games worth buying (the most wanted ones not enough of you own yet).
A posted game rests a week, the owned ones are from different genres, and close calls change from day to day."""

import random
import time

from db import Database

OWNED_PICKS = 3
BUY_PICKS = 2
MIN_OWNERS = 3   # an owned pick is in at least this many linked Steam libraries
MIN_WANT = 2     # a game to buy is wanted by at least this many
REST_DAYS = 7    # a posted game isn't picked again for this long
PLAYERS_TO_PING = 3  # once this many press "I'm in" on a game, the others who have it (or want it) are pinged

# Tags that say how a game is played rather than what it is: skipped when telling genres apart
GENERIC_TAGS = {"Multiplayer", "Online Co-Op", "Co-op", "Early Access", "Free to Play", "Casual", "Indie", "PvP",
                "PvE", "Action", "Adventure", "Singleplayer", "Massively Multiplayer", "Local Co-Op", "First-Person",
                "Third Person", "Funny", "Great Soundtrack"}


def main_genre(e: dict) -> str:
    """What kind of game it is, e.g. "Horror" or "Survival": its most voted tag that isn't generic."""
    return next((t for t in e["tags"] + e["genres"] if t not in GENERIC_TAGS), "")


def released(e: dict, now: float) -> bool:
    # A vague date ("Coming soon", "Q1 2027") has no timestamp and isn't out yet
    return e["release_ts"] <= now if e["release_ts"] else not e["release_date"]


def appid(e: dict) -> int | None:
    return int(e["key"].split(":")[1]) if e["kind"] == "steam" else None


def pick(db: Database, guild_id: int, day: str, rest_from: str, now: float | None = None) -> list[tuple[dict, str]]:
    """Today's games: [(entry, "owned" | "buy")], OWNED_PICKS owned ones first, then up to BUY_PICKS to buy.
    `day` seeds the day's small shuffle; games posted on `rest_from` or later are left out."""
    now = now or time.time()
    jitter = random.Random(day)
    rested = db.gotg_posted_since(rest_from)
    owners, last_played = db.owners(), db.last_played()
    games = [e for e in db.query(guild_id, source="games")
             if e["id"] not in rested and released(e, now) and (e["coop"] or e["multiplayer"])
             and len(db.players(e["id"])) < 2]  # a game already being played together needs no nudge
    ratings = {e["id"]: [s for _, s in db.ratings(e["id"])] for e in games}

    def owned_score(e):
        stars = ratings[e["id"]]
        days_idle = (now - last_played[e["id"]]) / 86400 if e["id"] in last_played else 30
        return (2 * len(owners.get(appid(e), ())) + e["interest"] + (sum(stars) / len(stars) if stars else 2.5)
                + min(days_idle, 30) / 10 + jitter.uniform(0, 2))

    owned = sorted((e for e in games if len(owners.get(appid(e), ())) >= MIN_OWNERS), key=owned_score, reverse=True)
    picks, genres = [], set()
    for e in owned:  # different kinds of game first...
        if len(picks) < OWNED_PICKS and main_genre(e) not in genres:
            picks.append(e)
            genres.add(main_genre(e))
    for e in owned:  # ...then the best of the rest, if there aren't enough kinds
        if len(picks) < OWNED_PICKS and e not in picks:
            picks.append(e)

    buy = sorted((e for e in games if e["status"] in ("want", "demo") and e["interest"] >= MIN_WANT
                  and len(owners.get(appid(e), ())) < MIN_OWNERS),
                 key=lambda e: (3 * e["interest"] + (e["discount_pct"] or 0) / 40 + (e["review_pct"] or 50) / 50
                                + jitter.uniform(0, 1.5)), reverse=True)
    return [(e, "owned") for e in picks] + [(e, "buy") for e in buy[:BUY_PICKS]]
