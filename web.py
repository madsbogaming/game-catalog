"""Serves the live catalog page (also shown inside Discord as an Activity) and opens a public Cloudflare tunnel to it."""

import asyncio
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import time
from collections import OrderedDict
from urllib.parse import urlencode, urlparse

import aiohttp
import discord
from aiohttp import web

import achievements
from catalog_html import page_data, render_catalog
from db import PLAYERS_NEEDED, STATUSES

log = logging.getLogger("gamebot.web")
mimetypes.add_type("text/javascript", ".mjs")  # some systems don't know .mjs, and browsers refuse it otherwise
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)  # don't log every image request

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
# The privacy policy and terms as public pages (the repo is private, and Discord and top.gg want links to them).
# Links between the documents point at these pages instead of the files.
LEGAL_PAGES = {"/privacy": "docs/PRIVACY.md", "/terms": "docs/TERMS.md"}
LEGAL_LINKS = {"PRIVACY.md": "/privacy", "TERMS.md": "/terms", "../LICENSE": "/license"}
LEGAL_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Game Catalog</title>
<style>
:root {{ color-scheme: light dark; --bg:#fff; --ink:#1f2328; --muted:#59636e; --line:#d1d9e0; --link:#4752c4; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141518; --ink:#e6e7ea; --muted:#9a9ca3; --line:#2e3036; --link:#949cf7; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.6 system-ui, sans-serif; }}
main {{ max-width:760px; margin:0 auto; padding:32px 16px 64px; }}
a {{ color:var(--link); }} h1 {{ line-height:1.2; }} em {{ color:var(--muted); }}
table {{ border-collapse:collapse; width:100%; display:block; overflow-x:auto; font-size:15px; }}
th, td {{ border:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }}
code {{ font-size:.9em; }}
</style></head><body><main>{body}</main></body></html>"""


def render_legal(path: str) -> tuple[str, str]:
    """docs/PRIVACY.md or docs/TERMS.md -> (title, the page's HTML)."""
    import markdown  # only these two pages need it
    with open(os.path.join(ROOT_DIR, path), encoding="utf-8") as f:
        text = f.read()
    for name, url in LEGAL_LINKS.items():
        text = text.replace(f"]({name})", f"]({url})")
    title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("# ")), "Game Catalog")
    body = markdown.markdown(text, extensions=["tables"])
    return title, LEGAL_TEMPLATE.format(title=title, body=body)
# Only images from these sites are proxied, so /img can't be used to fetch arbitrary URLs.
IMAGE_HOSTS = ("steamstatic.com", "steampowered.com", "cdninstagram.com", "fbcdn.net", "discordapp.com",
               "discordapp.net", "blz-contentstack-images.akamaized.net", "battle.net", "blizzard.com")
USER_CACHE_SECONDS = 1800
TAG_COOLDOWN = 1800  # seconds before the same person can tag the same friend again
RECENT_DAYS = 7  # a game stays in "recent activity" until nobody has played it for this long
DISCORD_API = "https://discord.com/api/v10"
MENTION_RE = re.compile(r"<@!?(\d+)>")
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED_PATHS = [os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "cloudflared.exe"),
                     r"C:\Program Files (x86)\cloudflared\cloudflared.exe"]


@web.middleware
async def share_art(request: web.Request, handler):
    """Let other sites (like the Developer Portal, for uploading Activity art) load the pictures in static/art."""
    response = await handler(request)
    if request.path.startswith("/static/art/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@web.middleware
async def log_failures(request: web.Request, handler):
    """Note catalog actions that fail (sign-in, missing game, not a member...), so "it didn't work" can be looked up."""
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if request.path.startswith("/api/") and exc.status >= 400:
            log.warning("%s failed: HTTP %s %s", request.path, exc.status, exc.text or "")
        raise


class CatalogSite:
    def __init__(self, bot, port: int, default_guild_id: int = 0, client_secret: str = ""):
        self.bot, self.port, self.default_guild_id = bot, port, default_guild_id
        self.client_secret = client_secret  # lets people sign in from the Activity to mark games
        self.public_url: str | None = None
        self.played_deadline: int | None = None  # after this, the page stops asking "Have you played these?"
        self._images: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._users: dict[tuple[int, int], tuple[float, dict]] = {}  # (server id, user id) -> (fetched at, profile)
        self._refreshing: set[tuple[int, int]] = set()
        self._shown: dict[int, set[int]] = {}  # guild id -> everyone the catalog shows, whose status the page may ask for
        self._tokens: dict[str, tuple[int, float]] = {}  # access token -> (user id, valid until)
        self._runner: web.AppRunner | None = None
        self._tunnel: asyncio.subprocess.Process | None = None

    # -- web server --

    async def start(self):
        self._runner = web.AppRunner(self.make_app())
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()
        log.info("Catalog page running at http://127.0.0.1:%s/", self.port)

    def make_app(self) -> web.Application:
        app = web.Application(middlewares=[log_failures, share_art])
        app.router.add_get("/", self.index)
        app.router.add_get("/data.json", self.data)
        app.router.add_get("/version", self.version)
        app.router.add_get("/members.json", self.members)
        app.router.add_get("/status.json", self.statuses)
        app.router.add_get("/img", self.image)
        app.router.add_post("/api/token", self.token)
        for page in LEGAL_PAGES:
            app.router.add_get(page, self.legal)
        app.router.add_get("/license", self.license)
        app.router.add_get("/auth/login", self.login)
        app.router.add_get("/auth/callback", self.callback)
        app.router.add_get("/api/me", self.whoami)
        app.router.add_post("/api/interest", self.interest)
        app.router.add_post("/api/status", self.status)
        app.router.add_post("/api/playing", self.playing)
        app.router.add_post("/api/tag", self.tag)
        app.router.add_post("/api/rate", self.rate)
        app.router.add_post("/api/refund", self.refund)
        app.router.add_post("/api/played", self.played_answer)
        app.router.add_post("/api/ping", self.ping)
        app.router.add_static("/static", STATIC_DIR)
        return app

    def guild(self, request: web.Request):
        # Discord passes ?guild_id=… when the page is opened as an Activity.
        requested = request.query.get("guild_id", "")
        guild = self.bot.get_guild(int(requested)) if requested.isdigit() else None
        return guild or self.bot.get_guild(self.default_guild_id) or next(iter(self.bot.guilds), None)

    def is_public(self, guild) -> bool:
        """Whether anyone with the catalog's address may see this server's catalog, as before there were several:
        the home server (GUILD_ID), or the only server when GUILD_ID isn't set. Any other server's catalog (its games,
        and its people's names, faces and online status) is only for its own signed-in members."""
        if guild is None:
            return True
        return guild.id == self.default_guild_id if self.default_guild_id else len(self.bot.guilds) <= 1

    async def visible_guild(self, request: web.Request):
        """The server whose catalog this request asks for, or None when it isn't public and the viewer isn't
        signed in as one of its members."""
        guild = self.guild(request)
        if self.is_public(guild):
            return guild
        try:
            uid = await self.viewer(request)
        except web.HTTPUnauthorized:
            return None
        if guild.get_member(uid) is None:
            try:
                await guild.fetch_member(uid)
            except discord.HTTPException:
                return None
        return guild

    async def catalog(self, guild) -> tuple[list[dict], dict[str, dict]]:
        """All games of `guild`, plus profile info for everyone who added, wants or is mentioned in one."""
        rows = self.bot.db.query(guild.id) if guild else []
        played, before_rule = self.bot.db.played_map(), self.bot.db.played_before_rule()
        recent = self.bot.db.recent_players(int(time.time()) - RECENT_DAYS * 86400)  # the "recent activity" feed
        ids = set()
        for r in rows:
            r["recent"] = [{"id": str(uid), "at": at} for uid, at in recent.get(r["id"], [])]
            ids.update(uid for uid, _ in recent.get(r["id"], []))
            r["interested"] = [{"id": str(uid), "name": name} for uid, name in self.bot.db.interested(r["id"])]
            r["players"] = [{"id": str(uid), "name": name} for uid, name in self.bot.db.players(r["id"])]
            r["ratings"] = self.ratings(r)
            r["refunds"] = [{"id": str(uid), "name": name} for uid, name in self.bot.db.refunds(r["id"])]
            r.update(self.played(played.get(r["id"])), played_before_rule=r["id"] in before_rule)
            ids.update(int(p["id"]) for p in r["interested"] + r["players"] + r["refunds"] + r["ratings"])
            ids.update(int(uid) for uid in r["played_by"])
            if r["added_by"]:
                ids.add(r["added_by"])
            ids.update(int(uid) for uid in MENTION_RE.findall(r["note"] or ""))
        users = await self.users(guild, ids)
        if guild:
            self._shown[guild.id] = ids
        status = self.status_of(guild, ids)
        # What each person has earned, for their profile card, and whether they're online
        # (copies, so the cached profiles stay as they were)
        return rows, {uid: {**u, "achievements": achievements.earned(self.bot.db, guild.id, int(uid)) if guild else [],
                            "status": status.get(uid)} for uid, u in users.items()}

    def status_of(self, guild, ids) -> dict[str, str]:
        """online, idle, dnd or offline, as Discord shows it, for those of `ids` still in the server.
        Someone invisible shows as offline, as they do in Discord. Nobody has one when the bot runs without
        presences (everyone would look offline)."""
        if not self.bot.intents.presences:
            return {}
        members = ((uid, guild.get_member(uid)) for uid in ids) if guild else ()
        return {str(uid): "offline" if str(m.status) == "invisible" else str(m.status) for uid, m in members if m}

    def ratings(self, entry) -> list[dict]:
        """The game's ratings. "played": the bot saw them playing it in Discord, or Steam has hours of it (or
        its demo) for them; "minutes": their Steam playtime, or else the time the bot saw them playing it in
        Discord ("tracked": true), which is all there is for Battle.net games."""
        seen = self.bot.db.seen_in_game(entry["id"])
        appids = [int(entry["key"].split(":")[1])] if entry["key"].startswith("steam:") else []
        appids += [entry["past_demo_appid"]] if entry["past_demo_appid"] else []  # its demo, even a removed one
        minutes, tracked = self.bot.db.playtime(appids), self.bot.db.tracked_playtime(entry["id"])
        return [{"id": str(uid), "stars": stars, "played": uid in seen or uid in minutes,
                 "minutes": minutes.get(uid) or tracked.get(uid, 0), "tracked": not minutes.get(uid) and uid in tracked}
                for uid, stars in self.bot.db.ratings(entry["id"])]

    @staticmethod
    def played(p: dict | None) -> dict:
        """Who played the game (ids as strings): "played_by" everyone, "played_auto" those the bot knows about
        without them saying so (a rating, refund, Steam hours...), "not_played" those who said they didn't."""
        p = p or {"by": (), "auto": (), "not": ()}
        return {"played_by": sorted(map(str, p["by"])), "played_auto": sorted(map(str, p["auto"])),
                "not_played": sorted(map(str, p["not"]))}

    def played_reply(self, entry_id: int) -> dict:
        """What the page needs after a change that can change who played a game (and so its status)."""
        for game in self.bot.db.promote_played():
            log.info("%s now has %d players, so it's Played", game["title"], PLAYERS_NEEDED)
        return {**self.played(self.bot.db.played_map().get(entry_id)), "status": self.bot.db.get(entry_id)["status"]}

    async def users(self, guild, ids) -> dict[str, dict]:
        """Profiles for `ids`. Known ones come straight from the cache (old ones are refreshed in the background,
        so the page never waits for Discord); only never-seen people are fetched before answering."""
        now, gid = time.monotonic(), guild.id if guild else 0  # nicknames and join dates differ per server
        missing = [uid for uid in ids if (gid, uid) not in self._users]
        old = [uid for uid in ids if (gid, uid) in self._users and now - self._users[gid, uid][0] > USER_CACHE_SECONDS
               and (gid, uid) not in self._refreshing]
        if old:
            self._refreshing.update((gid, uid) for uid in old)
            asyncio.create_task(self._fetch_users(guild, old))
        if missing:
            await self._fetch_users(guild, missing)
        return {str(uid): self._users[gid, uid][1] for uid in ids if (gid, uid) in self._users}

    async def _fetch_users(self, guild, uids):
        results = await asyncio.gather(*(self._fetch_user(guild, uid) for uid in uids), return_exceptions=True)
        for uid, result in zip(uids, results):
            self._refreshing.discard((guild.id if guild else 0, uid))
            if isinstance(result, Exception):  # one broken profile shouldn't break the page
                log.warning("Couldn't load Discord profile %s: %r", uid, result)

    async def warm_up(self):
        """Load everyone's profile once at startup, so the first /catalog after a restart is quick too."""
        for guild in self.bot.guilds:
            try:
                await self.catalog(guild)
            except Exception:
                log.exception("Couldn't preload the catalog for %s", guild.name)

    async def _fetch_user(self, guild, uid: int):
        member = guild.get_member(uid) if guild else None
        if member is None and guild:
            try:
                member = await guild.fetch_member(uid)
            except discord.HTTPException:
                pass  # left the server
        try:
            user = await self.bot.fetch_user(uid)  # banner and accent colour only come with a full user fetch
        except discord.HTTPException:
            user = member
        if user is None:
            return
        shown = member or user
        self._users[guild.id if guild else 0, uid] = (time.monotonic(), {
            "name": shown.display_name,
            "username": user.name,
            "avatar": shown.display_avatar.with_size(128).url,
            "banner": user.banner.with_size(512).url if getattr(user, "banner", None) else None,
            "accent": str(user.accent_color) if getattr(user, "accent_color", None) else None,
            "created": int(user.created_at.timestamp()),
            "joined": int(member.joined_at.timestamp()) if member and member.joined_at else None,
        })

    async def index(self, request: web.Request) -> web.Response:
        guild = self.guild(request)
        # Another server's catalog: the page comes empty, and loads the games once its member has signed in
        locked = not self.is_public(guild)
        rows, users = ([], {}) if locked else await self.catalog(guild)
        page = render_catalog(rows, guild.name if guild else "Game", users=users, live=True, locked=locked,
                              guild_icon=guild.icon.with_size(64).url if guild and guild.icon else None,
                              public_url=self.public_url, seen_playing=self.bot.db.seen_playing(),
                              steam_hours=self.bot.db.steam_hours_users(), played_deadline=self.played_deadline,
                              client_id=self.bot.application_id, can_sign_in=bool(self.client_secret))
        response = web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})
        response.enable_compression()  # ~700 KB of page shrinks to a fraction on the way through Discord
        return response

    async def data(self, request: web.Request) -> web.Response:
        guild = await self.visible_guild(request)
        if guild is None:
            raise web.HTTPForbidden(text="Sign in as a member of this server to see its catalog")
        rows, users = await self.catalog(guild)
        body = json.dumps({"games": page_data(rows), "users": users}, ensure_ascii=False)
        response = web.Response(text=body, content_type="application/json", headers={"Cache-Control": "no-store"})
        response.enable_compression()
        return response

    async def members(self, request: web.Request) -> web.Response:
        """Everyone in the server (not bots), so "Ping to play" can find people who aren't in the catalog yet.
        Only for signed-in members of the server: the catalog's address is public, the member list shouldn't be."""
        guild = self.guild(request)
        uid = await self.viewer(request)
        if guild is None or guild.get_member(uid) is None:
            raise web.HTTPForbidden(text="Only members of the server can see who's in it")
        status = self.status_of(guild, [m.id for m in guild.members if not m.bot] if guild else [])
        people = {str(m.id): {"name": m.display_name, "username": m.name, "avatar": m.display_avatar.with_size(64).url,
                              "status": status.get(str(m.id))}
                  for m in (guild.members if guild else []) if not m.bot}
        response = web.json_response(people, headers={"Cache-Control": "no-store"})
        response.enable_compression()
        return response

    # -- signing in from the Activity, and marking interest --

    async def token(self, request: web.Request) -> web.Response:
        """Swap the code from the Activity's authorize() for an access token (needs the client secret)."""
        if not self.client_secret:
            raise web.HTTPServiceUnavailable(text="DISCORD_CLIENT_SECRET isn't set")
        code = (await request.json()).get("code")
        if not isinstance(code, str) or not code:
            raise web.HTTPBadRequest()
        body = await self._exchange(code)
        if not body:
            raise web.HTTPUnauthorized()
        return web.json_response({"access_token": body["access_token"]})

    async def _exchange(self, code: str, redirect_uri: str | None = None) -> dict | None:
        """A sign-in code -> Discord's answer with the access token (None if Discord said no). Also links Steam."""
        form = {"client_id": str(self.bot.application_id), "client_secret": self.client_secret,
                "grant_type": "authorization_code", "code": code}
        if redirect_uri:
            form["redirect_uri"] = redirect_uri
        async with self.bot.web.post(f"{DISCORD_API}/oauth2/token", data=form) as r:
            body = await r.json(content_type=None)
        if r.status != 200 or "access_token" not in body:
            log.warning("Discord sign-in failed: HTTP %s %s", r.status, body.get("error_description") or body)
            return None
        if "connections" in body.get("scope", "").split():
            asyncio.create_task(self.link_steam(body["access_token"]))
        else:
            log.info("Sign-in without the connections scope (granted: %s), so no Steam hours", body.get("scope"))
        return body

    # -- signing in from a normal browser (outside Discord): Discord's own login page, then back here.
    # Needs <public address>/auth/callback in the Developer Portal's OAuth2 Redirects. --

    def _redirect_uri(self) -> str:
        return f"{self.public_url}/auth/callback"

    async def login(self, request: web.Request) -> web.Response:
        if not self.client_secret or not self.public_url:
            raise web.HTTPServiceUnavailable(text="Signing in from a browser needs DISCORD_CLIENT_SECRET and a public address")
        state = secrets.token_urlsafe(24)  # checked on the way back, so nobody else's login can be slipped in
        query = urlencode({"client_id": self.bot.application_id, "response_type": "code", "redirect_uri": self._redirect_uri(),
                           "scope": "identify connections", "state": state})
        response = web.Response(status=302, headers={"Location": f"https://discord.com/oauth2/authorize?{query}"})
        response.set_cookie("signin_state", state, max_age=600, httponly=True, secure=True, samesite="Lax", path="/auth")
        return response

    async def callback(self, request: web.Request) -> web.Response:
        """Back from Discord's login page: store the access token in this browser and go to the catalog."""
        code, state = request.query.get("code", ""), request.query.get("state", "")
        expected = request.cookies.get("signin_state", "")
        body = await self._exchange(code, self._redirect_uri()) if code and expected and secrets.compare_digest(state, expected) else None
        if not body:
            page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                    '<body style="background:#141518;color:#ececef;font:16px system-ui;padding:32px">'
                    '<p>Signing in didn\'t work. <a style="color:#8d96ff" href="/">Back to the catalog</a></p>')
            return web.Response(text=page, content_type="text/html", status=400)
        signin = {"token": body["access_token"], "until": int(time.time() + int(body.get("expires_in") or 604800) - 3600)}
        page = ('<!doctype html><meta charset="utf-8"><body style="background:#141518"><script>'
                f'try {{ localStorage.setItem("catalog-signin", JSON.stringify({json.dumps(signin)})); }} catch {{}}'
                'location.replace("/");</script>')
        response = web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})
        response.del_cookie("signin_state", path="/auth")
        return response

    async def whoami(self, request: web.Request) -> web.Response:
        """The signed-in person's Discord id (the browser's sign-in has no Discord app to ask)."""
        return web.json_response({"id": str(await self.viewer(request))})

    async def link_steam(self, token: str):
        """Remember the Steam account the person linked to Discord, and load their Steam hours."""
        try:
            auth = {"Authorization": f"Bearer {token}"}
            async with self.bot.web.get(f"{DISCORD_API}/users/@me", headers=auth) as r:
                uid = int((await r.json())["id"])
            async with self.bot.web.get(f"{DISCORD_API}/users/@me/connections", headers=auth) as r:
                if r.status != 200:
                    log.warning("Couldn't read the connections of %s: HTTP %s", uid, r.status)
                connections = await r.json() if r.status == 200 else []
            steam = next((c["id"] for c in connections if c.get("type") == "steam" and c.get("id")), None)
            if not steam:
                log.info("No Steam account linked to Discord for %s (connections: %s)", uid,
                         ", ".join(sorted({c.get("type", "?") for c in connections})) or "none")
                return
            self.bot.db.set_steam_account(uid, steam)
            loaded = await self.bot.refresh_playtime(uid, steam)
            log.info("Linked Steam for %s: %s", uid, "hours loaded" if loaded else "game details are private, no hours")
        except Exception:
            log.exception("Couldn't read the linked Steam account")

    async def viewer(self, request: web.Request) -> int:
        """The Discord user id behind the request's access token."""
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        now = time.monotonic()
        cached = self._tokens.get(token)
        if cached and cached[1] > now:
            return cached[0]
        if not token:
            raise web.HTTPUnauthorized()
        async with self.bot.web.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {token}"}) as r:
            if r.status != 200:
                raise web.HTTPUnauthorized()
            uid = int((await r.json())["id"])
        if len(self._tokens) > 1000:
            self._tokens.clear()
        self._tokens[token] = (uid, now + 600)
        return uid

    async def member_request(self, request: web.Request):
        """-> (request body, the game it's about, its server, the signed-in member). Only server members get through."""
        uid = await self.viewer(request)
        body = await request.json()
        entry_id = str(body.get("entry_id", ""))
        entry = self.bot.db.get(int(entry_id)) if entry_id.isdigit() else None
        if not entry:
            raise web.HTTPNotFound()
        guild = self.bot.get_guild(entry["guild_id"])
        member = guild.get_member(uid) if guild else None
        if member is None and guild:
            try:
                member = await guild.fetch_member(uid)
            except discord.HTTPException:
                pass
        if member is None:
            raise web.HTTPForbidden(text="Only members of the server can change games")
        return body, entry, guild, member

    async def status(self, request: web.Request) -> web.Response:
        body, entry, _guild, member = await self.member_request(request)
        if body.get("status") not in STATUSES:
            raise web.HTTPBadRequest()
        # Played comes from who played it: it can't be picked by hand (except to put back a game that was Played
        # before the rule), and a game enough of us played stays Played
        players = len(self.bot.db.played_by(entry["id"]))
        if body["status"] == "played" and entry["status"] != "played" and entry["id"] not in self.bot.db.played_before_rule():
            raise web.HTTPBadRequest(text=f"A game is Played once {PLAYERS_NEEDED} of you have played it")
        if body["status"] != "played" and entry["status"] == "played" and players >= PLAYERS_NEEDED:
            raise web.HTTPBadRequest(text=f"{players} of you have played it, so it stays Played")
        self.bot.db.update(entry["id"], status=body["status"])
        log.info("%s set %s to %s", member.display_name, entry["title"], body["status"])
        return web.json_response({"status": body["status"]})

    async def playing(self, request: web.Request) -> web.Response:
        """Mark (or unmark) the signed-in person as playing a game right now."""
        body, entry, guild, member = await self.member_request(request)
        playing = bool(body.get("playing"))
        self.bot.db.set_playing(entry["id"], member.id, member.display_name, playing, int(time.time()))
        if playing:  # playing a game means you want to play it
            self.bot.db.add_interest(entry["id"], member.id, member.display_name)
        players = [{"id": str(u), "name": n} for u, n in self.bot.db.players(entry["id"])]
        interested = [{"id": str(u), "name": n} for u, n in self.bot.db.interested(entry["id"])]
        return web.json_response({"players": players, "interested": interested, "interest": len(interested),
                                  "users": await self.users(guild, {member.id})})

    async def tag(self, request: web.Request) -> web.Response:
        """Ping a friend in the game's channel: the signed-in member wants to play this game with them."""
        body, entry, guild, member = await self.member_request(request)
        target_id = str(body.get("user_id", ""))
        if not target_id.isdigit() or int(target_id) == member.id:
            raise web.HTTPBadRequest()
        target = guild.get_member(int(target_id))
        if target is None:
            try:
                target = await guild.fetch_member(int(target_id))
            except discord.HTTPException:
                raise web.HTTPNotFound(text="They're not in the server anymore")
        now, last = int(time.time()), self.bot.db.last_tagged(member.id, target.id)
        if last and now - last < TAG_COOLDOWN:
            minutes = -(-(last + TAG_COOLDOWN - now) // 60)
            raise web.HTTPTooManyRequests(text=f"You can tag {target.display_name} again in {minutes} min")
        self.bot.db.mark_tagged(member.id, target.id, now)
        await self.bot.tag_to_play(entry, member, [target])
        return web.json_response({"ok": True})

    async def ping(self, request: web.Request) -> web.Response:
        """Ping the people picked in a game card's "Ping to play" list, in the game's channel.
        Each person can be pinged by the same sender once every 30 minutes; they're skipped until then."""
        body, entry, guild, member = await self.member_request(request)
        ids = {int(u) for u in body.get("user_ids") or [] if str(u).isdigit()} - {member.id}
        if not ids or len(ids) > 25:
            raise web.HTTPBadRequest(text="Pick who to ping first")
        now, targets, waiting = int(time.time()), [], []
        for uid in ids:
            target = guild.get_member(uid)
            if target is None:
                try:
                    target = await guild.fetch_member(uid)
                except discord.HTTPException:
                    continue  # left the server
            if target.bot:
                continue
            last = self.bot.db.last_tagged(member.id, uid)
            (waiting if last and now - last < TAG_COOLDOWN else targets).append(target)
        if not targets:
            names = ", ".join(t.display_name for t in waiting) or "them"
            raise web.HTTPTooManyRequests(text=f"You pinged {names} less than 30 minutes ago. Try again later")
        for target in targets:
            self.bot.db.mark_tagged(member.id, target.id, now)
        await self.bot.tag_to_play(entry, member, targets)
        return web.json_response({"pinged": len(targets), "waiting": [t.display_name for t in waiting]})

    async def rate(self, request: web.Request) -> web.Response:
        """The signed-in member rates a played game, a game they played themselves, or a game whose demo they
        played, 1-5 stars (0 removes it)."""
        body, entry, _guild, member = await self.member_request(request)
        stars = body.get("stars")
        if not isinstance(stars, int) or not 0 <= stars <= 5:
            raise web.HTTPBadRequest()
        if entry["status"] not in ("played", "demo") and member.id not in self.bot.db.played_by(entry["id"]):
            raise web.HTTPBadRequest(text="Only games you played (or played demos) can be rated")
        self.bot.db.set_rating(entry["id"], member.id, member.display_name, stars)
        return web.json_response({"ratings": self.ratings(entry), **self.played_reply(entry["id"])})

    async def refund(self, request: web.Request) -> web.Response:
        """The signed-in member says they refunded the game on Steam (or takes that back)."""
        body, entry, _guild, member = await self.member_request(request)
        refunded = bool(body.get("refunded"))
        self.bot.db.set_refund(entry["id"], member.id, member.display_name, refunded, int(time.time()))
        log.info("%s %s %s", member.display_name, "refunded" if refunded else "took back the refund of", entry["title"])
        return web.json_response({"refunds": [{"id": str(u), "name": n} for u, n in self.bot.db.refunds(entry["id"])],
                                  **self.played_reply(entry["id"])})

    async def played_answer(self, request: web.Request) -> web.Response:
        """The signed-in member says they played the game (true), didn't (false), or takes that back (null).
        Enough players makes the game Played."""
        body, entry, guild, member = await self.member_request(request)
        played = body.get("played")
        if played not in (True, False, None):
            raise web.HTTPBadRequest()
        self.bot.db.set_played_answer(entry["id"], member.id, played, int(time.time()))
        log.info("%s %s %s", member.display_name, {True: "played", False: "didn't play", None: "took back their answer for"}[played],
                 entry["title"])
        return web.json_response({**self.played_reply(entry["id"]), "users": await self.users(guild, {member.id})})

    async def statuses(self, request: web.Request) -> web.Response:
        """Who's online now, for the dots on the faces. Status changes don't change the catalog's version,
        so open pages ask for this every little while. Only the people the catalog shows."""
        guild = await self.visible_guild(request)
        if guild is None:
            raise web.HTTPForbidden(text="Sign in as a member of this server to see who's online")
        status = self.status_of(guild, self._shown.get(guild.id, ()))
        return web.json_response(status, headers={"Cache-Control": "no-store"})

    async def version(self, request: web.Request) -> web.Response:
        """A number that changes whenever the catalog does, so open pages know when to reload their data."""
        return web.json_response({"v": self.bot.db.version}, headers={"Cache-Control": "no-store"})

    async def legal(self, request: web.Request) -> web.Response:
        """The privacy policy or the terms, as a page anyone can open (read fresh, so edits show after a deploy)."""
        _, page = render_legal(LEGAL_PAGES[request.path])
        return web.Response(text=page, content_type="text/html")

    async def license(self, request: web.Request) -> web.Response:
        """LICENSE as plain text, which the terms link to."""
        return web.FileResponse(os.path.join(ROOT_DIR, "LICENSE"), headers={"Content-Type": "text/plain; charset=utf-8"})

    async def interest(self, request: web.Request) -> web.Response:
        body, entry, guild, member = await self.member_request(request)
        uid = member.id
        if body.get("want"):
            self.bot.db.add_interest(entry["id"], uid, member.display_name)
        else:
            self.bot.db.remove_interest(entry["id"], uid)
        log.info("%s %s %s", member.display_name, "wants to play" if body.get("want") else "no longer wants to play",
                 entry["title"])
        interested = [{"id": str(u), "name": n} for u, n in self.bot.db.interested(entry["id"])]
        return web.json_response({"interested": interested, "interest": len(interested),
                                  "users": await self.users(guild, {uid})})

    async def image(self, request: web.Request) -> web.Response:
        url = request.query.get("u", "")
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in IMAGE_HOSTS):
            raise web.HTTPNotFound()
        if url not in self._images:
            try:
                async with self.bot.web.get(url) as r:
                    if r.status != 200 or not r.content_type.startswith("image/"):
                        raise web.HTTPNotFound()
                    self._images[url] = (await r.read(), r.content_type)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                raise web.HTTPBadGateway()
            while len(self._images) > 400:
                self._images.popitem(last=False)
        body, content_type = self._images[url]
        return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "public, max-age=86400"})

    # -- tunnel --

    async def start_tunnel(self) -> str | None:
        """Start a Cloudflare quick tunnel and return its public https URL (it changes on every start)."""
        exe = shutil.which("cloudflared") or next((p for p in CLOUDFLARED_PATHS if os.path.exists(p)), None)
        if not exe:
            log.warning("cloudflared isn't installed, so the catalog is only reachable on this PC")
            return None
        self._tunnel = await asyncio.create_subprocess_exec(
            exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            while line := await asyncio.wait_for(self._tunnel.stdout.readline(), timeout=60):
                m = TUNNEL_RE.search(line.decode(errors="replace"))
                if m:
                    self.public_url = m.group(0)
                    break
        except asyncio.TimeoutError:
            pass
        asyncio.create_task(self._drain())  # keep reading so cloudflared never blocks on a full pipe
        if self.public_url:
            log.info("Public catalog URL: %s", self.public_url)
            log.info("Activity URL mapping (Developer Portal > Activities > URL Mappings, prefix /) must target: %s",
                     self.public_url.removeprefix("https://"))
        else:
            log.warning("Couldn't get a tunnel URL from cloudflared")
        return self.public_url

    async def _drain(self):
        while self._tunnel and await self._tunnel.stdout.readline():
            pass

    async def close(self):
        if self._tunnel and self._tunnel.returncode is None:
            self._tunnel.terminate()
        if self._runner:
            await self._runner.cleanup()
