"""S17: the bot's bio ends with the GitHub repo, added at start only when it's missing."""

import asyncio
import unittest

from bot import BIO_MAX, REPO_URL, GameBot

BIO = "Turns the game links your friends post into one catalog to browse, vote on and play from."


class FakeApp:
    def __init__(self, description):
        self.description, self.edits = description, []

    async def edit(self, *, description):
        self.edits.append(description)
        self.description = description


def run(description) -> FakeApp:
    app = FakeApp(description)
    bot = GameBot.__new__(GameBot)

    async def application_info():
        return app
    bot.application_info = application_info
    asyncio.run(bot.link_repo_in_bio())
    return app


class RepoInBio(unittest.TestCase):
    def test_adds_the_link_under_the_bio(self):
        self.assertEqual(run(BIO).edits, [f"{BIO}\n\n{REPO_URL}"])

    def test_leaves_a_bio_that_has_it_alone(self):
        self.assertEqual(run(f"{BIO} Code: {REPO_URL}").edits, [])

    def test_an_empty_bio_gets_just_the_link(self):
        self.assertEqual(run(None).edits, [REPO_URL])

    def test_never_cuts_a_full_bio(self):
        self.assertEqual(run("x" * (BIO_MAX - 10)).edits, [])


if __name__ == "__main__":
    unittest.main()
