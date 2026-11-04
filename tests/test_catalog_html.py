"""The page is filled in safely: names are only ever text. Found by HAZELNUT: a nickname or server called "__DATA__"
had the whole game list pasted into it, which broke the page (or, with a crafted name, could run a script)."""

import json
import re
import unittest

from catalog_html import render_catalog

GAME = {"id": 1, "title": "Game", "added_by": 5, "added_by_name": "<img src=x onerror=alert(1)>",
        "note": "</script><script>alert(2)</script>"}


def script_value(page: str, name: str):
    """What `let NAME = ...;` / `const NAME = ...;` holds, read as JSON (the page's own script would fail on bad JSON)."""
    line = next(l for l in page.splitlines() if re.match(rf"(let|const) {name} = ", l))
    return json.loads(line.split(" = ", 1)[1].removesuffix(";"))


class FilledInSafely(unittest.TestCase):
    def test_a_nickname_called_like_a_placeholder_stays_text(self):
        for nick in ("__DATA__", "__USERS__", "__CONFIG__", "__GUILD__", "__DATE__"):
            page = render_catalog([GAME], "Server", users={"5": {"name": nick}})
            self.assertEqual(script_value(page, "USERS")["5"]["name"], nick)
            self.assertEqual(script_value(page, "DATA")[0]["title"], "Game")

    def test_a_server_called_like_a_placeholder_stays_text(self):
        page = render_catalog([GAME], "__DATA__", users={"5": {"name": "Bo"}})
        self.assertEqual(script_value(page, "CONFIG")["guildName"], "__DATA__")
        self.assertNotIn("onerror=alert", page.replace("\\u003c", "").split("<script", 1)[0])
        self.assertIn("<span>__DATA__</span>", page)

    def test_names_can_never_become_html_or_end_the_script(self):
        page = render_catalog([GAME], '<b>"x\\</title>', users={"5": {"name": "</script><img src=x onerror=alert(3)>"}})
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn('<b>"x', page)
        self.assertEqual(script_value(page, "USERS")["5"]["name"], "</script><img src=x onerror=alert(3)>")
        self.assertEqual(script_value(page, "DATA")[0]["note"], GAME["note"])
        self.assertEqual(script_value(page, "CONFIG")["guildName"], '<b>"x\\</title>')


if __name__ == "__main__":
    unittest.main()
