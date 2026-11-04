# Privacy policy

*Last updated: 4 November 2026*

This policy covers the Game Catalog bot ("the bot") and its catalog page, run by Mads ([madsbogaming](https://github.com/madsbogaming) on GitHub). It says what the bot keeps about you, why, who else sees it, and how to have it removed.

**In short:** the bot keeps what it needs to build your server's game catalog: the games people post, who wants or played them, and, if you sign in, your Steam hours. It doesn't sell anything, show ads or read channels it hasn't been given.

## What the bot keeps

| What | Where it comes from | Why |
|---|---|---|
| Game links posted in the channels the bot watches, who posted them and when | Your server's watched channels | To build the catalog and credit whoever added a game |
| Your Discord user ID, display name and avatar | Discord | To show who added, wants, plays or rated a game |
| Games you want to play, ratings, refunds, "I played it" answers, #GOTG "I'm in" presses, and pings you send | What you click in the catalog or in Discord | So the catalog shows what everyone wants and has played |
| 🎮 reactions on game posts | Your server's watched channels | They count as "I want to play this" |
| Which catalog games you play, and for how long | Your Discord activity ("Playing …"), while the bot can see it | Played badges, Recent activity, Steam-free playtime and achievements |
| Your Steam ID, the games in your Steam library and your hours in them | Only if you sign in to the catalog with Discord and your Discord account has Steam linked. Read from Steam when your game details are public | Played counts, #GOTG picks of games you already own, and achievements |
| Achievements you've earned | Worked out from the above | To show them on your profile and announce them |
| Which servers you're in with the bot | Discord | So Steam data only counts in servers you're actually in |
| Each server's settings (channels, admins) | That server's admins, via `/setup` | So the bot reads and posts in the right places |

The bot reads messages **only** in the channels and threads a server's admins have picked. Messages anywhere else are ignored, even when Discord would let the bot see them. From the messages it does read, it keeps only game links and the message's ID, not your text.

When you sign in to the catalog in a browser, a sign-in token is stored in that browser for about a week so you don't have to sign in again. The bot keeps sign-in tokens only in memory, for at most 10 minutes. The page also remembers in your browser which tips you've closed.

The bot's log file records what happens (for example "Name wants to play Game") so problems can be fixed.

## Who can see it

- **People in your server.** The catalog shows your name, avatar, online status and what you've wanted, played, rated and refunded to everyone who opens it.
- **Anyone with the catalog's address, for the bot's home server only.** The home server's catalog page can be opened without signing in. Any other server's catalog is shown only to its own signed-in members.
- **Services the bot relies on.** Discord (everything the bot does goes through Discord), Steam (the bot asks Steam's API for game details and, for people who signed in, their library and hours), Instagram (for previews of posted reels), and the service that makes the catalog reachable from outside (Tailscale Funnel or a Cloudflare tunnel). Each of them has its own privacy policy.

Nothing is sold, shared for advertising, or given to anyone else.

## Where it's stored and for how long

Everything is stored in one database on the computer that runs the bot. A copy is backed up every night to the operator's private cloud storage (OneDrive), and only the newest 30 copies are kept, so a backup is gone after about a month.

Data is kept as long as the bot runs for your server. If the bot leaves a server, that server's catalog is kept in case it's added back, but its members stop counting toward anything. If you leave a server, your Steam hours stop counting there.

## Your choices

- **Don't sign in** to the catalog, and the bot never sees your Steam ID, library or hours.
- **Turn off activity sharing** in Discord (User Settings → Activity Privacy), and the bot can't see what you play.
- **Take back** a vote, rating, refund or "I played it" answer at any time in the catalog.
- **Ask for a copy of your data, or for it to be deleted**, by [opening an issue on GitHub](https://github.com/madsbogaming/mads-bo-wacky/issues/new/choose). Say which Discord account it's about. Issues are public, so don't post anything private there: your Discord username is enough. Requests are handled within 30 days, and deleted data drops out of the backups within about a month after that.

If you're in the EU or EEA, the GDPR gives you these rights (to see, correct and delete your data, and to object), and you can also complain to your national data protection authority. In Denmark that is [Datatilsynet](https://www.datatilsynet.dk/).

## Children

The bot is for Discord users, and Discord requires its users to be at least 13 (or older where local law says so). The bot doesn't knowingly keep data about anyone younger.

## Changes

If this policy changes, the new version replaces this page, with a new date at the top. The full history of changes is in the repository.
