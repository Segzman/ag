#!/usr/bin/env python3
"""Acceptance tests for auto compaction (stub echo backend only, no paid calls).

Covers: factory-disabled default, role defaults, per-agent override/clear,
off honored, max-turns + token-threshold triggers, success preservation
(guidance/task/memory/recent kept, old history not resent, log not deleted),
failure/timeout preservation, manual run, busy refusal, no recursion,
raw --command untouched, CLI validation + JSON.

Run: python3 tests/test_ag_compact.py
Env: AG_BIN overrides live ag path (default: repo ag).
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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
    td = Path(tempfile.mkdtemp(prefix="agcmp-state-"))
    (td / "agents.json").write_text(json.dumps(rows))
    return td

def tiny_proj():
    p = Path(tempfile.mkdtemp(prefix="agcmp-proj-"))
    (p / "custom.md").write_text("# T\n")
    return p

def echo_agent(name, role="sub", **kw):
    kw.setdefault("dir", str(tiny_proj()))
    r = {"name": name, "backend": "echo", "model": "default",
         "dir": kw.pop("dir"), "role": role, "persona": "", "system": ""}
    r.update(kw)
    return r

def enable(st, role="sub", **kw):
    base = {"enabled": True, "max_turns": 3, "token_threshold": 0,
            "keep_recent": 1, "timeout": 30}
    base.update(kw)
    r = ag.compact_set_default(st, role, base)
    assert "error" not in r, r
    return r

def turns(st, name, n, prefix="msg"):
    for i in range(n):
        r = ag.run_turn(st, name, f"{prefix}-{i}-UNIQUE{i}")
        assert "error" not in r, r
    return r

def log(st, name):
    return ag.load_chat_log(st, name)

from contextlib import contextmanager
@contextmanager
def canned_summary(marker="CANNED-SUMMARY"):
    """Deterministic short summary: summarizer prompts get a fixed reply,
    normal turns echo. Lets tests distinguish summary from quoted history."""
    orig = ag.backend_argv
    def cap(backend, **kw):
        if "conversation summarizer" in kw.get("prompt", ""):
            return ["printf", "%s", marker]
        return orig(backend, **kw)
    ag.backend_argv = cap
    try:
        yield
    finally:
        ag.backend_argv = orig

def test_factory_disabled():
    st = fresh_state([echo_agent("a")])
    eff = ag.compact_effective(st, "a")
    check("factory-disabled", eff["settings"]["enabled"] is False
          and eff["source"] == "role:sub", eff)
    eff_o = ag.compact_effective(st, "ghost")
    check("unknown-agent-sub-default", eff_o["role"] == "sub", eff_o)
    turns(st, "a", 6)
    rows = log(st, "a")
    check("disabled-no-summary", not [r for r in rows if r.get("role") == "summary"])
    check("disabled-no-flags", not [r for r in rows if r.get("compacted")])

def test_role_default_auto_compact():
    st = fresh_state([echo_agent("a")])
    enable(st)
    with canned_summary():
        r = turns(st, "a", 4)
    rows = log(st, "a")
    summ = [x for x in rows if x.get("role") == "summary"]
    check("auto-summary-row", len(summ) == 1, len(summ))
    flagged = [x for x in rows if x.get("compacted")]
    check("old-flagged", len(flagged) == 4, len(flagged))  # 2 user + 2 agent
    # raw log keeps every persisted snapshot row; logical accounting dedups
    # by event_id (snapshot rewrites never inflate turn counts).
    tl = ag.load_timeline(st, "a")
    check("logical-user-turns", sum(1 for x in tl if x.get("role") == "user") == 4, len(tl))
    check("logical-agent-turns", sum(1 for x in tl if x.get("role") == "agent") == 4, len(tl))
    check("logical-one-assistant-per-turn",
        sum(1 for x in tl if x.get("role") == "assistant") == 4, len(tl))
    check("raw-retained", len(rows) > len(tl), (len(rows), len(tl)))
    check("user-turn-count-stable", ag.compact_stats(st, "a").get("user_turns") == 4,
        ag.compact_stats(st, "a"))
    check("summary-cites-turns", "2 turns" in summ[0]["text"] and "CANNED-SUMMARY" in summ[0]["text"],
          summ[0]["text"][:80])
    # next prompt: summary present, old user rows flagged (excluded from
    # history input), recent kept. (Echo agent rows quote older text, so
    # absence is asserted at row level + exact-lines test below.)
    check("prompt-has-summary", "CANNED-SUMMARY" in r["reply"])
    carriers = [x for x in rows if x.get("role") == "user"
                and x.get("text", "").strip() in ("msg-0-UNIQUE0", "msg-1-UNIQUE1")]
    check("old-rows-flagged", len(carriers) == 2 and all(x.get("compacted") for x in carriers),
          [(x.get("text"), x.get("compacted")) for x in carriers])
    check("recent-kept", "msg-2-UNIQUE2" in r["reply"] and "msg-3-UNIQUE3" in r["reply"])
    # no retrigger on same rows: one more turn compacts nothing new
    n_before = len([x for x in log(st, "a") if x.get("role") == "summary"])
    with canned_summary():
        ag.run_turn(st, "a", "fifth")
    n_after = len([x for x in log(st, "a") if x.get("role") == "summary"])
    check("no-every-turn", n_before == n_after, (n_before, n_after))

def test_override_and_clear():
    st = fresh_state([echo_agent("a"), echo_agent("b")])
    enable(st)
    r = ag.compact_set_agent(st, "a", {"enabled": False})
    assert "error" not in r, r
    turns(st, "a", 5)
    check("override-off", not [x for x in log(st, "a") if x.get("role") == "summary"])
    turns(st, "b", 4)
    check("sibling-still-compacts", len([x for x in log(st, "b") if x.get("role") == "summary"]) == 1)
    eff = ag.compact_effective(st, "a")
    check("override-source", eff["source"] == "agent" and eff["settings"]["enabled"] is False)
    r = ag.compact_set_agent(st, "a", None)
    assert r.get("cleared"), r
    eff = ag.compact_effective(st, "a")
    check("clear-inherits", eff["source"] == "role:sub" and eff["settings"]["enabled"] is True)
    # per-agent keep override
    ag.compact_set_agent(st, "a", {"keep_recent": 0})
    check("keep-override", ag.compact_effective(st, "a")["settings"]["keep_recent"] == 0)

def test_flagged_excluded_from_history():
    st = fresh_state([echo_agent("a")])
    for i in range(4):
        ag.append_chat(st, "a", "user", f"U{i}")
        ag.append_chat(st, "a", "agent", f"A{i}")
    ag.compact_set_agent(st, "a", {"enabled": True, "max_turns": 100,
        "token_threshold": 0, "keep_recent": 1, "timeout": 30})
    with canned_summary():
        r = ag.compact_run(st, "a", force=True)
    assert r.get("compacted"), r
    h = ag.recent_history(st, "a")
    check("history-exact-lines", h.splitlines() == [
        "user: U3", "agent: A3",
        "summary: [context summary of 3 turns]", "CANNED-SUMMARY"], repr(h))

def test_token_threshold_trigger():
    st = fresh_state([echo_agent("a")])
    ag.compact_set_default(st, "sub", {"enabled": True, "max_turns": 0,
        "token_threshold": 10, "keep_recent": 1, "timeout": 30})
    r = ag.run_turn(st, "a", "a-long-enough-message-to-trip-threshold")
    r = ag.run_turn(st, "a", "second")
    r = ag.run_turn(st, "a", "third")
    rows = log(st, "a")
    check("threshold-fires", len([x for x in rows if x.get("role") == "summary"]) >= 1)
    s = ag.compact_stats(st, "a")
    check("stats-shape", s["est_tokens"] > 0 and "rough" in s["est_note"], s)

def test_preservation_after_compact():
    st = fresh_state([echo_agent("a")])
    proj = Path(tempfile.mkdtemp(prefix="agcmp-proj-"))
    (proj / "custom.md").write_text("# KEEP-GUIDANCE\n")
    rows = [echo_agent("a", str(proj))]
    (st / "agents.json").write_text(json.dumps(rows))
    enable(st)
    ag.ctx_save_assignments(st, {"a": {"project": str(proj.resolve()), "scope": "",
        "files": [], "task": "KEEP-TASK", "acceptance": "", "updated": "t"}})
    r = turns(st, "a", 4)
    check("guidance-survives", "KEEP-GUIDANCE" in r["reply"])
    check("task-survives", "KEEP-TASK" in r["reply"])

def test_failure_preserves_all():
    st = fresh_state([echo_agent("a")])
    enable(st)
    ag.run_turn(st, "a", "one")
    ag.run_turn(st, "a", "two")
    before = log(st, "a")
    # logical messages: 2 user + 2 agent (+1 assistant display row per turn);
    # raw snapshots retained, accounting via dedup.
    btl = ag.load_timeline(st, "a")
    assert sum(1 for x in btl if x.get("role") == "user") == 2, before
    assert sum(1 for x in btl if x.get("role") == "agent") == 2, before
    rows = json.loads((st / "agents.json").read_text())
    rows[0]["backend"] = "nosuch-backend"
    (st / "agents.json").write_text(json.dumps(rows))
    r = ag.compact_run(st, "a", force=True)
    check("failure-reported", r.get("compacted") is False and r.get("error"), r)
    after = log(st, "a")
    check("failure-log-intact", [x.get("text") for x in after] == [x.get("text") for x in before]
          and not [x for x in after if x.get("compacted") or x.get("role") == "summary"])
    check("failure-no-sid-clear", ag.load_chat_meta(st, "a").get("sid", "") == "")

def test_timeout_preserves():
    st = fresh_state([echo_agent("a")])
    enable(st, max_turns=100)
    turns(st, "a", 3)
    import subprocess as _sp
    orig = _sp.run
    def boom(*a, **k):
        raise _sp.TimeoutExpired(cmd="x", timeout=1)
    _sp.run = boom
    try:
        r = ag.compact_run(st, "a", force=True)
    finally:
        _sp.run = orig
    check("timeout-reported", r.get("compacted") is False and "TimeoutExpired" in r.get("error", ""), r)
    check("timeout-log-intact", not [x for x in log(st, "a") if x.get("compacted")])

def test_manual_run_and_busy():
    st = fresh_state([echo_agent("a")])
    enable(st, max_turns=100)
    turns(st, "a", 2)
    r = ag.compact_run(st, "a", force=True)
    check("manual-force", r.get("compacted") is True, r)
    lk = ag._acquire_agent_turn(st, "a")
    try:
        r2 = ag.compact_run(st, "a", force=True)
    finally:
        ag._release_agent_turn(lk)
    check("busy-refused", r2.get("compacted") is False and "busy" in r2.get("error", ""), r2)

def test_no_recursion():
    st = fresh_state([echo_agent("a")])
    enable(st, max_turns=100)
    turns(st, "a", 2)
    calls = []
    orig = ag.run_turn
    ag.run_turn = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    try:
        r = ag.compact_run(st, "a", force=True)
    finally:
        ag.run_turn = orig
    check("compact-succeeds", r.get("compacted") is True, r)
    check("no-run-turn-recursion", calls == [], calls)

def test_command_untouched():
    st = fresh_state([dict(echo_agent("o"), backend="opencode",
                           model="opencode/muse-spark-1.3-contributor-free")])
    enable(st)
    seen = {}
    orig = ag.backend_argv
    def cap(backend, **kw):
        if not kw.get("command"):
            seen.setdefault("prompts", []).append(kw.get("prompt", ""))
        return ["printf", "%s\n", kw.get("prompt", "")]
    ag.backend_argv = cap
    try:
        for i in range(5):
            r = ag.run_turn(st, "o", f"arg{i}", command="review")
            assert "error" not in r, r
    finally:
        ag.backend_argv = orig
    check("command-bare-each", all(p == f"arg{i}" for i, p in enumerate(seen.get("prompts", []))),
          seen.get("prompts"))
    check("command-no-compact", not [x for x in log(st, "o") if x.get("role") == "summary"])

def test_cli_validation_and_json():
    state = Path(tempfile.mkdtemp(prefix="agcmp-cli-"))
    def run(*args):
        env = dict(os.environ, AGENT_CLI_DIR=str(state))
        p = subprocess.run([sys.executable, str(AG), "--json"] + list(args),
                           capture_output=True, text=True, timeout=30, env=env)
        try:
            return p, json.loads(p.stdout)
        except Exception:
            raise AssertionError(f"not JSON for {args}: rc={p.returncode} out={p.stdout!r} err={p.stderr!r}")
    _, o = run("agents", "add", "c", "--backend", "echo")
    assert o["ok"], o
    _, o = run("compact", "config", "--role", "sub", "--max-turns", "0", "--tokens", "0")
    assert o["ok"], o
    _, o = run("compact", "config", "--role", "sub", "--enable")
    check("enable-needs-trigger", o["ok"] is False, o)
    _, o = run("compact", "config", "--role", "sub", "--enable", "--max-turns", "-1")
    check("range-rejected", o["ok"] is False, o)
    _, o = run("compact", "config", "--role", "sub", "--enable", "--disable")
    check("conflict-rejected", o["ok"] is False, o)
    _, o = run("compact", "config", "--agent", "ghost", "--enable", "--max-turns", "5")
    check("unknown-agent-rejected", o["ok"] is False, o)
    _, o = run("compact", "config", "--role", "sub", "--enable", "--max-turns", "2", "--keep", "1")
    assert o["ok"], o
    _, o = run("compact", "show", "c")
    check("show-json", o["ok"] and o["data"]["settings"]["enabled"] is True
          and "est_note" in o["data"]["stats"], o)
    _, o = run("compact", "run", "c")
    check("run-nothing", o["ok"] and o["data"]["compacted"] is False, o)

def test_prompt_pins_instructions():
    asg = {"task": "PINNED-TASK", "scope": "ui"}
    beyond = [{"role": "user", "text": f"do {i} " + "Y" * 2000} for i in range(10)]
    p = ag._compact_prompt(asg, [{"text": "OLD-DECISION"}], beyond)
    check("prompt-instructions-first", p.startswith("You are a conversation summarizer"), p[:80])
    check("prompt-no-tools", "Do not emit tool calls" in p)
    check("prompt-task-pinned", "PINNED-TASK" in p)
    check("prompt-prior-included", "OLD-DECISION" in p)
    check("prompt-body-bounded", len(p) < ag._COMPACT_BODY_CAP + 2000, len(p))
    check("prompt-trunc-notice", "[history truncated to cap]" in p)

def test_second_compaction_merges():
    import threading
    st = fresh_state([echo_agent("a")])
    enable(st)
    with canned_summary("SUM-ONE"):
        turns(st, "a", 4)
    rows = log(st, "a")
    first = [x for x in rows if x.get("role") == "summary"]
    check("first-summary", len(first) == 1 and "SUM-ONE" in first[0]["text"])
    with canned_summary("SUM-TWO"):
        turns(st, "a", 3)
    rows = log(st, "a")
    active = [x for x in rows if x.get("role") == "summary" and not x.get("compacted")]
    check("single-active-summary", len(active) == 1, len(active))
    check("merged-header", "merged 1 prior" in active[0]["text"] and "SUM-TWO" in active[0]["text"],
          active[0]["text"][:120])
    check("superseded-retired", all(x.get("compacted") for x in rows
                                    if x.get("role") == "summary" and x is not active[0]))
    _ = threading  # guard-concurrency covered below

def test_manual_holds_guard():
    import threading
    import time
    st = fresh_state([echo_agent("a")])
    enable(st, max_turns=100)
    turns(st, "a", 2)
    entered, release = threading.Event(), threading.Event()
    import subprocess as _sp
    orig = _sp.run
    def slow(*a, **k):
        entered.set()
        assert release.wait(timeout=15)
        return orig(*a, **k)
    _sp.run = slow
    out = {}
    t1 = threading.Thread(target=lambda: out.update(m=ag.compact_run(st, "a", force=True)))
    t1.start()
    assert entered.wait(timeout=15)
    t2 = threading.Thread(target=lambda: out.update(r=ag.run_turn(st, "a", "concurrent-turn")))
    t2.start()
    time.sleep(0.5)
    check("turn-blocked-behind-compact", t2.is_alive())
    release.set()
    t1.join(timeout=15)
    t2.join(timeout=15)
    _sp.run = orig
    check("manual-compacted", out.get("m", {}).get("compacted") is True, out.get("m"))
    rows = log(st, "a")
    check("snapshot-isolation", out["m"].get("turns") == 1, out["m"])
    users = [x["text"] for x in rows if x.get("role") == "user"]
    check("concurrent-turn-intact", "concurrent-turn" in users, users)
    check("concurrent-unflagged", all(not x.get("compacted") for x in rows
                                      if x.get("text") == "concurrent-turn"))

def test_summary_survives_many_rows():
    st = fresh_state([echo_agent("a")])
    for i in range(3):
        ag.append_chat(st, "a", "user", f"U{i}")
        ag.append_chat(st, "a", "agent", f"A{i}")
    ag.compact_set_agent(st, "a", {"enabled": True, "max_turns": 100,
        "token_threshold": 0, "keep_recent": 1, "timeout": 30})
    with canned_summary():
        r = ag.compact_run(st, "a", force=True)
    assert r.get("compacted"), r
    for i in range(3, 17):
        ag.append_chat(st, "a", "user", f"U{i}")
        ag.append_chat(st, "a", "agent", f"A{i}")
    h = ag.recent_history(st, "a")
    check("summary-survives", "summary: [context summary of 2 turns]" in h, h[:200])
    check("omission-explicit", "[history: 10 older turns omitted]" in h, h[:400])
    check("current-prominent", h.rstrip().endswith("agent: A16"), h[-60:])

def test_config_lock_and_validation():
    st = fresh_state([echo_agent("a")])
    check("float-rejected", ag.compact_validate({"max_turns": 2.5}) != [], )
    check("unknown-rejected", ag.compact_validate({"bogus": 1}) != [])
    check("str-enabled-rejected", ag.compact_validate({"enabled": "yes"}) != [])
    r = ag.compact_set_default(st, "sub", {"max_turns": 7})
    assert "error" not in r, r
    r = ag.compact_set_agent(st, "a", {"keep_recent": 3})
    assert "error" not in r, r
    cfg = ag.compact_load_config(st)
    check("role-preserved", cfg["defaults"]["sub"]["max_turns"] == 7, cfg["defaults"]["sub"])
    check("agent-preserved", cfg["agents"]["a"]["keep_recent"] == 3, cfg["agents"])
    check("lockfile", (Path(st) / "compact.lock").is_file())
    r = ag.compact_set_default(st, "sub", {"max_turns": -1})
    check("bad-not-saved", r.get("error") and
          ag.compact_load_config(st)["defaults"]["sub"]["max_turns"] == 7, r)

def test_auto_failure_surfaced_once():
    st = fresh_state([echo_agent("a")])
    enable(st, max_turns=1, keep_recent=0)
    ag.run_turn(st, "a", "one")
    rows = json.loads((st / "agents.json").read_text())
    rows[0]["backend"] = "nosuch-backend"
    (st / "agents.json").write_text(json.dumps(rows))
    seen = []
    r1 = ag.run_turn(st, "a", "two", on_event=lambda n, k, t: seen.append((k, t)))
    r2 = ag.run_turn(st, "a", "three", on_event=lambda n, k, t: seen.append((k, t)))
    check("turns-error", "error" in r1 and "error" in r2, (r1, r2))
    notes = [x for x in log(st, "a") if (x.get("text", "") or "").startswith("[compact] auto compaction failed")]
    check("failure-note-once", len(notes) == 1, len(notes))
    check("failure-event", any(k == "tool" and "auto compaction failed" in t for k, t in seen), seen)
    check("failure-no-summary", not [x for x in log(st, "a") if x.get("role") == "summary"])

def test_commit_and_meta_semantics():
    import json as _json
    st = fresh_state([echo_agent("a")])
    enable(st, max_turns=100)
    turns(st, "a", 2)
    before = Path(ag.chat_log_path(st, "a")).read_bytes()
    orig = _json.dumps
    ag.json.dumps = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    try:
        r = ag.compact_run(st, "a", force=True)
    finally:
        ag.json.dumps = orig
    check("commit-honest", r.get("compacted") is False and "commit failed" in r.get("error", ""), r)
    check("commit-preserved", Path(ag.chat_log_path(st, "a")).read_bytes() == before)
    check("commit-no-summary", not [x for x in log(st, "a") if x.get("role") == "summary"])
    # seeded session ids cleared on success
    ag.save_chat_meta(st, "a", {"sid": "ses_A", "sids": {"echo": "ses_B"},
                                "backend": "echo", "model": "default", "dir": ".", "role": "sub"})
    with canned_summary():
        r = ag.compact_run(st, "a", force=True)
    assert r.get("compacted"), r
    m = ag.load_chat_meta(st, "a")
    check("sids-cleared", m.get("sid", "") == "" and m.get("sids", {}) == {}, m)
    check("sid-reported", r.get("sid_cleared") is True, r)
    # meta write failure reports warning, keeps durable summary
    ag.save_chat_meta(st, "a", {**m, "sid": "ses_C", "sids": {"echo": "ses_D"}})
    turns(st, "a", 2)
    orig_clear = ag.clear_agent_sid
    ag.clear_agent_sid = lambda *a, **k: False
    try:
        with canned_summary():
            r = ag.compact_run(st, "a", force=True)
    finally:
        ag.clear_agent_sid = orig_clear
    check("meta-honest", r.get("compacted") is True and r.get("sid_cleared") is False
          and "warning" in r, r)
    m = ag.load_chat_meta(st, "a")
    check("meta-untouched", m.get("sid") == "ses_C", m)

if __name__ == "__main__":
    test_factory_disabled()
    test_role_default_auto_compact()
    test_flagged_excluded_from_history()
    test_override_and_clear()
    test_token_threshold_trigger()
    test_preservation_after_compact()
    test_failure_preserves_all()
    test_timeout_preserves()
    test_manual_run_and_busy()
    test_no_recursion()
    test_command_untouched()
    test_cli_validation_and_json()
    test_prompt_pins_instructions()
    test_second_compaction_merges()
    test_manual_holds_guard()
    test_summary_survives_many_rows()
    test_config_lock_and_validation()
    test_auto_failure_surfaced_once()
    test_commit_and_meta_semantics()
    print(f"PASS {PASS} checks")
