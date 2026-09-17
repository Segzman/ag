#!/usr/bin/env python3
"""Streaming timeline tests: honest incremental provider events.

Fake executables emit NDJSON with delays (no paid models). Covers:
pre-exit ordered callbacks, delta concatenation, tool update dedup by
event_id, error-after-partial-text, reload equivalence, bounded outputs
with omission labels, diff rows, legacy parse_events/callback compat,
recent_history + compaction exclusion.

Provider fixture shapes mirror primary sources (see provenance notes):
- opencode run.ts emit(): {type,timestamp,sessionID,...}; text/reasoning
  fire only on part.time.end; tool_use only at completed/error.
  (https://raw.githubusercontent.com/anomalyco/opencode/dev/packages/opencode/src/cli/cmd/run.ts,
  installed opencode 1.18.23 --help for --thinking/--format)
- claude stream-json block shapes + stream_event deltas
  (installed claude --help: --include-partial-messages, --output-format)
- cursor agent --stream-partial-output (installed agent --help)

Run: python3 tests/test_ag_streaming.py
Env: AG_BIN overrides live ag path (default: repo ag).
"""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

AG = Path(os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag")))
assert AG.exists(), f"missing ag: {AG}"

spec = importlib.util.spec_from_loader("agmod", loader=None)
ag = importlib.util.module_from_spec(spec)
ag.__file__ = str(AG)
exec(AG.read_text(), ag.__dict__)

PASS = 0
def check(name, cond, extra=""):
    global PASS
    if not cond:
        raise AssertionError(f"FAIL {name} {extra}")
    PASS += 1
    print(f"ok {name}")

def fresh_state(rows):
    td = Path(tempfile.mkdtemp(prefix="agstream-state-"))
    (td / "agents.json").write_text(json.dumps(rows))
    return td

def mkproj():
    return Path(tempfile.mkdtemp(prefix="agstream-proj-"))

def agent_row(name, backend, proj):
    return {"name": name, "backend": backend, "model": "", "dir": str(proj),
            "role": "sub", "persona": "", "system": ""}

def fixture_script(lines, exit_code=0, stderr_tail="", line_delay=0.15):
    """Executable shell script emitting NDJSON lines with delays, then exit."""
    fd, p = tempfile.mkstemp(prefix="agstream-fx-", suffix=".sh")
    body = ["#!/bin/sh"]
    for ln in lines:
        body.append("printf '%%s\\n' '%s'" % (ln.replace("'", "'\\''"),))
        body.append("sleep %.2f" % line_delay)
    if stderr_tail:
        body.append("printf '%%s\\n' '%s' >&2" % (stderr_tail.replace("'", "'\\''"),))
    body.append("exit %d" % exit_code)
    os.write(fd, ("\n".join(body) + "\n").encode())
    os.close(fd)
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    return p

def patched_argv(script):
    orig = ag.backend_argv
    def fake(backend, **kw):
        return [script]
    ag.backend_argv = fake
    return orig

def run_turn_thread(st, name, text, seen, timeline, lock):
    def cb(n, kind, payload):
        with lock:
            if kind == "timeline":
                timeline.append((time.monotonic(), dict(payload)))
            else:
                seen.append((kind, payload if not isinstance(payload, dict) else dict(payload)))
    return cb

# ---------- 1. ordered tool/assistant interleaving, visible pre-exit ----------

def test_ordered_interleave_preexit():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("s", "opencode", proj)]))
    lines = [
        json.dumps({"type": "text", "timestamp": 1, "sessionID": "ses-ord1",
            "part": {"type": "text", "text": "starting work"}}),
        json.dumps({"type": "tool_use", "timestamp": 2, "sessionID": "ses-ord1",
            "part": {"type": "tool", "id": "t-1", "tool": "Edit",
                "state": {"status": "completed", "title": "Edit a.py",
                    "input": {"file_path": "a.py", "old_string": "x=1\n",
                        "new_string": "x=2\n"}}}}),
        json.dumps({"type": "text", "timestamp": 3, "sessionID": "ses-ord1",
            "part": {"type": "text", "text": "done editing"}}),
    ]
    fx = fixture_script(lines, line_delay=0.4)
    orig = patched_argv(fx)
    seen, timeline, lock = [], [], threading.Lock()
    cb = run_turn_thread(st, "s", "go", seen, timeline, lock)
    out = {}
    th = threading.Thread(target=lambda: out.update(r=ag.run_turn(st, "s", "go", on_event=cb)))
    th.start()
    deadline = time.monotonic() + 15
    while th.is_alive() and time.monotonic() < deadline:
        with lock:
            n = len(timeline)
        if n >= 2:
            break
        time.sleep(0.05)
    with lock:
        early = list(timeline)
    check("pre-exit-callbacks", len(early) >= 2 and th.is_alive(), f"early={len(early)} alive={th.is_alive()}")
    th.join(timeout=20)
    check("turn-done", not th.is_alive() and "error" not in out.get("r", {}), out.get("r"))
    r = out["r"]
    check("reply-has-both", "starting work" in r["reply"] and "done editing" in r["reply"], r["reply"][:200])
    check("sid-captured", r["sid"] == "ses-ord1", r)
    with lock:
        full_tl = list(timeline)
    order = []
    seen_ids = set()
    for _, row in full_tl:
        if row["event_id"] not in seen_ids:
            seen_ids.add(row["event_id"])
            order.append(row["role"])
    check("interleave-order", order[:6] == ["user", "assistant", "tool", "diff", "assistant", "agent"], order)
    kinds = [k for k, _ in seen]
    check("legacy-callbacks", "text" in kinds and "exit" in kinds, kinds)
    ag.backend_argv = orig

# ---------- 2. delta concatenation without mid-token newlines ----------

def test_delta_concat():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("d", "cursor", proj)]))
    unit = "TOKEN-" * 100  # 600 chars
    want = unit * 6        # 3600 chars -> several snapshot emits
    parts = [want[i:i + 100] for i in range(0, len(want), 100)]
    lines = [json.dumps({"type": "text_delta", "seg": "s1", "delta": p}) for p in parts]
    fx = fixture_script(lines, line_delay=0.02)
    orig = patched_argv(fx)
    seen, timeline, lock = [], [], threading.Lock()
    r = ag.run_turn(st, "d", "go", on_event=run_turn_thread(st, "d", "go", seen, timeline, lock))
    ag.backend_argv = orig
    check("delta-reply", "error" not in r and r["reply"] == want, r.get("reply", "")[:80])
    assistants = [row for _, row in timeline if row["role"] == "assistant"]
    check("delta-one-row", len({row["event_id"] for row in assistants}) == 1, len(assistants))
    check("delta-concat", assistants[-1]["text"] == want and ("\n" not in assistants[-1]["text"]),
        repr(assistants[-1]["text"][:60]))
    snaps = [row["text"] for row in assistants]
    check("delta-growing", len(snaps) >= 2 and all(snaps[i] in snaps[i + 1] for i in range(len(snaps) - 1)),
        [len(s) for s in snaps])
    tl = ag.load_timeline(st, "d")
    a = [x for x in tl if x["role"] == "assistant"]
    check("delta-reload", len(a) == 1 and a[0]["text"] == want, [len(x["text"]) for x in a])

# ---------- 3. tool update dedup (partial input + full block, one row) ----------

def test_tool_update_dedup():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("c", "claude", proj)]))
    full_input = {"file_path": "a.py", "old_string": "x=1\n", "new_string": "x=2\n"}
    lines = [
        json.dumps({"type": "stream_event", "event": {"type": "content_block_start",
            "index": 1, "content_block": {"type": "tool_use", "id": "toolu-1", "name": "Edit"}}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
            "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"file_p'}}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "toolu-1", "name": "Edit", "input": full_input}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu-1", "content": "ok"}]}}),
    ]
    fx = fixture_script(lines, line_delay=0.2)
    orig = patched_argv(fx)
    seen, timeline, lock = [], [], threading.Lock()
    r = ag.run_turn(st, "c", "go", on_event=run_turn_thread(st, "c", "go", seen, timeline, lock))
    ag.backend_argv = orig
    check("claude-turn-ok", "error" not in r, r)
    tl = ag.load_timeline(st, "c")
    tools = [x for x in tl if x["role"] == "tool"]
    check("tool-single-row", len(tools) == 1, [(t.get("tool_id"), t["text"][:60]) for t in tools])
    check("tool-id-bound", tools[0].get("tool_id") == "toolu-1", tools[0])
    check("tool-completed", tools[0].get("status") == "completed", tools[0])
    diffs = [x for x in tl if x["role"] == "diff"]
    # one diff row (status transitions upsert the same event_id); the
    # successful tool_result confirms the edit, so proposal -> applied.
    check("diff-proposal-then-applied", len(diffs) == 1 and "-x=1" in diffs[0]["text"]
        and "+x=2" in diffs[0]["text"] and diffs[0].get("path") == "a.py"
        and diffs[0].get("status") == "applied"
        and "applied (provider-confirmed)" in diffs[0]["text"], diffs)
    raw_diff_rows = [l for l in (st / "chats" / "c.jsonl").read_text().splitlines()
        if json.loads(l).get("role") == "diff"]
    check("diff-same-eid", len({json.loads(l)["event_id"] for l in raw_diff_rows}) == 1,
        raw_diff_rows)
    legacy_tools = [t for k, t in seen if k == "tool"]
    check("legacy-tool-once", len(legacy_tools) == 1, legacy_tools)

# ---------- 4. error after partial text ----------

def test_error_after_partial():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("e", "opencode", proj)]))
    lines = [
        json.dumps({"type": "text", "timestamp": 1, "sessionID": "ses-err1",
            "part": {"type": "text", "text": "partial prose here"}}),
        json.dumps({"type": "tool_use", "timestamp": 2, "sessionID": "ses-err1",
            "part": {"type": "tool", "id": "t-9", "tool": "Bash",
                "state": {"status": "error", "title": "run tests", "output": "boom"}}}),
    ]
    fx = fixture_script(lines, exit_code=1, stderr_tail="fatal: kv exploded", line_delay=0.2)
    orig = patched_argv(fx)
    seen, timeline, lock = [], [], threading.Lock()
    r = ag.run_turn(st, "e", "go", on_event=run_turn_thread(st, "e", "go", seen, timeline, lock))
    ag.backend_argv = orig
    check("exit-1", r.get("exit") == 1, r)
    tl = ag.load_timeline(st, "e")
    by_role = {}
    for x in tl:
        by_role.setdefault(x["role"], []).append(x)
    check("partial-survives", any("partial prose" in x["text"] for x in by_role.get("assistant", [])), by_role.keys())
    check("tool-failed", any(x.get("status") == "failed" for x in by_role.get("tool", [])), by_role.get("tool"))
    check("error-row", any("(exit 1)" in x["text"] for x in by_role.get("error", [])), by_role.get("error"))
    agg = [x for x in tl if x["role"] == "agent"]
    check("aggregate-hidden-dup", len(agg) == 1 and agg[0].get("timeline_summary") is True, agg)
    # failure with NO assistant segments: aggregate still displays (no flag)
    st2 = fresh_state([])
    (st2 / "agents.json").write_text(json.dumps([agent_row("e2", "opencode", proj)]))
    fx2 = fixture_script([
        json.dumps({"type": "tool_use", "timestamp": 1, "sessionID": "ses-err2",
            "part": {"type": "tool", "id": "t-8", "tool": "Bash",
                "state": {"status": "error", "title": "x", "output": "nope"}}})],
        exit_code=1, stderr_tail="kablam", line_delay=0.1)
    orig2 = patched_argv(fx2)
    r2 = ag.run_turn(st2, "e2", "go")
    ag.backend_argv = orig2
    tl2 = ag.load_timeline(st2, "e2")
    agg2 = [x for x in tl2 if x["role"] == "agent"]
    check("aggregate-shown-no-assistant",
        len(agg2) == 1 and "timeline_summary" not in agg2[0] and "(exit 1)" in agg2[0]["text"], agg2)

# ---------- 5. reload equivalence + aggregate hiding ----------

def test_reload_equivalence():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("q", "opencode", proj)]))
    lines = [
        json.dumps({"type": "reasoning", "timestamp": 1, "sessionID": "ses-rl1",
            "part": {"type": "reasoning", "text": "thinking about it"}}),
        json.dumps({"type": "text", "timestamp": 2, "sessionID": "ses-rl1",
            "part": {"type": "text", "text": "final words"}}),
    ]
    fx = fixture_script(lines, line_delay=0.1)
    orig = patched_argv(fx)
    seen, timeline, lock = [], [], threading.Lock()
    ag.run_turn(st, "q", "go", on_event=run_turn_thread(st, "q", "go", seen, timeline, lock))
    ag.backend_argv = orig
    final = {}
    for _, row in timeline:
        final[row["event_id"]] = row
    tl = ag.load_timeline(st, "q")
    check("same-ids", {x["event_id"] for x in tl} == set(final), [x["event_id"] for x in tl])
    for x in tl:
        f = final[x["event_id"]]
        check(f"equiv-{x['role']}", x["text"] == f["text"] and x["role"] == f["role"], x)
    agg = [x for x in tl if x["role"] == "agent"][0]
    check("aggregate-hidden", agg.get("timeline_summary") is True, agg)
    reasoning = [x for x in tl if x["role"] == "reasoning"]
    check("reasoning-row", len(reasoning) == 1 and reasoning[0]["text"] == "thinking about it", reasoning)

# ---------- 6. bounded long output ----------

def test_bounded_output():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("b", "cursor", proj)]))
    big = "Z" * 50000
    fx = fixture_script([json.dumps({"type": "text_delta", "seg": "big", "delta": big})])
    orig = patched_argv(fx)
    r = ag.run_turn(st, "b", "go")
    ag.backend_argv = orig
    check("big-ok", "error" not in r, r)
    tl = ag.load_timeline(st, "b")
    a = [x for x in tl if x["role"] == "assistant"]
    check("big-one-row", len(a) == 1, len(a))
    check("big-capped", len(a[0]["text"]) <= 8000, len(a[0]["text"]))
    check("big-omission", "truncated" in a[0]["text"] and "omitted" in a[0]["text"],
        a[0]["text"][-120:])

# ---------- 7. legacy / context / compaction compat ----------

def test_legacy_compat():
    # parse_events untouched shapes
    cl = ag.parse_events("claude", json.dumps({"type": "assistant",
        "message": {"content": [{"type": "text", "text": "hi"}]}}))
    check("legacy-claude", cl == [("text", "hi")], cl)
    oc = ag.parse_events("opencode", json.dumps({"type": "tool_use",
        "part": {"type": "tool", "tool": "Bash",
            "state": {"status": "completed", "title": "ls", "output": "x"}}}))
    check("legacy-opencode", ("tool", "Bash ls") in oc and ("toolout", "x") in oc, oc)
    g = ag.parse_events("echo", "plain line")
    check("legacy-plain", g == [("text", "plain line")], g)
    # unknown JSON never becomes giant assistant prose in timeline
    unk = ag.parse_timeline("cursor", json.dumps({"weird": {"nested": [1, 2, {"x": "y"}]}}))
    check("unknown-suppressed", unk == [], unk)
    # argv keeps model/profile/session contracts + new verified flags
    av = ag.backend_argv("opencode", model="", workdir=".", sid="S1", prompt="hi")
    check("opencode-flags", "--thinking" in av and "--session" in av and "S1" in av, av)
    av = ag.backend_argv("claude", model="", workdir=".", sid="S2", prompt="hi", system="SYS")
    check("claude-flags", "--include-partial-messages" in av and "--resume" in av and "S2" in av, av)
    av = ag.backend_argv("cursor", model="", workdir=".", sid="", prompt="hi")
    check("cursor-flags", "--stream-partial-output" in av, av)

def test_replay_compaction_exclusion():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("h", "echo", proj)]))
    r1 = ag.run_turn(st, "h", "first")
    check("echo-turn", "error" not in r1, r1)
    lines = [
        json.dumps({"type": "reasoning", "timestamp": 1, "sessionID": "ses-h1",
            "part": {"type": "reasoning", "text": "REASON-Y"}}),
        json.dumps({"type": "text", "timestamp": 2, "sessionID": "ses-h1",
            "part": {"type": "text", "text": "PROSE-X"}})]
    (st / "agents.json").write_text(json.dumps([agent_row("h", "opencode", proj)]))
    fx = fixture_script(lines)
    orig = patched_argv(fx)
    r2 = ag.run_turn(st, "h", "second")
    ag.backend_argv = orig
    check("stream-turn", "error" not in r2, r2)
    h = ag.recent_history(st, "h")
    # canonical agent aggregate replays once; display rows never duplicate it
    check("replay-once", h.count("PROSE-X") == 1 and "second" in h, h[-200:])
    check("replay-no-reasoning", "REASON-Y" not in h, h[-200:])
    stats = ag.compact_stats(st, "h")
    check("compact-counts-users", stats.get("user_turns") == 2, stats)
    tl = ag.load_timeline(st, "h")
    check("timeline-kept", any(x["role"] == "assistant" for x in tl), [x["role"] for x in tl])

def test_user_row_timeline():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("u", "echo", proj)]))
    seen, timeline, lock = [], [], threading.Lock()
    ag.run_turn(st, "u", "hello-user", on_event=run_turn_thread(st, "u", "hello", seen, timeline, lock))
    users = [row for _, row in timeline if row["role"] == "user"]
    # UI upserts by event_id: final re-emit shares the id, never duplicates
    check("user-timeline", len({u["event_id"] for u in users}) == 1
        and users[0]["text"] == "hello-user", users)
    log = ag.load_chat_log(st, "u")
    stored = [x for x in log if x["role"] == "user"]
    check("user-stable-id", len(stored) == 1 and stored[0].get("event_id") == users[0]["event_id"], stored)

# ---------- 10. real opencode schemas: camelCase + metadata diff ----------

def test_real_opencode_diff_schema():
    # edit.ts Parameters are filePath/oldString/newString (camelCase);
    # completed state.metadata carries {diff, filediff:{file,patch,...}}.
    confirmed = "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x=1\n+x=2"
    lines = [
        json.dumps({"type": "tool_use", "timestamp": 1, "sessionID": "ses-real1",
            "part": {"type": "tool", "id": "t-r1", "tool": "edit",
                "state": {"status": "completed", "title": "f.py",
                    "input": {"filePath": "f.py", "oldString": "x=1\n",
                        "newString": "x=2\n"},
                    "metadata": {"diff": confirmed,
                        "filediff": {"file": "f.py", "patch": confirmed,
                            "additions": 1, "deletions": 1}},
                    "output": "Edit applied successfully."}}}),
    ]
    ops = ag.parse_timeline_opencode(lines[0])
    check("camel-input", ops and ops[0].get("args", {}).get("filePath") == "f.py", ops)
    d = ag.tl_diff_for_tool(ops[0]["tool_name"], ops[0]["args"], ops[0].get("meta"))
    check("meta-confirmed-verbatim", d and d[0] == confirmed and d[1] == "f.py" and d[2] is True, d)
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("r", "opencode", proj)]))
    fx = fixture_script(lines, line_delay=0.1)
    orig = patched_argv(fx)
    r = ag.run_turn(st, "r", "go")
    ag.backend_argv = orig
    check("real-turn-ok", "error" not in r, r)
    tl = ag.load_timeline(st, "r")
    diffs = [x for x in tl if x["role"] == "diff"]
    check("real-diff-applied", len(diffs) == 1 and diffs[0]["text"].endswith(confirmed)
        and diffs[0].get("status") == "applied"
        and "applied (provider-confirmed)" in diffs[0]["text"], diffs)

def test_apply_patch_and_state_error():
    # apply_patch Parameters carry patchText; metadata.files[{patch}].
    patch = "@@ -1 +1 @@\n-old\n+new"
    d = ag.tl_diff_for_tool("apply_patch", {"patchText": patch}, None)
    check("patchtext-fallback", d and patch in d[0] and d[2] is False, d)
    d = ag.tl_diff_for_tool("apply_patch", {}, {"diff": "D",
        "files": [{"relativePath": "g.py", "patch": patch}]})
    check("metadata-files", d and d[0] == patch and d[1] == "g.py" and d[2] is True, d)
    # error status with state.error and NO output still surfaces a body.
    ops = ag.parse_timeline_opencode(json.dumps({"type": "tool_use",
        "timestamp": 1, "sessionID": "ses-e1",
        "part": {"type": "tool", "id": "t-e", "tool": "Bash",
            "state": {"status": "error", "title": "run",
                "error": "File f.py not found"}}}))
    kinds = [o["op"] for o in ops]
    check("state-error-body", "toolout" in kinds and "error" in kinds
        and any("not found" in o.get("text", "") for o in ops), ops)
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("se", "opencode", proj)]))
    fx = fixture_script([json.dumps({"type": "tool_use", "timestamp": 1,
        "sessionID": "ses-e2", "part": {"type": "tool", "id": "t-e2",
            "tool": "Bash", "state": {"status": "error", "title": "run",
                "error": "SEGFAULT-VISIBLE"}}})], line_delay=0.1)
    orig = patched_argv(fx)
    ag.run_turn(st, "se", "go")
    ag.backend_argv = orig
    tl = ag.load_timeline(st, "se")
    check("state-error-persisted",
        any("SEGFAULT-VISIBLE" in x["text"] for x in tl if x["role"] == "toolout"), tl)

# ---------- 11. short deltas visible pre-exit (time cadence, not 2000 chars) ----------

def test_short_delta_preexit():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("p", "cursor", proj)]))
    lines = [
        json.dumps({"type": "text_delta", "seg": "s1", "delta": "HELLO"}),
        json.dumps({"type": "text_delta", "seg": "s1", "delta": "-WORLD"}),
    ]
    fx = fixture_script(lines, line_delay=0.6)
    orig = patched_argv(fx)
    seen, timeline, lock = [], [], threading.Lock()
    out = {}
    th = threading.Thread(target=lambda: out.update(
        r=ag.run_turn(st, "p", "go", on_event=run_turn_thread(st, "p", "go", seen, timeline, lock))))
    th.start()
    time.sleep(0.9)  # first line flushed, child still sleeping on line 2
    with lock:
        early = [dict(r) for _, r in timeline if r["role"] == "assistant"]
    check("5char-preexit", th.is_alive() and any("HELLO" in r["text"] for r in early),
        (len(early), th.is_alive()))
    th.join(timeout=20)
    ag.backend_argv = orig
    check("short-turn-done", not th.is_alive() and "error" not in out.get("r", {}), out.get("r"))
    check("short-concat", out["r"]["reply"] == "HELLO-WORLD", out["r"]["reply"][:80])
    tl = ag.load_timeline(st, "p")
    a = [x for x in tl if x["role"] == "assistant"]
    check("short-one-logical", len(a) == 1 and a[0]["text"] == "HELLO-WORLD", a)

# ---------- 12. claude parallel tools + repeated indices across messages ----------

def test_claude_parallel_tools():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("m", "claude", proj)]))
    lines = [
        json.dumps({"type": "stream_event", "event": {"type": "content_block_start",
            "index": 0, "content_block": {"type": "tool_use", "id": "tu-A", "name": "Edit"}}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_start",
            "index": 1, "content_block": {"type": "tool_use", "id": "tu-B", "name": "Bash"}}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
            "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"filePath": "a'}}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
            "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"command": "ls'}}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu-A", "name": "Edit",
                "input": {"filePath": "a.py", "oldString": "x=1\n", "newString": "x=2\n"}},
            {"type": "tool_use", "id": "tu-B", "name": "Bash",
                "input": {"command": "ls"}},
            {"type": "text", "text": "PROSE-AFTER-TOOLS"}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu-A", "content": "edited"},
            {"type": "tool_result", "tool_use_id": "tu-B", "content": "a.py"}]}}),
        # second message reuses block index 0 for an unrelated tool
        json.dumps({"type": "stream_event", "event": {"type": "content_block_start",
            "index": 0, "content_block": {"type": "tool_use", "id": "tu-C", "name": "Read"}}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu-C", "name": "Read",
                "input": {"filePath": "b.py"}}]}}),
    ]
    fx = fixture_script(lines, line_delay=0.05)
    orig = patched_argv(fx)
    r = ag.run_turn(st, "m", "go")
    ag.backend_argv = orig
    check("parallel-ok", "error" not in r, r)
    check("prose-after-tools", r["reply"].count("PROSE-AFTER-TOOLS") == 1, r["reply"][:200])
    tl = ag.load_timeline(st, "m")
    tools = [x for x in tl if x["role"] == "tool"]
    check("parallel-3-rows", len(tools) == 3
        and {t.get("tool_id") for t in tools} == {"tu-A", "tu-B", "tu-C"}, tools)
    check("parallel-completed", all(t.get("status") == "completed" for t in tools
        if t.get("tool_id") in ("tu-A", "tu-B")), tools)

# ---------- 13. truncation markers, unknown-json drop, legacy, persistence ----------

def test_truncation_markers():
    big_out = "Y" * 900
    ops = ag.parse_timeline_opencode(json.dumps({"type": "tool_use",
        "timestamp": 1, "sessionID": "s",
        "part": {"type": "tool", "id": "t", "tool": "Bash",
            "state": {"status": "completed", "title": "x", "output": big_out}}}))
    tout = [o for o in ops if o["op"] == "toolout"][0]
    check("toolout-600-marker", len(tout["text"]) <= 700 and "omitted" in tout["text"]
        and tout["text"].startswith("Y" * 10), tout["text"][-80:])
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("k", "cursor", proj)]))
    fx = fixture_script([json.dumps({"type": "text_delta", "seg": "big",
        "delta": "W" * 50000})])
    orig = patched_argv(fx)
    ag.run_turn(st, "k", "go")
    ag.backend_argv = orig
    a = [x for x in ag.load_timeline(st, "k") if x["role"] == "assistant"]
    check("row-8000-marker", len(a) == 1 and len(a[0]["text"]) <= 8000
        and "omitted" in a[0]["text"], a[0]["text"][-80:])

def test_unknown_json_dropped():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("z", "cursor", proj)]))
    fx = fixture_script([
        json.dumps({"weird": {"nested": [1, 2, {"x": "y"}]}, "blob": "z" * 200}),
        "plain hello",
    ], line_delay=0.1)
    orig = patched_argv(fx)
    r = ag.run_turn(st, "z", "go")
    ag.backend_argv = orig
    check("unknown-not-in-reply", "weird" not in r["reply"] and "plain hello" in r["reply"],
        r["reply"][:200])

def test_legacy_rows_preserved():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("g", "echo", proj)]))
    ag.append_chat(st, "g", "user", "legacy-u")  # no event_id: pre-streaming row
    ag.append_chat(st, "g", "agent", "legacy-a")
    tl = ag.load_timeline(st, "g")
    check("legacy-kept", [x["text"] for x in tl] == ["legacy-u", "legacy-a"], tl)
    h = ag.recent_history(st, "g")
    check("legacy-replays", "legacy-u" in h and "legacy-a" in h, h)

def test_persist_failure_honest():
    st = fresh_state([])
    (st / "chats").mkdir(parents=True, exist_ok=True)
    bad = st / "chats" / "bad.jsonl"
    bad.write_text("")  # placeholder; replaced by a directory to force failure
    bad.unlink()
    bad.mkdir()
    buf = ag.TimelineBuffer(st, "bad", None)
    buf._emit(buf._mkrow("assistant", "partial-text"))
    check("persist-flagged", buf.persist_failed is True, buf.persist_failed)
    check("no-fake-durable", all(r.get("persisted") is False for r in buf.rows.values()),
        buf.rows)

def test_echo_coalesce_accounting():
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps([agent_row("n", "echo", proj)]))
    r = ag.run_turn(st, "n", "line1\nline2\nline3")
    check("echo-ok", "error" not in r, r)
    tl = ag.load_timeline(st, "n")
    check("echo-one-assistant", len([x for x in tl if x["role"] == "assistant"]) == 1, tl)
    check("echo-one-agent", len([x for x in tl if x["role"] == "agent"]) == 1, tl)
    check("echo-one-user", len([x for x in tl if x["role"] == "user"]) == 1, tl)
    check("echo-turns", ag.compact_stats(st, "n").get("user_turns") == 1,
        ag.compact_stats(st, "n"))

# ---------- 14. concurrent claude streams: per-turn scope, no shared state ----------

def feed(buf, lines):
    for ln in lines:
        for op in ag.parse_timeline("claude", ln, scope=buf.tl_scope):
            buf.apply(op)

def test_concurrent_claude_scopes():
    # two interleaved turns (as in one TUI process) with provider msg ids:
    # per-turn scopes must never merge or split segments.
    st = fresh_state([])
    bA, bB = ag.TimelineBuffer(st, "a", None), ag.TimelineBuffer(st, "b", None)
    mA = lambda e, i=0, **k: json.dumps({"type": "stream_event",
        "event": {"type": e, "index": i, **k}})
    feed(bA, [mA("message_start", message={"id": "msg-A"})])
    feed(bA, [mA("content_block_delta", 0,
        delta={"type": "text_delta", "text": "AAA"})])
    feed(bB, [mA("message_start", message={"id": "msg-B"})])
    feed(bB, [mA("content_block_delta", 0,
        delta={"type": "text_delta", "text": "BBB"})])
    feed(bA, [mA("content_block_delta", 0,
        delta={"type": "text_delta", "text": "-A2"})])
    feed(bB, [mA("content_block_delta", 0,
        delta={"type": "text_delta", "text": "-B2"})])
    feed(bA, [json.dumps({"type": "assistant",
        "message": {"id": "msg-A", "content": [{"type": "text", "text": "AAA-A2"}]}})])
    feed(bB, [json.dumps({"type": "assistant",
        "message": {"id": "msg-B", "content": [{"type": "text", "text": "BBB-B2"}]}})])
    bA.finalize(); bB.finalize()
    tA = ag.load_timeline(st, "a")
    tB = ag.load_timeline(st, "b")
    aA = [x for x in tA if x["role"] == "assistant"]
    aB = [x for x in tB if x["role"] == "assistant"]
    check("concurrent-A-intact", len(aA) == 1 and aA[0]["text"] == "AAA-A2", aA)
    check("concurrent-B-intact", len(aB) == 1 and aB[0]["text"] == "BBB-B2", aB)

def test_repeated_index_next_message():
    # no provider ids: per-turn counter still separates index reuse.
    st = fresh_state([])
    buf = ag.TimelineBuffer(st, "c", None)
    m = lambda e, i=0, **k: json.dumps({"type": "stream_event",
        "event": {"type": e, "index": i, **k}})
    feed(buf, [m("message_start"),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "FIRST"}),
        m("message_stop"),
        m("message_start"),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "SECOND"})])
    buf.finalize()
    a = [x for x in ag.load_timeline(st, "c") if x["role"] == "assistant"]
    check("repeated-index-split", len(a) == 2
        and a[0]["text"] == "FIRST" and a[1]["text"] == "SECOND", a)

def test_full_blocks_match_deltas():
    # two same-role text blocks: each full block adopts its own delta row.
    st = fresh_state([])
    buf = ag.TimelineBuffer(st, "d", None)
    m = lambda e, i=0, **k: json.dumps({"type": "stream_event",
        "event": {"type": e, "index": i, **k}})
    feed(buf, [m("message_start"),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "Hello "}),
        m("content_block_delta", 1, delta={"type": "text_delta", "text": "World"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Hello there"},
            {"type": "text", "text": "World peace"}]}})])
    buf.finalize()
    a = [x for x in ag.load_timeline(st, "d") if x["role"] == "assistant"]
    check("per-block-adopt", len(a) == 2
        and a[0]["text"] == "Hello there" and a[1]["text"] == "World peace", a)
    # identity wins: a full block for the same segment replaces it even
    # when the text is unrelated (provider says it IS that block).
    st2 = fresh_state([])
    buf2 = ag.TimelineBuffer(st2, "e", None)
    feed(buf2, [m("message_start"),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "XYZ"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Hello"}]}})])
    buf2.finalize()
    a2 = [x for x in ag.load_timeline(st2, "e") if x["role"] == "assistant"]
    check("identity-wins", len(a2) == 1 and a2[0]["text"] == "Hello", a2)
    # without identity (legacy op, no seg): unrelated full text stays separate.
    st3 = fresh_state([])
    buf3 = ag.TimelineBuffer(st3, "f", None)
    feed(buf3, [m("message_start"),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "XYZ"})])
    buf3.apply({"op": "assistant_text", "text": "Hello"})
    buf3.finalize()
    a3 = [x for x in ag.load_timeline(st3, "f") if x["role"] == "assistant"]
    check("no-false-adopt", len(a3) == 2
        and a3[0]["text"] == "XYZ" and a3[1]["text"] == "Hello", a3)

def test_concurrent_turns():
    # end to end: two claude turns racing in threads, fixtures routed by
    # prompt, provider message ids distinct per turn.
    st = fresh_state([])
    proj = mkproj()
    (st / "agents.json").write_text(json.dumps(
        [agent_row("w1", "claude", proj), agent_row("w2", "claude", proj)]))
    def lines_for(tag, mid, prose):
        return [
            json.dumps({"type": "stream_event", "event": {"type": "message_start",
                "message": {"id": mid}}}),
            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                "index": 0, "delta": {"type": "text_delta", "text": prose[:4]}}}),
            json.dumps({"type": "assistant", "message": {"id": mid, "content": [
                {"type": "text", "text": prose}]}}),
        ]
    fx1 = fixture_script(lines_for("A", "msg-1", "PROSE-ONE"), line_delay=0.1)
    fx2 = fixture_script(lines_for("B", "msg-2", "PROSE-TWO"), line_delay=0.1)
    orig = ag.backend_argv
    def route(backend, **kw):
        return [fx1 if "PROMPT-A" in kw.get("prompt", "") else fx2]
    ag.backend_argv = route
    out = {}
    try:
        t1 = threading.Thread(target=lambda: out.update(
            a=ag.run_turn(st, "w1", "PROMPT-A")))
        t2 = threading.Thread(target=lambda: out.update(
            b=ag.run_turn(st, "w2", "PROMPT-B")))
        t1.start(); t2.start()
        t1.join(timeout=20); t2.join(timeout=20)
    finally:
        ag.backend_argv = orig
    check("threads-done", not t1.is_alive() and not t2.is_alive(), out)
    check("turn-A-reply", out["a"]["reply"].count("PROSE-ONE") == 1
        and "PROSE-TWO" not in out["a"]["reply"], out["a"]["reply"][:160])
    check("turn-B-reply", out["b"]["reply"].count("PROSE-TWO") == 1
        and "PROSE-ONE" not in out["b"]["reply"], out["b"]["reply"][:160])

# ---------- 15. identity-based reconciliation ----------

def test_identical_prefix_blocks():
    # same-role blocks sharing a prefix must land on distinct rows.
    st = fresh_state([])
    buf = ag.TimelineBuffer(st, "ip", None)
    m = lambda e, i=0, **k: json.dumps({"type": "stream_event",
        "event": {"type": e, "index": i, **k}})
    feed(buf, [m("message_start", message={"id": "msg-ip"}),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "Same "}),
        m("content_block_delta", 1, delta={"type": "text_delta", "text": "Same "}),
        json.dumps({"type": "assistant", "message": {"id": "msg-ip", "content": [
            {"type": "text", "text": "Same alpha"},
            {"type": "text", "text": "Same beta"}]}})])
    buf.finalize()
    a = [x for x in ag.load_timeline(st, "ip") if x["role"] == "assistant"]
    check("identical-prefix-split", len(a) == 2
        and a[0]["text"] == "Same alpha" and a[1]["text"] == "Same beta", a)

def test_repeated_message_text():
    # next message repeating (or extending) previous text stays separate.
    st = fresh_state([])
    buf = ag.TimelineBuffer(st, "rp", None)
    m = lambda e, i=0, **k: json.dumps({"type": "stream_event",
        "event": {"type": e, "index": i, **k}})
    feed(buf, [m("message_start", message={"id": "msg-1"}),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "Repeat"}),
        json.dumps({"type": "assistant", "message": {"id": "msg-1", "content": [
            {"type": "text", "text": "Repeat"}]}}),
        m("message_stop"),
        m("message_start", message={"id": "msg-2"}),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "Repeat extended"}),
        json.dumps({"type": "assistant", "message": {"id": "msg-2", "content": [
            {"type": "text", "text": "Repeat extended"}]}})])
    buf.finalize()
    a = [x for x in ag.load_timeline(st, "rp") if x["role"] == "assistant"]
    check("repeated-text-split", len(a) == 2
        and a[0]["text"] == "Repeat" and a[1]["text"] == "Repeat extended", a)
    # stale id cleared: message_start without id after an id message.
    sc = {"n": 0, "id": None}
    ag.parse_timeline_claude(
        m("message_start", message={"id": "msg-X"}), scope=sc)
    ag.parse_timeline_claude(m("message_start"), scope=sc)
    check("stale-id-cleared", sc["id"] is None and sc["n"] == 2, sc)

def test_reasoning_and_assistant_same_message():
    st = fresh_state([])
    buf = ag.TimelineBuffer(st, "ra", None)
    m = lambda e, i=0, **k: json.dumps({"type": "stream_event",
        "event": {"type": e, "index": i, **k}})
    feed(buf, [m("message_start", message={"id": "msg-ra"}),
        m("content_block_delta", 0, delta={"type": "thinking_delta", "thinking": "Hmm"}),
        m("content_block_delta", 1, delta={"type": "text_delta", "text": "Answer"}),
        json.dumps({"type": "assistant", "message": {"id": "msg-ra", "content": [
            {"type": "thinking", "thinking": "Hmm, let me think"},
            {"type": "text", "text": "Answer follows"}]}})])
    buf.finalize()
    tl = ag.load_timeline(st, "ra")
    r = [x for x in tl if x["role"] == "reasoning"]
    a = [x for x in tl if x["role"] == "assistant"]
    check("reasoning-adopt", len(r) == 1 and r[0]["text"] == "Hmm, let me think", r)
    check("assistant-adopt", len(a) == 1 and a[0]["text"] == "Answer follows", a)

def test_interleaved_turns_out_of_order():
    # full blocks landing out of order across turns still reconcile by id.
    st = fresh_state([])
    bA, bB = ag.TimelineBuffer(st, "ia", None), ag.TimelineBuffer(st, "ib", None)
    m = lambda e, i=0, **k: json.dumps({"type": "stream_event",
        "event": {"type": e, "index": i, **k}})
    feed(bA, [m("message_start", message={"id": "msg-ia"}),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "A-part"})])
    feed(bB, [m("message_start", message={"id": "msg-ib"}),
        m("content_block_delta", 0, delta={"type": "text_delta", "text": "B-part"})])
    # B's full block first, then A's — indices identical, ids distinct.
    feed(bB, [json.dumps({"type": "assistant", "message": {"id": "msg-ib", "content": [
        {"type": "text", "text": "B-part done"}]}})])
    feed(bA, [json.dumps({"type": "assistant", "message": {"id": "msg-ia", "content": [
        {"type": "text", "text": "A-part done"}]}})])
    bA.finalize(); bB.finalize()
    aA = [x for x in ag.load_timeline(st, "ia") if x["role"] == "assistant"]
    aB = [x for x in ag.load_timeline(st, "ib") if x["role"] == "assistant"]
    check("ooo-A", len(aA) == 1 and aA[0]["text"] == "A-part done", aA)
    check("ooo-B", len(aB) == 1 and aB[0]["text"] == "B-part done", aB)

def test_full_text_identity_authoritative():
    # unmatched identity must not adopt unrelated pending by prefix.
    st = fresh_state([])
    buf = ag.TimelineBuffer(st, "x", None)
    buf.assistant_delta("a:msg-1:0", "hello")
    buf.assistant_text("hello world", seg="a:msg-1:1")
    buf.finalize()
    a = [x for x in ag.load_timeline(st, "x") if x["role"] == "assistant"]
    check("unmatched-seg-distinct", len(a) == 2
        and a[0]["text"] == "hello" and a[1]["text"] == "hello world", a)
    # repeated identical text stays distinct by identity.
    st1 = fresh_state([])
    buf1 = ag.TimelineBuffer(st1, "y", None)
    buf1.assistant_text("Repeat", seg="a:msg-1:0")
    buf1.assistant_text("Repeat", seg="a:msg-1:1")
    buf1.finalize()
    a1 = [x for x in ag.load_timeline(st1, "y") if x["role"] == "assistant"]
    check("identical-text-distinct", len(a1) == 2
        and all(x["text"] == "Repeat" for x in a1)
        and a1[0]["event_id"] != a1[1]["event_id"], a1)
    # two no-identity candidates: no arbitrary prefix adoption.
    st2 = fresh_state([])
    buf2 = ag.TimelineBuffer(st2, "z", None)
    buf2.assistant_delta("a:k:0", "hello")
    buf2.assistant_delta("a:k:1", "hello")
    buf2.apply({"op": "assistant_text", "text": "hello world"})
    buf2.finalize()
    a2 = [x for x in ag.load_timeline(st2, "z") if x["role"] == "assistant"]
    check("two-cands-no-adopt", len(a2) == 3
        and a2[0]["text"] == "hello" and a2[1]["text"] == "hello"
        and a2[2]["text"] == "hello world", a2)
    # lone legacy candidate reconciles once, then closes.
    st3 = fresh_state([])
    buf3 = ag.TimelineBuffer(st3, "w", None)
    buf3.assistant_delta("a:k:0", "hello")
    buf3.apply({"op": "assistant_text", "text": "hello world"})
    buf3.apply({"op": "assistant_text", "text": "hello world plus"})
    buf3.finalize()
    a3 = [x for x in ag.load_timeline(st3, "w") if x["role"] == "assistant"]
    check("lone-adopt-once", len(a3) == 2
        and a3[0]["text"] == "hello world"
        and a3[1]["text"] == "hello world plus", a3)

if __name__ == "__main__":
    test_ordered_interleave_preexit()
    test_delta_concat()
    test_tool_update_dedup()
    test_error_after_partial()
    test_reload_equivalence()
    test_bounded_output()
    test_legacy_compat()
    test_replay_compaction_exclusion()
    test_user_row_timeline()
    test_real_opencode_diff_schema()
    test_apply_patch_and_state_error()
    test_short_delta_preexit()
    test_claude_parallel_tools()
    test_truncation_markers()
    test_unknown_json_dropped()
    test_legacy_rows_preserved()
    test_persist_failure_honest()
    test_echo_coalesce_accounting()
    test_concurrent_claude_scopes()
    test_repeated_index_next_message()
    test_full_blocks_match_deltas()
    test_concurrent_turns()
    test_identical_prefix_blocks()
    test_repeated_message_text()
    test_reasoning_and_assistant_same_message()
    test_interleaved_turns_out_of_order()
    test_full_text_identity_authoritative()
    print(f"PASS {PASS}")
