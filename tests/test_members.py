"""Issue #7: someone who left a server stops being in its data. The bot checks people one by one with Discord (no
Server Members intent needed) and deletes what a person who left did in that server; their other servers keep theirs."""

import asyncio
import unittest
from types import SimpleNamespace

import discord

from bot import GameBot
from tests.helpers import GUILD_A, GUILD_B, add_steam, new_db

STAYS, LEFT, BOT_ID = 1, 2, 99


def fill(db, guild_id: int, uid: int) -> dict:
    """A game in the server that `uid` added, wants, plays, rated, refunded, answered and is in for, plus an achievement."""
    game = add_steam(db, guild_id, 100 + uid, added_by=uid, added_by_name=f"user {uid}")
    db.add_interest(game["id"], uid, f"user {uid}")
    db.set_playing(game["id"], uid, f"user {uid}", True, 1)
    db.set_rating(game["id"], uid, f"user {uid}", 4)
    db.set_refund(game["id"], uid, f"user {uid}", True, 1)
    db.set_played_answer(game["id"], uid, True, 1)
    db.set_gotg_in("2026-11-04", game["id"], uid, True, 1)
    db.save_achievement(guild_id, uid, "explorer", 1, 1, announced=True)
    db.set_guild_meta(guild_id, f"achievement_ping:{uid}", 1)
    db.add_member(guild_id, uid)
    return game


class ForgetMember(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.left_a = fill(self.db, GUILD_A, LEFT)
        self.left_b = fill(self.db, GUILD_B, LEFT)
        self.stays = fill(self.db, GUILD_A, STAYS)

    def test_people_in_lists_everyone_the_data_is_about(self):
        self.db.set_steam_account(7, "765")
        self.assertEqual(self.db.people_in(GUILD_A), {STAYS, LEFT, 7})

    def test_deletes_only_that_server_and_keeps_the_games(self):
        self.assertGreater(self.db.forget_member(GUILD_A, LEFT), 0)
        self.assertNotIn(LEFT, self.db.people_in(GUILD_A))
        game = self.db.get(self.left_a["id"])
        self.assertIsNotNone(game)
        self.assertIsNone(game["added_by"])
        self.assertIsNone(self.db.get_guild_meta(GUILD_A, f"achievement_ping:{LEFT}"))
        # their other server and the person who stayed are untouched
        self.assertIn(LEFT, self.db.people_in(GUILD_B))
        self.assertEqual(self.db.get(self.left_b["id"])["added_by"], LEFT)
        self.assertEqual(self.db.interested(self.stays["id"]), [(STAYS, f"user {STAYS}")])
        self.assertEqual(self.db.achievements(GUILD_A, STAYS)[0][0], "explorer")


class FakeGuild:
    def __init__(self, guild_id: int, members: set[int], broken: bool = False):
        self.id, self.name, self._members, self.broken, self.fetched = guild_id, "Test", members, broken, []

    def get_member(self, uid):
        return None  # as without the Server Members intent: nobody is cached

    async def fetch_member(self, uid):
        self.fetched.append(uid)
        if self.broken:
            raise discord.HTTPException(SimpleNamespace(status=500, reason="Server Error"), "down")
        if uid not in self._members:
            raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Member")
        return SimpleNamespace(id=uid)


def fake_bot(db) -> GameBot:
    bot = GameBot.__new__(GameBot)  # just what check_members needs, without logging in
    bot.db, bot._membership = db, {}
    bot._connection = SimpleNamespace(user=SimpleNamespace(id=BOT_ID))
    return bot


class CheckMembers(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        fill(self.db, GUILD_A, LEFT)
        fill(self.db, GUILD_A, STAYS)
        add_steam(self.db, GUILD_A, 5, added_by=BOT_ID)

    def test_deletes_who_left_and_never_asks_about_itself(self):
        guild = FakeGuild(GUILD_A, {STAYS})
        asyncio.run(fake_bot(self.db).check_members(guild))
        self.assertEqual(self.db.people_in(GUILD_A), {STAYS, BOT_ID})
        self.assertNotIn(BOT_ID, guild.fetched)

    def test_deletes_nothing_when_discord_cant_answer(self):
        asyncio.run(fake_bot(self.db).check_members(FakeGuild(GUILD_A, set(), broken=True)))
        self.assertEqual(self.db.people_in(GUILD_A), {STAYS, LEFT, BOT_ID})


if __name__ == "__main__":
    unittest.main()
