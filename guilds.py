"""Per-server settings: which channels the bot reads, where it posts, and who its admins are, in every server it's in.

Admins set them with /setup, and they're kept in the database (guild_meta). Until a server sets one, the .env value
counts, but only for the server it belongs to: a channel ID always belongs to one server, and ADMIN_IDS belong to the
home server (GUILD_ID, or every server when GUILD_ID is empty). So a bot that ran for one server keeps working exactly
as before, and a server added later starts with nothing watched until its admins run /setup.
"""

import json
from dataclasses import dataclass, field
from typing import Callable

# Where the bot posts things on its own: setting -> what /setup calls it
POSTS = {
    "free_games": "Free Steam games",
    "deals": "Weekly deep discounts",
    "achievements": "Achievements",
    "played_rule": "Played rule posts",
    "gotg": "#GOTG (daily games to play)",
}
# Without a setting of its own, a kind of post goes where another one goes (free games: the first watched channel)
FALLBACK = {"deals": "free_games", "played_rule": "achievements"}


def parse_ids(text: str | None) -> list[int]:
    """'1, 2,3' -> [1, 2, 3] in the order given, each once."""
    return list(dict.fromkeys(int(p) for p in (text or "").replace(" ", "").split(",") if p))


def parse_channel(text: str | None) -> int | None:
    """A channel setting from .env: None when it isn't set, 0 when it's turned off."""
    text = (text or "").strip()
    return int(text) if text else None


@dataclass
class Defaults:
    """The .env settings, used by servers that haven't set their own."""
    home_guild: int = 0                                 # GUILD_ID; 0: every server counts as home
    watch: list[int] = field(default_factory=list)      # WATCH_CHANNEL_IDS
    read: list[int] = field(default_factory=list)       # READ_CHANNEL_IDS
    posts: dict[str, int | None] = field(default_factory=dict)  # e.g. "deals" -> DEALS_CHANNEL_ID
    admins: set[int] = field(default_factory=set)       # ADMIN_IDS


class Settings:
    def __init__(self, db, defaults: Defaults, guild_of: Callable[[int], int | None]):
        """`guild_of(channel id)` -> the id of the server that channel is in (None: the bot can't see it)."""
        self.db, self.defaults, self.guild_of = db, defaults, guild_of

    def is_home(self, guild_id: int) -> bool:
        return not self.defaults.home_guild or guild_id == self.defaults.home_guild

    def _belongs(self, guild_id: int, channel_id: int) -> bool:
        """Whether a channel from .env is in this server. One the bot can't see (e.g. an archived thread) counts for
        the home server."""
        where = self.guild_of(channel_id)
        return where == guild_id or (where is None and self.is_home(guild_id))

    def _stored(self, guild_id: int, key: str):
        value = self.db.get_guild_meta(guild_id, f"setting:{key}")
        return json.loads(value) if value is not None else None

    def _store(self, guild_id: int, key: str, value):
        self.db.set_guild_meta(guild_id, f"setting:{key}", None if value is None else json.dumps(value))

    # -- reading --

    def watch_channels(self, guild_id: int) -> list[int]:
        """The channels whose game links go into the server's catalog (threads inside them count too)."""
        stored = self._stored(guild_id, "watch")
        if stored is not None:
            return stored
        return [c for c in self.defaults.watch if self._belongs(guild_id, c)]

    def read_channels(self, guild_id: int) -> set[int]:
        """Every channel or thread the bot reads in the server: the watched ones plus the extra ones."""
        stored = self._stored(guild_id, "read")
        extra = stored if stored is not None else [c for c in self.defaults.read if self._belongs(guild_id, c)]
        return set(self.watch_channels(guild_id)) | set(extra)

    def may_read(self, guild_id: int, channel_id: int, parent_id: int | None = None) -> bool:
        """Only the read channels, and threads inside the watched channels."""
        return channel_id in self.read_channels(guild_id) or (parent_id is not None
                                                              and parent_id in self.watch_channels(guild_id))

    def watches(self, guild_id: int, channel_id: int, parent_id: int | None = None) -> bool:
        """Whether game links posted here go into the catalog."""
        watched = self.watch_channels(guild_id)
        return channel_id in watched or (parent_id is not None and parent_id in watched)

    def post_channel(self, guild_id: int, kind: str) -> int | None:
        """The channel the server's `kind` posts go to. 0: turned off. None (only #GOTG): not set, so the bot looks
        for a channel by its name."""
        stored = self._stored(guild_id, kind)
        if stored is not None:
            return stored
        env = self.defaults.posts.get(kind)
        if env == 0 and self.is_home(guild_id):
            return 0
        if env and self._belongs(guild_id, env):
            return env
        if kind == "gotg":
            return None
        if kind in FALLBACK:
            return self.post_channel(guild_id, FALLBACK[kind])
        return next(iter(self.watch_channels(guild_id)), 0)

    def admins(self, guild_id: int) -> set[int]:
        stored = self._stored(guild_id, "admins")
        if stored is not None:
            return set(stored)
        return set(self.defaults.admins) if self.is_home(guild_id) else set()

    def is_admin(self, guild_id: int | None, user_id: int, can_manage_messages: bool) -> bool:
        """The server's admins may use every command; with none set, its mods (Manage Messages) may."""
        admins = self.admins(guild_id) if guild_id else set(self.defaults.admins)
        return user_id in admins if admins else bool(can_manage_messages)

    # -- changing (from /setup) --

    def set_watched(self, guild_id: int, channel_id: int, on: bool) -> list[int]:
        channels = [c for c in self.watch_channels(guild_id) if c != channel_id] + ([channel_id] if on else [])
        self._store(guild_id, "watch", channels)
        return channels

    def set_read(self, guild_id: int, channel_id: int, on: bool) -> set[int]:
        stored = self._stored(guild_id, "read")
        extra = stored if stored is not None else [c for c in self.defaults.read if self._belongs(guild_id, c)]
        self._store(guild_id, "read", [c for c in extra if c != channel_id] + ([channel_id] if on else []))
        return self.read_channels(guild_id)

    def set_post_channel(self, guild_id: int, kind: str, channel_id: int | None):
        """A channel id, 0 to turn those posts off, or None to go back to the default."""
        if kind not in POSTS:
            raise ValueError(f"unknown kind of post: {kind}")
        self._store(guild_id, kind, channel_id)

    def set_admin(self, guild_id: int, user_id: int, on: bool) -> set[int]:
        admins = (self.admins(guild_id) - {user_id}) | ({user_id} if on else set())
        self._store(guild_id, "admins", sorted(admins))
        return admins
