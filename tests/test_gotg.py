import unittest

import gotg
from tests.helpers import GUILD_A, GUILD_B, add_steam, new_db


class PicksPerGuild(unittest.TestCase):
    def setUp(self):
        self.db = new_db()
        self.a = add_steam(self.db, GUILD_A, 1, "Owned in A")
        self.b = add_steam(self.db, GUILD_B, 1, "Same game in B")
        self.db.set_members(GUILD_A, [10, 11, 12])
        self.db.set_members(GUILD_B, [10, 20])
        for uid in (10, 11, 12):
            self.db.set_owned(uid, {1})

    def test_owned_by_enough_of_the_servers_people(self):
        self.assertEqual(gotg.pick(self.db, GUILD_A, "2026-11-03", "2026-10-28"), [(self.db.get(self.a["id"]), "owned")])
        self.assertEqual(gotg.pick(self.db, GUILD_B, "2026-11-03", "2026-10-28"), [])  # only one owner is in B

    def test_a_post_in_one_server_doesnt_rest_games_in_another(self):
        self.db.save_gotg_post(GUILD_B, "2026-11-02", [[self.a["id"], "owned"]])  # (nonsense, but a clash would show)
        self.assertEqual(len(gotg.pick(self.db, GUILD_A, "2026-11-03", "2026-10-28")), 1)
        self.db.save_gotg_post(GUILD_A, "2026-11-02", [[self.a["id"], "owned"]])
        self.assertEqual(gotg.pick(self.db, GUILD_A, "2026-11-03", "2026-10-28"), [])

    def test_games_to_buy_come_from_the_servers_catalog(self):
        want = add_steam(self.db, GUILD_B, 2, "Wanted in B")
        for uid in (10, 20):
            self.db.add_interest(want["id"], uid)
        self.assertEqual([(e["id"], g) for e, g in gotg.pick(self.db, GUILD_B, "2026-11-03", "2026-10-28")],
                         [(want["id"], "buy")])
        self.assertNotIn(want["id"], [e["id"] for e, _ in gotg.pick(self.db, GUILD_A, "2026-11-03", "2026-10-28")])


if __name__ == "__main__":
    unittest.main()
