#!/usr/bin/env python3
"""UI regression tests for ag TUI. Stdlib only, fake screen, echo backend.

Covers: OpenCode-inspired home (centered wordmark, charcoal composer with
blue rule, projects grouped by workdir, two-line rows, quiet status),
searchable Ctrl+P palette (filter/actions/returns), home prompt submit,
draft retention across dialogs, selectable configurator, responsive
breakpoints, wrap bounds, user/agent labels, empty-state hint, keyboard
separation (arrows/keys never become input text, get_wch str stays distinct
from KEY_* ints), non-ASCII input preserved, narrow/wide render smoke, and
the ag-level OpenCode default model (picker/argv/handoff/switch/add).

Run: python3 tests/test_ag_ui.py
Env: AG_BIN overrides live ag path (default: repo ag).
"""
import curses
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

AG = Path(os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag")))
assert AG.exists(), f"missing ag: {AG}"

# Render tests pin the default (color, Unicode) surface: scrub any ambient
# NO_COLOR/AG_ASCII the harness exports; the dedicated unicode test sets
# and restores them itself.
os.environ.pop("NO_COLOR", None)
os.environ.pop("AG_ASCII", None)

# stub curses init calls; keep KEY_*/A_* constants real
curses.curs_set = lambda *a, **k: None
curses.has_colors = lambda: False
curses.start_color = lambda *a, **k: None
curses.use_default_colors = lambda *a, **k: None
curses.init_pair = lambda *a, **k: None
if not hasattr(curses, "A_DIM"):
    curses.A_DIM = 0

spec = importlib.util.spec_from_loader("agmod", loader=None)
ag = importlib.util.module_from_spec(spec)
ag.__file__ = str(AG)
exec(AG.read_text(), ag.__dict__)


class FakeScr:
    """Fake curses screen. getch sleeps >60ms per key so the legacy esc
    assembly window expires (like a real terminal); no get_wch so the
    getch fallback path is exercised."""

    def __init__(self, w, h, keys):
        self.w, self.h = w, h
        self.keys = list(keys)
        self.cells = {}
        self.frames = []

    def getmaxyx(self):
        return (self.h, self.w)

    def erase(self):
        self.cells = {}

    def refresh(self):
        self.frames.append(dict(self.cells))

    def addstr(self, y, x, s, attr=0):
        for i, ch in enumerate(s):
            if 0 <= y < self.h and 0 <= x + i < self.w:
                self.cells[(y, x + i)] = ch

    def move(self, y, x):
        pass

    def timeout(self, ms):
        pass

    def nodelay(self, b):
        # mirror real curses: non-blocking reads return -1 immediately
        # without consuming queued keys (lets esc-assembly expire cleanly)
        self._nd = bool(b)

    def getch(self):
        if getattr(self, "_nd", False):
            return -1
        if self.keys:
            time.sleep(0.09)
            return self.keys.pop(0)
        time.sleep(0.01)
        return -1

    def lines(self):
        return ["".join(self.cells.get((y, x), " ") for x in range(self.w)).rstrip()
                for y in range(self.h)]


def fresh_td(rows=None):
    td = Path(tempfile.mkdtemp(prefix="ag-ui-"))
    (td / "agents.json").write_text(json.dumps(rows or [
        {"name": "claude", "backend": "echo", "model": "default", "dir": ".",
         "role": "orchestrator"},
        {"name": "oc", "backend": "echo", "model": "default", "dir": ".",
         "role": "sub"}]))
    (td / "approvals.json").write_text("[]")
    return td


def run(td, w, h, keys, timeout=10):
    scr = FakeScr(w, h, keys)
    t = threading.Thread(target=lambda: ag._tui_main(scr, td), daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        # Deterministic stop: walk any dialog/view stack back home
        # (Esc is a no-op in home selection mode, q quits from there),
        # then quit. Bounded; happy-path timing untouched.
        scr.keys.extend([27] * 8 + [ord("q")])
        t.join(timeout=5)
    return scr, t.is_alive()


def test_breakpoints():
    assert ag.tui_columns(130) == "wide"
    assert ag.tui_columns(110) == "wide"
    assert ag.tui_columns(100) == "medium"
    assert ag.tui_columns(70) == "medium"
    assert ag.tui_columns(69) == "narrow"
    assert ag.tui_columns(40) == "narrow"
    print("ok test_breakpoints")


def test_wrap_bounds():
    segs = ag.wrap_chat_line("x" * 100, 20)
    assert len(segs) > 1 and all(len(s) <= 20 for s in segs), segs
    rows = [{"role": "agent", "text": "y" * 100}]
    dl = ag.chat_screen_lines(rows, "", "t9", 30)
    bodies = [t for k, t in dl if k == "body"]
    assert len(bodies) > 1 and all(len(t) <= 30 for t in bodies), dl
    print("ok test_wrap_bounds")


def test_labels_and_empty_state():
    dl = ag.chat_screen_lines(
        [{"role": "user", "text": "hi"}, {"role": "agent", "text": "yo"}],
        "", "t9", 40)
    assert dl[0] == ("uh", "● you"), dl
    assert ("ah", "● t9") in dl, dl
    dl3 = ag.chat_screen_lines([], "", "t9", 40)
    assert dl3 and all(k == "hint" for k, t in dl3), dl3
    assert "Enter" in dl3[0][1], dl3
    print("ok test_labels_and_empty_state")


def test_readkey_separation():
    class Wch:
        def __init__(self, vals):
            self.vals = list(vals)

        def get_wch(self):
            if not self.vals:
                raise curses.error("empty")
            return self.vals.pop(0)

    assert ag._readkey(Wch([curses.KEY_UP])) == curses.KEY_UP
    assert ag._readkey(Wch([curses.KEY_DOWN])) == curses.KEY_DOWN
    assert ag._readkey(Wch(["é"])) == 233  # <256: int, getch parity
    # U+0103 == 259 == KEY_UP numerically: str stays distinct from the key
    assert ag._readkey(Wch(["ă"])) == "ă"
    assert ag._readkey(Wch(["ă"])) != curses.KEY_UP
    assert ag._readkey(Wch(["q"])) == ord("q")
    assert ag._tui_special_scroll(curses.KEY_UP) == 5
    assert ag._tui_special_scroll(curses.KEY_PPAGE) == 5
    assert ag._tui_special_scroll(curses.KEY_DOWN) == -5
    assert ag._tui_special_scroll(ord("a")) is None
    print("ok test_readkey_separation")


def test_arrows_not_inserted():
    td = fresh_td()
    scr, alive = run(td, 80, 24, [
        curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE,
        curses.KEY_LEFT, curses.KEY_RIGHT, 27, ord("q"),
    ])
    assert not alive, "TUI did not quit on q"
    inp = scr.lines()[scr.h - 3]
    for bad in ("ă", "Ă", "œ", "Œ"):
        assert bad not in inp, f"keycode leaked into input: {inp!r}"
    print("ok test_arrows_not_inserted")


def test_render_widths():
    # home at narrow/medium/wide: charcoal composer with blue rule,
    # project header + two-line rows, quiet bottom status.
    for w, h in ((40, 16), (80, 24), (120, 30)):
        td = fresh_td()
        ag.append_chat(td, "claude", "user", "hello")
        ag.append_chat(td, "claude", "agent", "hi back " * 10)
        scr, alive = run(td, w, h, [27, ord("q")])
        assert not alive, f"{w}x{h}: did not quit"
        assert scr.frames, f"{w}x{h}: no frames drawn"
        L = scr.lines()
        assert any("Ask anything" in ln for ln in L), L
        assert any("▍" in ln for ln in L), L
        assert any("/ for commands" in ln for ln in L), L
        assert any("claude" in ln for ln in L) and \
            any("oc" in ln for ln in L), L
        assert any("Muse Spark" in ln or "default" in ln for ln in L), L
        if w >= 60 and h >= 24:
            assert any("__ _" in ln for ln in L), L  # wordmark
        if w >= 70:
            assert any("approval" in ln or "ready" in ln or "live" in ln
                       for ln in L), L
    print("ok test_render_widths")


def test_home_nav_into_chat_and_back():
    td = fresh_td()
    ag.append_chat(td, "oc", "user", "hello oc")
    ag.append_chat(td, "oc", "agent", "oc here")
    scr, alive = run(td, 100, 24, [
        27, curses.KEY_DOWN, 10, 27, ord("q"),
    ])
    assert not alive, "TUI did not quit after home/chat/home/q"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("oc" in t and "echo" in t for t in texts), "home rows missing"
    assert any("● you" in t and "oc here" in t for t in texts), \
        "chat screen never showed the oc log"
    assert any("Ask anything" in t for t in texts), "never returned home"
    print("ok test_home_nav_into_chat_and_back")


def test_home_groups_and_status():
    rows = [
        {"name": "a1", "backend": "echo", "model": "default",
         "dir": ".", "role": "orchestrator"},
        {"name": "b1", "backend": "echo", "model": "default",
         "dir": "/tmp/proj", "role": "sub"},
        {"name": "b2", "backend": "echo", "model": "default",
         "dir": "/tmp/proj", "role": "sub"},
    ]
    groups = ag.group_agents_by_workdir(rows)
    assert [d for d, _ in groups] == [".", "/tmp/proj"], groups
    assert [a["name"] for a in ag.home_flat_agents(groups)] == [
        "a1", "b1", "b2"], groups
    short, full = ag.project_title(".", "myproj")
    assert (short, full) == ("myproj", "."), (short, full)
    assert ag.project_title("/tmp/proj") == ("proj", "/tmp/proj")
    # busy agent shows live dot on home
    td = fresh_td(rows)
    ag.mark_running("b1", True)
    try:
        scr, alive = run(td, 80, 24, [27, ord("q")])
    finally:
        ag.mark_running("b1", False)
    assert not alive, "did not quit"
    L = scr.lines()
    assert any("● b1" in ln for ln in L), L
    assert any("○ a1" in ln for ln in L), L
    print("ok test_home_groups_and_status")


def test_home_agent_line_and_config():
    a = {"name": "oc", "backend": "opencode", "model": "default",
         "dir": ".", "role": "sub", "persona": "", "profile": ""}
    ln = ag.home_agent_line(a, False, 3, "last bit", 100)
    assert "○ oc" in ln and "opencode" in ln and "3t" in ln, ln
    assert ag.OPENCODE_MODEL_DEFAULT in ln, ln  # effective default shown
    assert "last bit" in ln
    assert "last bit" not in ag.home_agent_line(a, False, 3, "last bit", 40)
    cl = ag.config_panel_lines(a, "abyss", 60)
    body = "\n".join(cl)
    assert "configure: oc" in body and "opencode" in body, body
    assert ag.OPENCODE_MODEL_DEFAULT in body, body
    assert "abyss" in body and "(none)" in body, body
    print("ok test_home_agent_line_and_config")


def test_config_overlay_opens():
    td = fresh_td()
    scr, alive = run(td, 100, 24, [27, ord("c"), 27, ord("q")])
    assert not alive, "did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("configure · claude" in t for t in texts), "overlay missing"
    assert any("Ask anything" in t for t in texts), "never returned home"
    print("ok test_config_overlay_opens")


def frame_rows(f):
    """Render a captured frame as ordered row strings (insertion order in
    FakeScr is not row-major once overlays overwrite cells)."""
    by_row = {}
    for (y, x), ch in f.items():
        by_row.setdefault(y, {})[x] = ch
    return ["".join(by_row[y][x] for x in sorted(by_row[y]))
            for y in sorted(by_row)]


def frame_text(f):
    """Row-major frame text with wrapped lines joined and whitespace runs
    collapsed, so a phrase split across wrapped lines or padded cells
    still matches. Use for multi-word content assertions at narrow
    widths; single-token markers can keep exact per-line checks."""
    return " ".join(" ".join(frame_rows(f)).split())


def test_home_flat_order_maps_to_roster():
    # A dir1, B dir2, C dir1 -> flat A,C,B differs from roster A,B,C.
    # DOWN lands on C (flat 1); Enter must open C's chat (roster 2), not B's;
    # returning home must keep C selected so the next DOWN opens B.
    # Chat-only markers: home rows show last-reply snippets too, but only the
    # chat pane renders the filled-dot agent header ("● C" vs home's "○ C").
    rows = [
        {"name": "A", "backend": "echo", "model": "default",
         "dir": "dir1", "role": "orchestrator"},
        {"name": "B", "backend": "echo", "model": "default",
         "dir": "dir2", "role": "sub"},
        {"name": "C", "backend": "echo", "model": "default",
         "dir": "dir1", "role": "sub"},
    ]
    groups = ag.group_agents_by_workdir(rows)
    flat = ag.home_flat_agents(groups)
    assert [a["name"] for a in flat] == ["A", "C", "B"], flat
    assert ag.index_by_name(rows, "C") == 2
    assert ag.index_by_name(flat, "C") == 1
    assert ag.index_by_name(rows, "ghost") is None
    td = fresh_td(rows)
    for nm, mark in (("A", "aaa"), ("B", "bbb"), ("C", "ccc")):
        ag.append_chat(td, nm, "user", "hi")
        ag.append_chat(td, nm, "agent", mark)
    scr, alive = run(td, 100, 24, [
        27, curses.KEY_DOWN, 10, 27,
        curses.KEY_DOWN, 10, 27, ord("q"),
    ])
    assert not alive, "did not quit after reorder round-trip"
    texts = ["".join(f.values()) for f in scr.frames]
    first_ccc = next(i for i, t in enumerate(texts) if "● C" in t)
    first_bbb = next(i for i, t in enumerate(texts) if "● B" in t)
    assert first_ccc < first_bbb, "wrong agent opened (flat/roster mixup)"
    print("ok test_home_flat_order_maps_to_roster")


def test_home_starts_composing():
    # composer-first: home boots typing (q is text, never quit), Esc
    # selects, then q quits; Enter dives into chat at once.
    td = fresh_td()
    scr, alive = run(td, 80, 24, [ord("q"), 27, ord("q")])
    assert not alive, "esc+q did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("Ask anything" in t for t in texts)
    td = fresh_td()
    ag.append_chat(td, "claude", "user", "hello")
    scr, alive = run(td, 80, 24, [27, 10, 27, ord("q")])
    assert not alive, "Enter-first nav did not finish"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("● you" in t for t in texts), "Enter never opened chat"
    assert any("Ask anything" in t for t in texts), "never returned home"
    print("ok test_home_starts_composing")


def test_home_tab_cycles():
    rows = [
        {"name": "A", "backend": "echo", "model": "default",
         "dir": ".", "role": "orchestrator"},
        {"name": "B", "backend": "echo", "model": "default",
         "dir": ".", "role": "sub"},
    ]
    td = fresh_td(rows)
    for nm, mark in (("A", "aaa"), ("B", "bbb")):
        ag.append_chat(td, nm, "user", "hi")
        ag.append_chat(td, nm, "agent", mark)
    scr, alive = run(td, 80, 24,
                     [27, ord("\t"), 10, 27, ord("q")])
    assert not alive, "tab nav did not quit"
    assert any("bbb" in "".join(f.values()) for f in scr.frames), \
        "Tab did not advance to B"
    td = fresh_td(rows)
    for nm, mark in (("A", "aaa"), ("B", "bbb")):
        ag.append_chat(td, nm, "user", "hi")
        ag.append_chat(td, nm, "agent", mark)
    scr, alive = run(td, 80, 24,
                     [27, ord("\t"), ord("\t"), 10, 27, ord("q")])
    assert not alive, "tab wrap nav did not quit"
    assert any("aaa" in "".join(f.values()) for f in scr.frames), \
        "Tab did not wrap to A"
    print("ok test_home_tab_cycles")


def test_norm_workdir_groups():
    assert ag.norm_workdir(".", "/tmp/x") == "/tmp/x"
    assert ag.norm_workdir("/tmp/x/", "/tmp/x") == "/tmp/x"
    assert ag.norm_workdir("sub", "/tmp/x") == "/tmp/x/sub"
    assert ag.norm_workdir("", "") == "."
    assert ag.norm_workdir(None, "") == "."
    rows = [
        {"name": "a", "dir": "."},
        {"name": "b", "dir": "/tmp/x"},
        {"name": "c", "dir": "/tmp/x/"},
        {"name": "d", "dir": "sub"},
    ]
    groups = ag.group_agents_by_workdir(rows, "/tmp/x")
    assert [(d, [x["name"] for x in m]) for d, m in groups] == [
        ("/tmp/x", ["a", "b", "c"]), ("/tmp/x/sub", ["d"])], groups
    # no cwd: literal grouping preserved (backward compatible)
    groups = ag.group_agents_by_workdir(
        [{"name": "a", "dir": "."}, {"name": "b", "dir": "/tmp/p"}])
    assert [d for d, _ in groups] == [".", "/tmp/p"], groups
    print("ok test_norm_workdir_groups")


def test_config_overlay_wraps_narrow():
    # narrow configurator: shared styled dialog, wrapped rows, esc returns
    # home with the draft-capable composer intact. Friendly Spark name shows
    # (full id lives in the model picker, covered below).
    td = fresh_td([{"name": "oc", "backend": "opencode", "model": "default",
                    "dir": ".", "role": "sub"}])
    scr, alive = run(td, 40, 24, [27, ord("c"), 27, ord("q")])
    assert not alive, "did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("configure · oc" in t for t in texts), "overlay missing"
    assert any("Muse Spark 1.3" in t for t in texts), "friendly model missing"
    assert any("Ask anything" in t for t in texts), "never returned home"
    print("ok test_config_overlay_wraps_narrow")


def test_model_picker_fits_spark():
    S = ag.OPENCODE_MODEL_DEFAULT
    td = fresh_td([{"name": "oc", "backend": "opencode", "model": "default",
                    "dir": ".", "role": "sub"}])
    scr, alive = run(td, 100, 24, [27, ord("m"), 27, ord("q")])
    assert not alive, "did not quit"
    # per rendered row: the full id must fit on one picker line
    rows_text = [ln for f in scr.frames for ln in frame_rows(f)]
    assert any(S in ln for ln in rows_text), "Spark id clipped in picker"
    print("ok test_model_picker_fits_spark")


def test_model_picker_lists_spark_default():
    S = ag.OPENCODE_MODEL_DEFAULT
    assert ag._model_opts("opencode") == [S, "[custom…]"]
    assert ag._model_opts("claude")[0] == "default"  # others untouched
    assert ag.eff_model("opencode", "default") == S
    assert ag.eff_model("opencode", "") == S
    assert ag.eff_model("opencode", "custom/x") == "custom/x"
    assert ag.eff_model("claude", "default") == ""
    assert ag.eff_model("claude", "opus") == "opus"
    td = fresh_td([{"name": "oc", "backend": "opencode", "model": "default",
                    "dir": ".", "role": "sub"}])
    scr, alive = run(td, 100, 24, [27, ord("m"), 27, ord("q")])
    assert not alive, "did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any(S in t for t in texts), "picker never listed the Spark default"
    print("ok test_model_picker_lists_spark_default")


def test_opencode_argv_and_handoff_carry_spark():
    S = ag.OPENCODE_MODEL_DEFAULT
    av = ag.backend_argv("opencode", model="default", workdir=".",
                         sid="", prompt="hi")
    assert "-m" in av and S in av, av
    av = ag.backend_argv("opencode", model="custom/x", workdir=".",
                         sid="", prompt="hi")
    assert av[av.index("-m") + 1] == "custom/x", av
    av = ag.backend_argv("claude", model="default", workdir=".",
                         sid="", prompt="hi")
    assert "-m" not in av and "--model" not in av, av
    argv, cwd = ag.native_handoff_argv("opencode", sid="s1", model="default")
    assert os.path.basename(argv[0]) == "opencode" and argv[1:] == ["-m", S], argv
    argv, _ = ag.native_handoff_argv("claude", sid="abc")
    assert argv == ["claude", "--resume", "abc"], argv
    print("ok test_opencode_argv_and_handoff_carry_spark")


def test_switch_and_add_materialize_spark():
    S = ag.OPENCODE_MODEL_DEFAULT
    td = fresh_td([{"name": "sl", "backend": "claude", "model": "opus",
                    "dir": ".", "role": "sub"}])
    ok, out = ag.switch_backend(td, "sl", "opencode")
    assert ok, out
    got = [x for x in ag.load_agents(td) if x["name"] == "sl"][0]
    assert got["model"] == S, got  # foreign-known model resets to ag default
    ok, out = ag.switch_backend(td, "sl", "claude")
    assert ok, out
    got = [x for x in ag.load_agents(td) if x["name"] == "sl"][0]
    assert got["model"] == "default", got  # leaving opencode resets as before
    td2 = fresh_td([{"name": "cu", "backend": "echo",
                     "model": "custom/keep", "dir": ".", "role": "sub"}])
    ok, out = ag.switch_backend(td2, "cu", "opencode")
    assert ok, out
    got = [x for x in ag.load_agents(td2) if x["name"] == "cu"][0]
    assert got["model"] == "custom/keep", got  # explicit custom survives
    print("ok test_switch_and_add_materialize_spark")


def test_friendly_model_and_wordmark():
    assert ag.friendly_model({"backend": "opencode", "model": "default"}) == \
        "Muse Spark 1.3"
    assert ag.friendly_model({"backend": "opencode", "model": ""}) == \
        "Muse Spark 1.3"
    assert ag.friendly_model({"backend": "opencode",
                              "model": "custom/x"}) == "x"
    assert ag.friendly_model({"backend": "claude", "model": "opus"}) == "opus"
    assert ag.friendly_model({"backend": "claude",
                              "model": "default"}) == "default"
    assert len(ag.ag_wordmark_lines("full")) == 5
    assert len(ag.ag_wordmark_lines("small")) == 3
    assert len(ag.ag_wordmark_lines("tiny")) == 1
    assert ag.wordmark_size(16, 40) == "none"
    assert ag.wordmark_size(20, 80) == "tiny"
    assert ag.wordmark_size(26, 80) == "small"
    assert ag.wordmark_size(36, 120) == "full"
    x0, cw = ag.content_region(120)
    assert cw == 84 and x0 == (120 - 84) // 2, (x0, cw)
    x0, cw = ag.content_region(40)
    assert cw == 36 and x0 == 2, (x0, cw)
    print("ok test_friendly_model_and_wordmark")


def test_palette_filter_pure():
    items = ag.palette_entries(
        [{"name": "oc", "backend": "opencode", "model": "default"}],
        ["docs"], ["opencode", "claude"])
    acts = [it["act"] for it in items]
    for need in ("home", "new", "delegate", "theme", "approve", "help",
                 "quit", "chat:oc", "config:oc", "model:oc",
                 "backend:opencode", "profile:docs", "handoff:oc"):
        assert need in acts, (need, acts)
    assert ag.palette_filter(items, "") == list(range(len(items)))
    hits = ag.palette_filter(items, "chat with oc")
    assert hits and all(items[i]["act"] == "chat:oc" for i in hits), hits
    assert ag.palette_filter(items, "CHAT") == hits  # case-insensitive
    assert ag.palette_filter(items, "zzz-no-match") == []
    print("ok test_palette_filter_pure")


def test_palette_opens_filters_and_runs():
    # Ctrl+P opens, typing filters, Enter runs the action (chat with oc),
    # Esc returns home; drafts and focus survive.
    td = fresh_td()
    ag.append_chat(td, "oc", "user", "hello oc")
    ag.append_chat(td, "oc", "agent", "oc here")
    keys = [16] + [ord(c) for c in "chat with oc"] + [10, 27,
                                                      ord("q"), ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "palette round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("commands" in t for t in texts), "palette never opened"
    assert any("● you" in t and "oc here" in t for t in texts), \
        "palette chat action never opened the oc log"
    assert any("Ask anything" in t for t in texts), "never returned home"
    print("ok test_palette_opens_filters_and_runs")


def test_home_prompt_submit_opens_chat():
    # composer-first: typing + Enter submits to the selected agent (echo
    # backend) and opens its chat showing the message. No i required.
    td = fresh_td()
    keys = [ord(c) for c in "hello palette"] + [10, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys, timeout=15)
    assert not alive, "home prompt submit did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("hello palette" in t for t in texts), \
        "submitted prompt never reached the chat log"
    print("ok test_home_prompt_submit_opens_chat")


def test_draft_survives_dialogs():
    # home draft typed, Esc keeps it, palette open/cancel keeps it: the
    # composer still shows the draft on home afterwards.
    td = fresh_td()
    draft = "zzdraft9"
    keys = [ord(c) for c in draft] + [27, 16, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "draft round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert sum(1 for t in texts if draft in t) >= 2, \
        "draft lost across esc/palette round-trip"
    print("ok test_draft_survives_dialogs")


def test_config_field_select_opens_picker():
    # configurator fields are selectable: Enter on backend opens the
    # backend picker (real operation), Esc walks back to home.
    td = fresh_td()
    scr, alive = run(td, 100, 30, [27, ord("c"), 10, 27, 27, ord("q")])
    assert not alive, "config->picker round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("configure · claude" in t for t in texts), "config missing"
    assert any("backend · claude" in t for t in texts), \
        "Enter on backend field never opened the picker"
    assert any("Ask anything" in t for t in texts), "never returned home"
    print("ok test_config_field_select_opens_picker")


def test_narrow_session_layout():
    # 40x16 session: subtle header, transcript, charcoal composer, quiet
    # status — no box cages, no crash.
    td = fresh_td()
    ag.append_chat(td, "claude", "user", "hello")
    ag.append_chat(td, "claude", "agent", "hi back")
    scr, alive = run(td, 40, 16, [27, 10, 27, ord("q")])
    assert not alive, "narrow session nav did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("● you" in t and "hi back" in t for t in texts), \
        "narrow chat never showed the log"
    assert any("Type a message" in t or "Build" in t for t in texts), \
        "narrow composer missing"
    print("ok test_narrow_session_layout")


def test_bounded_color_surfaces():
    # color-aware regression: every addstr stays on-screen (the old
    # userfill bug passed an attr as fill width), the user bubble paints
    # with the bg-carrying userfill/text_u pairs, and composer/dialog text
    # uses text_c/dim_c so the charcoal surface stays continuous.
    old_pair = curses.color_pair
    old_colors = getattr(curses, "COLORS", None)
    curses.color_pair = lambda i: (i + 1) << 24
    curses.COLORS = 256
    oob = []

    class StrictScr(FakeScr):
        def __init__(self, w, h, keys):
            super().__init__(w, h, keys)
            self.cellattr = {}

        def addstr(self, y, x, s, attr=0):
            for i, ch in enumerate(s):
                if 0 <= y < self.h and 0 <= x + i < self.w:
                    self.cells[(y, x + i)] = ch
                    self.cellattr[(y, x + i)] = int(attr)
                else:
                    oob.append((y, x + i))

        def refresh(self):
            # snapshot cells AND attrs (base class keeps cells only)
            self.frames.append((dict(self.cells), dict(self.cellattr)))

    def run_strict(td, w, h, keys, timeout=12):
        scr = StrictScr(w, h, keys)
        t = threading.Thread(target=lambda: ag._tui_main(scr, td),
                             daemon=True)
        t.start()
        t.join(timeout=timeout)
        return scr, t.is_alive()

    try:
        td = fresh_td()
        ag.append_chat(td, "claude", "user", "bubble wrap me")
        ag.append_chat(td, "claude", "agent", "plain reply")
        scr, alive = run_strict(td, 100, 30,
                                [27, 10, ord("x"), 27, 27, ord("q")])
        assert not alive, "strict session nav did not quit"
        assert not oob, f"out-of-bounds draws: {oob[:5]}"
        mark2role = {i + 1: r for i, r in ag.PAIR_ROLES.items()}
        seen = set()
        for _, attrs in scr.frames:
            for a in attrs.values():
                seen.add(mark2role.get((a >> 24) & 0xFF))
        assert "userfill" in seen, f"user bubble never painted: {seen}"
        assert "text_u" in seen, f"bubble text not on surface: {seen}"
        assert "composer" in seen and "text_c" in seen, \
            f"composer surface broken: {seen}"
        assert "dim_c" in seen, f"dialog/composer dim not surfaced: {seen}"
        # continuity: every cell painted with a composer-surface role has
        # a real background in the theme (no terminal-bg holes).
        for _, attrs in scr.frames:
            for (y, x), a in attrs.items():
                r = mark2role.get((a >> 24) & 0xFF)
                if r in ("composer", "text_c", "dim_c", "text_u",
                         "userfill", "rule", "sel", "alert_c"):
                    _, bg = ag.theme_rgb("abyss", r)
                    assert bg is not None, f"surface {r} has no bg"
    finally:
        curses.color_pair = old_pair
        if old_colors is None:
            try:
                del curses.COLORS
            except Exception:
                pass
        else:
            curses.COLORS = old_colors
    print("ok test_bounded_color_surfaces")


def test_compact_roles_independent():
    td = fresh_td([
        {"name": "orch", "backend": "echo", "model": "default", "dir": ".",
         "role": "orchestrator"},
        {"name": "sub", "backend": "echo", "model": "default", "dir": ".",
         "role": "sub"}])
    ok, m = ag.compact_apply_edit(td, "orchestrator", "max_turns", "31")
    assert ok, m
    ok, m = ag.compact_apply_edit(td, "sub", "max_turns", "22")
    assert ok, m
    assert ag.compact_effective(td, "orch")["settings"]["max_turns"] == 31
    assert ag.compact_effective(td, "sub")["settings"]["max_turns"] == 22
    assert ag.compact_effective(td, "orch")["source"] == \
        "role:orchestrator"
    print("ok test_compact_roles_independent")


def compact_open_keys(td, agent="claude"):
    # select the agent on home, config -> compact field (index 5) -> Enter;
    # returns prefix keys and the row table so tests can navigate
    # deterministically.
    flat = ag.home_flat_agents(ag.group_agents_by_workdir(
        ag.load_agents(td)))
    fi = ag.index_by_name(flat, agent) or 0
    rows = ag.compact_screen_rows(td, agent)
    keys = [27] + [curses.KEY_DOWN] * fi + [ord("c")] + \
        [curses.KEY_DOWN] * 5 + [10]
    return keys, rows


def row_index(rows, rid):
    return next(i for i, (k, r, _l, _v) in enumerate(rows) if r == rid)


def sel_steps(rows, rid):
    """DOWN presses from the first selectable to the selectable holding rid.
    Headers/stats are skipped by the TUI, so tests must step the selectable
    list, not the raw logical index."""
    sel = ag.compact_selectable(rows)
    tgt = row_index(rows, rid)
    return sel.index(tgt)


def test_compact_overview_first_screen():
    # First screen is a plain menu: dialog title + one purpose line, one
    # row per scope with On/Off + inheritance, run-now, Back. No raw
    # value grid. Title lives in the dialog chrome; rows start with the
    # purpose line. fresh_td's claude is an orchestrator, so the agent
    # row inherits orchestrator settings.
    td = fresh_td()
    rows = ag.compact_screen_rows(td, "claude")
    kinds = [k for k, _r, _l, _v in rows]
    assert kinds[0] == "sec" and "Full chat stays saved" in rows[0][2], \
        rows
    got = {r: (l, v) for k, r, l, v in rows if k in ("nav", "act")}
    assert set(got) == {"scope.orchestrator", "scope.sub", "scope.agent",
                        "act.compact", "act.back"}, got
    assert got["scope.orchestrator"][0] == "Orchestrator"
    assert got["scope.sub"][0] == "Subagents"
    assert got["scope.agent"][0] == "This agent: claude"
    assert got["scope.sub"][1] == "off", got
    assert "Using orchestrator settings" in got["scope.agent"][1], got
    # no raw counts on the overview rows
    for _l, v in got.values():
        assert "turns /" not in v and "tokens" not in v, got
    # help names who is affected
    assert "orchestrator" in ag.compact_help_for(
        "overview", "scope.orchestrator").lower()
    assert "claude" in ag.compact_help_for(
        "overview", "scope.agent", td, None, "claude")
    sel = ag.compact_selectable(rows)
    assert all(rows[i][0] in ("nav", "act") for i in sel)
    assert rows[ag.compact_first_selectable(rows)][1] == \
        "scope.orchestrator"
    print("ok test_compact_overview_first_screen")


def test_compact_scope_flow_saved_and_back():
    # overview -> sub scope -> on/off picker -> saved on -> Esc back to
    # overview -> Back. Each step shows Applies-to + row help + nav footer.
    td = fresh_td()
    keys, rows = compact_open_keys(td)
    keys += [curses.KEY_DOWN] * sel_steps(rows, "scope.sub") + [10]
    srows = ag.compact_scope_rows(td, "sub")
    assert any(r == "scope.onoff" for _k, r, _l, _v in srows)
    assert any("Applies to all subagents using these defaults" in l
                for _k, _r, l, _v in srows), srows
    keys += [curses.KEY_DOWN] * sel_steps(srows, "scope.onoff") + [10]
    orows = ag.compact_onoff_rows(False)
    keys += [curses.KEY_DOWN] * sel_steps(orows, "onoff.on") + \
        [10, 27, 27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "scope flow round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("Conversation length" in t for t in texts), \
        "overview title missing"
    assert any("Applies to all subagents using these defaults" in t
               for t in texts), "applies-to line missing"
    assert any("saved automatic summaries on" in t for t in texts), \
        "save never named what saved"
    assert any("esc back" in t for t in texts), "nav footer missing"
    assert ag.compact_effective(td, "oc")["settings"]["enabled"] is True
    print("ok test_compact_scope_flow_saved_and_back")


def test_compact_agent_inherit_and_reset():
    # Per-agent scope says Use role defaults vs Custom; reset reads
    # Use <role> defaults and drops the override.
    td = fresh_td()
    ok, m = ag.compact_apply_edit(td, "agent:oc", "keep_recent", "7")
    assert ok, m
    assert ag.compact_effective(td, "oc")["source"] == "agent"
    srows = ag.compact_scope_rows(td, "agent", "oc")
    assert any("Custom settings" in l for _k, _r, l, _v in srows), srows
    assert any(r == "act.reset" and "Use subagent settings" in l
               for _k, r, l, _v in srows), srows
    orows = ag.compact_screen_rows(td, "oc")
    assert "Custom settings" in dict((r, v) for _k, r, _l, v in orows) \
        .get("scope.agent", ""), orows
    # reset stays visible while inherited, explaining the state
    irows = ag.compact_scope_rows(td, "agent", "claude")
    assert any(r == "act.reset" for _k, r, _l, _v in irows), irows
    keys, rows = compact_open_keys(td, "oc")
    keys += [curses.KEY_DOWN] * sel_steps(rows, "scope.agent") + [10]
    srows = ag.compact_scope_rows(td, "agent", "oc")
    keys += [curses.KEY_DOWN] * sel_steps(srows, "act.reset") + \
        [10, 27, 27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "reset round-trip did not quit"
    assert ag.compact_effective(td, "oc")["source"] == "role:sub", \
        "override not cleared"
    assert any("using subagent settings" in "".join(f.values())
               for f in scr.frames), "reset never said what happened"
    print("ok test_compact_agent_inherit_and_reset")


def test_compact_preset_preserves_disabled():
    # At most 3 transparent presets on supported values; Standard matches
    # factory defaults; applying a cadence never enables a disabled scope.
    for role, std in (("orchestrator", (30, 20000, 10)),
                      ("sub", (20, 20000, 5))):
        defs = ag.COMPACT_PRESETS[role]
        assert len(defs) <= 3, defs
        assert (defs[0]["max_turns"], defs[0]["token_threshold"],
                defs[0]["keep_recent"]) == std, defs[0]
        for p in defs:
            assert "turns" in p["desc"] and "tokens" in p["desc"], p
    td = fresh_td()
    assert ag.compact_match_preset("sub", {"max_turns": 20,
        "token_threshold": 20000, "keep_recent": 5}) == "Standard"
    assert ag.compact_match_preset("sub", {"max_turns": 21,
        "token_threshold": 20000, "keep_recent": 5}) == "Custom"
    ok, m = ag.compact_apply_preset(td, "sub", "oc", "More often")
    assert ok, m
    assert ag.compact_load_config(td)["defaults"]["sub"] \
        ["max_turns"] == 10
    assert ag.compact_effective(td, "oc")["settings"]["enabled"] is \
        False, "preset silently enabled a disabled scope"
    # TUI preset picker shows exact effects before choice
    prows = ag.compact_cadence_rows("sub", {"max_turns": 21,
        "token_threshold": 20000, "keep_recent": 5})
    assert any("10 user turns" in v for _k, _r, _l, v in prows), prows
    assert any("Custom" in l for _k, _r, l, _v in prows), prows
    print("ok test_compact_preset_preserves_disabled")


def test_compact_palette_nav():
    td = fresh_td()
    keys = [16] + [ord(c) for c in "Conversation length"] + [10, 27,
                                                             ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "palette->compact nav did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("Conversation length" in t for t in texts), \
        "palette never opened the conversation-length menu"
    print("ok test_compact_palette_nav")


def test_compact_advanced_range_error_cancel():
    # Advanced page: exact labels, range error inline, Esc cancels,
    # valid save names the value.
    td = fresh_td()
    arows = ag.compact_advanced_rows(td, "sub")
    labels = [l for _k, _r, l, _v in arows]
    assert any("Messages before summary" in l for l in labels), labels
    assert any("Approximate token limit" in l for l in labels), labels
    assert any("Recent turns to keep" in l for l in labels), labels
    assert any("Summary time limit" in l for l in labels), labels
    keys, rows = compact_open_keys(td)
    keys += [curses.KEY_DOWN] * sel_steps(rows, "scope.sub") + [10]
    srows = ag.compact_scope_rows(td, "sub")
    keys += [curses.KEY_DOWN] * sel_steps(srows, "scope.advanced") + [10]
    arows = ag.compact_advanced_rows(td, "sub")
    cur = next(v for _k, r, _l, v in arows if r == "adv.timeout")
    keys += [curses.KEY_DOWN] * sel_steps(arows, "adv.timeout") + [10] + \
        [curses.KEY_BACKSPACE] * len(cur) + [ord("1")] + \
        [10, 27, 27, 27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "advanced range round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("timeout must be int 5..600" in t for t in texts), \
        "range error never surfaced inline"
    assert ag.compact_load_config(td)["defaults"]["sub"]["timeout"] == \
        120, "invalid edit persisted"
    # cancel path: type then Esc writes nothing
    keys, rows = compact_open_keys(td)
    keys += [curses.KEY_DOWN] * sel_steps(rows, "scope.sub") + [10]
    srows = ag.compact_scope_rows(td, "sub")
    keys += [curses.KEY_DOWN] * sel_steps(srows, "scope.advanced") + [10]
    arows = ag.compact_advanced_rows(td, "sub")
    cur = next(v for _k, r, _l, v in arows if r == "adv.max_turns")
    keys += [curses.KEY_DOWN] * sel_steps(arows, "adv.max_turns") + [10] + \
        [ord(c) for c in "99"] + [27, 27, 27, 27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "advanced cancel round-trip did not quit"
    assert ag.compact_load_config(td)["defaults"]["sub"]["max_turns"] == \
        20, "cancelled edit persisted"
    print("ok test_compact_advanced_range_error_cancel")


def test_context_view_opens():
    td = fresh_td()
    keys = [16] + [ord(c) for c in "Context for claude"] + [10, 27,
                                                            ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "palette->context nav did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("context · claude" in t for t in texts), \
        "context view never opened"
    assert any("sources" in t for t in texts), "sources never shown"
    print("ok test_context_view_opens")


def test_compact_manual_busy_failure_noop():
    td = fresh_td()
    keys, rows = compact_open_keys(td)
    idx = sel_steps(rows, "act.compact")
    before = ag.compact_effective(td, "claude")["settings"]["enabled"]
    # busy path: agent guard refuses, banner surfaces it, UI stays alive
    old_busy = ag.agent_busy
    ag.agent_busy = lambda sdir, nm: True
    try:
        scr, alive = run(td, 100, 30,
                         keys + [curses.KEY_DOWN] * idx + [10, 27, 27,
                                                           ord("q")])
    finally:
        ag.agent_busy = old_busy
    assert not alive, "busy compact round-trip did not quit"
    assert any("is busy" in "".join(f.values()) for f in scr.frames), \
        "busy state never surfaced"
    # failure path: stubbed summarizer fails, banner shows it, no freeze
    calls = []
    old_run = ag.compact_run
    ag.compact_run = lambda sdir, name, force=False: (
        calls.append(name), {"compacted": False, "error": "boom-x"})[1]
    try:
        scr, alive = run(td, 100, 30,
                         keys + [curses.KEY_DOWN] * idx + [10, 27, 27,
                                                           ord("q")],
                         timeout=15)
    finally:
        ag.compact_run = old_run
    assert not alive, "failed compact round-trip did not quit"
    assert calls == ["claude"], calls
    assert any("boom-x" in "".join(f.values()) for f in scr.frames), \
        "failure never surfaced"
    # no-op path: plain nothing-to-do message; auto setting untouched
    old_run = ag.compact_run
    ag.compact_run = lambda sdir, name, force=False: {
        "compacted": False, "reason": "nothing old enough: 2 turns"}
    try:
        scr, alive = run(td, 100, 30,
                         keys + [curses.KEY_DOWN] * idx + [10, 27, 27,
                                                           ord("q")],
                         timeout=15)
    finally:
        ag.compact_run = old_run
    assert not alive, "no-op compact round-trip did not quit"
    notes = [frame_text(f) for f in scr.frames]
    assert any("No older messages to summarize yet" in t for t in notes), \
        "no-op reason never surfaced"
    # claude is an orchestrator in fresh_td, so the kept count is the
    # orchestrator default (10), not the subagent one.
    assert any("newest 10 turns are kept" in t for t in notes), \
        "kept count never explained"
    assert ag.compact_effective(td, "claude")["settings"]["enabled"] == \
        before, "manual run toggled auto"
    print("ok test_compact_manual_busy_failure_noop")


def test_compact_narrow_geometry():
    # 40x16: purpose stays pinned while selection moves; help wraps;
    # footer never covers the options (nav hints on the last row).
    td = fresh_td()
    keys, rows = compact_open_keys(td)
    keys += [curses.KEY_DOWN] * sel_steps(rows, "scope.agent") + \
        [27, 27, ord("q")]
    scr, alive = run(td, 40, 16, keys, timeout=15)
    assert not alive, "narrow overview round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("Conversation length" in t for t in texts), \
        "overview missing at 40x16"
    frames = [f for f in scr.frames
              if "Conversation length" in "".join(f.values())]
    assert frames, "no overview frame captured"
    # the frame where This agent is actually selected (not the reset tail)
    sel = [f for f in frames if any(
        ">" in ln and "This agent" in ln for ln in frame_rows(f))]
    assert sel, "This agent never visibly selected at 40x16"
    L = frame_rows(sel[0])
    # purpose wraps mid-phrase at 40 cols ("...Full" / "chat stays
    # saved") over padded cells, so match on normalized frame text.
    assert "Full chat stays saved" in frame_text(sel[0]), \
        f"purpose not pinned at 40x16: {L}"
    assert any("esc back" in ln.lower() for ln in L), \
        f"nav footer missing at 40x16: {L}"
    print("ok test_compact_narrow_geometry")


def test_config_length_returns_to_config():
    # /config -> Conversation length -> scope -> Esc -> overview ->
    # Esc -> config (same row) -> Esc -> composer with draft kept.
    td = fresh_td()
    draft = "draftA9"
    keys = [ord(c) for c in draft] + [27, ord("c")]
    keys += [curses.KEY_DOWN] * 5 + [10]
    rows = ag.compact_screen_rows(td, "claude")
    keys += [curses.KEY_DOWN] * sel_steps(rows, "scope.sub") + \
        [10, 27, 27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "config round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("Conversation length" in t for t in texts), \
        "length menu never opened"
    scope_i = next(i for i, t in enumerate(texts)
                   if "Applies to all subagents" in t)
    back_i = next(i for i, t in enumerate(texts[scope_i:])
                  if "configure · claude" in t) + scope_i
    assert back_i > scope_i, "Esc never returned to the config menu"
    # the actual config screen after compact exit: config title present,
    # scope content gone (not a stale scope frame).
    assert "Applies to all subagents" not in texts[back_i], \
        "back frame is still the scope view"
    assert any("Conversation length" in t
               for t in texts[back_i:]), \
        "config row lost after return"
    assert sum(1 for t in texts if draft in t) >= 1, \
        "draft lost across config menus"
    print("ok test_config_length_returns_to_config")


def test_preset_effects_visible_before_enter():
    # 40x16 preset page: the whole selected option (exact limits) is
    # readable before Enter; after save the footer still says esc back.
    td = fresh_td()
    keys, rows = compact_open_keys(td)
    keys += [curses.KEY_DOWN] * sel_steps(rows, "scope.sub") + [10]
    srows = ag.compact_scope_rows(td, "sub")
    keys += [curses.KEY_DOWN] * sel_steps(srows, "scope.cadence") + [10]
    prows = ag.compact_cadence_rows("sub", {"max_turns": 20,
        "token_threshold": 20000, "keep_recent": 5})
    keys += [curses.KEY_DOWN] * sel_steps(prows, "preset.More often")
    pre_keys = list(keys)
    scr, alive = run(td, 40, 16, pre_keys + [27, 27, 27, 27, ord("q")],
                     timeout=15)
    assert not alive, "preset view round-trip did not quit"
    # preset effects wrap across padded lines at 40 cols: match exact
    # limits on normalized row-major text.
    frames = [frame_text(f) for f in scr.frames]
    whole = [t for t in frames if "More often" in t
             and "10 user turns" in t and "keep last 3" in t
             and "esc back" in t.lower()]
    assert whole, "selected preset effects not wholly visible at 40x16"
    # now choose it: saved message names the preset, footer stays fixed
    scr2, alive2 = run(td, 40, 16, keys + [10, 27, 27, 27, ord("q")],
                       timeout=15)
    assert not alive2, "preset save round-trip did not quit"
    frames2 = [frame_text(f) for f in scr2.frames]
    saved = [t for t in frames2 if "saved More often" in t
             and "esc back" in t.lower()]
    assert saved, "save hid the esc-back footer"
    assert ag.compact_load_config(td)["defaults"]["sub"] \
        ["max_turns"] == 10
    print("ok test_preset_effects_visible_before_enter")


def test_advanced_edit_replaces_prefill():
    # typing replaces the prefilled value (select-all style): "25"
    # saves exactly 25, never appends to the old value.
    td = fresh_td()
    keys, rows = compact_open_keys(td)
    keys += [curses.KEY_DOWN] * sel_steps(rows, "scope.sub") + [10]
    srows = ag.compact_scope_rows(td, "sub")
    keys += [curses.KEY_DOWN] * sel_steps(srows, "scope.advanced") + [10]
    arows = ag.compact_advanced_rows(td, "sub")
    keys += [curses.KEY_DOWN] * sel_steps(arows, "adv.max_turns") + \
        [10] + [ord(c) for c in "25"] + [10, 27, 27, 27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "replace-edit round-trip did not quit"
    assert ag.compact_load_config(td)["defaults"]["sub"] \
        ["max_turns"] == 25, "typed value appended instead of replacing"
    assert any("saved messages before summary = 25" in "".join(f.values())
               for f in scr.frames), "save never named the value"
    print("ok test_advanced_edit_replaces_prefill")


def test_compact_skips_headers():
    # section/purpose rows are display-only: never initially selected,
    # never landed on by arrows.
    td = fresh_td()
    rows = ag.compact_screen_rows(td, "claude")
    sel = ag.compact_selectable(rows)
    assert sel, "no selectable rows"
    assert all(rows[i][0] in ("nav", "act") for i in sel), rows
    first = ag.compact_first_selectable(rows)
    assert rows[first][1] == "scope.orchestrator", rows[first]
    assert ag.compact_step(rows, first, -1) == first  # clamped at top
    nxt = ag.compact_step(rows, first, +1)
    assert nxt in sel and rows[nxt][1] == "scope.sub", (nxt, rows[nxt])
    srows = ag.compact_scope_rows(td, "sub")
    ssel = ag.compact_selectable(srows)
    assert all(srows[i][0] in ("nav", "act") for i in ssel), srows
    print("ok test_compact_skips_headers")


def test_slash_compact_labels_and_aliases():
    # popup labels say the result; commands + backslash aliases keep working.
    descs = {e["cmd"]: e["desc"] for e in ag.SLASH_COMMANDS}
    assert descs["compact"] == "Manage conversation length", descs
    assert descs["compact-now"] == "Summarize this chat now", descs
    assert ag.parse_slash("\\compact") == ("compact", "")
    assert ag.parse_slash("\\compact-now") == ("compact-now", "")
    assert ag.parse_slash("// comment") is None  # prose stays prose
    hits = ag.slash_popup_filter("/compact")
    got = [ag.SLASH_COMMANDS[i]["cmd"] for i in hits]
    assert "compact" in got and "compact-now" in got, got
    td = fresh_td()
    scr, alive = run(td, 100, 30,
                     [ord(c) for c in "/compact-now"] + [10, 27, ord("q")],
                     timeout=15)
    assert not alive, "slash compact-now round-trip did not quit"
    print("ok test_slash_compact_labels_and_aliases")


def test_config_labels_back_help_and_draft():
    # plain-language labels, every row explained, explicit Back; Esc
    # preserves the typed draft.
    fields = ag.config_fields({"name": "oc", "backend": "echo",
                               "model": "default", "dir": "."})
    labs = {k: l for k, l, _v in fields}
    assert labs["backend"] == "Agent provider", labs
    assert labs["custom"] == "Custom model ID", labs
    assert labs["profile"] == "Shared instructions", labs
    assert labs["role"] == "Agent behavior", labs
    assert labs["compact"] == "Conversation length", labs
    assert labs["context"] == "Guidance & memory", labs
    assert labs["theme"] == "Appearance", labs
    assert labs["dir"] == "Working folder", labs
    assert labs["back"] == "Back", labs
    for k, _l, _v in fields:
        assert ag.config_help(k), k
    td = fresh_td()
    draft = "keep this draft"
    keys = [ord(c) for c in draft] + [27, ord("c")]
    # walk to the Back row and Enter it: returns home with draft intact
    nfields = len(fields)
    keys += [curses.KEY_DOWN] * (nfields - 1) + [10, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "config back round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("Agent provider" in t for t in texts), \
        "plain labels missing"
    assert any("Return to where you came from" in t for t in texts), \
        "row help missing"
    assert sum(1 for t in texts if draft in t) >= 1, \
        "draft lost across config Back"
    print("ok test_config_labels_back_help_and_draft")


def test_tool_without_status_shows_no_running():
    # Unknown status never invents Running: only explicit statuses render.
    bare = {"role": "tool", "tool_name": "Grep", "text": "Grep x",
            "event_id": "t-bare"}
    assert "Running" not in ag.timeline_tool_summary(bare), \
        ag.timeline_tool_summary(bare)
    live = {"role": "tool", "tool_name": "Grep", "text": "Grep x",
            "status": "running", "event_id": "t-live"}
    assert "Running" in ag.timeline_tool_summary(live), \
        ag.timeline_tool_summary(live)
    st = ag.timeline_new()
    ag.timeline_upsert(st, dict(bare))
    assert ag.timeline_rows(st)[0].get("status") is None, \
        "upsert inferred a status"
    print("ok test_tool_without_status_shows_no_running")


def test_home_long_list_keeps_selected_visible():
    # 15 agents in one project: DOWN to the last one must keep it visible
    # (selected › marker on screen) at small sizes — the old group-header
    # rewind hid far-down selections.
    rows = [{"name": f"a{i}", "backend": "echo", "model": "default",
             "dir": ".", "role": "sub"} for i in range(1, 16)]
    for w, h in ((40, 16), (80, 24)):
        td = fresh_td(rows)
        scr, alive = run(td, w, h,
                         [27] + [curses.KEY_DOWN] * 14 + [ord("q")])
        assert not alive, f"{w}x{h}: did not quit"
        L = scr.lines()
        assert any("›" in ln and "a15" in ln for ln in L), \
            f"{w}x{h}: selected a15 not visible: {L}"
    print("ok test_home_long_list_keeps_selected_visible")


def test_slash_popup_filter_pure():
    cmds = [e["cmd"] for e in ag.SLASH_COMMANDS]
    for need in ("projects", "agents", "config", "model", "backend",
                 "profile", "new", "delegate", "context", "compact",
                 "compact-now", "theme", "help", "quit", "handoff",
                 "approve", "harness"):
        assert need in cmds, cmds
    routes = {e["cmd"]: e["route"] for e in ag.SLASH_COMMANDS}
    assert routes["config"] == "ui" and routes["quit"] == "quit"
    assert routes["harness"] == "slash" and routes["handoff"] == "slash"
    assert ag.slash_popup_filter("") == list(range(len(ag.SLASH_COMMANDS)))
    hits = ag.slash_popup_filter("/model")
    assert ag.SLASH_COMMANDS[hits[0]]["cmd"] == "model", hits
    hits = ag.slash_popup_filter("MODEL OPUS")
    assert ag.SLASH_COMMANDS[hits[0]]["cmd"] == "model", hits
    hits = ag.slash_popup_filter("con")
    got = [ag.SLASH_COMMANDS[i]["cmd"] for i in hits]
    assert got[:2] == ["config", "context"], got  # prefix before substring
    assert ag.slash_popup_filter("zzz-no-match") == []
    print("ok test_slash_popup_filter_pure")


def test_slash_complete_pure():
    m = {"cmd": "model", "desc": "x", "route": "ui"}
    assert ag.slash_popup_complete("/mo", m) == "/model"
    assert ag.slash_popup_complete("/model op", m) == "/model op"
    assert ag.slash_popup_complete("/model", m) == "/model"
    assert ag.slash_popup_complete("/mo", {}) == "/mo"  # no entry: untouched
    print("ok test_slash_complete_pure")


def test_slash_popup_rect_sizes():
    # anchored to the composer at 120x36/80x24/60x24/40x16: on-screen,
    # status row free, composer never covered.
    cases = [(120, 36, 9, 2), (80, 24, 21, 2), (60, 24, 4, 2),
             (40, 16, 1, 2), (40, 16, 13, 2)]
    for w, h, cy, chh in cases:
        x0, cw = ag.content_region(w)
        bx, by, bw, bh, vis = ag.slash_popup_rect(w, h, x0, cw, cy, chh, 17)
        assert 0 <= bx and bx + bw <= w, (w, h, bx, bw)
        assert 0 <= by and by + bh <= h - 1, (w, h, by, bh)  # status free
        assert vis >= 1, (w, h, vis)
        covers = not (by + bh <= cy or by >= cy + chh)
        assert not covers, f"{w}x{h}: popup covers composer"
    print("ok test_slash_popup_rect_sizes")


def test_slash_popup_opens_and_esc_keeps_draft():
    td = fresh_td()
    scr, alive = run(td, 80, 24, [ord("/"), 27, 27, ord("q")])
    assert not alive, "slash esc round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("commands 1/" in t for t in texts), "popup never opened"
    assert any("/model" in t for t in texts), "popup rows missing"
    assert not any("commands 1/" in ln for ln in scr.lines()), \
        "popup still up after Esc"
    print("ok test_slash_popup_opens_and_esc_keeps_draft")


def test_slash_tab_completes_and_enter_runs():
    # "/con" + Tab completes to /config without running; Enter opens it.
    td = fresh_td()
    keys = [ord(c) for c in "/con"] + [9, 10, 27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "slash tab/enter round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("configure · claude" in t for t in texts), \
        "Enter never ran /config"
    assert any("Ask anything" in t for t in texts), "never returned home"
    print("ok test_slash_tab_completes_and_enter_runs")


def test_slash_typed_args_not_shadowed():
    # full typed "/harness list" + Enter runs verbatim (status ok): the
    # popup never substitutes a parameterless action.
    td = fresh_td()
    keys = [ord(c) for c in "/harness list"] + [10, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "typed slash round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("/harness ok" in t for t in texts), "typed args never ran"
    print("ok test_slash_typed_args_not_shadowed")


def test_slash_empty_match_and_unknown():
    # "/zzz" matches nothing: honest empty state; Enter runs the typed
    # text which errors honestly (never model prose, never stale selection).
    td = fresh_td()
    keys = [ord(c) for c in "/zzz"] + [10, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "unknown slash round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("no match" in t for t in texts), "empty state missing"
    assert any("/zzz err" in t for t in texts), "unknown slash never errored"
    log = ag.load_chat_log(td, "claude")
    assert not any(r.get("role") in ("user", "agent")
                   and "zzz" in (r.get("text") or "") for r in log), \
        "slash leaked into model turns"
    print("ok test_slash_empty_match_and_unknown")


def test_slash_in_prose_stays_literal():
    td = fresh_td()
    keys = [ord(c) for c in "see a/b please"] + [10, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys, timeout=15)
    assert not alive, "prose round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("see a/b please" in t for t in texts), "prose never sent"
    log = ag.load_chat_log(td, "claude")
    assert any(r.get("role") == "user" and "a/b" in (r.get("text") or "")
               for r in log), "prose slash treated as command"
    print("ok test_slash_in_prose_stays_literal")


def test_slash_backspace_dismiss_and_reopen():
    td = fresh_td()
    keys = [ord("/"), curses.KEY_BACKSPACE, ord("/"), 27, 27, ord("q")]
    scr, alive = run(td, 80, 24, keys)
    assert not alive, "slash reopen round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert sum(1 for t in texts if "commands 1/" in t) >= 2, \
        "popup did not open, dismiss, and reopen"
    print("ok test_slash_backspace_dismiss_and_reopen")


def test_config_context_compact_via_slash():
    for cmd, marker in (("/config", "configure · claude"),
                        ("/context", "context · claude"),
                        ("/compact", "Conversation length")):
        td = fresh_td()
        keys = [ord(c) for c in cmd] + [10, 27, 27, ord("q")]
        scr, alive = run(td, 100, 30, keys)
        assert not alive, f"{cmd} round-trip did not quit"
        texts = ["".join(f.values()) for f in scr.frames]
        assert any(marker in t for t in texts), f"{cmd} never opened"
        assert any("Ask anything" in t for t in texts), \
            f"{cmd} never returned home"
    print("ok test_config_context_compact_via_slash")


def test_projects_agents_choosers():
    rows = [
        {"name": "A", "backend": "echo", "model": "default",
         "dir": "dir1", "role": "orchestrator"},
        {"name": "B", "backend": "echo", "model": "default",
         "dir": "dir2", "role": "sub"},
    ]
    td = fresh_td(rows)
    keys = [ord(c) for c in "/agents"] + [10, curses.KEY_DOWN, 10, 27,
                                          ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "agents chooser did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("agents (enter switch)" in t for t in texts), \
        "agents chooser never opened"
    assert any("› ○ B" in t for t in texts), "Enter never switched to B"
    td = fresh_td(rows)
    keys = [ord(c) for c in "/projects"] + [10, 10, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "projects chooser did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("projects (enter open)" in t for t in texts), \
        "projects chooser never opened"
    assert any("A · echo" in t for t in texts), \
        "Enter never opened the project's chat"
    # Esc cancels a chooser with drafts untouched
    td = fresh_td(rows)
    keys = [ord(c) for c in "zz9"] + [27] + [ord(c) for c in "/agents"] + \
        [10, 27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys)
    assert not alive, "chooser cancel did not quit"
    print("ok test_projects_agents_choosers")


def test_charcoal_userfill_and_no_jargon():
    # abyss user bubble is neutral charcoal (not saturated green 22), text
    # rides on the same surface; compact UI carries no chars//4/off-thread
    # jargon and edit labels stay short + aligned.
    _fg, bg = ag.theme_rgb("abyss", "userfill")
    assert bg is not None and bg[0] == bg[1] == bg[2], bg  # neutral gray
    assert bg != ag._xterm256_rgb(22), "still saturated green"
    _fg2, bg2 = ag.theme_rgb("abyss", "text_u")
    assert bg2 == bg, (bg2, bg)
    _fg3, bg3 = ag.theme_rgb("abyss", "composer")
    assert bg3 is not None and bg3[0] == bg3[1] == bg3[2], bg3
    td = fresh_td()
    seen = []
    for builder in (ag.compact_screen_rows(td, "claude"),
                    ag.compact_scope_rows(td, "sub"),
                    ag.compact_advanced_rows(td, "sub"),
                    ag.compact_cadence_rows("sub", {})):
        for _k, _r, lab, val in builder:
            seen.append((lab, val))
    for lab, val in seen:
        for s in (lab, val):
            assert "chars//4" not in s and "off-thread" not in s, s
    adv = [lab.strip() for k, _r, lab, _v in
           ag.compact_advanced_rows(td, "sub") if k == "edit"]
    assert adv == ["Messages before summary", "Approximate token limit",
                   "Recent turns to keep", "Summary time limit"], adv
    assert "0..10000" in ag.compact_help_for("advanced", "adv.max_turns")
    assert "type a message" in ag.TUI_HOME_HINT, ag.TUI_HOME_HINT
    assert "/ for commands" in ag.TUI_HOME_HINT, ag.TUI_HOME_HINT
    assert "i ask" not in ag.TUI_HOME_HINT, ag.TUI_HOME_HINT
    assert "(i to type)" not in ag.TUI_HOME_HINT, ag.TUI_HOME_HINT
    print("ok test_charcoal_userfill_and_no_jargon")


def test_new_delegate_flows_keep_draft():
    # message draft typed, new/delegate opened via palette (draft-safe
    # route): capture uses its own buffer; cancel returns, draft intact.
    for filt, marker in (("delegate", "delegate: agent name"),
                         ("new agent", "new agent name")):
        td = fresh_td()
        draft = "keepme7"
        keys = ([ord(c) for c in draft] + [16] +
                [ord(c) for c in filt] + [10, 27, 27, ord("q")])
        scr, alive = run(td, 100, 30, keys)
        assert not alive, f"{filt} draft round-trip did not quit"
        texts = ["".join(f.values()) for f in scr.frames]
        assert any(marker in t for t in texts), \
            f"{filt} flow never opened"
        assert sum(1 for t in texts if draft in t) >= 2, \
            f"draft eaten by {filt} capture"
    print("ok test_new_delegate_flows_keep_draft")


def test_timeline_upsert_pure():
    st = ag.timeline_new()
    ag.timeline_upsert(st, {"event_id": "a", "role": "assistant",
                            "text": "one"})
    ag.timeline_upsert(st, {"event_id": "b", "role": "tool",
                            "tool_name": "Grep", "status": "running"})
    ag.timeline_upsert(st, {"event_id": "a", "role": "assistant",
                            "text": "one two"})
    rows = ag.timeline_rows(st)
    assert [r["event_id"] for r in rows] == ["a", "b"], rows
    assert rows[0]["text"] == "one two", rows  # replaced, not concatenated
    ag.timeline_upsert(st, {"role": "assistant", "text": "x"})
    assert len(ag.timeline_rows(st)) == 3, "missing id must append"
    print("ok test_timeline_upsert_pure")


def test_timeline_render_roles():
    rows = [
        {"role": "user", "text": "find it"},
        {"role": "assistant", "text": "looking"},
        {"role": "reasoning", "text": "check auth flow"},
        {"role": "tool", "tool_name": "Grep", "path": "src/auth",
         "status": "running"},
        {"role": "assistant", "text": "found it"},
        {"role": "tool", "tool_name": "Grep", "path": "src/auth",
         "status": "completed"},
        {"role": "toolout", "text": "src/auth/login.py:42"},
        {"role": "diff", "path": "login.py",
         "text": "+ check expiry\n- skip check\n  ctx"},
        {"role": "error", "text": "lint warning"},
        {"role": "agent", "text": "found it", "timeline_summary": True},
    ]
    dl = ag.chat_screen_lines(rows, "STREAMING LEFTOVER", "bob", 60)
    txt = "\n".join(t for _, t in dl)
    assert ("ah", "● bob") in dl, dl
    assert sum(1 for k, t in dl if (k, t) == ("ah", "● bob")) == 2, \
        dl  # one header per assistant block split by tool use, never per chunk
    assert any("Thinking" in t for k, t in dl if k == "think"), dl
    assert any("Grep" in t and "Running" in t
               for k, t in dl if k == "tool"), dl
    assert any("Done" in t for k, t in dl if k == "tool"), dl
    assert any(t.startswith("+") for k, t in dl if k == "diffadd"), dl
    assert any(t.startswith("-") for k, t in dl if k == "diffdel"), dl
    assert any("lint warning" in t for k, t in dl if k == "error"), dl
    assert "STREAMING LEFTOVER" not in txt, "stream_text double-rendered"
    assert txt.count("found it") == 1, txt  # summary aggregate suppressed
    s = ag.timeline_tool_summary(
        {"tool_name": "Bash", "text": '{"huge": "json blob"}',
         "status": "completed"}, 60)
    assert "Bash" in s and "Done" in s and "huge" not in s, s
    s2 = ag.timeline_tool_summary(
        {"tool_name": "Read", "path": "a.py", "status": "failed"}, 60)
    assert "a.py" in s2 and "Failed" in s2, s2
    s3 = ag.timeline_tool_summary(
        {"tool_name": "Grep", "text": "Grep pattern 'x' in src",
         "status": "completed"}, 60)
    assert s3.startswith("Grep pattern") and "Done" in s3, s3
    assert "Grep Grep" not in s3, s3  # engine-composed text reused as-is
    long_rows = [{"role": "assistant",
                  "text": "\n".join(f"line {i}" for i in range(20))}]
    dl2 = ag.chat_screen_lines(long_rows, "", "bob", 60, omit_cap=6)
    assert any(k == "omit" and "more lines" in t for k, t in dl2), dl2
    assert len([1 for k, t in dl2 if k == "body"]) == 6, dl2
    print("ok test_timeline_render_roles")


def test_timeline_live_upsert_no_duplicates():
    # fake backend emitting timeline rows (repeated event_ids are progress
    # updates) then persisting the same rows: each renders once, latest
    # text wins, error/diff stay visible, the summary aggregate is
    # suppressed, legacy stream never doubles the assistant.
    td = fresh_td()
    live = [
        {"event_id": "u1", "role": "user", "text": "fix login"},
        {"event_id": "a1", "role": "assistant", "text": "alpha DRAFT x"},
        {"event_id": "r1", "role": "reasoning", "text": "checking expiry"},
        {"event_id": "t1", "role": "tool", "tool_name": "Grep",
         "path": "src/auth", "status": "running"},
        {"event_id": "a1", "role": "assistant", "text": "alpha FINAL y"},
        {"event_id": "t1", "role": "tool", "tool_name": "Grep",
         "path": "src/auth", "status": "completed"},
        {"event_id": "d1", "role": "diff", "path": "login.py",
         "text": "+ check expiry\n- skip check\n  ctx"},
        {"event_id": "e1", "role": "error",
         "text": "lint warning: line too long"},
        {"event_id": "z9", "role": "agent", "text": "alpha FINAL y",
         "timeline_summary": True},
    ]

    def fake_turn(sdir, name, text, on_event=None, command=None):
        for row in live:
            on_event(name, "timeline", dict(row))
        for row in live:
            txt = row.get("text") or ag.timeline_tool_summary(row, 60)
            ag.append_chat(sdir, name, row["role"], txt)
        on_event(name, "exit", "0")
        return {"sid": "", "exit": 0, "reply": "alpha FINAL y"}

    old = ag.run_turn
    ag.run_turn = fake_turn
    try:
        scr, alive = run(td, 100, 30,
                         [ord(c) for c in "hi"] + [10, 27, ord("q")],
                         timeout=15)
    finally:
        ag.run_turn = old
    assert not alive, "timeline turn did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    full = [t for t in texts if "alpha FINAL y" in t]
    assert full, "assistant answer never displayed"
    assert all(t.count("alpha FINAL y") == 1 for t in full), \
        "duplicate answer rendered"
    assert not any("alpha DRAFT x" in t and "alpha FINAL y" in t
                   for t in texts), "stale snapshot concatenated"
    assert any("Thinking" in t for t in texts), "reasoning never shown"
    assert any("Grep" in t and "Done" in t for t in texts), \
        "tool card never settled"
    assert any("+ check expiry" in t for t in texts), "diff add line lost"
    assert any("- skip check" in t for t in texts), "diff del line lost"
    assert any("lint warning" in t for t in texts), "error line lost"
    print("ok test_timeline_live_upsert_no_duplicates")


def test_unicode_cells_no_color_ascii():
    assert ag.cell_len("abc") == 3
    assert ag.cell_len("あ") == 2
    assert ag.cell_len("é") == 1  # e + combining acute
    assert ag.cell_len("中") == 2
    assert ag.cell_trunc("あいう", 4) == "あ…"
    assert ag.cell_trunc("ab", 5) == "ab"
    assert ag.cell_wrap_line("あいうえおか", 10) == ["あいうえお", "か"]
    assert ag.cell_wrap_line("a b", 10) == ["a b"]
    old = os.environ.get("NO_COLOR")
    os.environ["NO_COLOR"] = "1"
    try:
        assert not ag.ui_colors_on()
        th = ag.theme_pairs("abyss")
        assert all(v == 0 for k, v in th.items() if k != "rounded"), th
        assert ag.select_attr(th) != 0, "no monochrome selection fallback"
    finally:
        if old is None:
            del os.environ["NO_COLOR"]
        else:
            os.environ["NO_COLOR"] = old
    assert ag.ui_colors_on()
    assert ag.G("▍") == "▍"  # default untouched
    os.environ["AG_ASCII"] = "1"
    try:
        assert ag.G("▍") == "|"
        assert ag.G("●") == "*"
        dl = ag.chat_screen_lines([{"role": "user", "text": "hi"}], "",
                                  "b", 40)
        assert dl[0] == ("uh", "* you"), dl
    finally:
        del os.environ["AG_ASCII"]
    print("ok test_unicode_cells_no_color_ascii")


def test_mouse_key_never_crashes():
    # FakeScr has no getmouse: KEY_MOUSE must degrade silently, quit cleanly.
    td = fresh_td()
    scr, alive = run(td, 80, 24, [curses.KEY_MOUSE, 27, ord("q")])
    assert not alive, "mouse key broke home"
    td = fresh_td()
    ag.append_chat(td, "claude", "user", "hi")
    scr, alive = run(td, 80, 24, [27, 10, curses.KEY_MOUSE, 27, ord("q")])
    assert not alive, "mouse key broke chat"
    print("ok test_mouse_key_never_crashes")


def test_help_lists_slash_and_no_insert_mode():
    body = "\n".join(ag.KEYS_HELP.splitlines())
    assert "/projects" in body and "/agents" in body
    assert "/compact-now" in body and "/quit" in body
    assert "Tab completes" in body and "Esc keeps draft" in body
    assert "no insert mode" in body
    assert "(i to type)" not in body and "i ask" not in body
    assert "ctrl+p" in body  # optional alias stays documented
    print("ok test_help_lists_slash_and_no_insert_mode")


def test_quit_via_slash():
    td = fresh_td()
    scr, alive = run(td, 80, 24, [ord(c) for c in "/quit"] + [10],
                     timeout=10)
    assert not alive, "/quit did not exit the TUI"
    print("ok test_quit_via_slash")


def test_compact_now_via_slash():
    td = fresh_td()
    old = ag.agent_busy
    ag.agent_busy = lambda sdir, nm: True
    try:
        scr, alive = run(td, 100, 30,
                         [ord(c) for c in "/compact-now"] + [10, 27,
                                                             ord("q")])
    finally:
        ag.agent_busy = old
    assert not alive, "compact-now round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    # busy refusal is honest: no fake 'compacting in the background'.
    assert any("is busy" in t for t in texts), \
        "compact-now busy refusal never surfaced"
    assert not any("compacting in the background" in t for t in texts), \
        "fake started message printed for a refused run"
    # success path: actually started, honestly acknowledged.
    td2 = fresh_td()
    scr2, alive2 = run(td2, 100, 30,
                       [ord(c) for c in "/compact-now"] + [10, 27,
                                                           ord("q")],
                       timeout=15)
    assert not alive2, "compact-now success round-trip did not quit"
    texts2 = ["".join(f.values()) for f in scr2.frames]
    assert any("compacting" in t for t in texts2), \
        "compact-now start never acknowledged"
    print("ok test_compact_now_via_slash")


def test_slash_consumed_before_navigation():
    # Real-PTX repro: /config Enter opens config; Esc must return to an
    # EMPTY composer (command consumed). Typing /quit Enter then quits
    # instead of gluing into '/config/quit' model prose.
    td = fresh_td()
    calls = []
    old = ag.run_turn
    ag.run_turn = lambda *a, **k: (calls.append(a[2] if len(a) > 2 else k),
                                   {"error": "must not run"})[1]
    try:
        keys = ([ord(c) for c in "/config"] + [10, 27] +
                [ord(c) for c in "/quit"] + [10])
        scr, alive = run(td, 100, 30, keys, timeout=15)
    finally:
        ag.run_turn = old
    assert not alive, "/config Esc /quit round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("configure · claude" in t for t in texts), \
        "/config never opened"
    assert calls == [], f"slash leaked into model turns: {calls}"
    log = ag.load_chat_log(td, "claude")
    assert not [r for r in log if r.get("role") == "user"], \
        f"slash persisted as a user turn: {log}"
    print("ok test_slash_consumed_before_navigation")


def test_slash_agents_consumes_then_prompt():
    # /agents Enter opens the chooser with the command consumed; Esc back
    # leaves a clean composer, so a fresh prompt sends verbatim.
    rows = [
        {"name": "A", "backend": "echo", "model": "default",
         "dir": "dir1", "role": "orchestrator"},
        {"name": "B", "backend": "echo", "model": "default",
         "dir": "dir2", "role": "sub"},
    ]
    td = fresh_td(rows)
    keys = ([ord(c) for c in "/agents"] + [10, 27] +
            [ord(c) for c in "hey7"] + [10, 27, ord("q")])
    scr, alive = run(td, 100, 30, keys, timeout=15)
    assert not alive, "agents/prompt round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("agents (enter switch)" in t for t in texts), \
        "agents chooser never opened"
    assert any("hey7" in t for t in texts), "fresh prompt never sent"
    assert not any("/agentshey7" in t.replace(" ", "") for t in texts), \
        "stale command glued onto the new prompt"
    print("ok test_slash_agents_consumes_then_prompt")


def test_slash_grammar_commands_and_paths():
    # every canonical command parses (slash + backslash alias); unknown
    # hyphenated words parse so they error locally; a glued slash on a
    # KNOWN word (/config/quit) parses so it errors locally; absolute
    # paths and // comments stay prose.
    for e in ag.SLASH_COMMANDS:
        want = ag.SLASH_ALIASES.get(e["cmd"], e["cmd"])
        pr = ag.parse_slash("/" + e["cmd"])
        assert pr is not None and pr[0] == want, (e, pr)
        pr2 = ag.parse_slash("\\" + e["cmd"])
        assert pr2 is not None and pr2[0] == want, (e, pr2)
    assert ag.parse_slash("/compact-now") == ("compact-now", "")
    assert ag.parse_slash("/model opus") == ("model", "opus")
    fb = ag.parse_slash("/foo-bar")
    assert fb is not None and fb[0] == "foo-bar", fb  # unknown -> local err
    mc = ag.parse_slash("/config/quit")
    assert mc is not None, "/config/quit must not become model prose"
    assert ag.parse_slash("/Users/me/file") is None, "abs path hijacked"
    assert ag.parse_slash("// comment") is None, "// hijacked"
    assert ag.parse_slash("see /x") is None, "mid-sentence / hijacked"
    assert ag.parse_slash("/") is None
    assert ag.parse_slash("/ compact") is None
    print("ok test_slash_grammar_commands_and_paths")


def test_slash_malformed_command_errors_locally():
    # glued '/config/quit' errors as an unknown command: visible err, no
    # user turn, never model prose.
    td = fresh_td()
    calls = []
    old = ag.run_turn
    ag.run_turn = lambda *a, **k: (calls.append(True),
                                   {"error": "must not run"})[1]
    try:
        scr, alive = run(td, 100, 30,
                         [ord(c) for c in "/config/quit"] + [10, 27,
                                                             ord("q")],
                         timeout=15)
    finally:
        ag.run_turn = old
    assert not alive, "malformed slash round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("/config/quit err" in t for t in texts), \
        "malformed command never errored locally"
    assert calls == [], "malformed command reached the model"
    log = ag.load_chat_log(td, "claude")
    assert not [r for r in log if r.get("role") == "user"], \
        f"malformed command persisted as a user turn: {log}"
    print("ok test_slash_malformed_command_errors_locally")


def test_busy_submit_keeps_draft():
    # rejected submit: visible busy error, unsent draft preserved, no turn.
    td = fresh_td()
    calls = []
    old_run, old_busy = ag.run_turn, ag.agent_busy
    ag.run_turn = lambda *a, **k: (calls.append(True),
                                   {"error": "must not run"})[1]
    ag.agent_busy = lambda sdir, nm: True
    try:
        scr, alive = run(td, 100, 30,
                         [ord(c) for c in "hello9"] + [10, 27, ord("q")],
                         timeout=15)
    finally:
        ag.run_turn, ag.agent_busy = old_run, old_busy
    assert not alive, "busy round-trip did not quit"
    assert calls == [], "busy agent still got a turn"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("is busy" in t for t in texts), "busy error never shown"
    assert any("hello9" in t for t in texts), "unsent draft was dropped"
    log = ag.load_chat_log(td, "claude")
    assert not [r for r in log if r.get("role") == "user"], \
        f"refused submit persisted a user turn: {log}"
    print("ok test_busy_submit_keeps_draft")


def test_slash_scroll_window_and_down15():
    # pure window math: selection never scrolls out of sight, mouse hit
    # pos = off + (my - by - 1) stays inside the rendered slice.
    assert ag.slash_visible_window(15, 0, 10, 17) == 6
    assert ag.slash_visible_window(0, 6, 10, 17) == 0
    assert ag.slash_visible_window(9, 0, 10, 17) == 0  # last visible stays
    assert ag.slash_visible_window(10, 0, 10, 17) == 1
    # live: 15 Downs keeps the selected row on screen.
    td = fresh_td()
    keys = [ord("/")] + [curses.KEY_DOWN] * 15 + [27, 27, ord("q")]
    scr, alive = run(td, 100, 30, keys, timeout=15)
    assert not alive, "slash scroll round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    assert any("› /help" in t for t in texts), \
        "Down15 selection scrolled out of sight"
    print("ok test_slash_scroll_window_and_down15")


def test_unicode_grapheme_clusters():
    fam = "👨‍👩‍👧‍👦"
    cls = ag._cell_clusters(fam)
    assert len(cls) == 1, cls  # ZWJ-joined family is one cluster
    assert ag.cell_len(fam) == 2, cls
    toned = "👍🏽"
    assert len(ag._cell_clusters(toned)) == 1, toned  # skin tone glued
    assert ag.cell_len("é") == 1  # combining mark glued
    assert ag.cell_len("中") == 2
    assert ag.cell_wrap_line("あいうえおか", 10) == ["あいうえお", "か"]
    # every touched message row is cell-aware: 10 CJK chars = 20 cells.
    segs = ag.wrap_chat_line("中" * 10, 10)
    assert segs == ["中" * 5, "中" * 5], segs
    assert all(ag.cell_len(s) <= 10 for s in segs), segs
    print("ok test_unicode_grapheme_clusters")


def test_chat_view_dedup_before_window():
    # snapshots dedup by event_id BEFORE the last-N slice; legacy rows
    # without event_id are preserved, never dropped.
    td = fresh_td()
    lp = ag.chat_log_path(td, "claude")
    rows = [{"ts": "t", "role": "user", "text": "u1"}]
    rows.append({"ts": "t", "event_id": "a1", "role": "assistant",
                 "text": "draft"})
    rows.append({"ts": "t", "event_id": "a1", "role": "assistant",
                 "text": "final"})
    rows.append({"ts": "t", "role": "tool", "text": "legacy note"})
    rows.append({"ts": "t", "role": "user", "text": "u2"})
    lp.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    view = ag.load_chat_view(td, "claude", limit=3)
    assert [r.get("text") for r in view] == ["final", "legacy note", "u2"], \
        view  # dedup first (u1 trimmed), legacy kept, latest wins
    print("ok test_chat_view_dedup_before_window")


def test_summary_same_turn_filter():
    # a flagged aggregate hides only when its OWN turn has assistant rows;
    # a legacy aggregate from another turn still shows.
    rows = [
        {"role": "user", "text": "q1"},
        {"role": "assistant", "text": "a1"},
        {"role": "agent", "text": "a1", "timeline_summary": True},
        {"role": "user", "text": "q2"},
        {"role": "agent", "text": "legacy answer"},
    ]
    dl = ag.chat_screen_lines(rows, "", "bob", 60)
    txt = "\n".join(t for _, t in dl)
    assert txt.count("a1") == 1, txt  # same-turn aggregate suppressed
    assert "legacy answer" in txt, txt  # other turn still shows
    rows2 = [{"role": "user", "text": "q"},
             {"role": "agent", "text": "solo", "timeline_summary": True}]
    dl2 = ag.chat_screen_lines(rows2, "", "bob", 60)
    assert "solo" in "\n".join(t for _, t in dl2), dl2
    print("ok test_summary_same_turn_filter")


def test_cursor_visibility():
    # composer focus shows the cursor; navigation/dialogs hide it.
    rec = []
    old = curses.curs_set
    curses.curs_set = lambda v: rec.append(v)
    try:
        td = fresh_td()
        scr, alive = run(td, 80, 24, [27, ord("q")])
        assert not alive, "cursor home round-trip did not quit"
        assert 1 in rec, f"cursor never shown at composer: {rec}"
        rec.clear()
        td2 = fresh_td()
        scr2, alive2 = run(td2, 80, 24, [16, 27, 27, ord("q")])
        assert not alive2, "cursor palette round-trip did not quit"
        assert 0 in rec, f"cursor never hidden in dialog: {rec}"
    finally:
        curses.curs_set = old
    print("ok test_cursor_visibility")


def test_legacy_tool_gated_by_timeline():
    # one invocation emitting both a timeline tool row and a legacy tool
    # callback renders once, never as card + legacy line.
    td = fresh_td()

    def fake_turn(sdir, name, text, on_event=None, command=None):
        on_event(name, "timeline",
                 {"event_id": "t1", "role": "tool", "tool_name": "Grep",
                  "text": "Grep xyz", "status": "completed"})
        time.sleep(0.5)
        on_event(name, "tool", "Grep xyz")
        time.sleep(0.5)
        on_event(name, "exit", "0")
        return {"sid": "", "exit": 0, "reply": "Grep xyz"}

    old = ag.run_turn
    ag.run_turn = fake_turn
    try:
        scr, alive = run(td, 100, 30,
                         [ord(c) for c in "go"] + [10, 27, ord("q")],
                         timeout=15)
    finally:
        ag.run_turn = old
    assert not alive, "gated tool round-trip did not quit"
    texts = ["".join(f.values()) for f in scr.frames]
    live = [t for t in texts if "Done" in t and "Grep xyz" in t]
    assert live, "tool card never displayed"
    assert all(t.count("Grep xyz") == 1 for t in live), \
        "legacy tool callback double-showed the timeline card"
    print("ok test_legacy_tool_gated_by_timeline")


TESTS = [test_breakpoints, test_wrap_bounds, test_labels_and_empty_state,
         test_friendly_model_and_wordmark, test_palette_filter_pure,
         test_bounded_color_surfaces,
         test_palette_opens_filters_and_runs,
         test_home_prompt_submit_opens_chat, test_draft_survives_dialogs,
         test_config_field_select_opens_picker, test_narrow_session_layout,
         test_compact_roles_independent,
         test_compact_overview_first_screen,
         test_compact_scope_flow_saved_and_back,
         test_compact_agent_inherit_and_reset,
         test_compact_preset_preserves_disabled,
         test_compact_palette_nav,
         test_compact_advanced_range_error_cancel,
         test_compact_manual_busy_failure_noop, test_context_view_opens,
         test_readkey_separation, test_arrows_not_inserted,
         test_render_widths, test_home_nav_into_chat_and_back,
         test_home_groups_and_status, test_home_agent_line_and_config,
         test_config_overlay_opens, test_home_flat_order_maps_to_roster,
         test_home_starts_composing, test_home_tab_cycles,
         test_norm_workdir_groups, test_config_overlay_wraps_narrow,
         test_model_picker_fits_spark,
         test_model_picker_lists_spark_default,
          test_opencode_argv_and_handoff_carry_spark,
          test_switch_and_add_materialize_spark,
          test_compact_narrow_geometry,
          test_config_length_returns_to_config,
          test_preset_effects_visible_before_enter,
          test_advanced_edit_replaces_prefill,
          test_compact_skips_headers,
          test_slash_compact_labels_and_aliases,
          test_config_labels_back_help_and_draft,
          test_tool_without_status_shows_no_running,
          test_home_long_list_keeps_selected_visible,
          test_charcoal_userfill_and_no_jargon,
          test_slash_popup_filter_pure, test_slash_complete_pure,
          test_slash_popup_rect_sizes,
          test_slash_popup_opens_and_esc_keeps_draft,
          test_slash_tab_completes_and_enter_runs,
          test_slash_typed_args_not_shadowed,
          test_slash_empty_match_and_unknown,
          test_slash_in_prose_stays_literal,
          test_slash_backspace_dismiss_and_reopen,
          test_config_context_compact_via_slash,
          test_projects_agents_choosers,
          test_new_delegate_flows_keep_draft,
          test_timeline_upsert_pure, test_timeline_render_roles,
          test_timeline_live_upsert_no_duplicates,
          test_unicode_cells_no_color_ascii,
          test_mouse_key_never_crashes,
          test_help_lists_slash_and_no_insert_mode,
          test_quit_via_slash, test_compact_now_via_slash,
          test_slash_consumed_before_navigation,
          test_slash_agents_consumes_then_prompt,
          test_slash_grammar_commands_and_paths,
          test_slash_malformed_command_errors_locally,
          test_busy_submit_keeps_draft,
          test_slash_scroll_window_and_down15,
          test_unicode_grapheme_clusters,
          test_chat_view_dedup_before_window,
          test_summary_same_turn_filter, test_cursor_visibility,
          test_legacy_tool_gated_by_timeline]

if __name__ == "__main__":
    want = sys.argv[1:]
    picked = [fn for fn in TESTS if not want or fn.__name__ in want]
    if want and len(picked) != len(want):
        missing = [n for n in want if n not in [fn.__name__ for fn in TESTS]]
        print(f"unknown tests: {missing}")
        sys.exit(2)
    fails = 0
    for fn in picked:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            fails += 1
            print(f"FAIL {fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"{len(picked) - fails}/{len(picked)} passed")
    sys.exit(1 if fails else 0)
