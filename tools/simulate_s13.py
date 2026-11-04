r"""Try "Played needs 3 players" (S13) without going live: the real catalog page and bot code, run on a copy.

    .venv\Scripts\python.exe tools\simulate_s13.py                   # then open http://127.0.0.1:8770/sim
    .venv\Scripts\python.exe tools\simulate_s13.py --after-deadline  # as it looks once the deadline has passed
    .venv\Scripts\python.exe tools\simulate_s13.py --page templates\catalog-s16.html  # with a page that isn't live yet

Copies catalog.db to a temporary file and works only on that copy: the real catalog, Discord and the bot are never
touched, nothing is posted and nobody is pinged. /sim lets you be anyone in the catalog: open the catalog as them,
or see the bot's Discord messages as they would see them (with working buttons). Stop it with Ctrl+C; the copy is
thrown away, so every run starts from the real catalog as it is now.
"""

import asyncio
import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import date, datetime
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PORT = 8770

# The bot module opens its database on import: point it at the copy first
COPY_DIR = tempfile.mkdtemp(prefix="s13-sim-")
COPY = os.path.join(COPY_DIR, "catalog.db")
shutil.copyfile(os.path.join(ROOT, "catalog.db"), COPY)
os.environ["DB_PATH"] = COPY

import aiohttp  # noqa: E402
import discord  # noqa: E402
from aiohttp import web  # noqa: E402

import bot as B  # noqa: E402
import catalog_html  # noqa: E402
from web import CatalogSite  # noqa: E402

# --page templates\catalog-s16.html: try a page change here before it goes into templates\catalog.html (which is live)
if "--page" in sys.argv:
    catalog_html.TEMPLATE_PATH = os.path.join(ROOT, sys.argv[sys.argv.index("--page") + 1])

db = B.bot.db
GUILD_ID = db.conn.execute("SELECT guild_id FROM entries GROUP BY guild_id ORDER BY COUNT(*) DESC").fetchone()[0]


def known_names() -> dict[int, str]:
    """Everyone the catalog has a name for (the simulation has no Discord to ask)."""
    names: dict[int, str] = {}
    for sql in ("SELECT added_by, added_by_name FROM entries", "SELECT user_id, user_name FROM interest",
                "SELECT user_id, user_name FROM refunds", "SELECT user_id, user_name FROM playing",
                "SELECT user_id, user_name FROM ratings"):
        for uid, name in db.conn.execute(sql):
            if uid and name:
                names[uid] = name
    return names


NAMES = known_names()
# Steam libraries and hours only count for people in the server: on the copy, everyone with a name is
db.conn.executemany("INSERT OR IGNORE INTO guild_members VALUES (?, ?)", [(GUILD_ID, uid) for uid in NAMES])
db.conn.commit()


class Member:
    def __init__(self, uid: int):
        self.id, self.display_name, self.name, self.bot = uid, NAMES.get(uid, f"Player {str(uid)[-4:]}"), "", False

    @property
    def status(self):  # made up, and changing every 30 seconds, so the dots on the faces can be seen updating
        return ("online", "idle", "dnd", "offline", "offline")[(self.id // 7 + int(time.time() // 30)) % 5]


class Guild:
    id, name, icon = GUILD_ID, "Simulation", None

    def get_member(self, uid):
        return Member(uid)

    async def fetch_member(self, uid):
        return Member(uid)

    @property
    def members(self):
        return []


class Bot:
    """Just enough of the bot for the catalog page."""
    application_id = 0

    def __init__(self, session):
        self.db, self.web, self.guild = db, session, Guild()
        self.guilds = [self.guild]

    def get_guild(self, gid):
        return self.guild if gid == GUILD_ID else None

    async def tag_to_play(self, entry, member, targets):
        print(f"(simulated, not sent) {member.display_name} pinged {len(targets)} to play {entry['title']}")


def people() -> list[tuple[int, str, int]]:
    """(id, name, games "Have you played these?" asks them) for everyone with a name, most to answer first."""
    return sorted(((uid, name, len(db.catch_up_games(GUILD_ID, uid))) for uid, name in NAMES.items()),
                  key=lambda p: (-p[2], p[1].lower()))


# -- Discord messages drawn as a page (the real views from bot.py, with their buttons as links) --

def markdown(text: str) -> str:
    out = []
    for line in html.escape(text).split("\n"):
        line = re.sub(r"&lt;t:(\d+):([DR])&gt;", lambda m: datetime.fromtimestamp(int(m[1])).strftime("%d %B %Y"), line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        line = re.sub(r"&lt;@(\d+)&gt;", lambda m: f'<span class="at">@{html.escape(Member(int(m[1])).display_name.split(" ")[0])}</span>', line)
        if line.startswith("### "):
            line = f"<h4>{line[4:]}</h4>"
        elif line.startswith("## "):
            line = f"<h3>{line[3:]}</h3>"
        elif line.startswith("-# "):
            line = f"<small>{line[3:]}</small>"
        out.append(line)
    return "<br>".join(out).replace("</h3><br>", "</h3>")


def draw(item, uid: int) -> str:
    if isinstance(item, discord.ui.Container):
        colour = item.accent_colour.value if isinstance(item.accent_colour, discord.Colour) else item.accent_colour or 0
        return f'<div class="box" style="border-color:#{colour:06x}">{"".join(draw(c, uid) for c in item.children)}</div>'
    if isinstance(item, discord.ui.TextDisplay):
        return f"<p>{markdown(item.content)}</p>"
    if isinstance(item, discord.ui.Section):
        thumb = draw(item.accessory, uid)
        return f'<div class="section"><div>{"".join(draw(c, uid) for c in item.children)}</div>{thumb}</div>'
    if isinstance(item, discord.ui.Thumbnail):
        return f'<img class="thumb" src="{html.escape(getattr(item.media, "url", str(item.media)))}" alt="">'
    if isinstance(item, discord.ui.ActionRow):
        return f'<div class="row">{"".join(draw(c, uid) for c in item.children)}</div>'
    button = getattr(item, "item", item)  # dynamic buttons wrap the real one
    if isinstance(button, discord.ui.Button):
        cid = button.custom_id or ""
        href = (button.url if button.url else f"/sim/discord/{uid}?open=1" if cid == "played-check"
                else f"/sim/as/{uid}" if cid == "open-catalog"
                else f"/sim/discord/{uid}/answer/{cid.split(':')[1]}/{cid.split(':')[2]}" if cid.startswith("played:")
                else f"/sim/gotg/{uid}/form" if cid.startswith("gotg-form:") and not button.disabled else "#")
        style = {discord.ButtonStyle.success: "green", discord.ButtonStyle.primary: "blue"}.get(button.style, "grey")
        return f'<a class="btn {style}" href="{html.escape(href)}">{html.escape(button.label or "")}</a>'
    return ""


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>S13 simulation</title><style>
body {{ background:#313338; color:#dbdee1; font:15px/1.45 system-ui, sans-serif; margin:0; padding:24px; }}
main {{ max-width:720px; margin:0 auto; }} a {{ color:#00a8fc; }} h1 {{ font-size:22px; color:#fff; }}
h3 {{ margin:0 0 4px; font-size:18px; color:#fff; }} h4 {{ margin:4px 0 0; font-size:16px; color:#fff; }}
.at {{ background:#5865f24d; color:#c9cdfb; border-radius:3px; padding:0 2px; }} small {{ color:#949ba4; font-size:12px; }}
.banner {{ background:#f0b23222; border:1px solid #f0b232; border-radius:8px; padding:10px 14px; margin-bottom:18px; }}
.who {{ color:#949ba4; font-size:13px; margin:18px 0 6px; }}
.box {{ background:#2b2d31; border-left:4px solid; border-radius:4px; padding:12px 16px; }}
.box p {{ margin:0 0 10px; }} .section {{ display:flex; gap:12px; justify-content:space-between; }}
.thumb {{ width:96px; height:45px; object-fit:cover; border-radius:4px; flex:none; }}
.row {{ display:flex; gap:8px; flex-wrap:wrap; margin:0 0 12px; }}
.btn {{ display:inline-block; padding:6px 14px; border-radius:4px; color:#fff; text-decoration:none; font-size:14px; }}
.btn.green {{ background:#248046; }} .btn.blue {{ background:#5865f2; }} .btn.grey {{ background:#4e5058; }}
table {{ border-collapse:collapse; width:100%; }} td {{ padding:6px 8px; border-top:1px solid #3f4147; }}
</style><main><div class="banner">Simulation on a copy of the catalog. Nothing here reaches Discord or the real catalog.</div>
{body}</main>"""


class SimSite(CatalogSite):
    """The real catalog site, where signing in just means picking who you are."""

    def make_app(self) -> web.Application:
        app = super().make_app()
        app.router.add_get("/sim", self.sim_home)
        app.router.add_get("/sim/as/{uid}", self.sim_as)
        app.router.add_get("/sim/discord/{uid}", self.sim_discord)
        app.router.add_get("/sim/discord/{uid}/answer/{entry}/{answer}", self.sim_answer)
        app.router.add_get("/sim/gotg/{uid}", self.sim_gotg)
        app.router.add_get("/sim/gotg/{uid}/form", self.sim_gotg_form)
        app.router.add_get("/sim/gotg/{uid}/answer", self.sim_gotg_answer)
        return app

    async def viewer(self, request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer sim:")
        if not token.isdigit():
            raise web.HTTPUnauthorized()
        return int(token)

    async def users(self, guild, ids):
        return {str(uid): {"name": Member(uid).display_name, "username": "", "avatar": None, "banner": None,
                           "accent": None, "created": 0, "joined": None} for uid in ids}

    async def login(self, request):  # "Sign in with Discord" on the page: pick someone instead
        raise web.HTTPFound("/sim")

    async def sim_home(self, request):
        progress, _ = B.bot.played_rule_state(GUILD_ID)
        rows = "".join(f'<tr><td>{html.escape(name)}</td><td>{n} to answer</td><td><a href="/sim/as/{uid}">Catalog as them</a></td>'
                       f'<td><a href="/sim/discord/{uid}">Discord as them</a></td>'
                       f'<td><a href="/sim/gotg/{uid}">#GOTG as them</a></td></tr>' for uid, name, n in people())
        body = (f"<h1>Who do you want to be?</h1><p>{progress[0]} of {progress[1]} Played games have "
                f"{B.PLAYERS_NEEDED} players right now. Answer as a few people and watch games reach "
                f"{B.PLAYERS_NEEDED}.</p><table>{rows}</table>")
        return web.Response(text=PAGE.format(body=body), content_type="text/html")

    async def sim_as(self, request):
        """Sign in as them: the same stored sign-in the real "Sign in with Discord" leaves behind."""
        signin = json.dumps({"token": f"sim:{int(request.match_info['uid'])}", "until": int(time.time()) + 86400})
        page = ('<!doctype html><meta charset="utf-8"><script>'
                f'localStorage.setItem("catalog-signin", JSON.stringify({signin})); location.replace("/");</script>')
        return web.Response(text=page, content_type="text/html")

    async def sim_discord(self, request, note=None):
        uid = int(request.match_info["uid"])
        progress, promoted = B.bot.played_rule_state(GUILD_ID)
        name = html.escape(Member(uid).display_name)
        body = (f'<p><a href="/sim">← Pick someone else</a> · <a href="/sim/as/{uid}">Catalog as {name}</a></p>'
                f'<div class="who">The post in the channel (everyone sees this)</div>'
                f'{"".join(draw(c, uid) for c in B.played_rule_view(progress, promoted).children)}')
        if request.query.get("open") or note:
            view = B.catch_up_view(GUILD_ID, uid, request.query.get("note") or note)
            body += (f'<div class="who">After pressing “Check my games” (only {name} sees this)</div>'
                     f'{"".join(draw(c, uid) for c in view.children)}')
        left = progress[1] - progress[0]
        body += (f'<div class="who">The reminder 3 days before the deadline (only sent if games are still short)</div>'
                 f'{"".join(draw(c, uid) for c in B.played_rule_reminder_view(left).children)}' if left else "")
        return web.Response(text=PAGE.format(body=body), content_type="text/html")

    async def sim_gotg(self, request, notes: list[tuple[str, str]] = ()):
        """Today's #GOTG post as the bot would send it at 16:30 (picked now on the copy if it hasn't been yet)."""
        uid, day = int(request.match_info["uid"]), date.today().isoformat()
        if not db.gotg_post(GUILD_ID, day):
            db.save_gotg_post(GUILD_ID, day, B.gotg_picks(GUILD_ID, date.today()))
        name = html.escape(Member(uid).display_name)
        body = (f'<p><a href="/sim">← Pick someone else</a> · Press “I\'m in…” as {name}, then as two others '
                f'to see the ping</p><div class="who">The 16:30 post in #{B.GOTG_CHANNEL_NAME} (pings nobody)</div>'
                f'{"".join(draw(c, uid) for c in B.gotg_view(GUILD_ID, day).children)}')
        for who, text in notes:
            body += f'<div class="who">{who}</div><div class="box" style="border-color:#5865f2"><p>{markdown(text)}</p></div>'
        return web.Response(text=PAGE.format(body=body), content_type="text/html")

    async def sim_gotg_form(self, request):
        """The "I'm in…" form, drawn as a page: the same questions and answers as B.GotgForm."""
        uid, day = int(request.match_info["uid"]), date.today().isoformat()
        form = B.GotgForm(GUILD_ID, day, uid)
        games = "".join(
            f'<label class="opt"><input type="checkbox" name="g" value="{o.value}"{" checked" if o.default else ""}> '
            f'{html.escape(o.label)}{f" <small>{o.description}</small>" if o.description else ""}</label>'
            for o in form.games.options)
        when = "".join(
            f'<label class="opt"><input type="radio" name="when" value="{o.value}"{" checked" if o.default else ""}> '
            f'{o.label}</label>' for o in form.when.options)
        body = (f'<p><a href="/sim/gotg/{uid}">← Back to the post</a></p><div class="box" style="border-color:#1e1f22">'
                f'<h3>{form.title}</h3><form action="/sim/gotg/{uid}/answer"><h4>Which games are you in for?</h4>'
                f'<small>Untick one to leave it</small>{games}<h4>When?</h4><small>Optional</small>{when}'
                f'<p><button class="btn blue" style="border:0">Submit</button></p></form></div>'
                '<style>.opt { display:block; padding:6px 0; } .opt small { margin-left:6px; }</style>')
        return web.Response(text=PAGE.format(body=body), content_type="text/html")

    async def sim_gotg_answer(self, request):
        uid = int(request.match_info["uid"])
        picked = {int(g) for g in request.query.getall("g", [])}
        slot = request.query.get("when") if request.query.get("when") in B.GOTG_SLOTS else None
        text, pings = B.gotg_answer(GUILD_ID, date.today().isoformat(), Member(uid), picked, slot)
        notes = [("Only they see this", text)] + [("The bot's reply (pings the people after “Join them”)", p)
                                                   for p, _ in pings]
        return await self.sim_gotg(request, notes)

    async def sim_answer(self, request):
        uid, entry, answer = int(request.match_info["uid"]), int(request.match_info["entry"]), request.match_info["answer"]
        note = B.answer_played(GUILD_ID, Member(uid), entry, answer == "y") or ""
        raise web.HTTPFound(f"/sim/discord/{uid}?open=1&note={quote(note)}")


async def main():
    sys.stdout.reconfigure(errors="replace")  # game titles the Windows console can't show
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),
                                     headers={"User-Agent": "Mozilla/5.0 (game-catalog discord bot)"}) as session:
        site = SimSite(Bot(session), PORT, GUILD_ID, client_secret="simulation")
        site.public_url = f"http://127.0.0.1:{PORT}"
        site.played_deadline = int(time.time()) - 60 if "--after-deadline" in sys.argv else B.PLAYED_RULE_DEADLINE
        for game in B.bot.promote_played():  # what the bot does a minute after it starts
            print(f"Now Played ({B.PLAYERS_NEEDED}+ players): {game['title']}")
        await site.start()
        print(f"\nSimulation running on a copy of the catalog: {site.public_url}/sim\nCtrl+C stops it.")
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        B.bot.db.conn.close()
        shutil.rmtree(COPY_DIR, ignore_errors=True)
