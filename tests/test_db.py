import json
import os
import sqlite3
import tempfile
import unittest

from db import Database
from tests.helpers import GUILD_A, GUILD_B, add_steam, new_db


class CatalogPerGuild(unittest.TestCase):
    def setUp(self):
        self.db = new_db()

    def test_same_game_can_be_in_two_servers(self):
        a = add_steam(self.db, GUILD_A, 10, "Shared")
        b = add_steam(self.db, GUILD_B, 10, "Shared")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(self.db.get_by_key(GUILD_A, "steam:10")["id"], a["id"])
        self.assertEqual(self.db.get_by_key(GUILD_B, "steam:10")["id"], b["id"])

    def test_query_search_and_genres_only_see_their_own_server(self):
        add_steam(self.db, GUILD_A, 1, "Alpha", genres=["Horror"])
        add_steam(self.db, GUILD_B, 2, "Beta", genres=["Racing"])
        self.assertEqual([e["title"] for e in self.db.query(GUILD_A)], ["Alpha"])
        self.assertEqual([e["title"] for e in self.db.search(GUILD_B, "a")], ["Beta"])
        self.assertEqual(set(self.db.genre_counts(GUILD_A)[0]), {"Horror"})

    def test_guild_meta_is_kept_per_server(self):
        self.db.set_guild_meta(GUILD_A, "deals_posted", "2026-11-05")
        self.assertEqual(self.db.get_guild_meta(GUILD_A, "deals_posted"), "2026-11-05")
        self.assertIsNone(self.db.get_guild_meta(GUILD_B, "deals_posted"))
        self.db.set_guild_meta(GUILD_A, "deals_posted", None)
        self.assertIsNone(self.db.get_guild_meta(GUILD_A, "deals_posted"))

    def test_members(self):
        self.db.set_members(GUILD_A, [1, 2, 3])
        self.db.set_members(GUILD_B, [3])
        self.db.set_members(GUILD_A, [2, 3, 4])
        self.db.add_member(GUILD_B, 5)
        self.db.remove_member(GUILD_A, 4)
        self.assertEqual(self.db.members(GUILD_A), {2, 3})
        self.assertEqual(self.db.members(GUILD_B), {3, 5})

    def test_unchanged_members_dont_make_pages_reload(self):
        self.db.set_members(GUILD_A, [1, 2])
        before = self.db.version
        self.db.set_members(GUILD_A, [2, 1])
        self.assertEqual(self.db.version, before)


class SteamDataOnlyCountsForMembers(unittest.TestCase):
    """Steam libraries and hours belong to a person: they only count in the servers that person is in."""

    def setUp(self):
        self.db = new_db()
        self.a = add_steam(self.db, GUILD_A, 50)
        self.b = add_steam(self.db, GUILD_B, 50)
        self.db.set_members(GUILD_A, [1])
        self.db.set_members(GUILD_B, [2])
        for uid in (1, 2):
            self.db.set_owned(uid, {50})
            self.db.set_playtime(uid, {50: (120, 1_700_000_500 + uid)})

    def test_owners(self):
        self.assertEqual(self.db.owners(GUILD_A), {50: {1}})
        self.assertEqual(self.db.owners(GUILD_B), {50: {2}})

    def test_played_by(self):
        self.assertEqual(self.db.played_by(self.a["id"]), [1])
        self.assertEqual(self.db.played_by(self.b["id"]), [2])
        played = self.db.played_map()
        self.assertEqual(played[self.a["id"]]["by"], {1})
        self.assertEqual(played[self.b["id"]]["by"], {2})

    def test_last_played(self):
        last = self.db.last_played()
        self.assertEqual(last[self.a["id"]], 1_700_000_501)
        self.assertEqual(last[self.b["id"]], 1_700_000_502)

    def test_someone_who_left_no_longer_counts(self):
        self.db.remove_member(GUILD_A, 1)
        self.assertEqual(self.db.owners(GUILD_A), {})
        self.assertEqual(self.db.played_by(self.a["id"]), [])


class GotgPerGuild(unittest.TestCase):
    def test_each_server_has_its_own_post_each_day(self):
        db = new_db()
        a, b = add_steam(db, GUILD_A, 1), add_steam(db, GUILD_B, 2)
        db.save_gotg_post(GUILD_A, "2026-11-03", [[a["id"], "owned"]])
        db.save_gotg_post(GUILD_B, "2026-11-03", [[b["id"], "buy"]], channel_id=9, message_id=8)
        self.assertEqual(db.gotg_post(GUILD_A, "2026-11-03")["picks"], [[a["id"], "owned"]])
        self.assertEqual(db.gotg_post(GUILD_B, "2026-11-03")["message_id"], 8)
        self.assertEqual(db.gotg_posted_since(GUILD_A, "2026-11-01"), {a["id"]})
        self.assertEqual(db.gotg_posted_since(GUILD_B, "2026-11-04"), set())


class AchievementsTable(unittest.TestCase):
    def test_kept_per_server(self):
        db = new_db()
        db.save_achievement(GUILD_A, 7, "explorer", 2, 100, announced=False)
        db.save_achievement(GUILD_B, 7, "explorer", 1, 100, announced=True)
        self.assertEqual(db.achievement_tier(GUILD_A, 7, "explorer"), 2)
        self.assertEqual(db.achievement_tier(GUILD_B, 7, "explorer"), 1)
        self.assertEqual(db.unannounced(GUILD_A), {7: [("explorer", 2)]})
        self.assertEqual(db.unannounced(GUILD_B), {})
        db.mark_announced(GUILD_A, 7, ["explorer"])
        self.assertEqual(db.unannounced(GUILD_A), {})

    def test_refund_title_moves_within_one_server(self):
        db = new_db()
        db.move_title(GUILD_A, None, 1, 100, announced=True)
        db.move_title(GUILD_B, None, 2, 100, announced=True)
        db.move_title(GUILD_A, 1, 3, 200, announced=False)
        self.assertEqual(db.achievements(GUILD_A, 3), [("biggest_refunder", 1, 200)])
        self.assertEqual(db.achievements(GUILD_A, 1), [])
        self.assertEqual(db.achievements(GUILD_B, 2), [("biggest_refunder", 1, 100)])


class MigrationFromOneServer(unittest.TestCase):
    """An older catalog.db (one server) opens with its bot state moved to the server its games are in."""

    def test_old_catalog_moves_to_its_server(self):
        path = os.path.join(tempfile.mkdtemp(), "catalog.db")
        db = Database(path)
        entry = add_steam(db, GUILD_A, 1)
        db.conn.close()
        conn = sqlite3.connect(path)  # make it look like the version before servers were kept apart
        conn.executescript(f"""
            DROP TABLE gotg_posts;
            CREATE TABLE gotg_posts (day TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER,
                                     message_id INTEGER, picks TEXT NOT NULL);
            INSERT INTO gotg_posts VALUES ('2026-11-02', {GUILD_A}, 5, 6, '[[{entry["id"]}, "owned"]]');
            DROP TABLE achievements;
            CREATE TABLE achievements (user_id INTEGER NOT NULL, key TEXT NOT NULL, tier INTEGER NOT NULL,
                                       at INTEGER NOT NULL, announced INTEGER NOT NULL DEFAULT 0,
                                       PRIMARY KEY (user_id, key));
            INSERT INTO achievements VALUES (7, 'explorer', 1, 100, 1);
            INSERT INTO meta VALUES ('deals_posted', '2026-10-29'), ('achievement_ping:7', '123'),
                                    ('allowed_apps', '[42]'), ('last_seen', '999');""")
        conn.commit()
        conn.close()

        db = Database(path)
        self.assertEqual(db.gotg_post(GUILD_A, "2026-11-02")["picks"], [[entry["id"], "owned"]])
        self.assertEqual(db.achievements(GUILD_A, 7), [("explorer", 1, 100)])
        self.assertEqual(db.get_guild_meta(GUILD_A, "deals_posted"), "2026-10-29")
        self.assertEqual(db.get_guild_meta(GUILD_A, "achievement_ping:7"), "123")
        self.assertEqual(json.loads(db.get_guild_meta(GUILD_A, "allowed_apps")), [42])
        self.assertIsNone(db.get_meta("deals_posted"))
        self.assertEqual(db.get_meta("last_seen"), "999")  # still one for the whole bot
        db.conn.close()
        Database(path).conn.close()  # opening it again changes nothing

    def test_new_catalog_needs_no_migration(self):
        db = new_db()
        self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM guild_meta").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
