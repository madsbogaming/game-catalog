"""Achievements: what they are, who has earned what (worked out from the catalog's own data), and the one title
that moves between people (Biggest refunder). The bot announces new ones (bot.py), the catalog shows them on
each profile (web.py, catalog.html), and tools/make_badges.py draws their badge pictures from this list."""

import time
from dataclasses import dataclass

TIER_COLORS = {"bronze": "#c47f3a", "silver": "#b8c0cc", "gold": "#f0b232"}
TITLE_COLOR = "#f04747"  # the Biggest refunder title: the one badge that can be taken from you
SECRET_COLOR = "#80848e"  # a secret one, as shown to people who haven't unlocked it themselves
ROMAN = ("I", "II", "III")


@dataclass(frozen=True)
class Achievement:
    key: str
    name: str
    icon: str              # an icon name from the catalog page's icon set
    desc: str              # what earned it; {n} is the tier's threshold
    thresholds: tuple      # the value needed for each tier
    tiers: tuple = ("bronze", "silver", "gold")
    secret: bool = False   # not mentioned anywhere until someone earns it

    def label(self, tier: int) -> str:
        return f"{self.name} {ROMAN[tier - 1]}" if len(self.thresholds) > 1 else self.name

    def color(self, tier: int) -> str:
        return TIER_COLORS[self.tiers[tier - 1]]

    def badge(self, tier: int) -> str:
        """File name of the badge picture in static/badges/."""
        return f"{self.key}-{tier}.png" if len(self.thresholds) > 1 else f"{self.key}.png"


ACHIEVEMENTS = [
    # Tiers set from the server's own numbers at launch (Nov 2026), so only one person starts at the top tier
    Achievement("explorer", "Explorer", "playlist-add", "Added {n} games to the catalog", (10, 50, 150)),
    Achievement("friendslopper", "Friendslopper", "users-group", "Played {n} different catalog games", (25, 50, 90)),
    Achievement("gamer", "Gamer", "hourglass", "{n} hours in catalog games", (500, 2500, 7500)),
    Achievement("big_spender", "Big spender", "coin", "Owns catalog games worth {n} €", (300, 750, 1250)),
    Achievement("first_refund", "First refund", "receipt-refund", "Refunded a catalog game", (1,), ("bronze",)),
    # Secret: other people's cards only show how many someone has, until you've unlocked the same one
    Achievement("trendsetter", "Trendsetter", "flame", "Added a game {n} people want to play", (7,), ("gold",), True),
    Achievement("tough_crowd", "Tough crowd", "star", "Gave {n} games 1 star", (10,), ("silver",), True),
    Achievement("buyers_remorse", "Buyer's remorse", "arrow-back-up", "Refunded a game they added themselves", (1,), ("bronze",), True),
    Achievement("early_bird", "Early bird", "calendar", "Added {n} games before they were released", (20,), ("silver",), True),
    Achievement("easy_to_please", "Easy to please", "heart", "Gave {n} games 5 stars", (25,), ("gold",), True),
    Achievement("wishlist_warrior", "Wishlist warrior", "device-gamepad-2", "Wants to play {n} catalog games", (50,), ("silver",), True),
]
BY_KEY = {a.key: a for a in ACHIEVEMENTS}
TITLE = Achievement("biggest_refunder", "Biggest refunder", "receipt-refund",
                    "Most refunds in the server ({n}). Can be taken", (1,), ("gold",))


def values(conn, bot_id: int | None) -> dict[int, dict[str, int]]:
    """Each person's number for every achievement: user id -> {achievement key -> value}."""
    out: dict[int, dict[str, int]] = {}

    def put(key, rows):
        for uid, value in rows:
            if uid and value:
                out.setdefault(uid, {})[key] = int(value)

    appid = "CAST(substr(e.key, 7) AS INTEGER)"  # "steam:123" -> 123
    put("explorer", conn.execute("SELECT added_by, COUNT(*) FROM entries e WHERE kind != 'instagram' "
                                 "AND added_by IS NOT NULL AND added_by != ? GROUP BY added_by", (bot_id or 0,)))
    # Played: Steam hours, time the bot saw them playing in Discord, or just seen playing it
    put("friendslopper", conn.execute(f"""
        SELECT user_id, COUNT(DISTINCT entry_id) FROM (
            SELECT p.user_id, e.id AS entry_id FROM steam_playtime p JOIN entries e
              ON e.key LIKE 'steam:%' AND {appid} = p.appid WHERE p.minutes > 0
            UNION SELECT user_id, entry_id FROM tracked_playtime WHERE minutes > 0
            UNION SELECT user_id, entry_id FROM seen_games) GROUP BY user_id"""))
    put("gamer", conn.execute(f"""
        SELECT user_id, SUM(minutes) / 60 FROM (
            SELECT p.user_id, p.minutes FROM steam_playtime p JOIN entries e ON e.key LIKE 'steam:%' AND {appid} = p.appid
            UNION ALL SELECT user_id, minutes FROM tracked_playtime) GROUP BY user_id"""))
    put("big_spender", conn.execute(f"""
        SELECT o.user_id, SUM(e.price_cents) / 100 FROM steam_owned o JOIN entries e
          ON e.key LIKE 'steam:%' AND {appid} = o.appid GROUP BY o.user_id"""))
    put("first_refund", conn.execute("SELECT user_id, COUNT(*) FROM refunds GROUP BY user_id"))
    put("trendsetter", conn.execute("""
        SELECT e.added_by, MAX((SELECT COUNT(*) FROM interest i WHERE i.entry_id = e.id)) FROM entries e
        WHERE e.added_by IS NOT NULL GROUP BY e.added_by"""))
    put("tough_crowd", conn.execute("SELECT user_id, COUNT(*) FROM ratings WHERE stars = 1 GROUP BY user_id"))
    put("buyers_remorse", conn.execute("SELECT r.user_id, COUNT(*) FROM refunds r JOIN entries e "
                                       "ON e.id = r.entry_id AND e.added_by = r.user_id GROUP BY r.user_id"))
    put("early_bird", conn.execute("SELECT added_by, COUNT(*) FROM entries WHERE release_ts IS NOT NULL "
                                   "AND added_at < release_ts GROUP BY added_by"))
    put("easy_to_please", conn.execute("SELECT user_id, COUNT(*) FROM ratings WHERE stars = 5 GROUP BY user_id"))
    put("wishlist_warrior", conn.execute("SELECT user_id, COUNT(*) FROM interest GROUP BY user_id"))
    return out


def tier_for(a: Achievement, value: int) -> int:
    return sum(value >= t for t in a.thresholds)


def refund_counts(conn) -> list[tuple[int, int, int]]:
    """(user id, refunds, when they made their latest one), most refunds first; ties: whoever got there first."""
    return conn.execute("SELECT user_id, COUNT(*), MAX(at) FROM refunds GROUP BY user_id "
                        "ORDER BY COUNT(*) DESC, MAX(at) ASC").fetchall()


def sync(db, bot_id: int | None, *, silent: bool) -> None:
    """Record newly earned achievements (and a new Biggest refunder). With `silent`, they're stored as already
    announced: the first run gives everyone what they had already earned without a single message. The same
    goes for an achievement added later: whoever already qualifies gets it quietly, only new unlocks are posted."""
    conn, now = db.conn, int(time.time())
    known = set((db.get_meta("achievement_keys") or "").split(","))
    for uid, vals in values(conn, bot_id).items():
        for key, value in vals.items():
            a = BY_KEY[key]
            tier = tier_for(a, value)
            if tier and tier > db.achievement_tier(uid, key):
                db.save_achievement(uid, key, tier, now, announced=silent or key not in known)
    db.set_meta("achievement_keys", ",".join(BY_KEY))
    counts = refund_counts(conn)
    holder = db.get_meta("refund_title")
    holder = int(holder) if holder else None
    held = next((n for uid, n, _ in counts if uid == holder), 0)
    best = counts[0] if counts else None
    # It only changes hands on MORE refunds than the holder has, so a tie never takes it
    if best and best[0] != holder and best[1] > held:
        db.move_title(holder, best[0], now, announced=silent)
        db.set_meta("refund_title", best[0])
        db.set_meta("refund_title_from", f"{holder or ''}:{held}")  # for the message: who had it, with how many
    elif holder and not held:  # the holder undid all their refunds
        db.move_title(holder, None, now, announced=True)
        db.set_meta("refund_title", "")


def earned(db, uid: int) -> list[dict]:
    """What one person has earned, for their profile: the title first, then by tier (gold first)."""
    out = []
    for key, tier, at in db.achievements(uid):
        if key == TITLE.key:
            n = next((n for u, n, _ in refund_counts(db.conn) if u == uid), 0)
            out.append({"key": key, "name": TITLE.name, "icon": TITLE.icon, "color": TITLE_COLOR, "title": True,
                        "desc": TITLE.desc.format(n=n), "at": at})
        elif key in BY_KEY:
            a = BY_KEY[key]
            out.append({"key": key, "name": a.label(tier), "icon": a.icon, "color": a.color(tier), "title": False,
                        "desc": a.desc.format(n=a.thresholds[tier - 1]), "secret": a.secret, "at": at,
                        "rank": ("bronze", "silver", "gold").index(a.tiers[tier - 1])})
    return sorted(out, key=lambda x: (not x["title"], -x.get("rank", 0), x["at"]))
