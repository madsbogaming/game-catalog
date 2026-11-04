"""CHANGELOG.md, read as entries that tools/announce_update.py can post to servers that turned on Bot updates
(/setup post). The bot never posts them by itself: only big updates are announced, and only when Mads says so.

Each entry starts with a `## <date>: <title>` heading; the heading is the entry's ID, so a server never gets the same
entry twice.
"""

import os
from dataclasses import dataclass

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHANGELOG.md")
DISCORD_LIMIT = 2000


@dataclass
class Entry:
    heading: str  # "2026-11-04: Game Catalog is open to every server", also the ID
    body: str

    @property
    def title(self) -> str:
        return self.heading.split(": ", 1)[-1]

    def message(self) -> str:
        """The entry as a Discord message, cut short to fit if it's very long."""
        text = f"## {self.title}\n{self.body}\n-# Game Catalog update · turn these off with `/setup post`"
        return text if len(text) <= DISCORD_LIMIT else text[:DISCORD_LIMIT - 1] + "…"


def parse(text: str) -> list[Entry]:
    """The entries, newest first (as they're written)."""
    entries: list[Entry] = []
    for block in text.split("\n## ")[1:]:
        heading, _, body = block.partition("\n")
        entries.append(Entry(heading.strip(), body.strip()))
    return entries


def latest() -> Entry | None:
    try:
        with open(PATH, encoding="utf-8") as f:
            entries = parse(f.read())
    except FileNotFoundError:
        return None
    return entries[0] if entries else None
