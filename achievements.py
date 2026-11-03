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


def values(conn, bot_id: int | None, guild_id: int) -> dict[int, dict[str, int]]:
    """Each person's number for every achievement in one server: user id -> {achievement key -> value}.
    Only that server's games count, and Steam libraries and hours only for people in it."""
    out: dict[int, dict[str, int]] = {}

    def put(key, rows):
        for uid, value in rows:
            if uid and value:
                out.setdefault(uid, {})[key] = int(value)

    g = (guild_id,)
    appid = "CAST(substr(e.key, 7) AS INTEGER)"  # "steam:123" -> 123
    # Steam data of the server's own people, for the server's own Steam games
    steam = (f"JOIN entries e ON e.key LIKE 'steam:%' AND {appid} = {{t}}.appid AND e.guild_id = ? "
             "JOIN guild_members m ON m.guild_id = e.guild_id AND m.user_id = {t}.user_id")
    in_guild = "JOIN entries e ON e.id = {t}.entry_id AND e.guild_id = ?"  # tables of one game each
    put("explorer", conn.execute("SELECT added_by, COUNT(*) FROM entries e WHERE guild_id = ? AND kind != 'instagram' "
                                 "AND added_by IS NOT NULL AND added_by != ? GROUP BY added_by", (guild_id, bot_id or 0)))
    # Played: Steam hours, time the bot saw them playing in Discord, or just seen playing it
    put("friendslopper", conn.execute(f"""
        SELECT user_id, COUNT(DISTINCT entry_id) FROM (
            SELECT p.user_id, e.id AS entry_id FROM steam_playtime p {steam.format(t="p")} WHERE p.minutes > 0
            UNION SELECT t.user_id, t.entry_id FROM tracked_playtime t {in_guild.format(t="t")} WHERE t.minutes > 0
            UNION SELECT s.user_id, s.entry_id FROM seen_games s {in_guild.format(t="s")}) GROUP BY user_id""", g * 3))
    put("gamer", conn.execute(f"""
        SELECT user_id, SUM(minutes) / 60 FROM (
            SELECT p.user_id, p.minutes FROM steam_playtime p {steam.format(t="p")}
            UNION ALL SELECT t.user_id, t.minutes FROM tracked_playtime t {in_guild.format(t="t")}) GROUP BY user_id""",
                                g * 2))
    put("big_spender", conn.execute(f"SELECT o.user_id, SUM(e.price_cents) / 100 FROM steam_owned o {steam.format(t='o')} "
                                    "GROUP BY o.user_id", g))
    put("first_refund", conn.execute(f"SELECT r.user_id, COUNT(*) FROM refunds r {in_guild.format(t='r')} "
                                     "GROUP BY r.user_id", g))
    put("trendsetter", conn.execute("""
        SELECT e.added_by, MAX((SELECT COUNT(*) FROM interest i WHERE i.entry_id = e.id)) FROM entries e
        WHERE e.guild_id = ? AND e.added_by IS NOT NULL GROUP BY e.added_by""", g))
    put("tough_crowd", conn.execute(f"SELECT r.user_id, COUNT(*) FROM ratings r {in_guild.format(t='r')} "
                                    "WHERE r.stars = 1 GROUP BY r.user_id", g))
    put("buyers_remorse", conn.execute(f"SELECT r.user_id, COUNT(*) FROM refunds r {in_guild.format(t='r')} "
                                       "WHERE e.added_by = r.user_id GROUP BY r.user_id", g))
    put("early_bird", conn.execute("SELECT added_by, COUNT(*) FROM entries WHERE guild_id = ? AND release_ts IS NOT NULL "
                                   "AND added_at < release_ts GROUP BY added_by", g))
    put("easy_to_please", conn.execute(f"SELECT r.user_id, COUNT(*) FROM ratings r {in_guild.format(t='r')} "
                                       "WHERE r.stars = 5 GROUP BY r.user_id", g))
    put("wishlist_warrior", conn.execute(f"SELECT i.user_id, COUNT(*) FROM interest i {in_guild.format(t='i')} "
                                         "GROUP BY i.user_id", g))
    return out


def tier_for(a: Achievement, value: int) -> int:
    return sum(value >= t for t in a.thresholds)


def refund_counts(conn, guild_id: int) -> list[tuple[int, int, int]]:
    """(user id, refunds of the server's games, when they made their latest one), most refunds first; ties: whoever
    got there first."""
    return conn.execute("SELECT r.user_id, COUNT(*), MAX(r.at) FROM refunds r JOIN entries e ON e.id = r.entry_id "
                        "WHERE e.guild_id = ? GROUP BY r.user_id ORDER BY COUNT(*) DESC, MAX(r.at) ASC",
                        (guild_id,)).fetchall()


def sync(db, bot_id: int | None, guild_id: int, *, silent: bool) -> None:
    """Record newly earned achievements (and a new Biggest refunder) in one server. With `silent`, they're stored as
    already announced: the first run gives everyone what they had already earned without a single message. The same
    goes for an achievement added later: whoever already qualifies gets it quietly, only new unlocks are posted."""
    conn, now = db.conn, int(time.time())
    known = set((db.get_guild_meta(guild_id, "achievement_keys") or "").split(","))
    for uid, vals in values(conn, bot_id, guild_id).items():
        for key, value in vals.items():
            a = BY_KEY[key]
            tier = tier_for(a, value)
            if tier and tier > db.achievement_tier(guild_id, uid, key):
                db.save_achievement(guild_id, uid, key, tier, now, announced=silent or key not in known)
    db.set_guild_meta(guild_id, "achievement_keys", ",".join(BY_KEY))
    counts = refund_counts(conn, guild_id)
    holder = db.get_guild_meta(guild_id, "refund_title")
    holder = int(holder) if holder else None
    held = next((n for uid, n, _ in counts if uid == holder), 0)
    best = counts[0] if counts else None
    # It only changes hands on MORE refunds than the holder has, so a tie never takes it
    if best and best[0] != holder and best[1] > held:
        db.move_title(guild_id, holder, best[0], now, announced=silent)
        db.set_guild_meta(guild_id, "refund_title", best[0])
        # for the message: who had it, with how many
        db.set_guild_meta(guild_id, "refund_title_from", f"{holder or ''}:{held}")
    elif holder and not held:  # the holder undid all their refunds
        db.move_title(guild_id, holder, None, now, announced=True)
        db.set_guild_meta(guild_id, "refund_title", "")


def earned(db, guild_id: int, uid: int) -> list[dict]:
    """What one person has earned in the server, for their profile: the title first, then by tier (gold first)."""
    out = []
    for key, tier, at in db.achievements(guild_id, uid):
        if key == TITLE.key:
            n = next((n for u, n, _ in refund_counts(db.conn, guild_id) if u == uid), 0)
            out.append({"key": key, "name": TITLE.name, "icon": TITLE.icon, "color": TITLE_COLOR, "title": True,
                        "desc": TITLE.desc.format(n=n), "at": at})
        elif key in BY_KEY:
            a = BY_KEY[key]
            out.append({"key": key, "name": a.label(tier), "icon": a.icon, "color": a.color(tier), "title": False,
                        "desc": a.desc.format(n=a.thresholds[tier - 1]), "secret": a.secret, "at": at,
                        "rank": ("bronze", "silver", "gold").index(a.tiers[tier - 1])})
    return sorted(out, key=lambda x: (not x["title"], -x.get("rank", 0), x["at"]))
