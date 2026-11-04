"""Per-server settings: which channels the bot reads, where it posts, who its admins are, and the country (for Steam
prices) and time zone (for the daily and weekly posts), in every server it's in.

Admins set them with /setup, and they're kept in the database (guild_meta). Until a server sets one, the .env value
counts, but only for the server it belongs to: a channel ID always belongs to one server, and ADMIN_IDS belong to the
home server (GUILD_ID, or every server when GUILD_ID is empty). So a bot that ran for one server keeps working exactly
as before, and a server added later starts with nothing watched until its admins run /setup.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

# What a server added later starts with, until its admins pick their own with /setup country and /setup timezone
OTHER_COUNTRY = "us"
OTHER_TIMEZONE = "UTC"

# Where the bot posts things on its own: setting -> what /setup calls it
POSTS = {
    "free_games": "Free Steam games",
    "deals": "Weekly deep discounts",
    "achievements": "Achievements",
    "played_rule": "Played rule posts",
    "gotg": "#GOTG (daily games to play)",
    "updates": "Bot updates (new features and fixes)",
}
# Posts no server gets until its admins pick a channel for them
OPT_IN = {"updates"}
# Without a setting of its own, a kind of post goes where another one goes (free games: the first watched channel)
FALLBACK = {"deals": "free_games", "played_rule": "achievements"}

# A server's language (Discord's "preferred locale") -> the country and time zone the setup form suggests.
# Only a guess: many servers keep the default en-US whatever country they're in, so the form lets it be changed.
LOCALES = {
    "bg": ("bg", "Europe/Sofia"), "cs": ("cz", "Europe/Prague"), "da": ("dk", "Europe/Copenhagen"),
    "de": ("de", "Europe/Berlin"), "el": ("gr", "Europe/Athens"), "en-GB": ("gb", "Europe/London"),
    "en-US": ("us", "America/New_York"), "es-ES": ("es", "Europe/Madrid"), "es-419": ("mx", "America/Mexico_City"),
    "fi": ("fi", "Europe/Helsinki"), "fr": ("fr", "Europe/Paris"), "hi": ("in", "Asia/Kolkata"),
    "hr": ("hr", "Europe/Zagreb"), "hu": ("hu", "Europe/Budapest"), "id": ("id", "Asia/Jakarta"),
    "it": ("it", "Europe/Rome"), "ja": ("jp", "Asia/Tokyo"), "ko": ("kr", "Asia/Seoul"),
    "lt": ("lt", "Europe/Vilnius"), "nl": ("nl", "Europe/Amsterdam"), "no": ("no", "Europe/Oslo"),
    "pl": ("pl", "Europe/Warsaw"), "pt-BR": ("br", "America/Sao_Paulo"), "ro": ("ro", "Europe/Bucharest"),
    "ru": ("ru", "Europe/Moscow"), "sv-SE": ("se", "Europe/Stockholm"), "th": ("th", "Asia/Bangkok"),
    "tr": ("tr", "Europe/Istanbul"), "uk": ("ua", "Europe/Kyiv"), "vi": ("vn", "Asia/Ho_Chi_Minh"),
    "zh-CN": ("cn", "Asia/Shanghai"), "zh-TW": ("tw", "Asia/Taipei"),
}


def guess_place(locale) -> tuple[str, str]:
    """(country, time zone) for a server in this language, e.g. "da" -> ("dk", "Europe/Copenhagen")."""
    return LOCALES.get(str(locale), (OTHER_COUNTRY, OTHER_TIMEZONE))


def welcome_channel(guild):
    """Where to greet a server the bot was just added to: its system channel (where Discord announces new members),
    else the first text channel the bot may post in. None: nowhere, so the owner gets a DM instead."""
    def can_post(channel):
        return channel is not None and channel.permissions_for(guild.me).send_messages
    if can_post(guild.system_channel):
        return guild.system_channel
    return next((c for c in guild.text_channels if can_post(c)), None)


def parse_ids(text: str | None) -> list[int]:
    """'1, 2,3' -> [1, 2, 3] in the order given, each once."""
    return list(dict.fromkeys(int(p) for p in (text or "").replace(" ", "").split(",") if p))


def parse_channel(text: str | None) -> int | None:
    """A channel setting from .env: None when it isn't set, 0 when it's turned off."""
    text = (text or "").strip()
    return int(text) if text else None


def parse_country(text: str | None) -> str | None:
    """'DK ' -> 'dk', the two-letter code Steam prices go by. None when it isn't one."""
    text = (text or "").strip().lower()
    return text if len(text) == 2 and text.isascii() and text.isalpha() else None


def parse_timezone(text: str | None) -> str | None:
    """'europe/copenhagen' -> 'Europe/Copenhagen'. None when there's no such time zone."""
    text = (text or "").strip()
    if not text:
        return None
    match = next((z for z in available_timezones() if z.lower() == text.lower()), None)
    try:
        return match and str(ZoneInfo(match))
    except ZoneInfoNotFoundError:
        return None


def resolve_timezone(text: str | None) -> str | None:
    """A time zone from what someone typed: its full name, or a city only one time zone has ('copenhagen')."""
    if zone := parse_timezone(text):
        return zone
    found = find_timezones(text or "") if (text or "").strip() else []
    return found[0] if len(found) == 1 else None


def find_timezones(text: str, limit: int = 25) -> list[str]:
    """Time zones whose name contains `text` ('copen' -> Europe/Copenhagen), for autocomplete."""
    text = text.strip().lower().replace(" ", "_")
    return sorted(z for z in available_timezones() if text in z.lower() and "/" in z)[:limit]


@dataclass
class Defaults:
    """The .env settings, used by servers that haven't set their own."""
    home_guild: int = 0                                 # GUILD_ID; 0: every server counts as home
    watch: list[int] = field(default_factory=list)      # WATCH_CHANNEL_IDS
    read: list[int] = field(default_factory=list)       # READ_CHANNEL_IDS
    posts: dict[str, int | None] = field(default_factory=dict)  # e.g. "deals" -> DEALS_CHANNEL_ID
    admins: set[int] = field(default_factory=set)       # ADMIN_IDS
    country: str = OTHER_COUNTRY                        # STEAM_COUNTRY
    timezone: str = OTHER_TIMEZONE                      # TIMEZONE


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

    def is_set(self, guild_id: int, key: str) -> bool:
        """Whether the server picked this setting itself (with /setup or the setup form)."""
        return self._stored(guild_id, key) is not None

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
        if kind in OPT_IN:
            return 0
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

    def country(self, guild_id: int | None) -> str:
        """The two-letter country the server's Steam prices are in."""
        stored = self._stored(guild_id, "country") if guild_id else None
        return stored or (self.defaults.country if not guild_id or self.is_home(guild_id) else OTHER_COUNTRY)

    def timezone(self, guild_id: int | None) -> ZoneInfo:
        """The time zone the server's #GOTG and weekly deals post in."""
        stored = self._stored(guild_id, "timezone") if guild_id else None
        return ZoneInfo(stored or (self.defaults.timezone if not guild_id or self.is_home(guild_id)
                                   else OTHER_TIMEZONE))

    def now(self, guild_id: int | None) -> datetime:
        """The time of day in the server, without a time zone attached (like datetime.now() on its own clock)."""
        return datetime.now(self.timezone(guild_id)).replace(tzinfo=None)

    def countries(self, guild_ids) -> dict[str, list[int]]:
        """country -> the servers whose prices are in it, so each country's prices are fetched once."""
        by_country: dict[str, list[int]] = {}
        for gid in guild_ids:
            by_country.setdefault(self.country(gid), []).append(gid)
        return by_country

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

    def set_country(self, guild_id: int, country: str | None):
        """A two-letter country, or None to go back to the default."""
        if country is not None and parse_country(country) != country:
            raise ValueError(f"not a two-letter country code: {country}")
        self._store(guild_id, "country", country)

    def set_timezone(self, guild_id: int, zone: str | None):
        """A time zone like Europe/Copenhagen, or None to go back to the default."""
        if zone is not None and parse_timezone(zone) != zone:
            raise ValueError(f"unknown time zone: {zone}")
        self._store(guild_id, "timezone", zone)

    def set_admin(self, guild_id: int, user_id: int, on: bool) -> set[int]:
        admins = (self.admins(guild_id) - {user_id}) | ({user_id} if on else set())
        self._store(guild_id, "admins", sorted(admins))
        return admins
