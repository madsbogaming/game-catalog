r"""Print messages in the "Madsbowacky feedback" Discord thread that haven't been put in the backlog yet.

    .venv\Scripts\python.exe tools\new_feedback.py          # list new messages (oldest first)
    .venv\Scripts\python.exe tools\new_feedback.py --mark   # remember that everything up to now is handled

Used by the scheduled "Update the Madsbowacky backlog" task. Reads with the bot's token from .env (never printed);
the last handled message id is kept in catalog.db (meta key feedback_last_id).
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402

import bot as B  # noqa: E402

# The thread itself, looked up by id: the bot may only read this thread, #builders and the watched channels
THREAD_ID = int(os.getenv("FEEDBACK_THREAD_ID") or 0)


async def main(mark: bool):
    if not THREAD_ID:
        sys.exit("Set FEEDBACK_THREAD_ID in .env first")
    client = discord.Client(intents=discord.Intents.none())
    await client.login(B.TOKEN)  # HTTP only, no gateway
    try:
        try:
            thread = await client.fetch_channel(THREAD_ID)
        except discord.HTTPException:
            print("Thread not found")
            return
        last = int(B.bot.db.get_meta("feedback_last_id") or 0)
        after = discord.Object(last) if last else None
        newest = last
        count = 0
        async for msg in thread.history(limit=None, after=after, oldest_first=True):
            newest = max(newest, msg.id)
            if msg.author.bot:
                continue
            # A forwarded message has no text of its own; the original is in its message snapshots
            parts = [(msg.content, msg.attachments, "")]
            for snap in getattr(msg, "message_snapshots", None) or []:
                parts.append((snap.content, snap.attachments, "(forwarded) "))
            for text, attachments, label in parts:
                if not text.strip() and not attachments:
                    continue
                files = f" [attached: {', '.join(a.url for a in attachments)}]" if attachments else ""
                print(f"{msg.created_at:%Y-%m-%d %H:%M} | {msg.author.display_name}: {label}{text}{files}")
                count += 1
        if not count:
            print("No new feedback")
        if mark and newest != last:
            B.bot.db.set_meta("feedback_last_id", newest)
            print("Marked as handled")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main("--mark" in sys.argv))
