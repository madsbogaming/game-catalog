import unittest

import achievements
from tests.helpers import GUILD_A, GUILD_B, add_steam, new_db

BOT = 1


class AchievementsPerGuild(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.db.set_members(GUILD_A, [7, 8])
        self.db.set_members(GUILD_B, [7])

    def test_only_the_servers_own_games_count(self):
        for appid in range(10):
            add_steam(self.db, GUILD_A, appid, added_by=7)
        for appid in range(5):
            add_steam(self.db, GUILD_B, appid, added_by=7)
        add_steam(self.db, GUILD_A, 99, added_by=BOT)  # the bot's own (free games) don't count
        self.assertEqual(achievements.values(self.db.conn, BOT, GUILD_A)[7]["explorer"], 10)
        self.assertEqual(achievements.values(self.db.conn, BOT, GUILD_B)[7]["explorer"], 5)
        self.assertNotIn(BOT, achievements.values(self.db.conn, BOT, GUILD_A))

    def test_steam_library_only_for_members(self):
        add_steam(self.db, GUILD_A, 1, price_cents=40000)
        add_steam(self.db, GUILD_B, 1, price_cents=40000)
        self.db.set_owned(8, {1})  # 8 is only in server A
        self.assertEqual(achievements.values(self.db.conn, BOT, GUILD_A)[8]["big_spender"], 400)
        self.assertNotIn(8, achievements.values(self.db.conn, BOT, GUILD_B))

    def test_sync_records_per_server(self):
        for appid in range(10):
            add_steam(self.db, GUILD_A, appid, added_by=7)
        achievements.sync(self.db, BOT, GUILD_A, silent=True)
        achievements.sync(self.db, BOT, GUILD_B, silent=True)
        self.assertEqual(self.db.achievement_tier(GUILD_A, 7, "explorer"), 1)
        self.assertEqual(self.db.achievement_tier(GUILD_B, 7, "explorer"), 0)
        self.assertEqual(self.db.unannounced(GUILD_A), {})  # silent: recorded without messages
        for appid in range(10, 50):
            add_steam(self.db, GUILD_A, appid, added_by=7)
        achievements.sync(self.db, BOT, GUILD_A, silent=False)
        self.assertEqual(self.db.unannounced(GUILD_A), {7: [("explorer", 2)]})
        self.assertEqual([a["key"] for a in achievements.earned(self.db, GUILD_A, 7)], ["explorer"])
        self.assertEqual(achievements.earned(self.db, GUILD_B, 7), [])

    def test_biggest_refunder_per_server(self):
        a, b = add_steam(self.db, GUILD_A, 1), add_steam(self.db, GUILD_B, 1)
        self.db.set_refund(a["id"], 7, "Seven", True, 100)
        self.db.set_refund(b["id"], 8, "Eight", True, 100)
        achievements.sync(self.db, BOT, GUILD_A, silent=False)
        achievements.sync(self.db, BOT, GUILD_B, silent=False)
        self.assertEqual(self.db.get_guild_meta(GUILD_A, "refund_title"), "7")
        self.assertEqual(self.db.get_guild_meta(GUILD_B, "refund_title"), "8")
        self.assertEqual([tuple(r) for r in achievements.refund_counts(self.db.conn, GUILD_A)], [(7, 1, 100)])
        self.assertTrue(achievements.earned(self.db, GUILD_A, 7)[0]["title"])
        self.assertFalse(any(x["title"] for x in achievements.earned(self.db, GUILD_B, 7)))


if __name__ == "__main__":
    unittest.main()
