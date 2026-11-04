"""The catalog web page: served live by web.py (and inside Discord as an Activity), or exported as a file by /export."""

import html
import json
import os
import re
from datetime import datetime, timezone

from db import PLAYERS_NEEDED

FIELDS = ("id", "kind", "url", "title", "description", "image", "genres", "tags", "custom_tags", "clips",
          "coop", "multiplayer", "release_date", "release_ts", "price_cents", "price_text", "discount_pct", "price_full_text", "discount_ends", "demo_appid", "past_demo_appid", "refunds", "review_pct",
          "review_desc", "status", "added_by", "added_by_name", "added_at", "interest", "interested", "players", "ratings", "note",
          "played_by", "played_auto", "not_played", "played_before_rule", "recent")

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "catalog.html")


def page_data(rows: list[dict]) -> list[dict]:
    data = [{k: r.get(k) for k in FIELDS} for r in rows]
    for d in data:
        d["interested"] = d["interested"] or []
        d["players"] = d["players"] or []
        d["ratings"] = d["ratings"] or []
        d["refunds"] = d["refunds"] or []
        d["recent"] = d["recent"] or []
        for k in ("played_by", "played_auto", "not_played"):
            d[k] = d[k] or []
        d["added_by"] = str(d["added_by"]) if d["added_by"] else None  # Discord ids are too big for JS numbers
    return data


def _json(value) -> str:
    """JSON to put inside the page's <script>. < > & are written as \\u escapes (the same text to the script), so a
    name like "</script><img onerror=...>" can never end the script or become HTML."""
    text = json.dumps(value, ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


PLACEHOLDER = re.compile(r"__(GUILD|DATE|CONFIG|USERS|DATA)__")


def render_catalog(rows: list[dict], guild_name: str, *, users: dict | None = None, live=False,
                   client_id: int | None = None, can_sign_in=False, guild_icon: str | None = None,
                   public_url: str | None = None, seen_playing: list[int] | None = None,
                   steam_hours: list[int] | None = None, played_deadline: int | None = None,
                   locked=False) -> str:
    """`locked`: another server's catalog, sent without its games; the page loads them once a member signs in."""
    config = {"live": live, "locked": locked, "clientId": str(client_id or ""), "signIn": can_sign_in,
              "guildName": guild_name, "guildIcon": guild_icon,
              "publicUrl": public_url, "seenPlaying": [str(u) for u in seen_playing or []],
              "steamHours": [str(u) for u in steam_hours or []], "playersNeeded": PLAYERS_NEEDED,
              "playedDeadline": played_deadline}
    with open(TEMPLATE_PATH, encoding="utf-8") as f:  # read every time, so page tweaks don't need a restart
        template = f.read()
    values = {"GUILD": html.escape(guild_name), "DATE": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
              "CONFIG": _json(config), "USERS": _json(users or {}), "DATA": _json(page_data(rows))}
    # One pass over the template: what's filled in is never searched again, so a server or nickname called
    # "__DATA__" stays just that text (filling them in one after another pasted the game list into it)
    return PLACEHOLDER.sub(lambda m: values[m[1]], template)
