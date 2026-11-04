"""Who may see which server's catalog: the home server's stays public, any other only for its signed-in members."""

import unittest
from types import SimpleNamespace

import discord
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from tests.helpers import GUILD_A, GUILD_B
from web import CatalogSite

MEMBER, STRANGER = 1, 2


class FakeGuild:
    def __init__(self, guild_id: int, members: set[int]):
        self.id, self._members = guild_id, members

    def get_member(self, uid):
        return SimpleNamespace(id=uid) if uid in self._members else None

    async def fetch_member(self, uid):
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")


def site(home: int, guilds: list[FakeGuild]) -> CatalogSite:
    by_id = {g.id: g for g in guilds}
    bot = SimpleNamespace(guilds=guilds, get_guild=by_id.get)
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


if __name__ == "__main__":
    unittest.main()
