"""Who may see which server's catalog: the home server's stays public, any other only for its signed-in members."""

import unittest
from types import SimpleNamespace

import discord
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from tests.helpers import GUILD_A, GUILD_B
from web import LEGAL_PAGES, CatalogSite, render_legal

MEMBER, STRANGER = 1, 2


class FakeGuild:
    def __init__(self, guild_id: int, members: set[int]):
        self.id, self._members = guild_id, members

    def get_member(self, uid):
        return SimpleNamespace(id=uid, status=discord.Status.online) if uid in self._members else None

    async def fetch_member(self, uid):
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")


def site(home: int, guilds: list[FakeGuild], presences: bool = True) -> CatalogSite:
    by_id = {g.id: g for g in guilds}
    bot = SimpleNamespace(guilds=guilds, get_guild=by_id.get, intents=SimpleNamespace(presences=presences))
    s = CatalogSite(bot, 0, default_guild_id=home)
    tokens = {"member": MEMBER, "stranger": STRANGER}

    async def viewer(request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in tokens:
            raise web.HTTPUnauthorized()
        return tokens[token]
    s.viewer = viewer
    return s


def request(guild_id: int, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return make_mocked_request("GET", f"/data.json?guild_id={guild_id}", headers=headers)


class VisibleGuildTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.a, self.b = FakeGuild(GUILD_A, {MEMBER}), FakeGuild(GUILD_B, {MEMBER})
        self.site = site(GUILD_A, [self.a, self.b])

    async def test_home_server_is_public(self):
        self.assertIs(await self.site.visible_guild(request(GUILD_A)), self.a)

    async def test_other_server_needs_sign_in(self):
        self.assertIsNone(await self.site.visible_guild(request(GUILD_B)))

    async def test_other_server_is_for_its_members(self):
        self.assertIs(await self.site.visible_guild(request(GUILD_B, "member")), self.b)
        self.assertIsNone(await self.site.visible_guild(request(GUILD_B, "stranger")))

    async def test_without_a_home_server_only_a_lone_server_is_public(self):
        self.assertTrue(site(0, [self.a]).is_public(self.a))
        self.assertFalse(site(0, [self.a, self.b]).is_public(self.a))


class OnlineStatusTest(unittest.TestCase):
    def test_shown_with_presences(self):
        s = site(GUILD_A, [FakeGuild(GUILD_A, {MEMBER})])
        self.assertEqual(s.status_of(s.bot.get_guild(GUILD_A), [MEMBER, STRANGER]), {str(MEMBER): "online"})

    def test_hidden_without_presences(self):
        """Without the presence intent Discord reports everyone as offline, so no status is shown at all."""
        s = site(GUILD_A, [FakeGuild(GUILD_A, {MEMBER})], presences=False)
        self.assertEqual(s.status_of(s.bot.get_guild(GUILD_A), [MEMBER]), {})

class LegalPagesTest(unittest.IsolatedAsyncioTestCase):
    """The privacy policy and terms are public pages, since the repo is private."""

    def test_rendered_with_links_between_them(self):
        title, page = render_legal(LEGAL_PAGES["/terms"])
        self.assertEqual(title, "Terms of service")
        self.assertIn('href="/privacy"', page)
        self.assertIn('href="/license"', page)
        self.assertNotIn("PRIVACY.md", page)
        _, privacy = render_legal(LEGAL_PAGES["/privacy"])
        self.assertIn("<table>", privacy)

    async def test_served_to_anyone(self):
        s = site(GUILD_A, [FakeGuild(GUILD_A, set())])
        response = await s.legal(make_mocked_request("GET", "/privacy"))
        self.assertEqual(response.content_type, "text/html")
        self.assertIn("<h1>Privacy policy</h1>", response.text)

if __name__ == "__main__":
    unittest.main()
