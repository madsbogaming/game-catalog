# Working on the bot together

Thanks for helping out. This page covers the three things to know before you change anything: run your own test bot, work on a branch, and open a pull request.

## The one rule: never run the real bot

The bot that lives in our Discord server runs on Mads' PC with the real token. If you start `bot.py` with that token too, two bots answer every message and post everything twice. So:

- **Never ask for or copy the real `.env`.** You make your own.
- **Test with your own bot in your own test server.** It costs nothing and takes about 10 minutes.

## Set up your test bot

1. **Make a test server** in Discord (the + at the bottom of the server list) with a `#games` channel.
2. **Create a test bot** in the [Discord Developer Portal](https://discord.com/developers/applications) and add it to your test server. Follow [step 1 of the guide](docs/GUIDE.md#1-create-the-bot-on-discord).
3. **Get the code:**
   ```bash
   git clone https://github.com/madsbogaming/game-catalog.git
   cd game-catalog
   py -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   copy .env.example .env
   ```
   On macOS or Linux use `python3 -m venv .venv`, `.venv/bin/pip` and `cp` instead.
4. **Fill in your `.env`** with your test bot's values:
   - `DISCORD_TOKEN`: your test bot's token
   - `GUILD_ID`: your test server's ID
   - `WATCH_CHANNEL_IDS`: your test `#games` channel
   - `ADMIN_IDS`: your own Discord user ID, so you get every command
   - `PUBLIC_TUNNEL=false`, so the catalog stays on your PC

   Everything else can stay empty. Get your own free `STEAM_API_KEY` if you work on Steam playtime.
5. **Start it** with `.venv\Scripts\python bot.py` and post a few Steam links in your test `#games` channel. The bot makes its own empty `catalog.db`, so you start with an empty catalog.

The catalog page works in your browser at <http://127.0.0.1:8765>. You only need the full [Activity setup](docs/GUIDE.md#3-turn-on-the-catalog-activity) if you change how `/catalog` behaves inside Discord.

## Make a change

1. **Start from the latest code:**
   ```bash
   git switch main
   git pull
   ```
2. **Make a branch** named after what you're doing:
   ```bash
   git switch -c better-random-picks
   ```
3. **Change the code and try it** with your test bot, and run the unit tests with `.venv\Scripts\python -m unittest`.
4. **Commit and push your branch:**
   ```bash
   git add -A
   git commit -m "Make /random favor games nobody has played yet"
   git push -u origin better-random-picks
   ```
5. **Open a pull request.** GitHub shows a "Compare & pull request" button on the repo page after your push. Say what you changed and how you tested it.

A check runs on every pull request and catches typos, missing imports and unused names, and runs the tests. If it shows a red X, click it to see what's wrong, fix it, and push again. The pull request updates on its own.

`main` is protected: it only takes changes through a pull request whose check has passed, so you can't push to it directly, force-push it or delete it. That's on purpose, since the live bot runs whatever is on `main`.

When the pull request is merged, the change is on `main`. The real bot picks it up the next time Mads pulls and restarts it.

## Tips

- **Keep pull requests small.** One feature or fix each. They're quicker to review and rarely clash with other work.
- **Say what you're working on** in Discord, especially if it touches `bot.py`, so two people don't change the same part at once.
- **Pull `main` often** while your branch is open. If Git reports a merge conflict, it means someone changed the same lines. Open the file, keep the right version between the `<<<<<<<` and `>>>>>>>` markers, delete the markers, and commit.
- **Never commit `.env`, `catalog.db` or `bot.log`.** `.gitignore` already skips them, so don't force them in.

## Your contributions and the license

The bot is source-available under [LICENSE](LICENSE). By opening a pull request you agree to section 4: you keep the rights to your code, and Mads may use it, change it and relicense it, for example if the bot goes open source later. Only send code you wrote yourself.

## Found a bug, have an idea, or need help?

[Open an issue](https://github.com/madsbogaming/game-catalog/issues/new/choose) and pick **Bug** or **Idea**. You don't need to write any code to help. Questions go in [Discussions](https://github.com/madsbogaming/game-catalog/discussions/categories/q-a), and security problems are reported privately (see [SECURITY.md](SECURITY.md)).
