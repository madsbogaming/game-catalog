r"""Post the WoW: Forever launch announcement (which also says Battle.net games are now in the catalog).

    .venv\Scripts\python.exe tools\announce_battlenet.py --preview   # DM it to the first ADMIN_IDS only
    .venv\Scripts\python.exe tools\announce_battlenet.py --post      # post it in the games channel

Reads with the bot's token from .env (never printed). The posted message is linked to WoW: Forever in the catalog,
so a 🎮 reaction on it counts as "I want to play" for that game, like on any game post.
"""

import asyncio
import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402
from discord import ui  # noqa: E402

import bot as B  # noqa: E402

LAUNCH = 1793746800  # Wednesday 4 November 2026, midnight Danish time (Blizzard hasn't said the hour)
SHOP = "https://shop.battle.net/product/world-of-warcraft-forever"
# The WoW: Forever logo over Azeroth: the share picture of Blizzard's own WoW: Forever page
KEY_ART = ("https://blz-contentstack-images.akamaized.net/v3/assets/blt9c12f249ac15c7ec/blt766f7698aedb7b7f/"
           "6a9bc5882437ed5612d4878f/Open_Graph_-_Camelot.jpg")
BNET_BLUE = 0x148EFF
PREVIEW_TO = int((os.getenv("ADMIN_IDS") or "0").split(",")[0])  # the first admin in .env

# The message itself says Battle.net is in the catalog; the big card below it is all about WoW: Forever
HEADLINE = """## Battle.net games are now in the catalog, just in time for WoW: Forever
Post a Battle.net link here just like a Steam link, and pressing a Battle.net game in `/catalog` opens it in the Battle.net app. WoW: Forever, World of Warcraft, WoW Classic, Diablo IV, Hearthstone, Heroes of the Storm and StarCraft II are already in: pick the **Battle.net** tag to see them all."""

FOREVER = f"""### Launches <t:{LAUNCH}:D> (<t:{LAUNCH}:R>)
Who's starting it with us? React {B.INTEREST_EMOJI} to this message, or press "I want to play" on it in `/catalog`, so we can see who's in and get a group going launch week."""


def open_link() -> str:
    """Opens WoW: Forever in the Battle.net app (falling back to the shop page), through the catalog's opener page."""
    base = B.PUBLIC_URL.rstrip("/") if B.PUBLIC_URL else None
    return f"{base}/static/open-battlenet.html?app=WoWF&name=WoW%3A%20Forever&web={quote(SHOP, safe='')}" if base else SHOP


def message() -> dict:
    card = discord.Embed(title="World of Warcraft: Forever", url=open_link(), description=FOREVER, color=BNET_BLUE)
    card.set_image(url=KEY_ART)
    view = ui.View()
    view.add_item(ui.Button(style=discord.ButtonStyle.link, label="Open in Battle.net", url=open_link()))
    return {"content": HEADLINE, "embed": card, "view": view}


async def main(post: bool):
    client = discord.Client(intents=discord.Intents.none())
    await client.login(B.TOKEN)  # HTTP only, no gateway
    try:
        if post:
            channel = await client.fetch_channel(next(iter(B.WATCH_CHANNELS)))
            sent = await channel.send(**message())
            await sent.add_reaction(B.INTEREST_EMOJI)
            for guild_id in {e["guild_id"] for e in B.bot.db.conn.execute(
                    "SELECT guild_id FROM entries WHERE key = 'bnet:world-of-warcraft-forever'")}:
                entry = B.bot.db.get_by_key(guild_id, "bnet:world-of-warcraft-forever")
                B.bot.db.link_message(sent.id, entry["id"])
            print(f"Posted in #{channel.name}")
        else:
            if not PREVIEW_TO:
                sys.exit("Set ADMIN_IDS in .env to get the preview")
            admin = await client.fetch_user(PREVIEW_TO)
            dm = await admin.create_dm()
            await dm.send("**Preview 3** of the WoW: Forever announcement (only you can see this):")
            await dm.send(**message())
            print(f"Preview sent to {admin.name}")
    finally:
        await client.close()


if __name__ == "__main__":
    if "--post" not in sys.argv and "--preview" not in sys.argv:
        sys.exit(__doc__)
    asyncio.run(main("--post" in sys.argv))
