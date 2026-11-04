import unittest

import changelog
from tests.helpers import GUILD_A, GUILD_B
from tests.test_guilds import settings

TEXT = """# Changelog

Intro text.

## 2026-12-01: Newer thing
- Second

## 2026-11-04: First thing
- First
"""


class Entries(unittest.TestCase):
    def test_newest_first_with_heading_as_id(self):
        entries = changelog.parse(TEXT)
        self.assertEqual([e.heading for e in entries], ["2026-12-01: Newer thing", "2026-11-04: First thing"])
        self.assertEqual(entries[0].title, "Newer thing")
        self.assertEqual(entries[0].body, "- Second")

    def test_message_fits_discord(self):
        entry = changelog.Entry("2026-12-01: Long", "x" * 5000)
        self.assertLessEqual(len(entry.message()), changelog.DISCORD_LIMIT)
        self.assertTrue(changelog.Entry("2026-12-01: Short", "- One").message().startswith("## Short\n- One"))

    def test_the_real_changelog_parses(self):
        self.assertIsNotNone(changelog.latest())


class OptIn(unittest.TestCase):
    def test_bot_updates_are_off_until_a_channel_is_picked(self):
        s = settings()
        self.assertEqual(s.post_channel(GUILD_A, "updates"), 0)  # not even the first watched channel
        self.assertEqual(s.post_channel(GUILD_B, "updates"), 0)
        s.set_post_channel(GUILD_B, "updates", 4)
        self.assertEqual(s.post_channel(GUILD_B, "updates"), 4)
        s.set_post_channel(GUILD_B, "updates", None)  # "default" means off again
        self.assertEqual(s.post_channel(GUILD_B, "updates"), 0)


if __name__ == "__main__":
    unittest.main()
