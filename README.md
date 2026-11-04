<p align="center"><img src="static/art/banner.png" alt="Game Catalog: what are we playing next?" width="720"></p>

<h1 align="center">Game Catalog bot</h1>

<p align="center"><b>Never lose another "we should play this!" link again.</b><br>
Your friends post games in Discord. The bot turns them into one catalog everyone can browse, vote on and launch, right inside Discord.</p>

---

## Why your server needs it

- **Just post a link.** Steam, Battle.net or an Instagram reel. The bot fills in price, reviews, tags and co-op modes for you.
- **See what everyone wants.** One click says "I want to play this", and the most wanted games rise to the top.
- **Can't decide?** `/random` picks tonight's game, favoring what most of you want.
- **Get the squad together.** "Ping to play" calls everyone who wants that game in one click.
- **Never miss a deal.** Free-to-keep Steam games and 70%+ sales on your list are posted automatically.
- **Earn achievements** for adding games, playing together and more (some are secret).
- **Only games you can play together.** Singleplayer and couch-only games are turned away, so the list stays useful.

## Add it to your server

It's free, and it runs around the clock. You need **Manage Server** on the server to add it.

<p align="center"><a href="https://discord.com/oauth2/authorize?client_id=1565754087776067584&permissions=274878024768&integration_type=0&scope=bot+applications.commands"><b>Add Game Catalog to Discord</b></a></p>

Then set it up in a minute:

1. **Pick your games channel:** `/setup watch` in the channel where people post games.
2. **Import what's already there:** `/backfill` in that channel.
3. **Your prices and time:** `/setup country` (for example `dk` or `us`) and `/setup timezone` (start typing your city).
4. **Optional:** `/setup post` picks where the daily #GOTG, the weekly deals and free games go, and can turn on Bot updates: a post now and then when something big is new.

That's it. Post a Steam link and watch it land in the catalog. `/setup show` lists everything the bot uses on your server.

## Everyone can use

| Command | What it does |
|---|---|
| `/catalog` | Opens the full catalog inside Discord: search, filter, vote and ping friends. |
| `/random` | Picks a game for tonight. Don't like it? Reroll. |
| `/link` | Tells the bot which game an Instagram reel is about. |

Admins get more commands to add, tag, refresh and remove games, and `/setup` to pick the channels the bot uses on their server.

## Want more?

The **[full guide](docs/GUIDE.md)** covers every feature and command, the in-Discord catalog Activity, Steam playtime tracking, and keeping the bot online 24/7.

## Privacy and terms

What the bot keeps about people and why is in the **[privacy policy](docs/PRIVACY.md)**. The rules for using it are in the **[terms of service](docs/TERMS.md)**.

## Help make it better

Found a bug or have an idea? [Open an issue](https://github.com/madsbogaming/mads-bo-wacky/issues/new/choose). Want to change the code? Start with **[CONTRIBUTING.md](CONTRIBUTING.md)**.

## License

The bot is **source-available, not open source.** You may read the code and run a copy on a private test server to develop contributions. You may not host your own copy or reuse the code elsewhere without written permission: [add the official bot](#add-it-to-your-server) instead. See [LICENSE](LICENSE), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the parts made by others.
