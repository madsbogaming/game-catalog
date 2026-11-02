"""Draw the achievement badge pictures (static/badges/*.png) that the bot puts in its achievement messages,
plus the badge strip for the launch post. Run it again after changing an achievement's icon or tiers:

    .venv\\Scripts\\python.exe tools\\make_badges.py

The badges use the catalog page's own icon set, and Microsoft Edge (always on Windows) draws them."""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import achievements as A  # noqa: E402

EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)), None)
OUT = os.path.join(ROOT, "static", "badges")


def icons() -> dict[str, str]:
    with open(os.path.join(ROOT, "templates", "catalog.html"), encoding="utf-8") as f:
        line = next(l for l in f if l.startswith("const ICONS = {"))
    return json.loads(line[len("const ICONS = "):line.rindex("}") + 1])


def badge_svg(icon: str, color: str, *, title=False, size=128) -> str:
    """A round badge: dark disc, ring and icon in the tier's colour; the title gets a red disc and a gold crown."""
    ic = ICONS[icon]
    disc = "#3b1f22" if title else "#2b2d31"
    crown = (f'<g transform="translate(44 0) scale(1.67)" fill="#f0b232" stroke="#f0b232" stroke-width="1.6" '
             f'stroke-linejoin="round">{ICONS["crown"]}</g>') if title else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 128 128">'
            f'<circle cx="64" cy="70" r="51" fill="{disc}" stroke="{color}" stroke-width="7"/>'
            f'<g transform="translate(36 42) scale(2.33)" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{ic}</g>{crown}</svg>')


def shoot(html: str, path: str, width: int, height: int):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><style>html,body{{margin:0;background:transparent}}</style>{html}')
        src = f.name
    try:
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                        "--default-background-color=00000000", f"--window-size={width},{height}",
                        f"--screenshot={path}", "file:///" + src.replace("\\", "/")],
                       check=True, capture_output=True, timeout=60)
    finally:
        os.remove(src)
    print("made", os.path.relpath(path, ROOT))


def secret_svg(size=128) -> str:
    """A secret achievement, as shown to people who haven't unlocked it: a grey "?"."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 128 128">'
            f'<circle cx="64" cy="70" r="51" fill="#2b2d31" stroke="{A.SECRET_COLOR}" stroke-width="7"/>'
            f'<text x="64" y="92" text-anchor="middle" font-size="64" font-family="Segoe UI, sans-serif" '
            f'font-weight="700" fill="{A.SECRET_COLOR}">?</text></svg>')


def strip_html() -> tuple[str, int, int]:
    """The launch post's picture: one badge per achievement (top tier), the title first, secrets as one "?"."""
    items = [(A.TITLE.name, badge_svg(A.TITLE.icon, A.TITLE_COLOR, title=True, size=88))]
    items += [(a.name, badge_svg(a.icon, a.color(len(a.thresholds)), size=88)) for a in A.ACHIEVEMENTS if not a.secret]
    items.append(("Secret", secret_svg(88)))
    width, height = 40 + 116 * len(items), 170
    cells = "".join(f'<div style="width:116px;display:flex;flex-direction:column;align-items:center;gap:8px">{svg}'
                    f'<span>{name}</span></div>' for name, svg in items)
    return (f'<div style="width:{width}px;height:{height}px;box-sizing:border-box;padding:24px 20px;border-radius:16px;'
            f'background:#1e1f22;display:flex;font:600 14px Segoe UI, sans-serif;color:#b5bac1">{cells}</div>',
            width, height)


if __name__ == "__main__":
    if not EDGE:
        sys.exit("Microsoft Edge wasn't found, so the badges can't be drawn")
    ICONS = icons()
    os.makedirs(OUT, exist_ok=True)
    shoot(badge_svg(A.TITLE.icon, A.TITLE_COLOR, title=True), os.path.join(OUT, "biggest_refunder.png"), 128, 128)
    for a in A.ACHIEVEMENTS:
        for tier in range(1, len(a.thresholds) + 1):
            shoot(badge_svg(a.icon, a.color(tier)), os.path.join(OUT, a.badge(tier)), 128, 128)
    shoot(secret_svg(), os.path.join(OUT, "secret.png"), 128, 128)
    html, width, height = strip_html()
    shoot(html, os.path.join(OUT, "launch.png"), width, height)
