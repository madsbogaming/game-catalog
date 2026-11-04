"""Reels show in the catalog only once they're linked to a game: /backfill links the ones whose caption names a game,
quietly, and the rest stay out (a channel can be full of reels that aren't about games)."""

import asyncio
import unittest
from types import SimpleNamespace

from bot import GameBot
from tests.helpers import GUILD_A, add_steam, new_db


def fake_message(text: str):
    replies = []

    async def reply(*args, **kwargs):
        replies.append(args)
    author = SimpleNamespace(id=7, display_name="Poster")
    return SimpleNamespace(id=500, content=text, guild=SimpleNamespace(id=GUILD_A), author=author, embeds=[],
                           channel=SimpleNamespace(id=10), created_at=SimpleNamespace(timestamp=lambda: 1_700_000_000),
                           reply=reply, add_reaction=lambda *a: asyncio.sleep(0)), replies


def fake_bot(db, caption_game: int | None):
    bot = GameBot.__new__(GameBot)
    bot.db, bot.linked, bot.asked = db, [], []

    async def ensure_reel(guild_id, code, url, origin, embeds):
        return db.insert(guild_id, key=f"ig:{code}", kind="instagram", url=url, title="Some reel", **origin), True

    async def link_from_caption(message, reel):
        bot.linked.append(reel["key"])
        if caption_game:
            db.merge(reel["id"], caption_game)
        return caption_game

    async def identify_reel(message, reel):
        bot.asked.append(reel["key"])
    bot.ensure_reel, bot.link_from_caption, bot.identify_reel = ensure_reel, link_from_caption, identify_reel
    return bot


class Reels(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.game = add_steam(self.db, GUILD_A, 1, "Named Game")

    def test_only_games_are_listed_for_the_catalog(self):
        self.db.insert(GUILD_A, key="ig:abc", kind="instagram", url="https://www.instagram.com/reel/abc/", title="Football",
                       added_at=1_700_000_000)
        self.assertEqual([e["title"] for e in self.db.query(GUILD_A, source="games")], ["Named Game"])

    def test_backfill_links_a_reel_from_its_caption_without_replying(self):
        bot = fake_bot(self.db, self.game["id"])
        message, replies = fake_message("look https://www.instagram.com/reel/abc123/")
        asyncio.run(bot.archive_message(message, announce=False))
        self.assertEqual((bot.linked, bot.asked, replies), (["ig:abc123"], [], []))
        self.assertIsNone(self.db.get_by_key(GUILD_A, "ig:abc123"))  # now a clip of the game

    def test_backfill_leaves_other_reels_out_of_the_catalog(self):
        bot = fake_bot(self.db, None)
        message, replies = fake_message("https://www.instagram.com/reel/foot99/")
        asyncio.run(bot.archive_message(message, announce=False))
        self.assertEqual((bot.asked, replies), ([], []))
        self.assertEqual([e["title"] for e in self.db.query(GUILD_A, source="games")], ["Named Game"])

    def test_a_new_post_still_asks_the_poster(self):
        bot = fake_bot(self.db, None)
        message, _ = fake_message("https://www.instagram.com/reel/new1/")
        asyncio.run(bot.archive_message(message))
        self.assertEqual(bot.asked, ["ig:new1"])


if __name__ == "__main__":
    unittest.main()
