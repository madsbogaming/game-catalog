import unittest
from types import SimpleNamespace

from guilds import (LOCALES, OTHER_COUNTRY, OTHER_TIMEZONE, Defaults, Settings, find_timezones, guess_place,
                    parse_channel, parse_country, parse_ids, parse_timezone, resolve_timezone, welcome_channel)
from tests.helpers import GUILD_A, GUILD_B, new_db

# Channels the fake Discord knows: 1-3 are in server A, 4 is in server B
CHANNELS = {1: GUILD_A, 2: GUILD_A, 3: GUILD_A, 4: GUILD_B}


def settings(db=None, **env) -> Settings:
    """Settings as the home server A's .env gives them: watch 1 and 2, read 3, admin 99."""
    env = {"home_guild": GUILD_A, "watch": [1, 2], "read": [3], "admins": {99}, **env}
    return Settings(db or new_db(), Defaults(**env), CHANNELS.get)


class Parsing(unittest.TestCase):
    def test_parse_ids(self):
        self.assertEqual(parse_ids(" 3, 1,3,, 2 "), [3, 1, 2])
        self.assertEqual(parse_ids(None), [])

    def test_parse_channel(self):
        self.assertIsNone(parse_channel(""))
        self.assertIsNone(parse_channel(None))
        self.assertEqual(parse_channel("0"), 0)
        self.assertEqual(parse_channel(" 12 "), 12)


class HomeServerKeepsItsEnvSettings(unittest.TestCase):
    def test_channels(self):
        s = settings()
        self.assertEqual(s.watch_channels(GUILD_A), [1, 2])
        self.assertEqual(s.read_channels(GUILD_A), {1, 2, 3})
        self.assertTrue(s.watches(GUILD_A, 1))
        self.assertTrue(s.watches(GUILD_A, 555, parent_id=2))  # a thread in a watched channel
        self.assertTrue(s.may_read(GUILD_A, 3))
        self.assertFalse(s.watches(GUILD_A, 3))
        self.assertFalse(s.may_read(GUILD_A, 4))

    def test_post_channels_fall_back_like_before(self):
        s = settings()
        self.assertEqual(s.post_channel(GUILD_A, "free_games"), 1)  # the first watched channel
        self.assertEqual(s.post_channel(GUILD_A, "deals"), 1)  # with the free games
        self.assertEqual(s.post_channel(GUILD_A, "achievements"), 1)
        self.assertEqual(s.post_channel(GUILD_A, "played_rule"), 1)  # with the achievements
        self.assertIsNone(s.post_channel(GUILD_A, "gotg"))  # looked up by its name

        s = settings(posts={"free_games": 2, "achievements": 0, "gotg": 3})
        self.assertEqual(s.post_channel(GUILD_A, "deals"), 2)
        self.assertEqual(s.post_channel(GUILD_A, "played_rule"), 0)  # off, like the achievements
        self.assertEqual(s.post_channel(GUILD_A, "gotg"), 3)

    def test_admins(self):
        s = settings()
        self.assertTrue(s.is_admin(GUILD_A, 99, can_manage_messages=False))
        self.assertFalse(s.is_admin(GUILD_A, 5, can_manage_messages=True))  # ADMIN_IDS set: only them
        self.assertTrue(settings(admins=set()).is_admin(GUILD_A, 5, can_manage_messages=True))  # none: mods

    def test_channel_the_bot_cant_see_counts_for_home(self):
        s = settings(read=[3, 777])  # 777: e.g. an archived thread
        self.assertIn(777, s.read_channels(GUILD_A))
        self.assertNotIn(777, s.read_channels(GUILD_B))

    def test_no_home_server_set_means_every_server_is_home(self):
        s = settings(home_guild=0, admins={99})
        self.assertEqual(s.admins(GUILD_B), {99})
        self.assertEqual(s.watch_channels(GUILD_B), [])  # the .env channels are still in server A


class OtherServersStartEmpty(unittest.TestCase):
    def test_nothing_watched_or_posted(self):
        s = settings(posts={"free_games": 1, "gotg": 0})
        self.assertEqual(s.watch_channels(GUILD_B), [])
        self.assertFalse(s.may_read(GUILD_B, 4))
        self.assertEqual(s.post_channel(GUILD_B, "free_games"), 0)  # channel 1 is server A's
        self.assertEqual(s.post_channel(GUILD_B, "deals"), 0)
        self.assertIsNone(s.post_channel(GUILD_B, "gotg"))  # .env's 0 only turns it off at home

    def test_mods_are_admins_until_set(self):
        s = settings()
        self.assertEqual(s.admins(GUILD_B), set())
        self.assertTrue(s.is_admin(GUILD_B, 5, can_manage_messages=True))
        self.assertFalse(s.is_admin(GUILD_B, 99, can_manage_messages=False))  # ADMIN_IDS are the home server's


class Setup(unittest.TestCase):
    def test_watch_starts_from_the_env_channels(self):
        s = settings()
        self.assertEqual(s.set_watched(GUILD_A, 3, True), [1, 2, 3])
        self.assertEqual(s.set_watched(GUILD_A, 1, False), [2, 3])
        self.assertEqual(s.watch_channels(GUILD_A), [2, 3])
        self.assertEqual(s.post_channel(GUILD_A, "free_games"), 2)  # the first watched channel now

    def test_servers_dont_share_settings(self):
        db = new_db()
        s = settings(db)
        s.set_watched(GUILD_B, 4, True)
        s.set_post_channel(GUILD_B, "gotg", 4)
        self.assertEqual(s.watch_channels(GUILD_B), [4])
        self.assertEqual(s.watch_channels(GUILD_A), [1, 2])
        self.assertEqual(s.post_channel(GUILD_B, "free_games"), 4)
        self.assertEqual(s.post_channel(GUILD_B, "gotg"), 4)
        self.assertIsNone(s.post_channel(GUILD_A, "gotg"))
        self.assertEqual(settings(db).watch_channels(GUILD_B), [4])  # kept in the database

    def test_post_channel_off_and_back_to_default(self):
        s = settings()
        s.set_post_channel(GUILD_A, "deals", 0)
        self.assertEqual(s.post_channel(GUILD_A, "deals"), 0)
        s.set_post_channel(GUILD_A, "deals", None)
        self.assertEqual(s.post_channel(GUILD_A, "deals"), 1)
        with self.assertRaises(ValueError):
            s.set_post_channel(GUILD_A, "nonsense", 1)

    def test_read_channels(self):
        s = settings()
        self.assertEqual(s.set_read(GUILD_B, 4, True), {4})
        self.assertEqual(s.set_read(GUILD_A, 3, False), {1, 2})

    def test_admins(self):
        s = settings()
        self.assertEqual(s.set_admin(GUILD_B, 5, True), {5})
        self.assertTrue(s.is_admin(GUILD_B, 5, can_manage_messages=False))
        self.assertFalse(s.is_admin(GUILD_B, 6, can_manage_messages=True))
        self.assertEqual(s.set_admin(GUILD_A, 5, True), {5, 99})  # starts from ADMIN_IDS
        self.assertEqual(s.set_admin(GUILD_B, 5, False), set())
        self.assertTrue(s.is_admin(GUILD_B, 6, can_manage_messages=True))  # none left: mods again


class CountryAndTimeZone(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(parse_country(" DK"), "dk")
        self.assertIsNone(parse_country("dnk"))
        self.assertIsNone(parse_country("d1"))
        self.assertIsNone(parse_country(""))
        self.assertEqual(parse_timezone("europe/copenhagen"), "Europe/Copenhagen")
        self.assertIsNone(parse_timezone("Mars/Base"))
        self.assertIsNone(parse_timezone(None))
        self.assertEqual(find_timezones("copen"), ["Europe/Copenhagen"])
        self.assertIn("America/New_York", find_timezones("new y"))

    def test_home_server_keeps_its_env_settings_others_start_neutral(self):
        s = settings(country="dk", timezone="Europe/Copenhagen")
        self.assertEqual(s.country(GUILD_A), "dk")
        self.assertEqual(str(s.timezone(GUILD_A)), "Europe/Copenhagen")
        self.assertEqual(s.country(GUILD_B), OTHER_COUNTRY)
        self.assertEqual(str(s.timezone(GUILD_B)), OTHER_TIMEZONE)

    def test_set_and_back_to_default(self):
        s = settings(country="dk")
        s.set_country(GUILD_B, "de")
        s.set_timezone(GUILD_B, "Europe/Berlin")
        self.assertEqual(s.country(GUILD_B), "de")
        self.assertEqual(str(s.timezone(GUILD_B)), "Europe/Berlin")
        s.set_country(GUILD_B, None)
        s.set_timezone(GUILD_B, None)
        self.assertEqual(s.country(GUILD_B), OTHER_COUNTRY)
        self.assertEqual(str(s.timezone(GUILD_B)), OTHER_TIMEZONE)
        with self.assertRaises(ValueError):
            s.set_country(GUILD_B, "DNK")
        with self.assertRaises(ValueError):
            s.set_timezone(GUILD_B, "Mars/Base")

    def test_countries_groups_servers_so_each_is_fetched_once(self):
        s = settings(country="dk")
        s.set_country(GUILD_B, "dk")
        self.assertEqual(s.countries([GUILD_A, GUILD_B]), {"dk": [GUILD_A, GUILD_B]})
        s.set_country(GUILD_B, "us")
        self.assertEqual(s.countries([GUILD_A, GUILD_B]), {"dk": [GUILD_A], "us": [GUILD_B]})

    def test_now_is_in_the_servers_time_zone(self):
        s = settings(timezone="Asia/Tokyo")  # no daylight saving: always 9 hours ahead of UTC
        s.set_timezone(GUILD_B, "UTC")
        difference = s.now(GUILD_A) - s.now(GUILD_B)
        self.assertAlmostEqual(difference.total_seconds(), 9 * 3600, delta=5)
        self.assertIsNone(s.now(GUILD_A).tzinfo)  # compares with the naive times the bot builds from GOTG_TIME


class Welcome(unittest.TestCase):
    """Which channel a server the bot was just added to gets its hello in."""

    @staticmethod
    def channel(name, can_post):
        return SimpleNamespace(name=name, permissions_for=lambda me: SimpleNamespace(send_messages=can_post))

    def guild(self, system, *others):
        return SimpleNamespace(me=object(), system_channel=system, text_channels=list(others))

    def test_system_channel_first(self):
        system = self.channel("welcome", True)
        self.assertIs(welcome_channel(self.guild(system, self.channel("general", True))), system)

    def test_first_channel_it_may_post_in(self):
        general = self.channel("general", True)
        guild = self.guild(self.channel("welcome", False), self.channel("rules", False), general)
        self.assertIs(welcome_channel(guild), general)
        self.assertIs(welcome_channel(self.guild(None, general)), general)

    def test_nowhere(self):
        self.assertIsNone(welcome_channel(self.guild(None, self.channel("rules", False))))

if __name__ == "__main__":
    unittest.main()


class SetupForm(unittest.TestCase):
    def test_guess_place_from_the_servers_language(self):
        self.assertEqual(guess_place("da"), ("dk", "Europe/Copenhagen"))
        self.assertEqual(guess_place("xx"), (OTHER_COUNTRY, OTHER_TIMEZONE))

    def test_every_guess_is_a_real_country_and_time_zone(self):
        for country, zone in LOCALES.values():
            self.assertEqual(parse_country(country), country)
            self.assertEqual(parse_timezone(zone), zone)

    def test_resolve_timezone_takes_a_city(self):
        self.assertEqual(resolve_timezone("europe/copenhagen"), "Europe/Copenhagen")
        self.assertEqual(resolve_timezone(" Copenhagen "), "Europe/Copenhagen")
        self.assertIsNone(resolve_timezone("america"))  # many: no guessing
        self.assertIsNone(resolve_timezone(""))

    def test_is_set(self):
        s = settings()
        self.assertFalse(s.is_set(GUILD_A, "country"))
        s.set_country(GUILD_A, "dk")
        self.assertTrue(s.is_set(GUILD_A, "country"))
