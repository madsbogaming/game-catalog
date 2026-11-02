"""Finding Steam / Battle.net / Instagram links in messages and fetching their metadata."""

import asyncio
import html
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser

import aiohttp

log = logging.getLogger(__name__)

STEAM_RE = re.compile(
    r"https?://(?:store\.steampowered\.com/(?:agecheck/)?app|steamcommunity\.com/app|s\.team/a)/(\d+)", re.I
)
# Also the sites that fix Instagram's previews in Discord: oginstagram.com, ddinstagram.com, kkinstagram.com, …
IG_RE = re.compile(
    r"https?://(?:[\w-]+\.)*(?:[a-z]{0,4}instagram\.com|instagramez\.com|instagr\.am)/(?:[\w.]+/)?(reels?|p|tv)/([\w-]+)", re.I
)
# Battle.net: a shop product or family page (eu.shop.battle.net/en-us/product/diablo-iv), or a Blizzard game's own site
# (worldofwarcraft.blizzard.com/en-us/) for games without a shop page, like World of Warcraft. Only a site's front
# page counts: its news and support pages aren't the game.
BNET_SHOP_RE = re.compile(r"https?://(?:[a-z]{2}\.)?shop\.battle\.net/(?:[a-z]{2}-[a-z]{2}/)?(product|family)/([a-z0-9-]+)", re.I)
BNET_SITE_RE = re.compile(r"https?://((?!www\.|shop\.|news\.|support\.|account\.)[a-z0-9-]+)\.blizzard\.com"
                          r"(?:/[a-z]{2}-[a-z]{2})?/?(?=[\s>)]|$)", re.I)
STEAM_LOBBY_RE = re.compile(r"steam://joinlobby/(\d+)/", re.I)  # Steam's "join my lobby" links
URL_RE = re.compile(r"<?https?://\S+>?")

# Steam store category ids
COOP_CATEGORIES = {9, 38, 39, 48}  # Co-op, Online Co-op, Split Screen Co-op, LAN Co-op
MULTI_CATEGORIES = COOP_CATEGORIES | {1, 24, 27, 36, 37, 44, 47, 49}  # Multi-player, PvP, Remote Play Together…

# Steam user tags that mean you can play it with others (used when the store categories are missing, e.g. unreleased games)
# How games can be played together. You play together online, so a game needs online multiplayer;
# "same screen" (shared/split screen, LAN, Remote Play Together streaming of local modes) doesn't count.
ONLINE_CATEGORIES = {"Online Co-op", "Online PvP", "MMO", "Cross-Platform Multiplayer"}
LOCAL_CATEGORIES = {"Shared/Split Screen", "Shared/Split Screen Co-op", "Shared/Split Screen PvP",
                    "LAN Co-op", "LAN PvP", "Remote Play Together"}
GENERIC_CATEGORIES = {"Multi-player", "Co-op", "PvP"}  # don't say whether it's online or local
ONLINE_TAG_WORDS = ("online co-op", "online pvp", "massively multiplayer", "mmo")
LOCAL_TAG_WORDS = ("local co-op", "local multiplayer", "split screen", "player local")
GENERIC_TAG_WORDS = ("multiplayer", "co-op", "pvp", "party game")

# Steam content descriptors 3 "Adult Only Sexual Content" and 4 "Frequent Nudity or Sexual Content"
# (1 "Some Nudity or Sexual Content" is allowed: mainstream games carry it too)
EXPLICIT_CONTENT_IDS = {3, 4}
EXPLICIT_TAGS = {"hentai", "sexual content", "nsfw"}
# Let through anyway: players tag these Sexual Content, but Steam itself only rates them "some nudity"
EXPLICIT_ALLOWED_APPIDS = {3393110}  # AION 2
# Games Steam no longer sells are turned away, except these (you can still play them if you own them)
DELISTED_ALLOWED_APPIDS = {1265860, 4241850}  # NASCAR Heat 5, Dreadmyst

# Steam app types other than "game", and how to describe them when turning one away
NON_GAME_TYPES = {"tool": "a tool (like a dedicated server, SDK or editor), not a game", "dlc": "DLC, not a game",
                  "demo": "a demo, not the full game", "music": "a soundtrack, not a game",
                  "video": "a video, not a game", "mod": "a mod, not a game", "advertising": "an ad, not a game",
                  "hardware": "hardware, not a game", "series": "a video series, not a game",
                  "episode": "a video episode, not a game"}
# Steam lists programs (Blender, Wallpaper Engine, …) as type "game" too, but files them under these genres
SOFTWARE_GENRES = {"Animation & Modeling", "Audio Production", "Design & Illustration", "Education",
                   "Game Development", "Photo Editing", "Software Training", "Utilities", "Video Production",
                   "Web Publishing", "Accounting"}
# Test builds and server software that Steam still lists as "game"
NON_GAME_NAME_RE = re.compile(
    r"\b(dedicated server|test server|experimental server|public test( server| realm)?|playtest|"
    r"beta test|staging (server|branch)|sdk|dev ?kit)\b", re.I)

# Skips the age gate on mature games' store pages.
STEAM_COOKIE = "birthtime=0; lastagecheckage=1-January-1990; wants_mature_content=1; mature_content=1"
TAG_RE = re.compile(r'class="app_tag"[^>]*>([^<]+)</a>')
# Overall (not "last 30 days") review tooltip, e.g. "95% of the 48,488 user reviews in your language are positive"
REVIEW_PCT_RE = re.compile(r'(\d+)% of the ([\d,.]+) user reviews (?!in the last)[^"]*are positive')
FREE_UNTIL_RE = re.compile(r"Free to keep when you get it before ([^.<]+)")
REVIEW_DESC_RE = re.compile(r'class="game_review_summary[^"]*" itemprop="description">([^<]+)<')
RELEASE_FORMATS = ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%b %Y", "%B %Y", "%Y")
# Instagram serves Open Graph tags to link-preview crawlers.
PREVIEW_UA = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"


# -- link parsing -------------------------------------------------------------

def find_steam_ids(text: str) -> list[int]:
    return list(dict.fromkeys(int(m) for m in STEAM_RE.findall(text or "")))


def find_battlenet(text: str) -> list[tuple[str, str]]:
    """-> [(key, url)] for Battle.net shop products and Blizzard game sites."""
    out = {}
    for page, slug in BNET_SHOP_RE.findall(text or ""):  # a product, or a "family" page for games sold as packs
        out.setdefault(f"bnet:{slug.lower()}", f"https://shop.battle.net/{page.lower()}/{slug.lower()}")
    for site in BNET_SITE_RE.findall(text or ""):
        out.setdefault(f"bnet:{site.lower()}", f"https://{site.lower()}.blizzard.com/")
    return list(out.items())


def find_reels(text: str) -> list[tuple[str, str]]:
    """-> [(shortcode, canonical url)]"""
    out = {}
    for kind, code in IG_RE.findall(text or ""):
        path = "p" if kind.lower() == "p" else "reel"
        out.setdefault(code, f"https://www.instagram.com/{path}/{code}/")
    return list(out.items())


def strip_links(text: str) -> str:
    return re.sub(r"\s+", " ", URL_RE.sub("", text or "")).strip()


def normalize_title(title: str) -> str:
    """For matching Discord's "Playing <game>" against catalog titles: no ™/®, case, accents or punctuation."""
    title = re.sub("[™®©]", "", title or "")  # ™ ® © (NFKD would turn ™ into "TM")
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", title).split())


def parse_release(text: str | None) -> int | None:
    for fmt in RELEASE_FORMATS:
        try:
            return int(datetime.strptime((text or "").strip(), fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return None


# -- Steam --------------------------------------------------------------------

async def _get(session: aiohttp.ClientSession, url: str, *, as_json=True, **kwargs):
    try:
        async with session.get(url, **kwargs) as r:
            if r.status != 200:
                log.warning("GET %s -> HTTP %s", url, r.status)
                return None
            return await r.json(content_type=None) if as_json else await r.text()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        log.warning("GET %s failed: %s", url, exc)
        return None


def price_fields(price: dict, free: bool) -> dict:
    """Our price fields from Steam's price_overview. A 100% discount (free to keep for a while) keeps the normal
    price; a smaller one is a sale, shown with the full price struck through."""
    discount = price.get("discount_percent") or 0
    giveaway, sale = discount == 100, 0 < discount < 100
    return {
        "price_cents": 0 if free else price.get("initial" if giveaway else "final"),
        "price_text": "Free" if free else price.get("initial_formatted" if giveaway else "final_formatted"),
        "discount_pct": discount if sale else 0,
        "price_full_text": price.get("initial_formatted") if sale else None,
    }


async def fetch_prices(session: aiohttp.ClientSession, appids: list[int], cc: str = "us") -> dict[int, dict]:
    """Current price fields of many games at once (Steam allows this for price info only). Free and
    delisted games have no price info and are left out."""
    found = {}
    for i in range(0, len(appids), 100):
        chunk = appids[i:i + 100]
        data = await _get(session, "https://store.steampowered.com/api/appdetails",
                          params={"appids": ",".join(map(str, chunk)), "filters": "price_overview", "cc": cc}) or {}
        for appid in chunk:
            app = data.get(str(appid)) or {}
            info = app.get("data")
            if app.get("success") and isinstance(info, dict) and info.get("price_overview"):
                found[appid] = price_fields(info["price_overview"], False)
        await asyncio.sleep(2)
    return found


def store_price_fields(option: dict) -> dict:
    """Our price fields (as price_fields(), plus when the sale ends) from a store API purchase option."""
    final, initial = option.get("final_price_in_cents"), option.get("original_price_in_cents")
    discount = 0 if option.get("price_cannot_be_displayed_as_discount") else option.get("discount_pct") or 0
    giveaway, sale = discount == 100, 0 < discount < 100
    ends = [d.get("discount_end_date") for d in option.get("active_discounts") or []]
    ends = [e for e in ends if e]
    return {
        "price_cents": int(initial if giveaway else final),
        "price_text": option.get("formatted_original_price" if giveaway else "formatted_final_price"),
        "discount_pct": discount if sale else 0,
        "price_full_text": option.get("formatted_original_price") if sale else None,
        "discount_ends": min(ends) if sale and ends else None,
    }


async def fetch_store_prices(session: aiohttp.ClientSession, appids: list[int], cc: str = "us") -> dict[int, dict]:
    """Current price fields of many games at once, with when each sale ends, from the store API (no key
    needed). Unlike appdetails it isn't cached for long, so a sale is gone as soon as it ends. Free games
    and games the API doesn't answer for are left out."""
    found = {}
    for i in range(0, len(appids), 50):
        chunk = appids[i:i + 50]
        query = {"ids": [{"appid": a} for a in chunk], "context": {"language": "english", "country_code": cc.upper()},
                 "data_request": {"include_all_purchase_options": True}}
        data = await _get(session, "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
                          params={"input_json": json.dumps(query)}) or {}
        for item in (data.get("response") or {}).get("store_items") or []:
            # The game's own package, not a cheaper bundle with other games (the "best" option can be one)
            best = item.get("best_purchase_option") or {}
            option = best if best.get("packageid") else next(
                (o for o in item.get("purchase_options") or [] if o.get("packageid")), {})
            if item.get("appid") and not item.get("is_free") and option.get("final_price_in_cents") is not None:
                found[item["appid"]] = store_price_fields(option)
        await asyncio.sleep(1)
    return found


async def fetch_playtime(session: aiohttp.ClientSession, key: str, steam_id: str) -> dict[int, tuple[int, int | None]] | None:
    """Hours someone has in each of their Steam games: appid -> (minutes played, last played as unix time).
    None when Steam won't say (their game details are private, or Steam is down)."""
    data = await _get(session, "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/",
                      params={"key": key, "steamid": steam_id, "include_played_free_games": 1, "format": "json"})
    games = ((data or {}).get("response") or {}).get("games")
    if games is None:
        return None
    return {g["appid"]: (g.get("playtime_forever") or 0, g.get("rtime_last_played") or None) for g in games}


async def fetch_now_playing(session: aiohttp.ClientSession, key: str, steam_ids: list[str]) -> dict[str, int]:
    """Which game each of these Steam accounts is in right now: steam id -> app id (only those in a game).
    Steam only says for public profiles that aren't set to Invisible."""
    playing = {}
    for i in range(0, len(steam_ids), 100):
        data = await _get(session, "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
                          params={"key": key, "steamids": ",".join(steam_ids[i:i + 100]), "format": "json"})
        for p in ((data or {}).get("response") or {}).get("players") or []:
            if str(p.get("gameid", "")).isdigit():
                playing[p["steamid"]] = int(p["gameid"])
    return playing


async def fetch_steam(session: aiohttp.ClientSession, appid: int, cc: str = "us") -> dict | None:
    data = await _get(session, "https://store.steampowered.com/api/appdetails",
                      params={"appids": appid, "l": "english", "cc": cc})
    app = (data or {}).get(str(appid)) or {}
    if not app.get("success"):
        return None
    d = app["data"]
    cats = d.get("categories") or []
    cat_ids = {c.get("id") for c in cats}
    price = d.get("price_overview") or {}
    giveaway = price.get("discount_percent") == 100  # temporarily free to keep: remember the normal price
    free = bool(d.get("is_free")) and not giveaway  # Steam also flags giveaways as free while they last
    release = (d.get("release_date") or {}).get("date") or None
    meta = {
        "title": d.get("name") or f"Steam app {appid}",
        # Keys starting with "_" are only used for checking what's allowed in, not stored
        "_type": d.get("type"),
        "_content_ids": (d.get("content_descriptors") or {}).get("ids") or [],
        "_giveaway": giveaway,
        "_recommendations": (d.get("recommendations") or {}).get("total"),  # shown in the tag message's Steam card
        "_metacritic": (d.get("metacritic") or {}).get("score"),
        "description": html.unescape(d.get("short_description") or "") or None,
        "image": d.get("header_image"),
        "developer": ", ".join(d.get("developers") or []) or None,
        "genres": [g["description"] for g in d.get("genres") or []],
        "categories": [c["description"] for c in cats],
        "coop": bool(cat_ids & COOP_CATEGORIES),
        "multiplayer": bool(cat_ids & MULTI_CATEGORIES),
        "release_date": release,
        "release_ts": parse_release(release),
        **price_fields(price, free),
        "demo_appid": next((dm.get("appid") for dm in d.get("demos") or []), None),
    }

    # User tags and the review summary are only on the store page itself.
    page = await _get(session, f"https://store.steampowered.com/app/{appid}/", as_json=False,
                      params={"l": "english"}, headers={"Cookie": STEAM_COOKIE}) or ""
    tags = [html.unescape(t).strip() for t in TAG_RE.findall(page)]
    meta["tags"] = [t for t in tags if t and t != "+"][:10]
    m = REVIEW_PCT_RE.search(page)
    if m:
        meta["review_pct"] = int(m.group(1))
        meta["review_count"] = int(m.group(2).replace(",", "").replace(".", ""))
        desc = REVIEW_DESC_RE.search(page)
        meta["review_desc"] = desc.group(1).strip() if desc else None
    meta["_delisted"] = "is no longer available on the Steam store" in page
    m = FREE_UNTIL_RE.search(page)
    meta["_free_until"] = html.unescape(m.group(1)).strip() if m else None
    return meta


async def find_free_games(session: aiohttp.ClientSession, cc: str = "us") -> list[int]:
    """App ids of games that are 100% off on Steam right now (free to keep), not permanently free ones."""
    data = await _get(session, "https://store.steampowered.com/search/results/",
                      params={"query": "", "start": 0, "count": 100, "specials": 1, "maxprice": "free",
                              "infinite": 1, "cc": cc, "l": "english"})
    found = []
    for item in re.split(r'(?=<a href="https://store\.steampowered\.com/)', (data or {}).get("results_html", ""))[1:]:
        appid = re.search(r'data-ds-appid="(\d+)"', item)  # bundles list several ids; skip those
        if appid and 'data-discount="100"' in item:
            found.append(int(appid.group(1)))
    return found


def not_a_game(meta: dict) -> str | None:
    """Why this Steam app isn't a real game (a dedicated server, SDK, DLC, test build, …), or None if it is one."""
    app_type = (meta.get("_type") or "game").lower()
    if app_type != "game":
        return NON_GAME_TYPES.get(app_type, f"a {app_type}, not a game")
    if SOFTWARE_GENRES & set(meta.get("genres") or []):
        return "software, not a game"
    m = NON_GAME_NAME_RE.search(meta.get("title") or "")
    if not m:
        return None
    what = m.group(0).lower()
    return f"{'an' if what[0] in 'aeiou' else 'a'} {what}, not the game itself"


def explicit_content(meta: dict) -> bool:
    """Steam marks it Adult Only / Frequent nudity or sexual content, or players tag it Hentai; a player tag of
    Sexual Content or NSFW only counts when Steam's own rating mentions nudity or sexual content too (players tag
    plenty of games that way that Steam rates as just violent or mature, like DEVOUR)."""
    ids = set(meta.get("_content_ids") or [])
    tags = {t.lower() for t in meta.get("tags") or []}
    return bool(ids & EXPLICIT_CONTENT_IDS or "hentai" in tags or (tags & EXPLICIT_TAGS and 1 in ids))


def online_play_problem(meta: dict) -> str | None:
    """Why this game can't be played together online ("is singleplayer only", …), or None if it can."""
    cats = set(meta.get("categories") or [])
    local = "only has local (shared/split screen) multiplayer"
    if cats & ONLINE_CATEGORIES or (cats & GENERIC_CATEGORIES and not cats & LOCAL_CATEGORIES):
        return None
    if cats & LOCAL_CATEGORIES and not cats & GENERIC_CATEGORIES:
        return local
    # Generic plus same-screen categories is ambiguous: older games (e.g. Call of Duty: Black Ops III) list online
    # play only as "Multi-player". With no multiplayer categories at all (common before release) there's nothing
    # to go by either. Either way the Steam user tags decide.
    words = [t.lower() for t in (meta.get("tags") or []) + (meta.get("genres") or [])]
    has = lambda needles: any(n in w for w in words for n in needles)
    if has(ONLINE_TAG_WORDS) or (has(GENERIC_TAG_WORDS) and not has(LOCAL_TAG_WORDS)):
        return None
    return local if cats & LOCAL_CATEGORIES or has(LOCAL_TAG_WORDS) else "is singleplayer only"


async def search_steam(session: aiohttp.ClientSession, term: str, cc: str = "us") -> list[tuple[int, str]]:
    data = await _get(session, "https://store.steampowered.com/api/storesearch/",
                      params={"term": term, "l": "english", "cc": cc})
    return [(item["id"], item["name"]) for item in (data or {}).get("items", [])]


# Where reel captions name their game: "Game: Nested Lands", "… - Cursed Companions #gaming",
# "Beta Decay is one of …". "This game is like REPO" names another game, so it matches none of these.
_NAME = r"[A-Z0-9][\w'’:!.&+-]*(?:\s+(?:of|the|and|in|on|a|to|[A-Z0-9][\w'’:!.&+-]*)){0,5}"
CAPTION_PATTERNS = [
    re.compile(r"\bgame(?:\s+name)?\s*[:\-–—]\s*([^\n#@|(\[]+)", re.I),
    re.compile(r"\s[-–—|]\s+(" + _NAME + r")\s*(?=https?://|#|@|\n|$)"),
    re.compile(r"^\W*(" + _NAME + r")\s+(?:is|are|lets you|has)\b"),
    re.compile(r"[\"“”]([^\"“”\n]{2,60})[\"“”]"),
    re.compile(r"^\W*([^\n#@]{2,40}?)\s*(?=#)"),  # just the title, then hashtags: "RV THERE YET? #gaming"
]


def caption_game_names(caption: str) -> list[str]:
    """Names a reel's caption gives for its game, most likely first."""
    names = []
    for pattern in CAPTION_PATTERNS:
        for m in pattern.finditer(caption or ""):
            name = m.group(1).strip(" .,:;!-–—")
            if 2 <= len(name) <= 60 and name not in names:
                names.append(name)
    return names


async def game_from_caption(session: aiohttp.ClientSession, caption: str, cc: str = "us") -> int | None:
    """The Steam app a reel's caption names, if a name in it is exactly a Steam game's title (ignoring case,
    ™ and punctuation). Strict on purpose: a wrong guess is worse than asking."""
    for name in caption_game_names(caption):
        for appid, title in (await search_steam(session, name, cc))[:5]:
            if normalize_title(title) == normalize_title(name):
                return appid
    return None


# -- Battle.net ---------------------------------------------------------------

def battlenet_title(title: str) -> str:
    """"Diablo® IV - Diablo® IV | Battle.net" -> "Diablo IV", "Call of Duty: Black Ops 7 - Call of Duty" -> its first half."""
    title = re.sub(r"\s*[|–-]\s*(?:Battle\.net|Blizzard Entertainment)\s*$", "", html.unescape(title or ""), flags=re.I)
    title = re.sub("[™®©]", "", title).strip()
    half = title.split(" - ")
    if len(half) == 2 and half[0].strip().lower().startswith(half[1].strip().lower()):  # "Game - Game" / "Game - Series"
        title = half[0].strip()
    return title


# Blizzard's own games aren't on Steam, and their shop pages say little (no game modes, no reviews), so the basics
# are kept here, by normalize_title(). The shop page's price and release date, and a Steam twin's reviews and tags,
# go on top of these.
BLIZZARD_GAMES = {
    "world of warcraft": {"genres": ["Massively Multiplayer", "RPG"], "release_date": "23 Nov, 2004",
                          "tags": ["MMORPG", "Fantasy", "Open World", "PvE", "PvP", "Online Co-Op"],
                          "coop": True, "multiplayer": True, "price_text": "Subscription"},
    "world of warcraft forever": {"genres": ["Massively Multiplayer", "RPG"], "release_date": "4 Nov, 2026",
                                  "tags": ["MMORPG", "Fantasy", "Open World", "PvE", "PvP", "Online Co-Op"],
                                  "coop": True, "multiplayer": True, "price_text": "Subscription"},
    "heroes of the storm": {"genres": ["Strategy", "Free To Play"], "release_date": "2 Jun, 2015",
                            "tags": ["MOBA", "Free to Play", "Team-Based", "PvP", "Online Co-Op"],
                            "coop": True, "multiplayer": True},
    "hearthstone": {"genres": ["Strategy", "Free To Play"], "release_date": "11 Mar, 2014",
                    "tags": ["Card Game", "Deckbuilding", "Free to Play", "PvP", "Online PvP"], "multiplayer": True},
    "starcraft ii": {"genres": ["Strategy", "Free To Play"], "release_date": "27 Jul, 2010",
                     "tags": ["RTS", "Sci-fi", "Free to Play", "PvP", "Online Co-Op"], "coop": True, "multiplayer": True},
    "diablo iv": {"genres": ["Action", "RPG"], "release_date": "6 Jun, 2023",
                  "tags": ["Action RPG", "Hack and Slash", "Dark Fantasy", "Loot", "Online Co-Op"],
                  "coop": True, "multiplayer": True},
    "world of warcraft classic": {"genres": ["Massively Multiplayer", "RPG"], "release_date": "26 Aug, 2019",
                                  "tags": ["MMORPG", "Fantasy", "Classic", "Open World", "PvE", "PvP", "Online Co-Op"],
                                  "coop": True, "multiplayer": True, "price_text": "Subscription"},
}
BNET_GENRE_RE = re.compile(r'product-details__genre">\s*<h3[^>]*>Genre</h3>\s*<p[^>]*>([^<]+)</p>')
BNET_LAUNCH_RE = re.compile(r"launches on ([A-Z][a-z]+ \d{1,2}, \d{4})")
LD_JSON_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


def known_battlenet_game(title: str) -> dict:
    """The built-in basics for a Blizzard game (empty for others), with release_ts worked out."""
    known = dict(BLIZZARD_GAMES.get(normalize_title(title), {}))
    if known.get("release_date"):
        known["release_ts"] = parse_release(known["release_date"])
    return known


def battlenet_price(page: str) -> dict:
    """The cheapest edition on a shop page, from its schema.org offers: {"price_text", "price_cents"} or {}."""
    prices = []
    for block in LD_JSON_RE.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "Product":
                for offer in item.get("offers") or []:
                    try:
                        prices.append((float(offer.get("price")), offer.get("priceCurrency") or ""))
                    except (TypeError, ValueError):
                        pass
    if not prices:
        return {}
    price, currency = min(prices)
    if price == 0:
        return {"price_text": "Free", "price_cents": 0}
    amount = f"{price:.2f}".replace(".", ",")
    return {"price_text": f"{amount}€" if currency == "EUR" else f"{amount} {currency}".strip(),
            "price_cents": round(price * 100)}


async def steam_twin(session: aiohttp.ClientSession, title: str, cc: str = "us") -> dict:
    """Reviews, tags and game modes of the same game on Steam (exact title match), for Battle.net games sold on both."""
    for appid, name in await search_steam(session, title, cc):
        if normalize_title(name) == normalize_title(title):
            meta = await fetch_steam(session, appid, cc) or {}
            keep = ("review_pct", "review_count", "review_desc", "tags", "genres", "categories", "coop", "multiplayer",
                    "developer", "release_date", "release_ts")
            return {k: meta[k] for k in keep if meta.get(k) not in (None, [], "")}
    return {}


async def fetch_battlenet(session: aiohttp.ClientSession, url: str, cc: str = "us") -> dict | None:
    """A Battle.net game's info. Blizzard has no public store API, so it's pieced together: name, description and
    cover from the page's link preview; genre, cheapest edition and launch date from a shop page; the built-in basics
    for Blizzard's own games; and reviews and tags from Steam when the game is sold there too.
    None for pages that aren't a game."""
    page = await _get(session, url, as_json=False, headers={"User-Agent": BROWSER_UA})
    if not page:
        return None
    parser = _MetaParser()
    parser.feed(page)
    m = parser.meta
    title = battlenet_title(m.get("og:title") or "")
    if not title or title.lower() in ("home", "battle.net", "shop"):  # unknown products land on the shop's front page
        return None
    meta = {"genres": [], "tags": [], "coop": False, "multiplayer": True}  # Battle.net games are online games
    meta |= known_battlenet_game(title)
    genre = BNET_GENRE_RE.search(page)
    if genre and not meta["genres"]:
        meta["genres"] = [html.unescape(genre.group(1)).strip()]
    if not meta.get("price_text"):  # "Subscription" beats the shop's packs (WoW's packs still need a subscription)
        meta |= battlenet_price(page)
    launch = BNET_LAUNCH_RE.search(page)
    if launch and not meta.get("release_date"):  # family pages mention other games' launches too
        when = datetime.strptime(launch.group(1), "%B %d, %Y")
        meta["release_date"] = f"{when.day} {when:%b}, {when.year}"
        meta["release_ts"] = parse_release(meta["release_date"])
    twin = await steam_twin(session, title, cc)
    meta |= {k: v for k, v in twin.items() if k in ("review_pct", "review_count", "review_desc", "tags", "categories",
                                                     "coop", "multiplayer") or not meta.get(k)}
    meta |= {
        "title": title,
        "description": html.unescape(m.get("og:description") or "").strip() or None,
        "image": m.get("og:image") if (m.get("og:image") or "").startswith("https://") else None,
        "developer": twin.get("developer") or ("Blizzard Entertainment" if normalize_title(title) in BLIZZARD_GAMES
                                               else None),
    }
    return meta


# -- Instagram ----------------------------------------------------------------

class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        a = dict(attrs)
        key = a.get("property") or a.get("name")
        if key and a.get("content") and key not in self.meta:
            self.meta[key] = a["content"]


def instagram_meta(title: str | None, description: str | None, image: str | None) -> dict:
    """Turn Open Graph / Discord-embed fields of a reel into entry fields."""
    author = caption = None
    m = re.match(r'^(.*?) on Instagram: ["“](.*)["”]\s*$', title or "", re.S)
    if m:
        author, caption = m.group(1).strip(), m.group(2)
    if not caption and description:
        m = re.search(r'on [^:]+:\s*["“](.*)["”]\s*\.?\s*$', description, re.S)
        caption = m.group(1) if m else description
    out = {}
    if caption and caption.strip():
        caption = html.unescape(caption).strip()
        out["description"] = caption[:1000]
        first_line = caption.splitlines()[0].strip()
        out["title"] = first_line[:80] + ("…" if len(first_line) > 80 else "")
    elif author:
        out["title"] = f"Reel by {author}"
    if image and len(image) < 2000:
        out["image"] = image
    return out


async def fetch_instagram(session: aiohttp.ClientSession, url: str) -> dict:
    page = await _get(session, url, as_json=False, headers={"User-Agent": PREVIEW_UA})
    if not page:
        return {}
    parser = _MetaParser()
    parser.feed(page)
    m = parser.meta
    return instagram_meta(m.get("og:title"), m.get("og:description") or m.get("description"), m.get("og:image"))
