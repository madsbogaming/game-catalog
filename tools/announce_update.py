r"""Announce the newest CHANGELOG.md entry in every server that turned on Bot updates (/setup post).

Only for big updates, and only after Mads has said yes to this exact text. The bot never posts updates by itself.

    .venv/bin/python tools/announce_update.py              # show what would be posted, and where
    .venv/bin/python tools/announce_update.py --preview    # DM it to the first ADMIN_IDS only
    .venv/bin/python tools/announce_update.py --post       # post it in every server that turned Bot updates on

Run it on the server, where the live catalog is:
    ssh bot-server 'cd /opt/mads-bo-wacky && sudo -u bot .venv/bin/python tools/announce_update.py'

Reads with the bot's token from .env (never printed). A server that already got this entry doesn't get it again
(guild_meta key update_posted), so running --post twice is safe.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

import bot as B  # noqa: E402
import changelog  # noqa: E402

PREVIEW_TO = int((os.getenv("ADMIN_IDS") or "0").split(",")[0])  # the first admin in .env


def targets(heading: str) -> list[tuple[int, int]]:
    """(server id, channel id) for every server with a Bot updates channel that hasn't had this entry yet."""
    rows = B.bot.db.conn.execute("SELECT guild_id, value FROM guild_meta WHERE key = 'setting:updates'").fetchall()
    return [(gid, json.loads(value)) for gid, value in rows
            if value and json.loads(value) and B.bot.db.get_guild_meta(gid, "update_posted") != heading]


async def main(mode: str | None):
    entry = changelog.latest()
    if not entry:
        sys.exit("CHANGELOG.md has no entries")
    where = targets(entry.heading)
    print(f"Newest entry: {entry.heading}\n\n{entry.message()}\n")
    print(f"Would go to {len(where)} server(s) with Bot updates on that haven't had it yet.")
    if not mode:
        return
    client = discord.Client(intents=discord.Intents.none())
    await client.login(B.TOKEN)  # HTTP only, no gateway
    try:
        if mode == "--preview":
            if not PREVIEW_TO:
                sys.exit("Set ADMIN_IDS in .env to get the preview")
            admin = await client.fetch_user(PREVIEW_TO)
            await admin.send("**Preview** of the next Bot updates post (only you can see this):")
            await admin.send(entry.message(), allowed_mentions=discord.AllowedMentions.none())
            print(f"Preview sent to {admin.name}")
            return
        posted = 0
        for guild_id, channel_id in where:
            try:
                channel = await client.fetch_channel(channel_id)
                await channel.send(entry.message(), allowed_mentions=discord.AllowedMentions.none())
                B.bot.db.set_guild_meta(guild_id, "update_posted", entry.heading)
                posted += 1
                print(f"Posted in #{channel.name} ({guild_id})")
            except discord.HTTPException as exc:  # channel gone, or no longer allowed to post there
                print(f"Couldn't post in server {guild_id}: {exc}")
            await asyncio.sleep(1)
        print(f"Posted in {posted} of {len(where)} server(s)")
    finally:
        await client.close()


if __name__ == "__main__":
    mode = next((a for a in sys.argv[1:] if a in ("--preview", "--post")), None)
    asyncio.run(main(mode))
