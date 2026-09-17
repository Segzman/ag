#!/usr/bin/env python3
"""Dev-only UI preview generator: actual ag renderer frames -> SVG/PNG.

Captures REAL TUI output (FakeScr + 256-color stubs, no curses terminal)
and repaints attrs to exact theme RGB via PAIR_ROLES/theme_rgb. For each
scene the intended frame is shot BEFORE the appended quit keys (last frame
containing the scene marker), so session shots are sessions, not HOME.

Seeds a richer multi-message conversation (user/agent/tool/toolout) and
exercises the narrow 40x16 compact screen with a mid-list selection.

Run: python3 scripts/make_ui_previews.py  (needs rsvg-convert for PNGs)
Writes: docs/ui-previews/*.svg, *.png, index.json
Stdlib only.
"""
import curses
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent
AG = REPO / "ag"
OUT = REPO / "docs" / "ui-previews"

# Previews pin the default (color, Unicode) surface.
os.environ.pop("NO_COLOR", None)
os.environ.pop("AG_ASCII", None)

# stub curses init; keep KEY_*/A_* real; force 256-color pairs
curses.curs_set = lambda *a, **k: None
curses.has_colors = lambda: False
curses.start_color = lambda *a, **k: None
curses.use_default_colors = lambda *a, **k: None
curses.init_pair = lambda *a, **k: None
curses.COLORS = 256
curses.color_pair = lambda i: (i + 1) << 24
if not hasattr(curses, "A_DIM"):
    curses.A_DIM = 0

spec = importlib.util.spec_from_loader("agmod", loader=None)
ag = importlib.util.module_from_spec(spec)
ag.__file__ = str(AG)
exec(AG.read_text(), ag.__dict__)

THEME = "abyss"
CW, CH, FS = 8.4, 17.0, 13.5  # cell px, row px, font size


class CapScr:
    """Fake screen capturing chars AND attrs per refresh."""

    def __init__(self, w, h, keys):
        self.w, self.h = w, h
        self.keys = list(keys)
        self.cells = {}
        self.attrs = {}
        self.frames = []

    def getmaxyx(self):
        return (self.h, self.w)

    def erase(self):
        self.cells = {}
        self.attrs = {}

    def refresh(self):
        self.frames.append((dict(self.cells), dict(self.attrs)))

    def addstr(self, y, x, s, attr=0):
        for i, ch in enumerate(s):
            if 0 <= y < self.h and 0 <= x + i < self.w:
                self.cells[(y, x + i)] = ch
                self.attrs[(y, x + i)] = int(attr)

    def move(self, y, x):
        pass

    def timeout(self, ms):
        pass

    def nodelay(self, b):
        self._nd = bool(b)

    def getch(self):
        if getattr(self, "_nd", False):
            return -1
        if self.keys:
            time.sleep(0.05)
            return self.keys.pop(0)
        time.sleep(0.01)
        return -1


def seed_td():
    td = Path(tempfile.mkdtemp(prefix="ag-prev-"))
    rows = [
        {"name": "claude", "backend": "echo", "model": "default",
         "dir": ".", "role": "orchestrator"},
        {"name": "oc", "backend": "opencode", "model": "default",
         "dir": ".", "role": "sub"},
        {"name": "web", "backend": "echo", "model": "default",
         "dir": "site", "role": "sub"},
        {"name": "tl", "backend": "echo", "model": "default",
         "dir": ".", "role": "sub"},
    ]
    (td / "agents.json").write_text(json.dumps(rows))
    (td / "approvals.json").write_text("[]")
    convo = [
        ("user", "Find the auth bug in login.py"),
        ("agent", "Looking at the login flow now."),
        ("tool", "* Grep pattern 'def login' in src"),
        ("toolout", "src/auth/login.py:42: def login(user, pw)"),
        ("agent", "Found it — token expiry is never checked. Patching."),
        ("user", "Also add a regression test"),
        ("agent", "Added tests/test_login_expiry.py — green."),
    ]
    for role, text in convo:
        ag.append_chat(td, "claude", role, text)
    ag.append_chat(td, "oc", "user", "hello oc")
    ag.append_chat(td, "oc", "agent", "oc here — Muse Spark ready")
    # live-timeline shapes (reasoning/tool card/diff/error roles with
    # event_ids, exactly as the backend persists them) for the tl agent.
    tl_convo = [
        {"role": "user", "text": "Find the auth bug in login.py",
         "event_id": "p-u1"},
        {"role": "assistant", "text": "Looking at the login flow now.",
         "event_id": "p-a1"},
        {"role": "reasoning",
         "text": "Token expiry is checked on refresh but never on the login path itself.",
         "event_id": "p-r1"},
        {"role": "tool", "tool_name": "Grep", "tool_id": "t1",
         "path": "src/auth", "status": "completed",
         "text": "Grep pattern 'def login' in src", "event_id": "p-t1"},
        {"role": "toolout", "tool_id": "t1",
         "text": "src/auth/login.py:42: def login(user, pw)",
         "event_id": "p-o1"},
        {"role": "assistant",
         "text": "Found it — token expiry is never checked. Patching.",
         "event_id": "p-a2"},
        {"role": "diff", "path": "login.py",
         "text": "--- a/login.py\n+++ b/login.py\n@@ -40,7 +40,7 @@\n"
                 "     if token:\n-        login(user)\n+        if not expired(token):\n+            login(user)",
         "event_id": "p-d1"},
        {"role": "error",
         "text": "lint: line too long in login.py:44 (non-blocking)",
         "event_id": "p-e1"},
        {"role": "user", "text": "Also add a regression test",
         "event_id": "p-u2"},
        {"role": "assistant",
         "text": "Added tests/test_login_expiry.py — green.",
         "event_id": "p-a3"},
    ]
    lp = ag.chat_log_path(td, "tl")
    lp.parent.mkdir(parents=True, exist_ok=True)
    with open(lp, "a") as f:
        for row in tl_convo:
            f.write(json.dumps({"ts": ag.now_iso(), **row}) + "\n")
    return td


def run(td, w, h, keys, timeout=25):
    scr = CapScr(w, h, keys)
    t = threading.Thread(target=lambda: ag._tui_main(scr, td), daemon=True)
    t.start()
    t.join(timeout=timeout)
    return scr, t.is_alive()


def frame_text(cells):
    return "".join(cells.values())


def pick_frame(scr, marker):
    """Last frame containing the marker (intended shot before quit keys)."""
    hit = None
    for f in scr.frames:
        if marker in frame_text(f[0]):
            hit = f
    assert hit is not None, f"marker {marker!r} never rendered"
    return hit


def hex3(rgb):
    return "#%02x%02x%02x" % rgb


def render_svg(name, w, h, frame):
    cells, attrs = frame
    mark2role = {i + 1: r for i, r in ag.PAIR_ROLES.items()}
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'width="{w * CW}" height="{h * CH}" '
             f'font-family="Menlo, monospace" font-size="{FS}" '
             f'xml:space="preserve">',
             f'<rect width="{w * CW}" height="{h * CH}" fill="#14161c"/>',
             f"<title>{escape(name)}</title>"]
    bold_bit = getattr(curses, "A_BOLD", 0) or 0
    for y in range(h):
        # bg runs
        x = 0
        while x < w:
            a = attrs.get((y, x), 0)
            role = mark2role.get((a >> 24) & 0xFF)
            bg = None
            if role is not None:
                _fg, bg = ag.theme_rgb(THEME, role)
            if bg is None:
                x += 1
                continue
            x2 = x
            while x2 < w:
                a2 = attrs.get((y, x2), 0)
                r2 = mark2role.get((a2 >> 24) & 0xFF)
                b2 = ag.theme_rgb(THEME, r2)[1] if r2 else None
                if b2 != bg:
                    break
                x2 += 1
            lines.append(
                f'<rect x="{x * CW}" y="{y * CH}" width="{(x2 - x) * CW}" '
                f'height="{CH}" fill="{hex3(bg)}"/>')
            x = x2
        # fg text runs
        x = 0
        while x < w:
            ch = cells.get((y, x), " ")
            a = attrs.get((y, x), 0)
            role = mark2role.get((a >> 24) & 0xFF)
            if role is not None:
                fg, _bg = ag.theme_rgb(THEME, role)
                fill = hex3(fg)
            else:
                fill = "#d0d0d0"
            bold = bool(bold_bit and (a & bold_bit))
            x2 = x
            while x2 < w:
                ch2 = cells.get((y, x2), " ")
                a2 = attrs.get((y, x2), 0)
                r2 = mark2role.get((a2 >> 24) & 0xFF)
                f2 = hex3(ag.theme_rgb(THEME, r2)[0]) if r2 else "#d0d0d0"
                b2 = bool(bold_bit and (a2 & bold_bit))
                if f2 != fill or b2 != bold:
                    break
                x2 += 1
            seg = "".join(cells.get((y, i), " ") for i in range(x, x2))
            if seg.strip():
                bw = ' font-weight="bold"' if bold else ""
                lines.append(
                    f'<text x="{x * CW}" y="{y * CH + 13}" fill="{fill}"'
                    f"{bw} xml:space=\"preserve\">{escape(seg)}</text>")
            x = x2
    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def scene_keys(name):
    q = [27, ord("q"), ord("q")]
    if name.startswith("home-"):
        return [27, ord("q")]
    if name.startswith("session-") and "timeline" not in name:
        # Esc selects, Enter opens claude chat (rich convo), Esc back, quit
        return [27, 10, 27] + q
    if name.startswith("session-timeline-"):
        # DOWN x2 reaches the tl agent (flat order: claude, oc, tl, web),
        # Enter opens its reasoning/tool/diff timeline, Esc back, quit.
        # At 40x16 the transcript is bottom-anchored, so PgUp reveals the
        # upper Thinking/tool rows before the shot.
        keys = [27, curses.KEY_DOWN, curses.KEY_DOWN, 10]
        if name.endswith("-40x16"):
            keys += [curses.KEY_PPAGE] * 3
        if name.endswith("-60x24"):
            keys += [curses.KEY_PPAGE] * 2
        return keys + [27] + q
    if name == "slash-100x30":
        return [ord("/"), 27, 27, ord("q")]
    if name == "slash-filter-80x24":
        return [ord("/"), ord("c"), ord("o"), ord("n"), 27, 27,
                ord("q")]
    if name == "palette-100x30":
        return [16, 27, 27, ord("q")]
    if name == "palette-filter-100x30":
        return [16] + [ord(c) for c in "config"] + [27, 27, ord("q")]
    if name.startswith("config-"):
        return [27, ord("c"), 27, ord("q")]
    if name == "length-overview-100x30":
        return ([16] + [ord(c) for c in "Conversation length"] + [10, 27,
                                                                  ord("q")])
    if name == "length-overview-80x24":
        return ([16] + [ord(c) for c in "Conversation length"] + [10, 27,
                                                                  ord("q")])
    if name == "length-overview-40x16":
        # narrow: move to This agent so selection + pinned purpose show
        return ([16] + [ord(c) for c in "Conversation length"] + [10] +
                [curses.KEY_DOWN] * 2 + [27, ord("q")])
    if name == "length-scope-100x30":
        return ([16] + [ord(c) for c in "Conversation length"] + [10] +
                [curses.KEY_DOWN] + [10, 27, 27, ord("q")])
    if name == "length-onoff-80x24":
        return ([16] + [ord(c) for c in "Conversation length"] + [10] +
                [curses.KEY_DOWN] + [10, 10, 27, 27, 27, ord("q")])
    if name == "length-preset-100x30":
        return ([16] + [ord(c) for c in "Conversation length"] + [10] +
                [curses.KEY_DOWN] + [10, curses.KEY_DOWN] + [10, 27, 27,
                                                             27, ord("q")])
    if name == "length-preset-40x16":
        # narrow: highlight More often so whole exact effects must fit
        return ([16] + [ord(c) for c in "Conversation length"] + [10] +
                [curses.KEY_DOWN] + [10, curses.KEY_DOWN] + [10] +
                [curses.KEY_DOWN] + [27, 27, 27, ord("q")])
    if name == "length-advanced-80x24":
        return ([16] + [ord(c) for c in "Conversation length"] + [10] +
                [curses.KEY_DOWN] + [10, curses.KEY_DOWN,
                                     curses.KEY_DOWN] + [10, 27, 27, 27,
                                                         ord("q")])
    if name.startswith("context-"):
        return ([16] + [ord(c) for c in "Context for oc"] + [10, 27,
                                                             ord("q")])
    raise AssertionError(name)


def scene_marker(name):
    if "ascii" in name:
        return "* you"  # AG_ASCII swaps ●/◆/▍ to ASCII twins
    if name.startswith("home-"):
        return "Ask anything"
    if name.startswith("session-timeline-"):
        return "Thinking"
    if name.startswith("session-"):
        return "● you"
    if name.startswith("slash-"):
        return "commands 1/"
    if name == "palette-100x30":
        return "commands"
    if name == "palette-filter-100x30":
        return "Configure"
    if name.startswith("config-"):
        return "configure ·"
    if name.startswith("length-overview-"):
        return "Conversation length"
    if name == "length-scope-100x30":
        return "Applies to all subagents"
    if name == "length-onoff-80x24":
        return "On: older messages"
    if name.startswith("length-preset-"):
        return "More often"
    if name == "length-advanced-80x24":
        return "Messages before summary"
    if name.startswith("context-"):
        return "context · oc"
    raise AssertionError(name)


def scene_size(name):
    return tuple(int(v) for v in name.rsplit("-", 1)[1].split("x"))


def scene_env(name):
    """Env override pinned per scene (restored after the run)."""
    if "nocolor" in name:
        return {"NO_COLOR": "1"}
    if "ascii" in name:
        return {"AG_ASCII": "1"}
    return {}


def main():
    scenes = ["home-120x36", "home-100x30", "home-80x24", "home-60x24",
              "home-40x16",
              "session-120x36", "session-40x16",
              "session-timeline-120x36", "session-timeline-80x24",
              "session-timeline-60x24", "session-timeline-40x16",
              "session-timeline-nocolor-80x24", "session-ascii-80x24",
              "slash-100x30", "slash-filter-80x24",
              "palette-100x30", "palette-filter-100x30",
              "config-100x30", "config-40x16",
              "length-overview-100x30", "length-overview-80x24",
              "length-overview-40x16", "length-scope-100x30",
              "length-onoff-80x24", "length-preset-100x30",
              "length-preset-40x16", "length-advanced-80x24",
              "context-100x30"]
    if shutil.which("rsvg-convert") is None:
        print("needs rsvg-convert", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    index = {}
    for name in scenes:
        w, h = scene_size(name)
        td = seed_td()
        keys = scene_keys(name)
        marker = scene_marker(name)
        saved = dict(os.environ)
        os.environ.update(scene_env(name))
        try:
            scr, alive = run(td, w, h, keys)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        assert not alive, f"{name}: TUI did not quit"
        frame = pick_frame(scr, marker)
        (OUT / f"{name}.svg").write_text(render_svg(name, w, h, frame))
        r = subprocess.run(
            ["rsvg-convert", "-o", str(OUT / f"{name}.png"),
             str(OUT / f"{name}.svg")],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        index[name] = {"png": str(OUT / f"{name}.png"),
                       "frames": len(scr.frames),
                       "size": [w, h], "marker": marker}
        print(f"wrote {name} ({len(scr.frames)} frames)")
    (OUT / "index.json").write_text(json.dumps(index, indent=2))
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
