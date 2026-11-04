# Game Catalog bot: the full guide

A Discord bot that watches your server for **Steam store links** and **Instagram reels**, archives them, and turns them into a catalog of games you and your friends want to play together.

- Post a Steam link → the bot looks up the name, genres, Steam tags, co-op/multiplayer modes, price, release date and review score.
- Post a Battle.net link → a shop page (`shop.battle.net/…/product/diablo-iv`, or a `…/family/…` page like WoW Classic's) or a Blizzard game's own site (`worldofwarcraft.blizzard.com`, for games without a shop page) is added with a blue "Battle.net" tag (also in the genres & tags filter). Blizzard has no public store API, so the info is pieced together: name, description and cover from the page's link preview; genre, cheapest edition and launch date from a shop page; built-in basics for Blizzard's own games (genres, tags, modes, release date, in `BLIZZARD_GAMES` in `sources.py`); and reviews and tags from Steam when the game is sold there too. They have no Steam hours and skip the online-play check. A game already in the catalog from Steam counts as a repeat. Call of Duty games come from Steam only: Battle.net Call of Duty links are turned away. Pressing a Battle.net game in the catalog opens it in the Battle.net app (`static/open-battlenet.html`, falling back to its web page). `tools/announce_battlenet.py` posts the Battle.net announcement (`--preview` DMs it to the first admin first).
- Post an Instagram reel (also as an oginstagram.com / ddinstagram.com / kkinstagram.com link, the sites that fix Instagram previews in Discord) → archived as a "reel". If its caption names the game ("Game: Nested Lands", "… - Cursed Companions #gaming", "RV THERE YET? #gaming") and that name is exactly a Steam game's title, the reel becomes a clip of that game right away. Otherwise the bot asks the person who posted it which game it is, with a "Tell me the game" button (only they or an admin can answer). `/link` still works to fix or set it by hand. Post a reel *together with* a Steam link and they're linked automatically.
- The bot reacts 🎮 to every archived post. Friends click it to say "I want to play this", which feeds the **Most wanted** sort and `/random`.
- If someone posts a game that's already in the catalog, the bot tells them who added it first.
- When someone starts playing a catalog game (Discord shows "Playing …" on their profile), the bot marks them as playing it and wanting to play it. When the bot starts, it also checks for catalog games people are already playing. With `STEAM_API_KEY` set, it also asks Steam every 2 minutes what people who linked Steam are playing, so they're marked even when Discord doesn't show their games (activity sharing off, Discord in a browser); that needs a public Steam profile that isn't set to Invisible. They unmark it themselves with the "You're playing" button. Every 5 minutes the bot also counts who Discord shows playing which catalog game, so ratings show hours for games Steam can't tell about (Battle.net games, or people without a linked Steam).
- When someone sends a game invite for a catalog game (Discord's "Invite to Join" or a `steam://joinlobby/...` link) in a channel the bot reads, the bot @mentions everyone marked "Wants to play" for it.
- Every 2 hours the bot checks Steam for games that are 100% off (free to keep) and posts each one once, with its normal price and when the offer ends. DLC, soundtracks and explicit games are skipped. Free games that fit the catalog rules are added to the catalog too. Set `FREE_GAMES_CHANNEL_ID` to post them somewhere other than the first watched channel.
- Every Thursday at 19:00 (in the server's time zone) the bot posts the catalog games nobody has played yet that are **70% off or more** on Steam. Games 3 or more people want to play come first, with their cover, sorted by how many want them; the rest follow by discount (20 games at most). If the bot is off at that time, it posts as soon as it's back that week. Set `DEALS_CHANNEL_ID` (default: the free-games channel), `DEALS_DAY` (0 = Monday … 6 = Sunday), `DEALS_HOUR` and `DEALS_MIN_DISCOUNT` to change it.
- Only games you can play together online are accepted: Steam must list online co-op, online PvP, MMO or cross-platform multiplayer (or tags like these). Singleplayer games and games with only same-screen multiplayer (shared/split screen, LAN, Remote Play Together) are turned away with a reply, and so are links Steam has no store info for and anything that isn't a game: DLC, demos, soundtracks, software, dedicated/test servers and playtests. Explicit games are turned away too: ones Steam marks "Adult Only" or "Frequent" nudity/sexual content, or tags Hentai, Sexual Content or NSFW. Games listed in `EXPLICIT_ALLOWED_APPIDS` in `sources.py` (currently AION 2) skip this check.

## Commands

| Command | What it does |
|---|---|
| `/catalog` | Opens the interactive catalog inside Discord (an Activity): search, sort and filter at the top, every game below. Click "I want to play" on any game. "Ping to play" on a game lists who to ping (everyone who wants it is ticked) and pings them in the game's channel; each person can be pinged by you once every 30 minutes. Clicking a game's title or cover opens it in the Steam app (through [`static/open-steam.html`](../static/open-steam.html), which falls back to the store website if Steam doesn't open). A game's status (Not played yet / Demo played / Played) is shared by everyone; refunding is personal: "I refunded it" in the same menu marks only you, the card shows who refunded it, and the Refunded filter and "Most refunded" sort show which games most of you refunded. Hover an avatar to see the person's profile, or click it to keep the profile open. Updates live as games are posted. |
| `/game` | Everything about one game: description, modes, who's interested, clips. |
| `/random` | Pick a game for tonight (weighted toward games more people want). Has a reroll button. |
| `/add` | Add a Steam game by name or link without posting it. |
| `/link` | Tell the bot which Steam game a reel is about. |
| `/status` | Mark a game as Want to play / Playing / Played / Dropped. |
| `/tag` | Add your own tags, e.g. `horror, party, 4 players`. |
| `/genres` | See the genres and tags in your catalog. |
| `/export` | Get the whole catalog as a web page you can sort and filter in your browser. |
| `/refresh` | Update a game's price, reviews and tags from Steam. |
| `/remove` | Remove a game. |
| `/allow` | Let a game in even though its Steam page breaks the rules (e.g. playable online but Steam only lists split screen). It's added, credited to whoever posted it first, and stays allowed. |
| `/backfill` | Archive links posted before the bot joined. Reads the whole channel unless you give a `limit`. |
| `/setup show` | See which channels the bot reads and posts in on this server, and who its admins are. |
| `/setup watch` | Put game links from a channel into the catalog (`on: False` stops it). Run `/backfill` there afterwards for the older links. |
| `/setup read` | Let the bot read a channel or thread without adding its links, e.g. a feedback thread. |
| `/setup post` | Pick the channel for free Steam games, weekly deals, achievements, Played rule posts or #GOTG. Leave the channel empty to turn those posts off, or pick `default: True` to go back to the default. |
| `/setup admin` | Let someone use every command on this server (`on: False` takes it away). |
| `/setup country` | Show Steam prices in your country's currency, e.g. `dk` or `us`. Leave it empty to go back to the default. |
| `/setup timezone` | The time zone #GOTG and the weekly deals post in, e.g. `Europe/Copenhagen`. Start typing a city to pick one. |

Everyone can use `/catalog`, `/random` and `/link`. All other commands are only for the server's admins, and regular members don't see them. A server's admins are the ones picked with `/setup admin`; on the home server (`GUILD_ID`) it's `ADMIN_IDS` in `.env` until you pick some. With no admins picked, mods (Manage Messages permission) can use them.

### More than one server

One bot can serve any number of servers. Each server has its own catalog, votes, ratings, achievements, #GOTG and weekly deals; nothing is shared between them. Steam libraries and hours belong to a person, so they count in every server that person is in, and only there.

The channels in `.env` are the defaults for the server they're in. A server the bot is added to later starts with nothing watched: a mod runs `/setup watch` on its games channel, then `/backfill` there. Its free games, deals and achievements go to the first watched channel unless `/setup post` picks another one, and #GOTG is off until `/setup post` picks a channel (only the home server falls back to a channel named `GOTG_CHANNEL_NAME`). Its prices come from Steam's US store and its posts go by UTC until `/setup country` and `/setup timezone` change them.

## Setup

This is for running a copy of the bot yourself, which [LICENSE](../LICENSE) allows only on a private test server for developing contributions (see [CONTRIBUTING.md](../CONTRIBUTING.md)). To use the bot, [add the official one](../README.md#add-it-to-your-server).

### 1. Create the bot on Discord
1. Go to <https://discord.com/developers/applications> → **New Application**.
2. Open **Bot** → **Reset Token**, then copy the token.
3. On the same page, turn on **Message Content Intent** (to read links), **Presence Intent** and **Server Members Intent** (to see when someone starts playing a catalog game).
4. Open **OAuth2 → URL Generator**. Tick the scopes `bot` and `applications.commands`. Then tick these permissions: View Channels, Send Messages, Embed Links, Attach Files, Add Reactions, Read Message History.
5. Open the generated URL and add the bot to your server.

### 2. Configure
Copy `.env.example` to `.env` and fill in:
- `DISCORD_TOKEN`: the token from step 1.
- `GUILD_ID`: your server's ID, the bot's home server. Turn on Developer Mode in Discord settings, then right-click the server → Copy Server ID. `ADMIN_IDS` count for this server. Other servers set up their own with `/setup` (see [More than one server](#more-than-one-server)).
- `WATCH_CHANNEL_IDS`: the channels whose game links go into the catalog, e.g. your `#games` channel. You can also pick them later with `/setup watch`.
- `READ_CHANNEL_IDS` (optional): other channels or threads the bot may read, e.g. a feedback thread. The bot ignores messages in every channel that isn't in one of these two lists, even where Discord lets it see them. To make Discord enforce it too, take **View Channels** away from the bot's roles server-wide and give it back only on those channels.
- `STEAM_COUNTRY`: the country used for the home server's prices, e.g. `dk` for DKK.
- `TIMEZONE`: the home server's time zone for #GOTG and the weekly deals, e.g. `Europe/Copenhagen`. Without it they go by UTC, whatever the computer's own clock says.
- `PRESENCE_INTENT` (optional, default `true`): set it to `false` to run without Discord's Presence intent, for example if Discord's review turns it down (switch it off in the portal too). The bot then sees who's playing only through Steam, for people who linked it, and the catalog shows no online status.
- `STEAM_API_KEY` (optional): a free key from <https://steamcommunity.com/dev/apikey>. Ratings of played games get a green "played" badge when the bot has seen that person playing the game in Discord; with this key, the badge also shows their Steam hours (including play from before the bot existed). That works for people who linked Steam to their Discord account and have **Game details** set to public in Steam's privacy settings. The catalog reads the linked Steam account when they sign in, and the hours are refreshed every 2 hours. The bot also notices refunds: when a catalog game disappears from someone's Steam library after less than 2 hours of play (Steam's refund limit), it marks them as having refunded it ("Undo my refund" in the status menu takes it back).
- `DISCORD_CLIENT_SECRET`: from **OAuth2 → Reset Secret**. It lets people click "I want to play" in `/catalog`. Discord asks each person once to let the app see their username. The portal's OAuth2 **Redirects** must also contain `https://127.0.0.1`, which is a placeholder Activities require.

### 3. Turn on the catalog Activity
`/catalog` opens a web page inside Discord. The bot serves that page itself and opens a free Cloudflare tunnel so Discord can reach it. `tools/cloudflared.exe` must be in the project folder.

1. In the Developer Portal open **Activities → Settings** and turn on **Enable Activities**.
2. Start the bot (step 4). It prints a line like `Activity URL mapping ... must target: something.trycloudflare.com`.
3. Open **Activities → URL Mappings** and set the Root Mapping (prefix `/`) target to that address.

**Fixed address (recommended):** with [Tailscale](https://tailscale.com) installed and signed in on the bot's PC, run `tailscale funnel --bg 8765` once (approve Funnel when it asks). That publishes the catalog at `https://<pc-name>.<tailnet>.ts.net`, which never changes and survives reboots. Put that address in `.env` as `PUBLIC_URL=https://…ts.net` and in the Root Mapping once, and you're done: no tunnel is started and nothing changes on restart. In the Tailscale admin page, turn off **key expiry** for that PC, or the address stops working when its login expires.

**Without a fixed address**, the bot opens a free Cloudflare tunnel, and **the tunnel address changes every time the bot starts**, so repeat step 3 after each restart. The bot DMs the new address and a link to that page to everyone in `ADMIN_IDS` when it starts. Until you do, `/catalog` opens a blank Activity. The page also works in a normal browser at the printed `https://…trycloudflare.com` address, or at <http://127.0.0.1:8765> on the PC running the bot. Set `PUBLIC_TUNNEL=false` in `.env` to keep it on your PC only.

### 4. Run
```bash
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python bot.py
```

Then run `/backfill` in your games channel to import what's already been posted.

**Start with Windows:** `start-bot.bat` runs the bot with its log in `bot.log`, and starts it again 30 seconds after it stops (for example when the internet isn't up yet right after boot). `Game catalog bot.vbs` in the Windows Startup folder (`shell:startup`) runs it hidden at login. To stop the bot, end `python.exe` (and the `cmd.exe` running `start-bot.bat`) in Task Manager, or delete that `.vbs` to stop it starting with Windows.

**Catching up:** while running and connected to Discord, the bot notes the time every minute (not while the internet is down, so an outage counts as offline time too). When it starts, it goes through what was posted in the watched channels since then and handles game links as if they had just been posted (posts it already answered, e.g. turned away, are left alone), counts 🎮 reactions on game posts from the last 7 days, and marks people already playing a catalog game as playing.

The bot only works while this program is running. To keep it online 24/7, run it on an always-on machine. [SERVER.md](SERVER.md) shows how it runs on a small Hetzner server, and how to put new code live there.

## Notes
- Instagram blocks a lot of scraping, so a reel's caption and thumbnail are best-effort. Discord's own link preview is used as a fallback. The link itself is always saved.
- Files: `bot.py` (events and commands), `sources.py` (link parsing, Steam/Instagram lookups), `db.py` (SQLite storage), `catalog_html.py` (the catalog page), `web.py` (serves the page and runs the tunnel), `static/discord-sdk.mjs` (Discord's Embedded App SDK 2.5.0).
