"""Discord bot that archives Steam links and Instagram reels into a browsable game catalog."""

import asyncio
import io
import json
import logging
import os
import random
import time
from datetime import date, datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
from dotenv import load_dotenv

import achievements
import sources
import gotg
from catalog_html import render_catalog
from db import PLAYERS_NEEDED, STATUSES, Database
from guilds import (POSTS, Defaults, Settings, find_timezones, parse_channel, parse_country, parse_ids,
                    parse_timezone)
from web import CatalogSite

load_dotenv()
TOKEN = os.environ.get("DISCORD_TOKEN", "")
# The home server: the .env settings below are its defaults (each server can change its own with /setup)
GUILD_ID = int(os.getenv("GUILD_ID") or 0)
WATCH_CHANNELS = parse_ids(os.getenv("WATCH_CHANNEL_IDS"))
# The only channels (and threads) the bot reads at all: the watched ones plus these. Messages anywhere else are
# ignored, even when Discord lets the bot see them.
READ_CHANNELS = parse_ids(os.getenv("READ_CHANNEL_IDS"))
# The home server's country for Steam prices and its time zone for the daily and weekly posts (each server can pick
# its own with /setup country and /setup timezone)
STEAM_COUNTRY = parse_country(os.getenv("STEAM_COUNTRY")) or "us"
TIMEZONE = parse_timezone(os.getenv("TIMEZONE")) or "UTC"
DB_PATH = os.getenv("DB_PATH", "catalog.db")
INTEREST_EMOJI = os.getenv("INTEREST_EMOJI", "🎮")
# Discord user IDs allowed to use every command in the home server (others only get /catalog, /random and /link).
# They also get the bot's DMs about its public address.
ADMIN_IDS = set(parse_ids(os.getenv("ADMIN_IDS") or os.getenv("REMOVE_ALLOWED_IDS")))
WEB_PORT = int(os.getenv("WEB_PORT") or 8765)
PUBLIC_TUNNEL = os.getenv("PUBLIC_TUNNEL", "true").lower() in ("1", "true", "yes")
# A fixed public address for the catalog (e.g. Tailscale Funnel). When set, no Cloudflare tunnel is started.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
# Steam Web API key (https://steamcommunity.com/dev/apikey): lets ratings show real Steam hours. Optional.
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "").strip()
# An uptime monitor's ping address (e.g. https://hc-ping.com/<uuid> from Healthchecks.io). Optional: when set, the bot
# pings it every UPTIME_PING_MINUTES while it's connected to Discord, and the monitor emails us when the pings stop.
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", "").strip()
UPTIME_PING_MINUTES = 5
# Whether the bot asks Discord for presences (who's playing what, who's online). Discord reviews this privileged intent
# once a bot grows; set PRESENCE_INTENT=false to run without it.
PRESENCE_INTENT = os.getenv("PRESENCE_INTENT", "true").lower() in ("1", "true", "yes")
TRACK_MINUTES = 5  # how often the bot counts who's playing what in Discord (each look adds this much time)
PLAYING_GRACE_MINUTES = 10  # a "Playing" mark the bot set goes this long after Discord and Steam stop showing the game
REFUND_MAX_MINUTES = 120  # Steam refunds only games played less than 2 hours
# The home server's channels for the bot's own posts (each server can pick its own with /setup; 0 turns them off).
# Free-to-keep Steam games (default: the first watched channel)
FREE_GAMES_CHANNEL_ID = parse_channel(os.getenv("FREE_GAMES_CHANNEL_ID"))
# Weekly post of catalog games on deep discount: where (default: with the free games), when (day 0 = Monday, in each
# server's time zone) and how deep
DEALS_CHANNEL_ID = parse_channel(os.getenv("DEALS_CHANNEL_ID"))
DEALS_DAY = int(os.getenv("DEALS_DAY") or 3)  # Thursday: Steam's weekly deals start then
DEALS_HOUR = int(os.getenv("DEALS_HOUR") or 19)
DEALS_MIN_DISCOUNT = int(os.getenv("DEALS_MIN_DISCOUNT") or 70)
DEALS_MIN_WANT = 3  # games this many people want to play are listed first, with their cover
DEALS_MAX = 20
CATCH_UP_REACTIONS = timedelta(days=7)  # on startup, 🎮 reactions on game posts this recent are re-read
# Where new achievements are announced (default: the first watched channel)
ACHIEVEMENTS_CHANNEL_ID = parse_channel(os.getenv("ACHIEVEMENTS_CHANNEL_ID"))
ACHIEVEMENT_PING_GAP = 24 * 3600  # one achievement message per person per day at most; more wait and go in one
# Where the bot asks once for help sorting out who played the Played games (default: the achievements channel), and
# the day after which Played games still short of players simply keep their spot. A reminder goes out 3 days before.
# Only in servers that had Played games before the rule.
PLAYED_RULE_CHANNEL_ID = parse_channel(os.getenv("PLAYED_RULE_CHANNEL_ID"))
PLAYED_RULE_DEADLINE = int(datetime.strptime(os.getenv("PLAYED_RULE_DEADLINE") or "2026-11-15", "%Y-%m-%d")
                           .replace(hour=23, minute=59).timestamp())
CATCH_UP_PAGE = 5  # games per "Have you played these?" message (Discord allows 40 parts in one)
# "Get on the game" (#GOTG): the daily post of games to play together tonight (gotg.py), when (in each server's time
# zone) and where. Without GOTG_CHANNEL_ID the home server's goes in the channel named GOTG_CHANNEL_NAME; posting starts
# no later than GOTG_LATEST_HOUR (if the bot was off at GOTG_TIME), and its "I'm in" buttons last until midnight.
GOTG_CHANNEL_ID = parse_channel(os.getenv("GOTG_CHANNEL_ID"))
GOTG_CHANNEL_NAME = os.getenv("GOTG_CHANNEL_NAME", "madsbo-wacky-gamerweekend")
GOTG_TIME = datetime.strptime(os.getenv("GOTG_TIME") or "16:30", "%H:%M").time()
GOTG_LATEST_HOUR = 20
BADGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "badges")
DEFAULTS = Defaults(home_guild=GUILD_ID, watch=WATCH_CHANNELS, read=READ_CHANNELS, admins=ADMIN_IDS,
                    country=STEAM_COUNTRY, timezone=TIMEZONE, posts={
    "free_games": FREE_GAMES_CHANNEL_ID, "deals": DEALS_CHANNEL_ID, "achievements": ACHIEVEMENTS_CHANNEL_ID,
    "played_rule": PLAYED_RULE_CHANNEL_ID, "gotg": GOTG_CHANNEL_ID})

log = logging.getLogger("gamebot")
Choice = app_commands.Choice

ACCENT = 0x5865F2
STATUS_COLORS = {"want": ACCENT, "demo": 0x1F9E9E, "played": 0x23A55A}
STATUS_CHOICES = [Choice(name=label, value=key) for key, (_, label) in STATUSES.items()]


# -- cards ----------------------------------------------------------------------
# Built from Discord's layout components so they look like the cards in the /export page:
# cover image, title, a stats line, tag "chips" and a small footer.

def all_tags(e: dict) -> list[str]:
    return list(dict.fromkeys(e["custom_tags"] + e["genres"] + e["tags"]))


def chips(tags: list[str]) -> str:
    return " ".join(f"`{t.replace('`', '')}`" for t in tags)


def md_link(title: str, url: str) -> str:
    return f"[{title.replace('[', '(').replace(']', ')')}]({url})"


def stats(e: dict, *, full=False) -> str:
    bits = []
    if e["review_pct"] is not None:
        bits.append(f"{e['review_pct']}%" + (f" {e['review_desc']}" if full and e["review_desc"] else ""))
    if e["coop"]:
        bits.append("Co-op")
    elif e["multiplayer"]:
        bits.append("Multiplayer")
    if e["price_text"]:
        bits.append(e["price_text"])
    if e.get("demo_appid"):
        bits.append("Has a demo")
    bits.append(f"{e['interest']} want to play")
    if full or e["status"] != "want":
        bits.append(STATUSES[e["status"]][1])
    return " · ".join(bits)


def added_line(e: dict) -> str:
    who = f"<@{e['added_by']}>" if e["added_by"] else "someone"
    line = f"Added by {who} <t:{e['added_at']}:R>"
    if e["clips"]:
        line += f" · {len(e['clips'])} clip{'s' if len(e['clips']) > 1 else ''}"
    return line


def link_button(e: dict) -> ui.Button:
    label = {"steam": "Open on Steam", "battlenet": "Open on Battle.net"}.get(e["kind"], "Open reel")
    return ui.Button(style=discord.ButtonStyle.link, label=label, url=e["url"])


def detail_card(e: dict, *, heading: str | None = None, buttons: list[ui.Item] = ()) -> ui.Container:
    """A full card for one game: big banner, description, who wants to play it."""
    items: list[ui.Item] = []
    if heading:
        items.append(ui.TextDisplay(heading))
    if e["image"]:
        items.append(ui.MediaGallery(discord.MediaGalleryItem(e["image"])))
    lines = [f"## {md_link(e['title'], e['url'])}", stats(e, full=True)]
    tags = all_tags(e)[:12]
    if tags:
        lines.append(chips(tags))
    if e["kind"] == "instagram":
        lines.append("This reel isn't linked to a game yet. Use `/link` to link it.")
    items.append(ui.TextDisplay("\n".join(lines)))
    if e["description"]:
        items.append(ui.TextDisplay(e["description"][:700]))
    if e["note"]:
        items.append(ui.TextDisplay(f"> “{e['note'][:300]}”"))

    foot = [added_line(e)]
    facts = [f for f in (e["release_date"] and f"Released {e['release_date']}",
                         e["developer"] and f"By {e['developer'][:80]}") if f]
    if facts:
        foot.append(" · ".join(facts))
    interested = bot.db.interested(e["id"])
    if interested:
        foot.append("Wants to play: " + " ".join(f"<@{uid}>" for uid, _ in interested))
    if e["clips"]:
        foot.append("Clips: " + " · ".join(f"[clip {i}]({u})" for i, u in enumerate(e["clips"][:8], 1)))
    items += [ui.Separator(), ui.TextDisplay("\n".join(f"-# {line}" for line in foot))]
    items.append(ui.ActionRow(*buttons, link_button(e)))
    return ui.Container(*items, accent_colour=STATUS_COLORS[e["status"]])


class FreeGameView(ui.LayoutView):
    """The post announcing a Steam game that's free to keep for a limited time."""

    def __init__(self, appid: int, meta: dict, *, in_catalog: bool):
        super().__init__(timeout=None)
        url = f"https://store.steampowered.com/app/{appid}/"
        lines = [f"## Free on Steam: {md_link(meta['title'], url)}"]
        deal = "Free to keep" + (f" (normally {meta['price_text']})" if meta.get("price_text") else "")
        lines.append(deal + (f" — get it before **{meta['_free_until']}**" if meta.get("_free_until") else ""))
        tags = list(dict.fromkeys(meta.get("genres", []) + meta.get("tags", [])))[:6]
        if tags:
            lines.append(chips(tags))
        items: list[ui.Item] = []
        if meta.get("image"):
            items.append(ui.MediaGallery(discord.MediaGalleryItem(meta["image"])))
        items.append(ui.TextDisplay("\n".join(lines)))
        if meta.get("description"):
            items.append(ui.TextDisplay(meta["description"][:400]))
        items.append(ui.TextDisplay(
            f"-# Added to the catalog. React {INTEREST_EMOJI} if you want to play it" if in_catalog
            else "-# Not added to the catalog (it's not an online multiplayer game), but it's free to grab"))
        items.append(ui.ActionRow(ui.Button(style=discord.ButtonStyle.link, label="Get it on Steam", url=url)))
        self.add_item(ui.Container(*items, accent_colour=0x23A55A))


class ReelGameModal(ui.Modal, title="Which game is this reel?"):
    game = ui.TextInput(label="Game name or Steam link", placeholder="e.g. R.E.P.O.", max_length=200)

    def __init__(self, reel_id: int, question: discord.Message):
        super().__init__()
        self.reel_id, self.question = reel_id, question

    async def on_submit(self, interaction: discord.Interaction):
        reel = bot.db.get(self.reel_id)
        if not reel or reel["kind"] != "instagram":
            return await interaction.response.send_message("That reel is already linked to a game.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        appid = await find_appid(self.game.value, interaction.guild_id)
        if not appid:
            return await interaction.followup.send(f"Couldn't find “{self.game.value}” on Steam. Try its Steam link.",
                                                   ephemeral=True)
        try:
            game = await bot.link_reel(reel, appid)
        except TurnedAway as exc:
            return await interaction.followup.send(f"{exc} The reel wasn't linked.", ephemeral=True)
        log.info("%s said reel %s is %s, linked", interaction.user.display_name, reel["key"], game["title"])
        await self.question.edit(content=f"This reel is **{game['title']}** (thanks {interaction.user.mention}), "
                                         "so it's now a clip of that game in the catalog.", view=None,
                                 allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(f"Linked to **{game['title']}**.", ephemeral=True)


class ReelGameButton(ui.DynamicItem[ui.Button], template=r"reel-game:(?P<reel>\d+):(?P<action>name|none)"):
    """The buttons under "which game is this reel about?". Only the poster (or an admin) can answer.
    Dynamic, so they still work after the bot restarts."""

    def __init__(self, reel_id: int, action: str):
        label, style = ("Tell me the game", discord.ButtonStyle.primary) if action == "name" \
            else ("Not about one game", discord.ButtonStyle.secondary)
        super().__init__(ui.Button(label=label, style=style, custom_id=f"reel-game:{reel_id}:{action}"))
        self.reel_id, self.action = reel_id, action

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match):
        return cls(int(match["reel"]), match["action"])

    async def callback(self, interaction: discord.Interaction):
        reel = bot.db.get(self.reel_id)
        if not reel or reel["kind"] != "instagram":
            await interaction.message.edit(view=None)
            return await interaction.response.send_message("That reel is already linked to a game.", ephemeral=True)
        is_admin = bot.settings.is_admin(interaction.guild_id, interaction.user.id, interaction.permissions.manage_messages)
        if interaction.user.id != reel["added_by"] and not is_admin:
            return await interaction.response.send_message(f"Only {reel['added_by_name']} (who posted it) can answer.",
                                                           ephemeral=True)
        if self.action == "name":
            return await interaction.response.send_modal(ReelGameModal(self.reel_id, interaction.message))
        await interaction.response.edit_message(content="Okay, this reel stays a reel (not about one game).", view=None)


class ReelGameView(ui.View):
    def __init__(self, reel_id: int):
        super().__init__(timeout=None)
        self.add_item(ReelGameButton(reel_id, "name"))
        self.add_item(ReelGameButton(reel_id, "none"))


def deal_line(e: dict) -> str:
    """'**[Title](store)** ~~€59.99~~ **€11.99** −80% · 4 want to play'"""
    price = (f"~~{e['price_full_text']}~~ " if e.get("price_full_text") else "") + f"**{e['price_text']}**"
    want = f" · {e['interest']} want to play" if e["interest"] else ""
    return f"**{md_link(e['title'], e['url'])}** {price} −{e['discount_pct']}%{want}"


class DealsView(ui.LayoutView):
    """The weekly post of deep Steam discounts on catalog games: the most wanted first, with their covers."""

    def __init__(self, wanted: list[dict], others: list[dict]):
        super().__init__(timeout=None)
        items: list[ui.Item] = [ui.TextDisplay(
            f"## Deep discounts this week\nCatalog games at {DEALS_MIN_DISCOUNT}% off or more on Steam.")]
        if wanted:
            items.append(ui.TextDisplay(f"### Most wanted ({DEALS_MIN_WANT}+ want to play)"))
            for e in wanted:
                line = ui.TextDisplay(deal_line(e))
                items.append(ui.Section(line, accessory=ui.Thumbnail(e["image"])) if e.get("image") else line)
        if others:
            items.append(ui.Separator())
            items.append(ui.TextDisplay(("### Also on sale\n" if wanted else "") + "\n".join(map(deal_line, others))))
        items.append(ui.TextDisplay(f"-# Prices change; check Steam before you buy. React {INTEREST_EMOJI} on a game's post, "
                                    "or use /catalog, to say you want to play it."))
        self.add_item(ui.Container(*items, accent_colour=0x4C6B22))


class CardView(ui.LayoutView):
    """A static card (no buttons besides the link)."""

    def __init__(self, e: dict, heading: str | None = None):
        super().__init__(timeout=None)
        self.add_item(detail_card(e, heading=heading))


class OwnedView(ui.LayoutView):
    """Interactive message that only the person who ran the command can control."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.message: discord.PartialMessage | None = None

    def render(self, controls=True):
        raise NotImplementedError

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the person who ran this command can use these controls — run it yourself to get your own copy.",
            ephemeral=True)
        return False

    async def send(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=self)
        sent = await interaction.original_response()
        self.message = interaction.channel.get_partial_message(sent.id)

    async def on_timeout(self):
        # Drop the controls so they don't look clickable after they stop working.
        if self.message:
            self.render(controls=False)
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class RandomView(OwnedView):
    def __init__(self, owner_id: int, rows: list[dict]):
        super().__init__(owner_id)
        self.rows, self.current = rows, None
        self.pick()

    def pick(self):
        # More interested friends -> more likely to come up. Never the same game twice in a row.
        pool = [r for r in self.rows if r is not self.current] or self.rows
        self.current = random.choices(pool, weights=[1 + r["interest"] for r in pool])[0]
        self.render()

    def render(self, controls=True):
        self.clear_items()
        buttons = []
        if controls and len(self.rows) > 1:
            reroll = ui.Button(label="Reroll", style=discord.ButtonStyle.primary)

            async def on_reroll(interaction: discord.Interaction):
                self.pick()
                await interaction.response.edit_message(view=self)

            reroll.callback = on_reroll
            buttons.append(reroll)
        self.add_item(detail_card(self.current, heading="### Tonight's pick", buttons=buttons))


# -- the bot ------------------------------------------------------------------

COD_FROM_STEAM = "Call of Duty games are added from Steam: post the game's Steam link instead."


def is_call_of_duty(meta: dict) -> bool:
    return sources.normalize_title(meta.get("title")).startswith("call of duty")


class TurnedAway(Exception):
    """A Steam game the catalog doesn't take. str(exc) is the reply that says why."""



def check_real_game(appid: int, meta: dict):
    """Raise TurnedAway for DLC, tools, test servers, software, games Steam no longer sells and explicit games."""
    problem = sources.not_a_game(meta)
    if problem:
        log.info("Turned away non-game %s (%s): %s", appid, meta["title"], problem)
        raise TurnedAway(f"**{meta['title']}** is {problem}, so it wasn't added. Only games go in the catalog.")
    if meta.get("_delisted") and int(appid) not in sources.DELISTED_ALLOWED_APPIDS:
        log.info("Turned away delisted game %s (%s)", appid, meta["title"])
        raise TurnedAway(f"**{meta['title']}** is no longer available on the Steam store, so it wasn't added.")
    if int(appid) not in sources.EXPLICIT_ALLOWED_APPIDS and sources.explicit_content(meta):
        log.info("Turned away explicit game %s (%s)", appid, meta["title"])
        raise TurnedAway(f"**{meta['title']}** has sexual content or nudity, so it wasn't added.")


def check_playable_online(appid: int, meta: dict):
    """Raise TurnedAway for singleplayer games and games with only same-screen multiplayer."""
    problem = sources.online_play_problem(meta)
    if problem:
        log.info("Turned away game %s (%s): %s", appid, meta["title"], problem)
        raise TurnedAway(f"**{meta['title']}** {problem}, so it wasn't added. "
                         "The catalog is only for games you can play together online.")


def stored_fields(meta: dict) -> dict:
    """Steam info minus the "_" keys that are only used for checking."""
    return {k: v for k, v in meta.items() if not k.startswith("_")}


def steam_card(entry: dict, meta: dict) -> discord.Embed:
    """A card like Discord's own preview of a Steam store link (which bot messages don't reliably get)."""
    description = meta.get("description") or entry["description"] or ""
    card = discord.Embed(title=entry["title"], url=entry["url"],
                         description=description[:347] + "..." if len(description) > 350 else description or None)
    card.set_author(name="Steam")
    if meta.get("price_text") or entry["price_text"]:
        card.add_field(name="Price", value=meta.get("price_text") or entry["price_text"])
    if meta.get("_recommendations"):
        card.add_field(name="Recommendations", value=str(meta["_recommendations"]))
    if meta.get("_metacritic"):
        card.add_field(name="Metacritic", value=str(meta["_metacritic"]))
    if meta.get("image") or entry["image"]:
        card.set_image(url=meta.get("image") or entry["image"])
    return card


def not_on_steam(appid: int) -> TurnedAway:
    return TurnedAway(f"Couldn't find Steam app {appid} on Steam, so it wasn't added. "
                      "If it's a real game, post it again in a bit.")


class GameBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        # To see "Playing <game>" and online status (switched on in the portal). Without it, playing is only seen on
        # Steam, for people who linked it, and the catalog shows no online status.
        intents.presences = PRESENCE_INTENT
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         allowed_mentions=discord.AllowedMentions.none())
        self.db = Database(DB_PATH)
        self.settings = Settings(self.db, DEFAULTS, self.channel_guild)  # each server's channels and admins
        self._titles: dict[int, tuple[float, dict[str, int]]] = {}  # guild -> (built at, normalized title -> entry id)
        self._catching_up = False
        self._connected = False  # to Discord right now (the heartbeat only counts that time as "online")
        self._last_uptime_ping = 0.0
        self.web: aiohttp.ClientSession | None = None
        self.site = CatalogSite(self, WEB_PORT, GUILD_ID, os.getenv("DISCORD_CLIENT_SECRET", "").strip())
        self.site.played_deadline = PLAYED_RULE_DEADLINE

    async def setup_hook(self):
        self.web = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "Mozilla/5.0 (game-catalog discord bot)"},
        )
        if not PRESENCE_INTENT:
            log.info("Running without presences: playing is only seen on Steam%s, and the catalog shows no online "
                     "status", "" if STEAM_API_KEY else " (but STEAM_API_KEY isn't set, so not at all)")
        self.add_dynamic_items(ReelGameButton)  # "which game is this reel?" buttons on older messages
        self.add_dynamic_items(OpenCatalogButton)  # "Open the catalog" on the achievements launch post
        self.add_dynamic_items(CheckPlayedButton, PlayedAnswerButton)  # "Have you played these?"
        self.add_dynamic_items(GotgInButton)  # "I'm in" on the #GOTG posts
        await self.site.start()
        # These run for every server; a server whose channel for them is turned off is skipped
        self.post_free_games.start()
        self.refresh_prices.start()
        self.refresh_all_playtime.start()
        self.steam_now_playing.start()
        self.track_playtime.start()
        self.post_deals.start()
        self.check_achievements.start()
        self.played_rule.start()
        self.post_gotg.start()
        if PUBLIC_URL:  # fixed address: nothing to start, and the Activity URL mapping never changes
            self.site.public_url = PUBLIC_URL
            log.info("Public catalog URL: %s", PUBLIC_URL)
        elif PUBLIC_TUNNEL:
            asyncio.create_task(self.share_tunnel())
        # Global commands, so every server the bot is in gets them. Older versions gave the home server its own copies
        # (they showed up faster); those are cleared, or the home server would list every command twice.
        await self.sync_global_commands()
        if GUILD_ID:
            self.tree.clear_commands(guild=discord.Object(GUILD_ID))
            await self.tree.sync(guild=discord.Object(GUILD_ID))

    async def sync_global_commands(self):
        """What tree.sync() does for the global commands, but keeping the Activity's Entry Point command (the
        "launch" that opens the catalog). Discord refuses an update that leaves it out, and discord.py doesn't send it."""
        entry_points = [c for c in await self.http.get_global_commands(self.application_id) if c.get("type") == 4]
        payload = [command.to_dict(self.tree) for command in self.tree.get_commands()] + entry_points
        await self.http.bulk_upsert_global_commands(self.application_id, payload=payload)

    async def share_tunnel(self):
        """Start the public tunnel and DM the admins its address, which changes on every start
        and has to be put in the Developer Portal for /catalog to open."""
        url = await self.site.start_tunnel()
        if not url:
            return
        portal = f"https://discord.com/developers/applications/{self.application_id}/embedded/url-mappings"
        text = (f"I'm back online. For `/catalog` to work, set the Activity URL mapping (prefix `/`) to:\n"
                f"`{url.removeprefix('https://')}`\n<{portal}>")
        for uid in ADMIN_IDS:
            try:
                await (await self.fetch_user(uid)).send(text)
            except discord.HTTPException as exc:
                log.warning("Couldn't DM %s the new catalog address: %s", uid, exc)

    async def close(self):
        await self.site.close()
        if self.web:
            await self.web.close()
        await super().close()

    def channel_guild(self, channel_id: int) -> int | None:
        """The server a channel or thread is in (None: the bot can't see it)."""
        guild = getattr(self.get_channel(channel_id), "guild", None)
        return guild.id if guild else None

    def may_read(self, channel) -> bool:
        """Only the server's read channels, and threads inside its watched channels."""
        guild = getattr(channel, "guild", None)
        return bool(guild) and self.settings.may_read(guild.id, channel.id, getattr(channel, "parent_id", None))

    def watches(self, channel) -> bool:
        guild = getattr(channel, "guild", None)
        return self.may_read(channel) and self.settings.watches(guild.id, channel.id, getattr(channel, "parent_id", None))

    def post_channel(self, guild: discord.Guild, kind: str):
        """The channel in `guild` for its `kind` posts (see guilds.POSTS), or None when they're turned off."""
        channel_id = self.settings.post_channel(guild.id, kind)
        if channel_id is None:  # #GOTG without a channel set: in the home server, the one with its usual name
            home = self.settings.is_home(guild.id)
            return discord.utils.get(guild.text_channels, name=GOTG_CHANNEL_NAME) if home else None
        channel = self.get_channel(channel_id) if channel_id else None
        return channel if channel and channel.guild.id == guild.id else None

    def sync_members(self, guild: discord.Guild):
        self.db.set_members(guild.id, [m.id for m in guild.members if not m.bot])

    # -- archiving --

    async def ensure_steam(self, guild_id: int, appid: int, origin: dict) -> tuple[dict, bool]:
        """Find or add a Steam game. Raises TurnedAway for games the catalog doesn't take (and says why)."""
        existing = self.db.get_by_key(guild_id, f"steam:{appid}")
        if existing:
            return existing, False
        meta = await sources.fetch_steam(self.web, appid, self.settings.country(guild_id))
        if not meta:
            log.info("Turned away unknown Steam app %s", appid)
            raise not_on_steam(appid)
        if appid not in self.allowed_apps(guild_id):  # games the server's admin let through with /allow skip the rules
            check_real_game(appid, meta)
            check_playable_online(appid, meta)
        return self.add_steam(guild_id, appid, meta, origin), True

    def allowed_apps(self, guild_id: int) -> set[int]:
        return set(json.loads(self.db.get_guild_meta(guild_id, "allowed_apps") or "[]"))

    def allow_app(self, guild_id: int, appid: int):
        self.db.set_guild_meta(guild_id, "allowed_apps", json.dumps(sorted(self.allowed_apps(guild_id) | {appid})))

    def add_steam(self, guild_id: int, appid: int, meta: dict, origin: dict) -> dict:
        entry = self.db.insert(guild_id, key=f"steam:{appid}", kind="steam",
                               url=f"https://store.steampowered.com/app/{appid}/", **origin, **stored_fields(meta))
        log.info("Archived Steam app %s (%s)", appid, entry["title"])
        # The new game counts for "playing" straight away, also for anyone already in it right now
        self._titles.pop(guild_id, None)
        guild = self.get_guild(guild_id)
        for member in guild.members if guild else []:
            if not member.bot:
                self.mark_playing(member, self.games_playing(member))
        return entry

    # -- prices --

    @tasks.loop(hours=3)
    async def refresh_prices(self):
        """Keep prices up to date, so Steam sales show up (and disappear again) in the catalog. Each server's games are
        priced in its own country (/setup country), and each country's prices are fetched once."""
        try:
            rows = self.db.conn.execute(
                "SELECT id, guild_id, key, price_cents, price_text, discount_pct, price_full_text, discount_ends "
                "FROM entries WHERE kind = 'steam'"
            ).fetchall()
            changed = sales = 0
            for country, guild_ids in self.settings.countries({r["guild_id"] for r in rows}).items():
                ours = [r for r in rows if r["guild_id"] in guild_ids]
                appids = sorted({int(r["key"].split(":")[1]) for r in ours})
                # The store API is fresh and says when each sale ends; appdetails (cached for a while, so it can
                # still show a sale that just ended) only covers the games the store API didn't answer for
                prices = await sources.fetch_store_prices(self.web, appids, country)
                missing = [a for a in appids if a not in prices]
                if missing:
                    old = await sources.fetch_prices(self.web, missing, country)
                    prices |= {a: {**p, "discount_ends": None} for a, p in old.items()}
                for r in ours:
                    new = prices.get(int(r["key"].split(":")[1]))
                    if new is None:
                        continue
                    sales += bool(new["discount_pct"])
                    if any(new[k] != r[k] for k in new):
                        self.db.update(r["id"], **new)
                        changed += 1
            log.info("Refreshed prices: %d changed, %d games on sale", changed, sales)
        except Exception:
            log.exception("Couldn't refresh prices")

    # -- Steam playtime (for the "played" badge on ratings) --

    async def refresh_playtime(self, user_id: int, steam_id: str) -> bool:
        """Fetch `user_id`'s hours per game from Steam. False when Steam won't say (game details private)."""
        games = await sources.fetch_playtime(self.web, STEAM_API_KEY, steam_id) if STEAM_API_KEY else None
        if games is None:
            return False
        before, minutes = self.db.owned(user_id), self.db.user_playtime(user_id)
        if before and games and len(games) >= len(before) // 2:  # suddenly (half) empty is a Steam hiccup, not refunds
            for appid in before - games.keys():
                self.detect_refund(user_id, appid, minutes.get(appid, 0))
        if games:  # keep the last real snapshot through an empty answer
            self.db.set_owned(user_id, set(games))
            self.db.set_playtime(user_id, games)
        return True

    def detect_refund(self, user_id: int, appid: int, minutes: int):
        """A game left someone's Steam library. Steam refunds only games played under 2 hours, so with less than
        that it was most likely refunded (more and it was e.g. family sharing ending): mark it, they can undo."""
        if minutes >= REFUND_MAX_MINUTES:
            return
        for guild in self.guilds:
            entry = self.db.get_by_key(guild.id, f"steam:{appid}")
            if entry and user_id not in {uid for uid, _ in self.db.refunds(entry["id"])}:
                member = guild.get_member(user_id)
                self.db.set_refund(entry["id"], user_id, member.display_name if member else None, True, int(time.time()))
                log.info("%s refunded %s (gone from their Steam library after %d min), marked as refunded",
                         member.display_name if member else user_id, entry["title"], minutes)

    @tasks.loop(hours=2)
    async def refresh_all_playtime(self):
        if not STEAM_API_KEY:
            return
        accounts, ok = self.db.steam_accounts(), 0
        for uid, steam_id in accounts.items():
            try:
                ok += await self.refresh_playtime(uid, steam_id)
            except Exception:
                log.exception("Couldn't load Steam hours of %s", uid)
            await asyncio.sleep(1)
        if accounts:
            log.info("Steam hours: %d of %d linked accounts loaded (the rest have private game details)", ok, len(accounts))

    @refresh_all_playtime.before_loop
    async def _playtime_wait(self):
        await self.wait_until_ready()  # refunds are marked per server, so the servers must be loaded first

    # -- free games --

    @tasks.loop(hours=2)
    async def post_free_games(self):
        try:
            await self._post_free_games()
        except Exception:  # keep the loop running; try again next time
            log.exception("Free game check failed")

    async def _post_free_games(self):
        """Post Steam games that are 100% off (free to keep), once each, in every server's free-games channel."""
        channels = [c for c in (self.post_channel(g, "free_games") for g in self.guilds) if c]
        # Each country is looked up once: what's free and the normal price both depend on it
        for country, guild_ids in self.settings.countries({c.guild.id for c in channels}).items():
            free, metas = await sources.find_free_games(self.web, country), {}
            for channel in channels:
                if channel.guild.id in guild_ids:
                    await self._post_free_games_in(channel, free, metas, country)

    async def _post_free_games_in(self, channel, free: list[int], metas: dict, country: str):
        """The free games not posted in `channel`'s server yet. `metas`: Steam info already fetched (for any server
        in the same country)."""
        now = int(time.time())
        for appid in free:
            if self.db.free_posted_since(channel.guild.id, appid, now - 30 * 86400):
                continue
            if appid not in metas:
                metas[appid] = await sources.fetch_steam(self.web, appid, country)
            meta = metas[appid]
            if not meta or not meta["_giveaway"]:
                continue
            self.db.mark_free_posted(channel.guild.id, appid, now)
            try:
                check_real_game(appid, meta)  # no DLC, soundtracks or explicit games
            except TurnedAway:
                continue
            try:
                check_playable_online(appid, meta)
                fits = True
            except TurnedAway as exc:
                fits, why = False, str(exc)
            existing = self.db.get_by_key(channel.guild.id, f"steam:{appid}")
            message = await channel.send(view=FreeGameView(appid, meta, in_catalog=fits or bool(existing)))
            if fits and not existing:
                existing = self.add_steam(channel.guild.id, appid, meta, {
                    "added_by": self.user.id, "added_by_name": "Free on Steam", "added_at": now,
                    "channel_id": channel.id, "message_id": message.id,
                    "note": f"Free to keep on Steam until {meta['_free_until']}" if meta["_free_until"] else None})
            if existing:
                self.db.link_message(message.id, existing["id"])  # 🎮 on the post counts as interest
                await message.add_reaction(INTEREST_EMOJI)
            log.info("Posted free game %s (%s)%s", appid, meta["title"], "" if fits else f": {why}")
            await asyncio.sleep(2)

    @post_free_games.before_loop
    async def _wait_until_ready(self):
        await self.wait_until_ready()

    # -- weekly deep discounts --

    def deals(self, guild_id: int) -> tuple[list[dict], list[dict]]:
        """Catalog games nobody has played yet at DEALS_MIN_DISCOUNT% off or more: (most wanted, the rest).
        The most wanted are sorted by how many want them, the rest by discount; DEALS_MAX at most in all."""
        rows = [e for e in self.db.query(guild_id, source="steam", status=("want", "demo"))
                if (e["discount_pct"] or 0) >= DEALS_MIN_DISCOUNT and e["price_text"]]
        wanted = sorted((e for e in rows if e["interest"] >= DEALS_MIN_WANT),
                        key=lambda e: (-e["interest"], -e["discount_pct"]))[:6]  # each one shows its cover
        others = sorted((e for e in rows if e not in wanted), key=lambda e: (-e["discount_pct"], -e["interest"]))
        return wanted, others[:DEALS_MAX - len(wanted)]

    @tasks.loop(minutes=30)
    async def post_deals(self):
        """Once a week, on DEALS_DAY after DEALS_HOUR in each server's time zone (or as soon as the bot is back, if it
        was off then)."""
        try:
            refreshed = False
            for guild in self.guilds:
                now = self.settings.now(guild.id)
                due = (now - timedelta(days=(now.weekday() - DEALS_DAY) % 7)).replace(hour=DEALS_HOUR, minute=0,
                                                                                       second=0, microsecond=0)
                if due > now:
                    due -= timedelta(days=7)
                last = self.db.get_guild_meta(guild.id, "deals_posted")
                if last is None:  # just switched on: start with next week's, rather than posting right away
                    self.db.set_guild_meta(guild.id, "deals_posted", due.date().isoformat())
                    continue
                if last == due.date().isoformat():
                    continue
                channel = self.post_channel(guild, "deals")
                if channel is None:
                    continue
                if not refreshed:
                    await self.refresh_prices()  # fresh prices, so no sale that just ended is listed
                    refreshed = True
                self.db.set_guild_meta(guild.id, "deals_posted", due.date().isoformat())
                wanted, others = self.deals(guild.id)
                if not wanted and not others:
                    log.info("No deep discounts this week in %s", guild.name)
                    continue
                await channel.send(view=DealsView(wanted, others))
                log.info("Posted %d deep discounts (%d most wanted) in %s", len(wanted) + len(others), len(wanted),
                         guild.name)
        except Exception:
            log.exception("Weekly deals post failed")

    @post_deals.before_loop
    async def _deals_wait(self):
        await self.wait_until_ready()

    # -- achievements --

    @tasks.loop(minutes=10)
    async def check_achievements(self):
        """Record newly earned achievements and announce them. The very first run records what everyone had
        already earned without a word, then posts the launch message once. After that each person gets at
        most one message a day; anything else they earn waits and goes into that one message."""
        for guild in self.guilds:
            try:
                channel = self.post_channel(guild, "achievements")
                if channel is None:  # turned off in this server
                    continue
                await self.check_achievements_in(guild, channel)
            except Exception:
                log.exception("Couldn't check achievements in %s", guild.name)

    async def check_achievements_in(self, guild: discord.Guild, channel):
        gid = guild.id
        first = not self.db.get_guild_meta(gid, "achievements_started")
        achievements.sync(self.db, self.user.id, gid, silent=first)
        if first:
            self.db.set_guild_meta(gid, "achievements_started", int(time.time()))
            log.info("Achievements started in %s: what people had already earned was recorded without messages",
                     guild.name)
        if not self.db.get_guild_meta(gid, "achievements_launch_posted"):
            await channel.send(view=achievements_launch_view(gid), files=[badge_file("launch.png")])
            self.db.set_guild_meta(gid, "achievements_launch_posted", int(time.time()))
            log.info("Posted the achievements launch message in %s", guild.name)
        now = time.time()
        for uid, items in self.db.unannounced(gid).items():
            if now - float(self.db.get_guild_meta(gid, f"achievement_ping:{uid}") or 0) < ACHIEVEMENT_PING_GAP:
                continue
            view, files = achievement_view(gid, uid, items)
            await channel.send(view=view, files=files, allowed_mentions=discord.AllowedMentions(users=[discord.Object(uid)]))
            self.db.mark_announced(gid, uid, [key for key, _ in items])
            self.db.set_guild_meta(gid, f"achievement_ping:{uid}", int(now))
            log.info("Announced %d achievement(s) for %s in %s", len(items), uid, guild.name)

    @check_achievements.before_loop
    async def _achievements_wait(self):
        await self.wait_until_ready()
        await asyncio.sleep(60)  # let the startup catch-up and price refresh go first

    # -- Played needs PLAYERS_NEEDED players --

    def promote_played(self) -> list[dict]:
        """Mark games enough of us played as Played (Steam hours, ratings... come in all the time)."""
        promoted = self.db.promote_played()
        for game in promoted:
            log.info("%s now has %d players, so it's Played", game["title"], PLAYERS_NEEDED)
        return promoted

    @tasks.loop(minutes=10)
    async def played_rule(self):
        """Promote games that reached enough players. Once, ask everyone to help sort out who played the Played
        games (the post's progress line is kept up to date), and remind them once 3 days before the deadline."""
        try:
            self.promote_played()
        except Exception:
            log.exception("Couldn't check the Played rule")
        before = self.db.played_before_rule()
        for guild in self.guilds:
            try:
                # The posts explain a change to games that were already Played: servers that had none skip them
                channel = self.post_channel(guild, "played_rule")
                if channel is None or not any(e["id"] in before for e in self.db.query(guild.id, status="played")):
                    continue
                await self.played_rule_in(guild.id, channel)
            except Exception:
                log.exception("Couldn't post the Played rule in %s", guild.name)

    async def played_rule_in(self, gid: int, channel):
        progress, promoted = self.played_rule_state(gid)
        post = self.db.get_guild_meta(gid, "played_rule_post")
        state = f"{progress[0]}:{progress[1]}:{len(promoted)}"
        if not post:
            message = await channel.send(view=played_rule_view(progress, promoted))
            self.db.set_guild_meta(gid, "played_rule_post", message.id)
            self.db.set_guild_meta(gid, "played_rule_progress", state)
            log.info("Posted the Played rule announcement in %s (%d of %d Played games have %d players)",
                     channel.guild.name, *progress, PLAYERS_NEEDED)
        elif self.db.get_guild_meta(gid, "played_rule_progress") != state:
            await channel.get_partial_message(int(post)).edit(view=played_rule_view(progress, promoted))
            self.db.set_guild_meta(gid, "played_rule_progress", state)
        left = progress[1] - progress[0]
        if left and 0 < PLAYED_RULE_DEADLINE - time.time() < 3 * 86400 and not self.db.get_guild_meta(gid, "played_rule_reminded"):
            await channel.send(view=played_rule_reminder_view(left))
            self.db.set_guild_meta(gid, "played_rule_reminded", int(time.time()))
            log.info("Posted the Played rule reminder in %s (%d games short of players)", channel.guild.name, left)

    def played_rule_state(self, guild_id: int) -> tuple[tuple[int, int], list[str]]:
        """What the Played rule post shows: (Played games with enough players, all Played games), and the games
        that became Played by the rule (named in the post, so nobody wonders why they moved)."""
        before = self.db.played_before_rule()
        promoted = [e["title"] for e in self.db.query(guild_id, status="played", sort="oldest") if e["id"] not in before]
        return self.db.played_progress(guild_id), promoted

    @played_rule.before_loop
    async def _played_rule_wait(self):
        await self.wait_until_ready()
        await asyncio.sleep(90)  # after the startup catch-up and the first Steam hours

    # -- Get on the game (#GOTG) --

    @tasks.loop(minutes=5)
    async def post_gotg(self):
        """Every day at GOTG_TIME in each server's time zone: its #GOTG post (once a day; later the same day if the bot
        was off then)."""
        for guild in self.guilds:
            now = self.settings.now(guild.id)
            day = now.date().isoformat()
            if now.time() < GOTG_TIME or now.hour >= GOTG_LATEST_HOUR:
                continue
            try:
                if self.db.gotg_post(guild.id, day):
                    continue
                channel = self.post_channel(guild, "gotg")
                if channel is None:  # turned off, or no channel with its usual name (noted at startup)
                    continue
                picks = gotg_picks(guild.id, now.date())
                if not picks:
                    self.db.save_gotg_post(guild.id, day, [])
                    log.info("No games for #GOTG today in %s", guild.name)
                    continue
                self.db.save_gotg_post(guild.id, day, picks)  # saved first: never posted twice
                message = await channel.send(view=gotg_view(guild.id, day))
                self.db.save_gotg_post(guild.id, day, picks, channel.id, message.id)
                log.info("Posted #GOTG in %s: %s", guild.name, ", ".join(self.db.get(eid)["title"] for eid, _ in picks))
            except Exception:
                log.exception("#GOTG post failed in %s", guild.name)

    @post_gotg.before_loop
    async def _gotg_wait(self):
        await self.wait_until_ready()
        for guild in self.guilds:
            channel = self.post_channel(guild, "gotg")
            log.info("#GOTG posts at %s (%s) in %s: %s", f"{GOTG_TIME:%H:%M}", self.settings.timezone(guild.id),
                     guild.name, f"#{channel.name}" if channel else "off (pick a channel with /setup post)")
        await asyncio.sleep(120)  # after the startup catch-up and the first Steam libraries

    async def ensure_battlenet(self, guild_id: int, key: str, url: str, origin: dict) -> tuple[dict, bool]:
        """Find or add a Battle.net game. Blizzard has no store API with game modes, so the online-play check is
        skipped (Battle.net games are online games); a game already in the catalog from Steam counts as a repeat.
        Call of Duty games come from Steam only."""
        if key.startswith("bnet:call-of-duty"):
            raise TurnedAway(COD_FROM_STEAM)
        existing = self.db.get_by_key(guild_id, key)
        if existing:
            return existing, False
        meta = await sources.fetch_battlenet(self.web, url, self.settings.country(guild_id))
        if not meta:
            log.info("Turned away unknown Battle.net page %s", url)
            raise TurnedAway(f"Couldn't find a game at <{url}>. Post the game's Battle.net shop page or its own site.")
        if is_call_of_duty(meta):
            raise TurnedAway(COD_FROM_STEAM)
        same = self.catalog_titles(guild_id).get(sources.normalize_title(meta["title"]))
        if same:
            return self.db.get(same), False
        entry = self.db.insert(guild_id, key=key, kind="battlenet", url=url, **origin, **meta)
        log.info("Archived Battle.net game %s (%s)", key, entry["title"])
        self._titles.pop(guild_id, None)  # counts for "playing" straight away, like a new Steam game
        guild = self.get_guild(guild_id)
        for member in guild.members if guild else []:
            if not member.bot:
                self.mark_playing(member, self.games_playing(member))
        return entry, True

    async def ensure_reel(self, guild_id: int, code: str, url: str, origin: dict,
                          embeds: list[discord.Embed]) -> tuple[dict, bool]:
        existing = self.db.get_by_key(guild_id, f"ig:{code}") or self.db.find_by_clip(guild_id, url)
        if existing:
            return existing, False
        meta = await sources.fetch_instagram(self.web, url)
        if not meta.get("description"):
            meta = {**self.embed_meta(code, embeds), **meta}
        meta.setdefault("title", f"Instagram reel {code}")
        entry = self.db.insert(guild_id, key=f"ig:{code}", kind="instagram", url=url, **origin, **meta)
        log.info("Archived reel %s (%s)", code, entry["title"])
        return entry, True

    async def link_reel(self, reel: dict, appid: int) -> dict:
        """Make `reel` a clip of Steam game `appid` (adding the game, credited to the reel's poster, if it's new).
        Raises TurnedAway when the game doesn't fit the catalog."""
        origin = {k: reel[k] for k in ("added_by", "added_by_name", "added_at", "channel_id", "message_id", "note")}
        target, _ = await self.ensure_steam(reel["guild_id"], appid, origin)
        self.db.merge(reel["id"], target["id"])
        return self.db.get(target["id"])

    async def identify_reel(self, message: discord.Message, reel: dict):
        """A new reel: link it to the game its caption names, or ask the poster which game it is."""
        caption = "\n".join(filter(None, [reel.get("description"), reel.get("title"), sources.strip_links(message.content)]))
        appid = await sources.game_from_caption(self.web, caption, self.settings.country(reel["guild_id"]))
        if appid:
            try:
                game = await self.link_reel(reel, appid)
                log.info("Reel %s is %s (from its caption), linked", reel["key"], game["title"])
                await message.reply(f"This reel is **{game['title']}** (from its caption), so it's now a clip of that "
                                    "game in the catalog. Wrong game? Use `/link`.", mention_author=False)
                return
            except TurnedAway as exc:
                log.info("Reel %s names app %s, which was turned away: %s", reel["key"], appid, exc)
        await message.reply(
            f"{message.author.mention} which game is this reel about? I couldn't tell from its caption.",
            view=ReelGameView(reel["id"]), allowed_mentions=discord.AllowedMentions(users=[message.author]))

    @staticmethod
    def embed_meta(code: str, embeds: list[discord.Embed]) -> dict:
        """Discord's own link preview of a reel is often richer than what we can scrape."""
        for em in embeds:
            if em.url and code in em.url:
                image = (em.thumbnail and em.thumbnail.url) or (em.image and em.image.url)
                return sources.instagram_meta(em.title, em.description, image)
        return {}

    @staticmethod
    def origin(message: discord.Message) -> dict:
        return {
            "added_by": message.author.id,
            "added_by_name": message.author.display_name,
            "added_at": int(message.created_at.timestamp()),
            "channel_id": message.channel.id,
            "message_id": message.id,
            "note": sources.strip_links(message.content)[:500] or None,
        }

    async def archive_message(self, message: discord.Message, *, announce=True) -> tuple[int, int]:
        """Archive every game link in `message`. Returns (new entries, already-known entries)."""
        appids = sources.find_steam_ids(message.content)
        bnet = sources.find_battlenet(message.content)
        reels = sources.find_reels(message.content)
        if not appids and not bnet and not reels:
            return 0, 0
        gid, origin = message.guild.id, self.origin(message)
        results: list[tuple[dict, bool]] = []
        rejected: list[str] = []
        for appid in appids:
            try:
                results.append(await self.ensure_steam(gid, appid, origin))
            except TurnedAway as exc:
                rejected.append(str(exc))
        for key, url in bnet:
            try:
                results.append(await self.ensure_battlenet(gid, key, url, origin))
            except TurnedAway as exc:
                rejected.append(str(exc))
        for code, url in reels:
            if len(appids) + len(bnet) == 1:
                # Reel posted together with its game's page: attach it as a clip of that game.
                if not results:
                    continue  # that game was turned away, so its reel is too
                game = results[0][0]
                self.db.add_clip(game["id"], url)
                old = self.db.get_by_key(gid, f"ig:{code}")
                if old:
                    self.db.merge(old["id"], game["id"])
            else:
                results.append(await self.ensure_reel(gid, code, url, origin, message.embeds))

        for entry, created in results:
            self.db.link_message(message.id, entry["id"])
            if not created and entry["added_by"] != message.author.id:
                # Posting a game doesn't count as wanting to play it (that's what the button/🎮 is for),
                # but re-posting someone else's game does.
                self.db.add_interest(entry["id"], message.author.id, message.author.display_name)
        new = sum(created for _, created in results)
        dupes = [e for e, created in results if not created]

        if announce:
            if results:
                try:
                    await message.add_reaction(INTEREST_EMOJI)
                except discord.HTTPException:
                    pass
            lines = [
                f"**{e['title']}** is already in the catalog (added by <@{e['added_by']}> <t:{e['added_at']}:R>)"
                for e in dupes
            ]
            if any(e["added_by"] != message.author.id for e in dupes):
                lines.append("Counted you as wanting to play it")
            lines += rejected
            if lines:
                await message.reply("\n".join(lines), mention_author=False)
            for entry, created in results:
                if created and entry["kind"] == "instagram":
                    await self.identify_reel(message, entry)
        return new, len(dupes)

    # -- events --

    async def on_ready(self):
        log.info("Logged in as %s, in %d server(s)", self.user, len(self.guilds))
        self._connected = True
        for guild in self.guilds:
            self.sync_members(guild)  # who joined or left while the bot was offline
            watched = self.settings.watch_channels(guild.id)
            log.info("%s: watching %s", guild.name, ", ".join(map(str, watched)) if watched else
                     "no channels yet (an admin can pick them with /setup watch)")
        # Games people started while the bot was offline (or restarting) never send a "started" update
        for guild in self.guilds:
            for member in guild.members:
                if not member.bot:
                    self.mark_playing(member, self.games_playing(member))
        asyncio.create_task(self.site.warm_up())
        if not self._catching_up:  # on_ready also fires after reconnects
            self._catching_up = True
            try:
                await self.catch_up()
            finally:
                self._catching_up = False
            if not self.heartbeat.is_running():
                self.heartbeat.start()

    async def on_guild_join(self, guild: discord.Guild):
        """Added to another server: it gets its own empty catalog, and watches nothing until its admins run /setup."""
        log.info("Joined %s (%s)", guild.name, guild.id)
        self.sync_members(guild)

    async def on_guild_remove(self, guild: discord.Guild):
        """Its catalog is kept (in case the bot is added back), but its people no longer count as members."""
        log.info("Left %s (%s)", guild.name, guild.id)
        self.db.set_members(guild.id, [])

    async def on_member_join(self, member: discord.Member):
        if not member.bot:
            self.db.add_member(member.guild.id, member.id)

    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        self.db.remove_member(payload.guild_id, payload.user.id)

    async def on_disconnect(self):
        self._connected = False

    async def on_resumed(self):
        self._connected = True  # Discord sends what was posted in between, so nothing to catch up on

    @tasks.loop(minutes=1)
    async def heartbeat(self):
        """Remember when the bot was last online, so the next start knows what it missed. Only while it's really
        connected: when the internet drops, the time must stay at the moment it went, or the posts made while it
        was gone are skipped later (that's how two Steam links were missed on 2 Nov)."""
        if self._connected:
            self.db.set_meta("last_seen", int(time.time()))
            await self.ping_uptime()

    async def ping_uptime(self):
        """Tell the uptime monitor (HEALTHCHECK_URL, e.g. Healthchecks.io) that the bot is up and connected. When the
        pings stop for a few minutes, the monitor emails us. Only every UPTIME_PING_MINUTES, and never fatal."""
        if not HEALTHCHECK_URL or time.time() - self._last_uptime_ping < UPTIME_PING_MINUTES * 60:
            return
        self._last_uptime_ping = time.time()
        try:
            async with self.web.get(HEALTHCHECK_URL, timeout=aiohttp.ClientTimeout(total=10)):
                pass
        except Exception as exc:  # the monitor being down is no reason to stop the bot
            log.warning("Couldn't ping the uptime monitor: %s", exc)

    async def catch_up(self):
        """Handle what was posted and reacted to in the watched channels while the bot was offline:
        new game posts are archived (with the usual replies), and 🎮 reactions on recent game posts count."""
        last_seen = self.db.get_meta("last_seen")
        if not last_seen:
            return  # first start: nothing to catch up on
        posted_since = datetime.fromtimestamp(int(last_seen) - 60, timezone.utc)
        reacted_since = min(posted_since, discord.utils.utcnow() - CATCH_UP_REACTIONS)
        archived = reactions = 0
        for channel_id in [c for guild in self.guilds for c in self.settings.watch_channels(guild.id)]:
            channel = self.get_channel(channel_id)
            if channel is None:
                continue
            messages = [m async for m in channel.history(after=reacted_since, limit=None, oldest_first=True)]
            # Posts the bot already answered (e.g. "turned away") count as handled too, so they never get a second reply
            replied = {m.reference.message_id for m in messages if m.author.id == self.user.id and m.reference}
            for msg in messages:
                if msg.author.bot:
                    continue
                handled = bool(self.db.entries_for_message(msg.id)) or any(r.me for r in msg.reactions) or msg.id in replied
                if msg.created_at >= posted_since and not handled:
                    new, known = await self.archive_message(msg)
                    archived += new + known
                for reaction in msg.reactions:
                    if str(reaction.emoji) == INTEREST_EMOJI and reaction.count > reaction.me:
                        async for user in reaction.users():
                            if not user.bot:
                                for entry_id in self.db.entries_for_message(msg.id):
                                    self.db.add_interest(entry_id, user.id, user.display_name)
                                    reactions += 1
        log.info("Caught up since %s: %d game links archived, %d 🎮 reactions checked",
                 posted_since.astimezone().strftime("%Y-%m-%d %H:%M"), archived, reactions)

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not self.may_read(message.channel):
            return
        await self.ping_for_invite(message)
        if self.watches(message.channel):
            await self.archive_message(message)

    async def tag_to_play(self, entry: dict, sender: discord.Member, targets: list[discord.abc.Snowflake]):
        """Someone pressed "Tag" on a profile, or "Ping everyone" on a game, in the catalog:
        ping those people in the game's channel."""
        guild = self.get_guild(entry["guild_id"])
        channel = self.get_channel(entry["channel_id"] or 0) or (guild and self.post_channel(guild, "free_games"))
        if channel is None:
            raise RuntimeError(f"No channel to ping in for {entry['title']}")
        # Who's asking first, then the people asked on a line of their own, so the two can't be mixed up
        text = (f"{sender.mention} wants to play **{entry['title']}** and is asking you to join:\n"
                f"{' · '.join(f'<@{t.id}>' for t in targets)}")
        mentions = discord.AllowedMentions(users=list(targets))
        if entry["kind"] == "steam":  # a Steam card with fresh price and scores, and the link to get the game
            meta = await sources.fetch_steam(self.web, int(entry["key"].split(":")[1]),
                                             self.settings.country(entry["guild_id"])) or {}
            await channel.send(text, embed=steam_card(entry, meta), allowed_mentions=mentions)
        else:  # a reel: Discord's own preview of the link
            await channel.send(f"{text}\n{entry['url']}", allowed_mentions=mentions)
        log.info("%s tagged %d to play %s", sender.display_name, len(targets), entry["title"])

    async def ping_for_invite(self, message: discord.Message):
        """A game invite (Discord's "Invite to Join" or a steam://joinlobby link) for a catalog game:
        @mention everyone who wants to play it."""
        entry = None
        lobby = sources.STEAM_LOBBY_RE.search(message.content)
        if lobby:
            entry = self.db.get_by_key(message.guild.id, f"steam:{lobby.group(1)}")
        if entry is None and message.activity and message.application:
            entry_id = self.catalog_titles(message.guild.id).get(sources.normalize_title(message.application.name))
            entry = self.db.get(entry_id) if entry_id else None
        if entry is None:
            return
        invited = [uid for uid, _ in self.db.interested(entry["id"]) if uid != message.author.id]
        if not invited:
            return
        log.info("%s sent an invite for %s, pinging %d", message.author.display_name, entry["title"], len(invited))
        await message.reply(
            f"{' '.join(f'<@{uid}>' for uid in invited)}: {message.author.mention} is inviting you to play "
            f"**{entry['title']}**.", allowed_mentions=discord.AllowedMentions(users=True), mention_author=False)

    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if after.author.bot or not after.guild or not self.watches(after.channel):
            return
        if before.content != after.content:
            await self.archive_message(after, announce=False)
        # Discord fills in link previews by editing the message; use them for reels we couldn't scrape.
        for code, _url in sources.find_reels(after.content):
            entry = self.db.get_by_key(after.guild.id, f"ig:{code}")
            if entry and not entry["description"]:
                meta = self.embed_meta(code, after.embeds)
                if meta:
                    self.db.update(entry["id"], **meta)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id and str(payload.emoji) == INTEREST_EMOJI and payload.user_id != self.user.id:
            name = payload.member.display_name if payload.member else None
            for entry_id in self.db.entries_for_message(payload.message_id):
                self.db.add_interest(entry_id, payload.user_id, name)

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id and str(payload.emoji) == INTEREST_EMOJI:
            for entry_id in self.db.entries_for_message(payload.message_id):
                self.db.remove_interest(entry_id, payload.user_id)

    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Someone started a game that's in the catalog: mark them as playing it (and wanting to).
        The mark goes again once they stop (see track_playtime), unless they set it themselves."""
        if not after.bot:
            self.mark_playing(after, self.games_playing(after) - self.games_playing(before))

    @staticmethod
    def games_playing(member: discord.Member) -> set[str]:
        return {a.name for a in member.activities if a.type == discord.ActivityType.playing and a.name}

    def mark_playing(self, member: discord.Member, names: set[str]):
        """Mark `member` as playing (and wanting to play) each of these games that's in the catalog."""
        titles = self.catalog_titles(member.guild.id)
        now = int(time.time())
        if names:  # Discord shares their games, so the catalog's tip about activity sharing isn't needed
            self.db.saw_playing(member.id, now)
        for name in names:
            entry_id = titles.get(sources.normalize_title(name))
            if entry_id:
                self.mark_entry_playing(member, entry_id, name, now)

    def mark_entry_playing(self, member: discord.Member, entry_id: int, name: str, now: int, via: str = "Discord"):
        self.db.saw_game(entry_id, member.id, now)  # their rating of it shows a "played" badge
        if member.id not in {uid for uid, _ in self.db.players(entry_id)}:
            self.db.set_playing(entry_id, member.id, member.display_name, True, now, by_bot=True)
            self.db.add_interest(entry_id, member.id, member.display_name)
            log.info("%s is playing %s (seen on %s), marked as playing", member.display_name, name, via)
        else:
            self.db.still_playing(entry_id, member.id, now)

    @tasks.loop(minutes=2)
    async def steam_now_playing(self):
        """Mark people as playing from Steam too: catches those whose Discord doesn't show their games
        (activity sharing off, browser Discord, ...), as long as they linked Steam and their profile is public."""
        accounts = self.db.steam_accounts()
        if not STEAM_API_KEY or not accounts:
            return
        try:
            playing = await sources.fetch_now_playing(self.web, STEAM_API_KEY, list(set(accounts.values())))
            now = int(time.time())
            for uid, steam_id in accounts.items():
                appid = playing.get(steam_id)
                if appid is None:
                    continue
                for guild in self.guilds:
                    entry = self.db.get_by_key(guild.id, f"steam:{appid}")
                    member = guild.get_member(uid)
                    if entry and member:
                        self.mark_entry_playing(member, entry["id"], entry["title"], now, via="Steam")
        except Exception:
            log.exception("Steam now-playing check failed")

    @steam_now_playing.before_loop
    async def _steam_wait(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=TRACK_MINUTES)
    async def track_playtime(self):
        """Count hours from Discord: everyone shown "Playing" a catalog game gets TRACK_MINUTES more of it.
        Steam can't tell about Battle.net games (or people without a linked Steam), so this gives them hours too."""
        now = int(time.time())
        for guild in self.guilds:
            titles = self.catalog_titles(guild.id)
            for member in guild.members:
                if member.bot:
                    continue
                for entry_id in {titles.get(sources.normalize_title(n)) for n in self.games_playing(member)} - {None}:
                    self.db.add_tracked_playtime(entry_id, member.id, TRACK_MINUTES, now)
                    self.db.still_playing(entry_id, member.id, now)
        # Playing marks the bot set go once Discord and Steam stop showing the game (not on the first run after a
        # start: the Steam check hasn't had its first look yet)
        stale = self.db.end_stale_playing(now - PLAYING_GRACE_MINUTES * 60) if self.track_playtime.current_loop else []
        for r in stale:
            log.info("%s stopped playing %s, no longer marked as playing", r["user_name"], r["title"])

    @track_playtime.before_loop
    async def _track_wait(self):
        await self.wait_until_ready()

    def catalog_titles(self, guild_id: int) -> dict[str, int]:
        built, titles = self._titles.get(guild_id, (0, {}))
        if time.monotonic() - built > 300:  # rebuilt every 5 minutes so new games are picked up
            titles = {sources.normalize_title(e["title"]): e["id"] for e in self.db.query(guild_id, source="games")}
            self._titles[guild_id] = (time.monotonic(), titles)
        return titles


bot = GameBot()


# -- autocomplete & lookups ---------------------------------------------------

def entry_choices(rows: list[dict]) -> list[Choice]:
    return [Choice(name=f"{r['title'][:90]} (#{r['id']})", value=str(r["id"])) for r in rows]


async def entry_autocomplete(interaction: discord.Interaction, current: str) -> list[Choice]:
    return entry_choices(bot.db.search(interaction.guild_id, current))


async def reel_autocomplete(interaction: discord.Interaction, current: str) -> list[Choice]:
    return entry_choices(bot.db.search(interaction.guild_id, current, kind="instagram"))


async def steam_autocomplete(interaction: discord.Interaction, current: str) -> list[Choice]:
    if len(current) < 2 or sources.STEAM_RE.search(current):
        return []
    try:
        hits = await asyncio.wait_for(sources.search_steam(bot.web, current, bot.settings.country(interaction.guild_id)), timeout=2.5)
    except asyncio.TimeoutError:
        return []
    return [Choice(name=name[:100], value=str(appid)) for appid, name in hits[:25]]


async def genre_autocomplete(interaction: discord.Interaction, current: str) -> list[Choice]:
    genres, tags = bot.db.genre_counts(interaction.guild_id)
    names = [g for g, _ in (genres + tags).most_common() if current.lower() in g.lower()]
    return [Choice(name=g, value=g) for g in names[:25]]


def find_entry(interaction: discord.Interaction, value: str) -> dict | None:
    if value.isdigit():
        entry = bot.db.get(int(value))
        if entry and entry["guild_id"] == interaction.guild_id:
            return entry
    hits = bot.db.search(interaction.guild_id, value, limit=1)
    return hits[0] if hits else None


async def find_appid(value: str, guild_id: int | None) -> int | None:
    ids = sources.find_steam_ids(value)
    if ids:
        return ids[0]
    if value.isdigit():
        return int(value)
    hits = await sources.search_steam(bot.web, value, bot.settings.country(guild_id))
    return hits[0][0] if hits else None


async def not_found(interaction: discord.Interaction, what="that game"):
    await interaction.response.send_message(f"Couldn't find {what} in the catalog.", ephemeral=True)


# -- commands -----------------------------------------------------------------

class NotAdmin(app_commands.CheckFailure):
    pass


async def is_admin(interaction: discord.Interaction) -> bool:
    """The server's admins (set with /setup admin, or ADMIN_IDS in .env for the home server; mods if there are none)."""
    if not bot.settings.is_admin(interaction.guild_id, interaction.user.id, interaction.permissions.manage_messages):
        raise NotAdmin()
    return True


def admin_only(func):
    """Only the server's admins can use this; regular members don't see it."""
    return app_commands.default_permissions(manage_messages=True)(app_commands.check(is_admin)(func))


@bot.tree.error
async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, NotAdmin):
        text = "Only the bot's admin can use this command. Everyone can use /catalog, /random and /link."
    else:
        log.error("Command /%s failed", interaction.command and interaction.command.name, exc_info=error)
        text = "Something went wrong — try again."
    send = interaction.followup.send if interaction.response.is_done() else interaction.response.send_message
    await send(text, ephemeral=True)


def badge_file(name: str) -> discord.File:
    return discord.File(os.path.join(BADGES_DIR, name), filename=name)


def achievement_view(guild_id: int, uid: int, items: list[tuple[str, int]]) -> tuple[ui.LayoutView, list[discord.File]]:
    """One message for everything `uid` earned in the server since their last one, each with its badge on the right."""
    parts = []  # (what happened, its name, the detail line, badge picture, colour)
    for key, tier in items:
        if key == achievements.TITLE.key:
            count = next((n for u, n, _ in achievements.refund_counts(bot.db.conn, guild_id) if u == uid), 0)
            prev, _, held = (bot.db.get_guild_meta(guild_id, "refund_title_from") or ":0").partition(":")
            detail = f"{count} refunds" + (f", more than <@{prev}>'s {held}, who held it until now"
                                           if prev and int(prev) != uid else "")
            parts.append(("is now the", "**Biggest refunder**", detail, "biggest_refunder.png", achievements.TITLE_COLOR))
        elif achievements.BY_KEY[key].secret:  # the channel only learns that it was a secret one
            parts.append(("unlocked a", "**Secret achievement**", "Unlock it yourself to see what it is",
                          "secret.png", achievements.SECRET_COLOR))
        else:
            a = achievements.BY_KEY[key]
            parts.append(("unlocked", f"**{a.label(tier)}**", a.desc.format(n=a.thresholds[tier - 1]), a.badge(tier), a.color(tier)))
    many = len(parts) > 1
    shown: list[ui.Item] = [ui.TextDisplay(f"<@{uid}> earned {len(parts)} achievements")] if many else []
    for verb, name, detail, badge, _ in parts:
        head = (f"Now the {name}" if verb == "is now the" else name) if many else f"<@{uid}> {verb} {name}"
        shown.append(ui.Section(ui.TextDisplay(f"{head}\n-# {detail}"), accessory=ui.Thumbnail(f"attachment://{badge}")))
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*shown, accent_colour=int(parts[0][4][1:], 16)))
    return view, [badge_file(badge) for *_, badge, _ in parts]


def achievements_launch_view(guild_id: int) -> ui.LayoutView:
    """The one-time post when achievements go live in a server. Pings nobody (the bot's default)."""
    lines = ["Earn them by adding games, playing catalog games together, and more. Some are secret. "
             "You've already earned some."]
    holder = bot.db.get_guild_meta(guild_id, "refund_title")
    if holder:
        count = next((n for u, n, _ in achievements.refund_counts(bot.db.conn, guild_id) if u == int(holder)), 0)
        lines.append(f"<@{holder}> holds the **Biggest refunder** title with {count} refunds. Refund more than that to take it.")
    lines.append("Hover anyone's avatar in the catalog to see their achievements and the scores they've given. "
                 "New ones are announced here.")
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(
        ui.TextDisplay("## Achievements are here"),
        ui.MediaGallery(discord.MediaGalleryItem("attachment://launch.png")),
        ui.TextDisplay("\n\n".join(lines)),
        ui.ActionRow(OpenCatalogButton()),
        ui.TextDisplay("-# One message a day at most per person, so nobody gets spammed"),
        accent_colour=0xF0B232))
    return view


def progress_bar(done: int, total: int, width: int = 12) -> str:
    filled = round(done / total * width) if total else width
    return "🟩" * filled + "⬛" * (width - filled)


def played_rule_view(progress: tuple[int, int], promoted: list[str] = ()) -> ui.LayoutView:
    """The one-time post asking everyone to help sort out who played the Played games. Pings nobody.
    `progress`: (Played games with enough players, all Played games), kept up to date by editing the post."""
    full, total = progress
    lines = [f"From now on a game moves to **Played** once {PLAYERS_NEEDED} of you have played it. You count when you "
             "rate it, refund it, have Steam hours in it, the bot sees you play it, or you say so yourself. "
             "Your ratings are kept, and nothing is taken out of Played."]
    if promoted:
        shown = ", ".join(f"**{t}**" for t in promoted[:10]) + (f" and {len(promoted) - 10} more" if len(promoted) > 10 else "")
        lines.append(f"Now Played because {PLAYERS_NEEDED} or more of you have played them: {shown}.")
    lines.append(f"**{full} of {total}** Played games have {PLAYERS_NEEDED} players. " + (
        f"The other {total - full} need a hand: press the button to see the ones the bot can't tell about for you. "
        "It takes a few minutes." if full < total else "All of them, thanks everyone!"))
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(
        ui.TextDisplay(f"## Played now needs {PLAYERS_NEEDED} players"),
        ui.TextDisplay("\n\n".join(lines)),
        ui.TextDisplay(progress_bar(full, total)),
        ui.ActionRow(CheckPlayedButton(), OpenCatalogButton()),
        ui.TextDisplay(f"-# Played games still under {PLAYERS_NEEDED} players after <t:{PLAYED_RULE_DEADLINE}:D> keep their "
                       f"spot, marked “Played before the {PLAYERS_NEEDED}-player rule”"),
        accent_colour=STATUS_COLORS["played"]))
    return view


def played_rule_reminder_view(left: int) -> ui.LayoutView:
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(
        ui.TextDisplay(f"## {left} Played game{'s' if left != 1 else ''} still need{'' if left != 1 else 's'} players\n"
                       f"Have you played any of them? Answer before <t:{PLAYED_RULE_DEADLINE}:R>. After that they keep "
                       f"their spot, marked “Played before the {PLAYERS_NEEDED}-player rule”."),
        ui.ActionRow(CheckPlayedButton()),
        accent_colour=STATUS_COLORS["played"]))
    return view


def catch_up_view(guild_id: int, user_id: int, note: str | None = None) -> ui.LayoutView:
    """"Have you played these?", only for `user_id`: the first few Played games short of players that the bot
    doesn't know they played and they haven't answered for. Answered games drop off and the next ones come up."""
    games = bot.db.catch_up_games(guild_id, user_id)
    items: list[ui.Item] = [ui.TextDisplay(note)] if note else []
    if not games:
        items.append(ui.TextDisplay("## All done\nYou've answered for every Played game that's short of players. Thanks!"))
    else:
        items.append(ui.TextDisplay(f"## Have you played these?\n-# {len(games)} left for you: Played games with fewer "
                                    f"than {PLAYERS_NEEDED} players that the bot can't tell about for you"))
        for e in games[:CATCH_UP_PAGE]:
            n = len(e["played_by"])
            text = ui.TextDisplay(f"**{md_link(e['title'], e['url'])}**\n-# {n} of {PLAYERS_NEEDED} players")
            items.append(ui.Section(text, accessory=ui.Thumbnail(e["image"])) if e["image"] else text)
            items.append(ui.ActionRow(PlayedAnswerButton(e["id"], "y"), PlayedAnswerButton(e["id"], "n")))
        if len(games) > CATCH_UP_PAGE:
            items.append(ui.TextDisplay(f"-# {len(games) - CATCH_UP_PAGE} more come up as you answer these"))
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=STATUS_COLORS["played"]))
    return view


class CheckPlayedButton(ui.DynamicItem[ui.Button], template=r"played-check"):
    """"Check my games" on the Played rule posts: opens "Have you played these?" just for the person who pressed it."""

    def __init__(self):
        super().__init__(ui.Button(label="Check my games", style=discord.ButtonStyle.success, custom_id="played-check"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=catch_up_view(interaction.guild_id or GUILD_ID, interaction.user.id),
                                                ephemeral=True)


class PlayedAnswerButton(ui.DynamicItem[ui.Button], template=r"played:(?P<entry>\d+):(?P<answer>[yn])"):
    """"Played it" / "Didn't play" in "Have you played these?". Dynamic, so it still works after the bot restarts."""

    def __init__(self, entry_id: int, answer: str):
        label, style = ("Played it", discord.ButtonStyle.success) if answer == "y" else ("Didn't play", discord.ButtonStyle.secondary)
        super().__init__(ui.Button(label=label, style=style, custom_id=f"played:{entry_id}:{answer}"))
        self.entry_id, self.answer = entry_id, answer

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match):
        return cls(int(match["entry"]), match["answer"])

    async def callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or GUILD_ID
        note = answer_played(guild_id, interaction.user, self.entry_id, self.answer == "y")
        await interaction.response.edit_message(view=catch_up_view(guild_id, interaction.user.id, note))


def answer_played(guild_id: int, user, entry_id: int, played: bool) -> str | None:
    """Save `user`'s answer from "Have you played these?" -> the line saying what was saved (None: no such game)."""
    entry = bot.db.get(entry_id)
    if not entry or entry["guild_id"] != guild_id:
        return None
    bot.db.set_played_answer(entry["id"], user.id, played, int(time.time()))
    bot.promote_played()
    log.info("%s %s %s (from Discord)", user.display_name, "played" if played else "didn't play", entry["title"])
    if not played:
        return f"-# Saved: you didn't play **{entry['title']}**"
    n = len(bot.db.played_by(entry["id"]))
    return (f"-# Saved: you played **{entry['title']}** ({n} of {PLAYERS_NEEDED} players"
            f"{', so it counts as played now' if n >= PLAYERS_NEEDED else ''}). Change it in the catalog's status menu")


class OpenCatalogButton(ui.DynamicItem[ui.Button], template=r"open-catalog"):
    """Opens the catalog Activity, like /catalog. Dynamic, so it still works after the bot restarts."""

    def __init__(self):
        super().__init__(ui.Button(label="Open the catalog", style=discord.ButtonStyle.primary, custom_id="open-catalog"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        await open_catalog(interaction)


def gotg_picks(guild_id: int, today: date) -> list[list]:
    """Today's #GOTG games as stored: [[entry id, "owned" | "buy"]]. Games posted in the last REST_DAYS days rest."""
    rest_from = (today - timedelta(days=gotg.REST_DAYS - 1)).isoformat()
    return [[e["id"], group] for e, group in gotg.pick(bot.db, guild_id, today.isoformat(), rest_from)]


def gotg_line(e: dict, group: str, ins: list[int], owners: dict[int, set[int]]) -> str:
    """One game in the #GOTG post: what it is, who has it or wants it, and who's in tonight."""
    kind = "Co-op" if e["coop"] else "Multiplayer"
    if group == "owned":
        stars = [s for _, s in bot.db.ratings(e["id"])]
        last = bot.db.last_played().get(e["id"])
        bits = [f"{len(owners.get(gotg.appid(e), ()))} of you own it", kind,
                stars and f"our rating {sum(stars) / len(stars):.1f}", last and f"last played <t:{last}:R>"]
    else:
        price = "Free" if e["price_cents"] == 0 else e["price_text"] and (
            f"{e['price_text']} (−{e['discount_pct']}%)" if e["discount_pct"] else e["price_text"])
        wants = [uid for uid, _ in bot.db.interested(e["id"])]
        bits = [price, kind, e["review_pct"] is not None and f"{e['review_pct']}% on Steam",
                f"{len(wants)} want to play: " + " ".join(f"<@{uid}>" for uid in wants[:8])]
    line = f"**{md_link(e['title'], e['url'])}**\n-# " + " · ".join(b for b in bits if b)
    if ins:
        line += "\nIn tonight: " + " ".join(f"<@{uid}>" for uid in ins)
    return line


def gotg_view(guild_id: int, day: str) -> ui.LayoutView:
    """The server's #GOTG post of `day`, with who's in so far (redrawn on every "I'm in"). Pings nobody."""
    post, owners = bot.db.gotg_post(guild_id, day), bot.db.owners(guild_id)
    over = day != bot.settings.now(guild_id).date().isoformat()
    items: list[ui.Item] = [ui.TextDisplay(
        "## Get on the game #GOTG\nWho's up for a game tonight? Press **I'm in** on the ones you'd play. Once "
        f"{gotg.PLAYERS_TO_PING} of you are in on the same game, the bot pings the others who have it or want it.")]
    for group, heading in (("owned", "### Already in your libraries"), ("buy", "### Worth buying")):
        games = [e for eid, g in post["picks"] if g == group and (e := bot.db.get(eid))]
        if games:
            items.append(ui.TextDisplay(heading))
        for e in games:
            ins = bot.db.gotg_in(day, e["id"])
            text = ui.TextDisplay(gotg_line(e, group, ins, owners))
            items.append(ui.Section(text, accessory=ui.Thumbnail(e["image"])) if e["image"] else text)
            items.append(ui.ActionRow(GotgInButton(day, e["id"], len(ins), over)))
    items.append(ui.ActionRow(OpenCatalogButton()))
    items.append(ui.TextDisplay(f"-# A new one every day at {GOTG_TIME:%H:%M} · “Own it” goes by the Steam libraries "
                                f"linked to Discord ({len({u for us in owners.values() for u in us})} people)"))
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(*items, accent_colour=ACCENT))
    return view


def gotg_press(guild_id: int, day: str, entry_id: int, user) -> tuple[str | None, list[int]]:
    """`user` pressed "I'm in" (again = out) on the server's #GOTG. When that makes PLAYERS_TO_PING, the first time for
    this game today: (the message pinging the others who own it or want it, who to ping). Otherwise (None, [])."""
    ins = bot.db.gotg_in(day, entry_id)
    joining = user.id not in ins
    bot.db.set_gotg_in(day, entry_id, user.id, joining, int(time.time()))
    entry = bot.db.get(entry_id)
    log.info("%s is %s for %s (#GOTG)", user.display_name, "in" if joining else "out", entry["title"])
    ins = bot.db.gotg_in(day, entry_id)
    pinged = f"gotg_pinged:{day}:{entry_id}"
    if not joining or len(ins) < gotg.PLAYERS_TO_PING or bot.db.get_meta(pinged):
        return None, []
    bot.db.set_meta(pinged, int(time.time()))
    group = dict(bot.db.gotg_post(guild_id, day)["picks"]).get(entry_id)
    others = {uid for uid, _ in bot.db.interested(entry_id)}
    if group == "owned":
        others |= bot.db.owners(guild_id).get(gotg.appid(entry), set())
    others = sorted(others - set(ins))
    text = (f"**{len(ins)} of you are in for {entry['title']} tonight:** " + " · ".join(f"<@{uid}>" for uid in ins)
            + ("\nJoin them: " +" · ".join(f"<@{uid}>" for uid in others) if others else ""))
    return text, others


class GotgInButton(ui.DynamicItem[ui.Button], template=r"gotg:(?P<day>\d{4}-\d{2}-\d{2}):(?P<entry>\d+)"):
    """"I'm in" on a #GOTG post, for one game. Works the day of the post; dynamic, so it survives restarts."""

    def __init__(self, day: str, entry_id: int, count: int = 0, over: bool = False):
        label = "Over" if over else f"I'm in · {count}" if count else "I'm in"
        super().__init__(ui.Button(label=label, style=discord.ButtonStyle.success, disabled=over,
                                   custom_id=f"gotg:{day}:{entry_id}"))
        self.day, self.entry_id = day, entry_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match):
        return cls(match["day"], int(match["entry"]))

    async def callback(self, interaction: discord.Interaction):
        gid = interaction.guild_id or 0
        post = bot.db.gotg_post(gid, self.day)
        if self.day != bot.settings.now(gid).date().isoformat() or not post or self.entry_id not in dict(post["picks"]):
            await interaction.response.send_message(
                f"This #GOTG is over. A new one comes at {GOTG_TIME:%H:%M}.", ephemeral=True)
            return
        text, ping = gotg_press(gid, self.day, self.entry_id, interaction.user)
        await interaction.response.edit_message(view=gotg_view(gid, self.day))
        if text:
            await interaction.message.reply(text, allowed_mentions=discord.AllowedMentions(
                users=[discord.Object(uid) for uid in ping]), mention_author=False)
            log.info("#GOTG: %d in, pinged %d", gotg.PLAYERS_TO_PING, len(ping))


@bot.tree.command(description="Open the interactive game catalog")
@app_commands.guild_only()
async def catalog(interaction: discord.Interaction):
    await open_catalog(interaction)


async def open_catalog(interaction: discord.Interaction):
    try:
        await interaction.response.launch_activity()  # opens web.py's page inside Discord
        return
    except discord.HTTPException as exc:
        log.warning("Couldn't launch the catalog Activity (%s), sending a link instead", exc)
    if not bot.site.public_url:
        return await interaction.response.send_message("The catalog page isn't online right now.", ephemeral=True)
    view = discord.ui.View()
    view.add_item(ui.Button(style=discord.ButtonStyle.link, label="Open game catalog", url=bot.site.public_url))
    try:
        await interaction.response.send_message("**Game catalog**: search, sort and filter every game", view=view)
    except discord.HTTPException as exc:  # the interaction may have expired while the launch was attempted
        log.warning("Couldn't send the catalog link either (%s)", exc)


@bot.tree.command(description="Show everything about one game")
@admin_only
@app_commands.guild_only()
@app_commands.autocomplete(game=entry_autocomplete)
async def game(interaction: discord.Interaction, game: str):
    entry = find_entry(interaction, game)
    if not entry:
        return await not_found(interaction)
    await interaction.response.send_message(view=CardView(entry))


@bot.tree.command(name="random", description="Can't decide? Pick a random game to play together")
@app_commands.guild_only()
@app_commands.describe(genre="Only pick from this genre/tag", coop_only="Only co-op games",
                       include_played="Also pick games you've already played")
@app_commands.autocomplete(genre=genre_autocomplete)
async def random_game(interaction: discord.Interaction, genre: str | None = None, coop_only: bool = False,
                      include_played: bool = False):
    rows = bot.db.query(interaction.guild_id, genre=genre, coop=coop_only, source="games",
                        status=None if include_played else ("want", "demo"))
    if not rows:
        return await interaction.response.send_message("No games match that.", ephemeral=True)
    await RandomView(interaction.user.id, rows).send(interaction)


@bot.tree.command(description="Add a Steam game without posting a link")
@admin_only
@app_commands.guild_only()
@app_commands.describe(game="Steam link, app id or name", note="Why you want to play it")
@app_commands.autocomplete(game=steam_autocomplete)
async def add(interaction: discord.Interaction, game: str, note: str | None = None):
    await interaction.response.defer()
    appid = await find_appid(game, interaction.guild_id)
    if not appid:
        return await interaction.followup.send(f"Couldn't find “{game}” on Steam.")
    user = interaction.user
    origin = {"added_by": user.id, "added_by_name": user.display_name, "added_at": int(time.time()),
              "channel_id": interaction.channel_id, "note": note}
    try:
        entry, created = await bot.ensure_steam(interaction.guild_id, appid, origin)
    except TurnedAway as exc:
        return await interaction.followup.send(str(exc))
    if created:
        heading = "-# Added to the catalog. Press \"I want to play\" in /catalog if you want to play it"
    elif entry["added_by"] == user.id:
        heading = "-# You already added this one"
    else:
        bot.db.add_interest(entry["id"], user.id, user.display_name)
        heading = "-# Already in the catalog — counted you as interested"
    await interaction.followup.send(view=CardView(bot.db.get(entry["id"]), heading))


@bot.tree.command(description="Let a game into the catalog even though its Steam page breaks the rules")
@admin_only
@app_commands.guild_only()
@app_commands.describe(game="Steam link, app id or name")
@app_commands.autocomplete(game=steam_autocomplete)
async def allow(interaction: discord.Interaction, game: str):
    """For games Steam describes wrongly, e.g. playable online though Steam only lists split screen."""
    await interaction.response.defer()
    appid = await find_appid(game, interaction.guild_id)
    if not appid:
        return await interaction.followup.send(f"Couldn't find “{game}” on Steam.")
    bot.allow_app(interaction.guild_id, appid)
    # Credit whoever posted it first in the server's watched channels; nobody did -> the admin
    first = None
    for channel_id in bot.settings.watch_channels(interaction.guild_id):
        channel = bot.get_channel(channel_id)
        if channel:
            async for msg in channel.history(limit=None, oldest_first=True):
                if not msg.author.bot and appid in sources.find_steam_ids(msg.content):
                    first = msg
                    break
        if first:
            break
    if first:
        origin = bot.origin(first)
    else:
        user = interaction.user
        origin = {"added_by": user.id, "added_by_name": user.display_name, "added_at": int(time.time()),
                  "channel_id": interaction.channel_id}
    try:
        entry, created = await bot.ensure_steam(interaction.guild_id, appid, origin)
    except TurnedAway as exc:  # only "not on Steam" is left
        return await interaction.followup.send(str(exc))
    if first:
        bot.db.link_message(first.id, entry["id"])
    heading = "-# Allowed and added to the catalog" if created else "-# Allowed (it was already in the catalog)"
    await interaction.followup.send(view=CardView(bot.db.get(entry["id"]), heading))


@bot.tree.command(description="Tell the bot which Steam game an Instagram reel is about")
@app_commands.guild_only()
@app_commands.describe(reel="The unidentified reel", game="Steam link, app id or name")
@app_commands.autocomplete(reel=reel_autocomplete, game=steam_autocomplete)
async def link(interaction: discord.Interaction, reel: str, game: str):
    entry = find_entry(interaction, reel)
    if not entry or entry["kind"] != "instagram":
        return await not_found(interaction, "that reel")
    await interaction.response.defer()
    appid = await find_appid(game, interaction.guild_id)
    if not appid:
        return await interaction.followup.send(f"Couldn't find “{game}” on Steam.")
    try:
        target = await bot.link_reel(entry, appid)
    except TurnedAway as exc:
        return await interaction.followup.send(f"{exc} The reel wasn't linked.")
    await interaction.followup.send(view=CardView(target, "-# Linked. The reel is now a clip of this game"))


@bot.tree.command(description="Mark a game as not played yet, demo played or played")
@admin_only
@app_commands.guild_only()
@app_commands.autocomplete(game=entry_autocomplete)
@app_commands.choices(status=STATUS_CHOICES)
async def status(interaction: discord.Interaction, game: str, status: str):
    entry = find_entry(interaction, game)
    if not entry:
        return await not_found(interaction)
    players = len(bot.db.played_by(entry["id"]))
    if status != "played" and players >= PLAYERS_NEEDED:  # it would only go back to Played on the next check
        return await interaction.response.send_message(
            f"**{entry['title']}** stays Played: {players} of you have played it.", ephemeral=True)
    bot.db.update(entry["id"], status=status)
    await interaction.response.send_message(f"**{entry['title']}** is now marked {STATUSES[status][1]}")


@bot.tree.command(description="Add or remove your own tags/genres on a game (comma separated)")
@admin_only
@app_commands.guild_only()
@app_commands.autocomplete(game=entry_autocomplete)
@app_commands.describe(add="Tags to add, e.g. horror, party, 4 players", remove="Tags to remove")
async def tag(interaction: discord.Interaction, game: str, add: str | None = None, remove: str | None = None):
    entry = find_entry(interaction, game)
    if not entry:
        return await not_found(interaction)
    split = lambda s: [t.strip() for t in (s or "").split(",") if t.strip()]
    drop = {t.lower() for t in split(remove)}
    tags = [t for t in dict.fromkeys(entry["custom_tags"] + split(add)) if t.lower() not in drop]
    bot.db.update(entry["id"], custom_tags=tags)
    await interaction.response.send_message(
        f"**{entry['title']}** tags: {chips(tags) if tags else 'no custom tags'}")


@bot.tree.command(description="Re-fetch a game's Steam info (price, reviews, tags)")
@admin_only
@app_commands.guild_only()
@app_commands.autocomplete(game=entry_autocomplete)
async def refresh(interaction: discord.Interaction, game: str):
    entry = find_entry(interaction, game)
    if not entry or entry["kind"] != "steam":
        return await not_found(interaction, "that Steam game")
    await interaction.response.defer()
    meta = await sources.fetch_steam(bot.web, int(entry["key"].split(":")[1]), bot.settings.country(entry["guild_id"]))
    if not meta:
        return await interaction.followup.send("Steam didn't answer — try again later.")
    bot.db.update(entry["id"], **stored_fields(meta))
    await interaction.followup.send(view=CardView(bot.db.get(entry["id"]), "-# Refreshed from Steam"))


@bot.tree.command(description="Remove a game from the catalog")
@admin_only
@app_commands.guild_only()
@app_commands.autocomplete(game=entry_autocomplete)
async def remove(interaction: discord.Interaction, game: str):
    entry = find_entry(interaction, game)
    if not entry:
        return await not_found(interaction)
    bot.db.delete(entry["id"])
    bot._titles.pop(interaction.guild_id, None)
    await interaction.response.send_message(f"Removed **{entry['title']}** from the catalog.")


@bot.tree.command(description="List the genres and tags in the catalog")
@admin_only
@app_commands.guild_only()
async def genres(interaction: discord.Interaction):
    genre_counts, tag_counts = bot.db.genre_counts(interaction.guild_id)
    fmt = lambda c: " ".join(f"`{name} · {n}`" for name, n in c.most_common(25)) or "—"
    view = ui.LayoutView(timeout=None)
    view.add_item(ui.Container(
        ui.TextDisplay("## Genres and tags"),
        ui.TextDisplay(f"**Genres**\n{fmt(genre_counts)}"),
        ui.TextDisplay(f"**Popular Steam tags**\n{fmt(tag_counts)}"),
        ui.Separator(),
        ui.TextDisplay("-# Use `/catalog genre:<name>` or the genre menu in /catalog to filter"),
        accent_colour=ACCENT,
    ))
    await interaction.response.send_message(view=view)


@bot.tree.command(description="Download the whole catalog as a sortable web page")
@admin_only
@app_commands.guild_only()
async def export(interaction: discord.Interaction):
    await interaction.response.defer()
    rows, users = await bot.site.catalog(interaction.guild)
    page = render_catalog(rows, interaction.guild.name, users=users,
                          guild_icon=interaction.guild.icon.with_size(64).url if interaction.guild.icon else None)
    await interaction.followup.send(
        f"{len(rows)} games. Open the file in your browser to sort and filter.",
        file=discord.File(io.BytesIO(page.encode("utf-8")), filename="game-catalog.html"))


@bot.tree.command(description="Archive links that were posted before the bot joined")
@admin_only
@app_commands.guild_only()
@app_commands.describe(channel="Channel to scan (default: this one)",
                       limit="How many recent messages to scan (default: the whole channel)")
async def backfill(interaction: discord.Interaction, channel: discord.TextChannel | None = None,
                   limit: app_commands.Range[int, 1, 100000] | None = None):
    channel = channel or interaction.channel
    if not bot.may_read(channel):
        return await interaction.response.send_message(
            "I only read the channels picked with `/setup watch` and `/setup read`. See them with `/setup show`.",
            ephemeral=True)
    await interaction.response.defer(thinking=True)
    messages = [m async for m in channel.history(limit=limit)]
    messages.reverse()  # oldest first, so "added by" goes to whoever posted it first
    new = known = 0
    for msg in messages:
        if msg.author.bot:
            continue
        n, d = await bot.archive_message(msg, announce=False)
        if not d and (sources.find_steam_ids(msg.content) or sources.find_reels(msg.content)):
            await asyncio.sleep(1)  # it was looked up on Steam/Instagram: go easy on their rate limits
        new, known = new + n, known + d
        if n or d:
            for reaction in msg.reactions:
                if str(reaction.emoji) == INTEREST_EMOJI:
                    async for user in reaction.users():
                        if not user.bot:
                            for entry_id in bot.db.entries_for_message(msg.id):
                                bot.db.add_interest(entry_id, user.id, user.display_name)
    summary = (f"Scanned {len(messages)} messages in {channel.mention}: {new} new games/reels archived, "
               f"{known} links were already known.")
    try:
        await interaction.followup.send(summary)
    except discord.HTTPException:  # the reply window closes after 15 minutes; long scans post in the channel
        await interaction.channel.send(summary)


# -- /setup: each server's own channels and admins (guilds.py) -----------------

setup = app_commands.Group(name="setup", description="Pick the channels the bot reads and posts in, and its admins",
                           guild_only=True, default_permissions=discord.Permissions(manage_messages=True))
bot.tree.add_command(setup)
POST_CHOICES = [Choice(name=label, value=key) for key, label in POSTS.items()]


def mention_channel(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else "off"


def setup_summary(guild: discord.Guild) -> str:
    s, gid = bot.settings, guild.id
    watched = s.watch_channels(gid)
    extra = s.read_channels(gid) - set(watched)
    lines = ["## Bot setup for this server",
             "**Game links from:** " + (" ".join(map(mention_channel, watched)) or "no channels yet: use `/setup watch`"),
             "**Also reads:** " + (" ".join(map(mention_channel, sorted(extra))) or "nothing else")]
    for key, label in POSTS.items():
        channel = bot.post_channel(guild, key)
        lines.append(f"**{label}:** " + (channel.mention if channel else "off"))
    admins = s.admins(gid)
    lines.append("**Admins:** " + (" ".join(f"<@{u}>" for u in sorted(admins)) if admins
                                   else "everyone with Manage Messages"))
    lines.append(f"**Prices from:** Steam's {s.country(gid).upper()} store")
    lines.append(f"**Time zone:** {s.timezone(gid)} (it's {s.now(gid):%H:%M} there now)")
    return "\n".join(lines)


@setup.command(name="show", description="Show which channels the bot uses here, and who its admins are")
@app_commands.check(is_admin)
async def setup_show(interaction: discord.Interaction):
    await interaction.response.send_message(setup_summary(interaction.guild), ephemeral=True)


@setup.command(name="watch", description="Put game links posted in a channel into the catalog (or stop)")
@app_commands.check(is_admin)
@app_commands.describe(channel="The channel", on="Watch it (default), or stop watching it")
async def setup_watch(interaction: discord.Interaction, channel: discord.TextChannel, on: bool = True):
    bot.settings.set_watched(interaction.guild_id, channel.id, on)
    log.info("%s %s %s for game links", interaction.user.display_name, "watches" if on else "stopped watching",
             channel.name)
    text = (f"Game links in {channel.mention} now go into the catalog. Run `/backfill` there to add the older ones."
            if on else f"Game links in {channel.mention} no longer go into the catalog.")
    await interaction.response.send_message(text, ephemeral=True)


@setup.command(name="read", description="Let the bot read a channel or thread without adding its links (or stop)")
@app_commands.check(is_admin)
@app_commands.describe(channel="The channel or thread", on="Read it (default), or stop reading it")
async def setup_read(interaction: discord.Interaction, channel: discord.TextChannel | discord.Thread, on: bool = True):
    bot.settings.set_read(interaction.guild_id, channel.id, on)
    await interaction.response.send_message(f"I {'now read' if on else 'no longer read'} {channel.mention}.",
                                            ephemeral=True)


@setup.command(name="post", description="Pick the channel for one kind of the bot's posts, or turn them off")
@app_commands.check(is_admin)
@app_commands.describe(kind="Which posts", channel="Where they go (leave empty to turn them off)",
                       default="Go back to the default channel instead")
@app_commands.choices(kind=POST_CHOICES)
async def setup_post(interaction: discord.Interaction, kind: str, channel: discord.TextChannel | None = None,
                     default: bool = False):
    bot.settings.set_post_channel(interaction.guild_id, kind, None if default else channel.id if channel else 0)
    now = bot.post_channel(interaction.guild, kind)
    await interaction.response.send_message(f"**{POSTS[kind]}:** " + (now.mention if now else "off"), ephemeral=True)


@setup.command(name="admin", description="Let someone use every command here (or not)")
@app_commands.check(is_admin)
@app_commands.describe(user="Who", on="Make them an admin (default), or take it away")
async def setup_admin(interaction: discord.Interaction, user: discord.Member, on: bool = True):
    gid = interaction.guild_id
    if not on and user.id == interaction.user.id:
        return await interaction.response.send_message("Ask another admin to take it away, so nobody locks themselves "
                                                       "out by mistake.", ephemeral=True)
    if on and not bot.settings.admins(gid):  # the first admin: the mod setting it up stays one too
        bot.settings.set_admin(gid, interaction.user.id, True)
    admins = bot.settings.set_admin(gid, user.id, on)
    who = " ".join(f"<@{u}>" for u in sorted(admins)) if admins else "everyone with Manage Messages"
    await interaction.response.send_message(f"Admins here: {who}", ephemeral=True)


@setup.command(name="country", description="Show Steam prices in your country's currency")
@app_commands.check(is_admin)
@app_commands.describe(country="Two-letter country code, e.g. dk, us, de or gb (leave empty for the default)")
async def setup_country(interaction: discord.Interaction, country: str | None = None):
    gid, code = interaction.guild_id, parse_country(country)
    if country and not code:
        return await interaction.response.send_message(
            f"“{country}” isn't a two-letter country code. Try e.g. `dk`, `us`, `de` or `gb`.", ephemeral=True)
    bot.settings.set_country(gid, code)
    log.info("%s set the country to %s in %s", interaction.user.display_name, bot.settings.country(gid),
             interaction.guild.name)
    await interaction.response.send_message(
        f"Prices now come from Steam's **{bot.settings.country(gid).upper()}** store. The catalog shows them within a "
        "minute.", ephemeral=True)
    asyncio.create_task(bot.refresh_prices())  # so the catalog doesn't show the old currency until the next refresh


async def timezone_autocomplete(_interaction: discord.Interaction, current: str) -> list[Choice]:
    return [Choice(name=z, value=z) for z in find_timezones(current or "europe")]


@setup.command(name="timezone", description="When #GOTG and the weekly deals post, in your time zone")
@app_commands.check(is_admin)
@app_commands.describe(timezone="Your time zone, e.g. Europe/Copenhagen (leave empty for the default)")
@app_commands.autocomplete(timezone=timezone_autocomplete)
async def setup_timezone(interaction: discord.Interaction, timezone: str | None = None):
    gid, zone = interaction.guild_id, parse_timezone(timezone)
    if timezone and not zone:
        return await interaction.response.send_message(
            f"“{timezone}” isn't a time zone I know. Start typing a city, e.g. `Copenhagen`, and pick one from the list.",
            ephemeral=True)
    bot.settings.set_timezone(gid, zone)
    log.info("%s set the time zone to %s in %s", interaction.user.display_name, bot.settings.timezone(gid),
             interaction.guild.name)
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    await interaction.response.send_message(
        f"Time zone: **{bot.settings.timezone(gid)}** (it's {bot.settings.now(gid):%H:%M} there now). #GOTG posts at "
        f"{GOTG_TIME:%H:%M} and the weekly deals on {days[DEALS_DAY]}s at {DEALS_HOUR}:00, both in that time.",
        ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in .env first (see README.md).")
    bot.run(TOKEN, root_logger=True)
