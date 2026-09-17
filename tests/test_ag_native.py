#!/usr/bin/env python3
"""Regression tests for native slash commands + backend switching.

Stdlib only, temp state, fake echo backend + argv capture. No remote calls.
Run: python3 tests/test_ag_native.py
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
    td = Path(tempfile.mkdtemp(prefix="agn-"))
    (td / "agents.json").write_text(json.dumps(rows))
    return td

def echo_agent(name="sl", backend="echo", model="default"):
    return {"name": name, "backend": backend, "model": model,
            "dir": ".", "role": "orchestrator", "persona": "", "system": ""}

def test_parse_prefixes():
    check("slash-help", ag.parse_slash("/help") == ("help", ""))
    check("slash-args", ag.parse_slash("  /harness use opencode") == ("harness", "use opencode"))
    check("slash-alias", ag.parse_slash("/backend list") == ("harness", "list"))
    check("backslash-alias", ag.parse_slash("\\model opus") == ("model", "opus"))
    check("backslash-help", ag.parse_slash("\\help") == ("help", ""))
    check("slash-unknown-is-cmd", ag.parse_slash("/frobnicate x") == ("frobnicate", "x"))
    check("plain-text", ag.parse_slash("hi there") is None)
    check("win-path-safe", ag.parse_slash("C:\\path\\x") is None)
    check("backslash-unknown-is-text", ag.parse_slash("\\nonsense here") is None)
    check("backslash-share-safe", ag.parse_slash("\\\\server\\share") is None)
    check("double-slash-is-text", ag.parse_slash("// comment") is None)
    check("bare-slash-is-text", ag.parse_slash("/  spaced") is None)

def test_backslash_preserves_normal_text():
    td = fresh_state([echo_agent()])
    r = ag.run_turn(td, "sl", "C:\\notes\\todo")
    check("backslash-goes-to-model", "slash" not in r and "C:\\notes\\todo" in r["reply"], r)
    log = ag.load_chat_log(td, "sl")
    check("backslash-logged-user-agent",
          any(x["role"] == "user" for x in log) and any(x["role"] == "agent" for x in log), log)

def test_slash_never_calls_backend():
    td = fresh_state([echo_agent()])
    orig = ag.backend_argv
    def boom(backend, **kw):
        raise AssertionError("backend must not be called for slash")
    ag.backend_argv = boom
    try:
        r = ag.run_turn(td, "sl", "/help")
        check("help-ok", r.get("slash") == "help" and r["exit"] == 0 and "/harness" in r["reply"], r)
        r = ag.run_turn(td, "sl", "\\harness current")
        check("harness-current", r["exit"] == 0 and "echo" in r["reply"], r)
        r = ag.run_turn(td, "sl", "/model list")
        check("model-list", r["exit"] == 0 and "default" in r["reply"], r)
    finally:
        ag.backend_argv = orig

def test_unknown_command_errors_with_handoff():
    td = fresh_state([echo_agent()])
    orig = ag.backend_argv
    ag.backend_argv = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no backend call"))
    try:
        r = ag.run_turn(td, "sl", "/frobnicate x")
    finally:
        ag.backend_argv = orig
    check("unknown-exit", r["exit"] == 1 and r.get("slash") == "frobnicate", r)
    check("unknown-points-help", "/help" in r["reply"] and "/handoff" in r["reply"], r["reply"])
    log = ag.load_chat_log(td, "sl")
    check("unknown-tool-roles-only", all(x["role"] in ("tool", "toolout") for x in log), log)
    check("unknown-absent-user-agent",
          not any("frobnicate" in (x.get("text", "") or "")
                  for x in log if x.get("role") in ("user", "agent")), log)

def test_model_set_persists():
    td = fresh_state([echo_agent(backend="claude")])
    r = ag.run_turn(td, "sl", "/model opus")
    check("model-set", r["exit"] == 0 and "opus" in r["reply"], r)
    rows = {x["name"]: x for x in ag.load_agents(td)}
    check("model-saved", rows["sl"]["model"] == "opus", rows["sl"])
    meta = ag.load_chat_meta(td, "sl")
    check("model-meta", meta.get("model") == "opus", meta)

def test_switch_away_back_with_sid_segregation():
    td = fresh_state([dict(echo_agent(backend="claude", model="opus"), role="orchestrator")])
    ag.save_chat_meta(td, "sl", {"backend": "claude", "model": "opus",
        "sid": "claude-sid-9",
        "sids": {"claude": "claude-sid-9", "opencode": "ses-oc-1"}})
    calls = []
    orig = ag.backend_argv
    def fake(backend, *, model="", workdir=".", sid="", prompt="", system="", command=""):
        calls.append((backend, sid))
        return ["printf", "%s\n", prompt]
    ag.backend_argv = fake
    try:
        r = ag.run_turn(td, "sl", "ping")
        check("claude-resumes-claude-sid", calls[-1] == ("claude", "claude-sid-9"), calls)
        r = ag.run_turn(td, "sl", "/harness use opencode")
        check("switch-ok", r["exit"] == 0 and "claude -> opencode" in r["reply"], r)
        rows = {x["name"]: x for x in ag.load_agents(td)}
        a = rows["sl"]
        check("identity-preserved",
              a["name"] == "sl" and a["dir"] == "." and a["role"] == "orchestrator"
              and a.get("persona") == "" and a.get("system") == "", a)
        check("model-reset", a["model"] == ag.OPENCODE_MODEL_DEFAULT, a)
        meta = ag.load_chat_meta(td, "sl")
        check("switch-loads-opencode-sid", meta.get("sid") == "ses-oc-1", meta)
        check("switch-keeps-claude-sid", meta["sids"].get("claude") == "claude-sid-9", meta)
        r = ag.run_turn(td, "sl", "ping2")
        check("opencode-resumes-own-sid", calls[-1] == ("opencode", "ses-oc-1"), calls)
        check("no-cross-resume", all(s != "claude-sid-9" for b, s in calls if b == "opencode"), calls)
        r = ag.run_turn(td, "sl", "/backend echo")
        check("switch-back", r["exit"] == 0 and "opencode -> echo" in r["reply"], r)
        meta = ag.load_chat_meta(td, "sl")
        check("echo-fresh-sid", meta.get("sid") == "", meta)
        r = ag.run_turn(td, "sl", "/harness use claude")
        meta = ag.load_chat_meta(td, "sl")
        check("claude-sid-restored", meta.get("sid") == "claude-sid-9", meta)
    finally:
        ag.backend_argv = orig

def test_busy_guard():
    td = fresh_state([echo_agent()])
    ag.mark_running("sl", True)
    try:
        ok, msg = ag.switch_backend(td, "sl", "opencode")
        check("busy-rejected", not ok and "busy" in msg, msg)
        rows = {x["name"]: x for x in ag.load_agents(td)}
        check("busy-no-change", rows["sl"]["backend"] == "echo", rows["sl"])
    finally:
        ag.mark_running("sl", False)
    ok, msg = ag.switch_backend(td, "sl", "opencode")
    check("idle-switches", ok, msg)

def test_handoff_has_no_bypass_flags():
    td = fresh_state([echo_agent(backend="claude")])
    ag.save_chat_meta(td, "sl", {"backend": "claude", "sid": "abc", "sids": {"claude": "abc"}})
    for b in ("claude", "opencode", "gemini", "codex", "cursor"):
        argv, cwd = ag.native_handoff_argv(b, sid="s1")
        check(f"handoff-{b}-clean", not any(t in ag.BYPASS_FLAGS for t in argv), argv)
    argv, _ = ag.native_handoff_argv("claude", sid="abc")
    check("handoff-claude-resume", argv == ["claude", "--resume", "abc"], argv)
    argv, _ = ag.native_handoff_argv("opencode", sid="s1", model="default")
    check("handoff-opencode-spark",
          argv == ["opencode", "-m", ag.OPENCODE_MODEL_DEFAULT], argv)
    ok, txt = ag.handle_slash(td, "sl", "handoff", "")
    check("handoff-text", ok and "claude" in txt and "--resume" in txt and "abc" in txt, txt)
    ok, txt = ag.handle_slash(td, "sl", "handoff", "opencode")
    check("handoff-other", ok and "opencode" in txt, txt)
    td2 = fresh_state([echo_agent()])
    ok, txt = ag.handle_slash(td2, "sl", "handoff", "")
    check("handoff-echo-err", not ok and "no native" in txt, txt)

def test_backend_opts_stable():
    check("backend-opts", ag.tui_backend_opts() == ["claude", "opencode", "gemini", "codex", "cursor", "echo"])

def test_opencode_command_argv_only():
    S = ag.OPENCODE_MODEL_DEFAULT
    av = ag.backend_argv("opencode", model="", workdir=".", sid="", prompt="hi", command="rev")
    check("command-argv", av == ["opencode", "run", "--format", "json", "--thinking",
        "--command", "rev", "-m", S, "--dir", ".", "hi"], av)
    av = ag.backend_argv("opencode", model="", workdir=".", sid="", prompt="hi")
    check("no-command-clean", "--command" not in av, av)
    check("default-resolves-spark", "-m" in av and S in av, av)
    av = ag.backend_argv("opencode", model="custom/x", workdir=".",
                         sid="", prompt="hi")
    check("custom-preserved", av[av.index("-m") + 1] == "custom/x", av)
    for b in ("claude", "gemini", "codex", "cursor", "echo"):
        try:
            ag.backend_argv(b, model="", workdir=".", sid="", prompt="hi", command="rev")
        except TypeError:
            raise AssertionError(f"command kwarg breaks {b}")
    td = fresh_state([echo_agent()])
    r = ag.run_turn(td, "sl", "hi", command="rev")
    check("command-rejected-non-opencode",
          "error" in r and "opencode-only" in r["error"], r)
    td = fresh_state([echo_agent(backend="opencode")])
    calls = []
    orig = ag.backend_argv
    def fake(backend, *, model="", workdir=".", sid="", prompt="", system="", command=""):
        calls.append((backend, command, prompt))
        return ["printf", "%s\n", "cmd-ok"]
    ag.backend_argv = fake
    try:
        r = ag.run_turn(td, "sl", "some args", command="rev")
    finally:
        ag.backend_argv = orig
    check("command-bare-args", calls and calls[-1] == ("opencode", "rev", "some args"), calls)
    check("command-reply", r.get("reply") == "cmd-ok", r)

def test_cli_headless_discovers_slash():
    td = Path(tempfile.mkdtemp(prefix="agncli-"))
    env = dict(os.environ, AGENT_CLI_DIR=str(td))
    def run(*args):
        return subprocess.run([sys.executable, str(AG)] + list(args),
            capture_output=True, text=True, timeout=30, env=env)
    p = run("agents", "add", "h", "--backend", "echo", "--role", "sub")
    assert p.returncode == 0, (p.stdout, p.stderr)
    p = run("chat", "send", "h", "/help")
    check("cli-help", p.returncode == 0 and "/harness" in p.stdout, (p.stdout, p.stderr))
    p = run("chat", "send", "h", "/nope")
    check("cli-unknown", "unknown command" in (p.stdout + p.stderr), (p.stdout, p.stderr))
    p = run("chat", "send", "h", "\\harness current")
    check("cli-backslash", "echo" in (p.stdout + p.stderr), (p.stdout, p.stderr))
    p = run("chat", "send", "h", "/harness use claude")
    check("cli-switch", "echo -> claude" in (p.stdout + p.stderr), (p.stdout, p.stderr))
    p = run("chat", "send", "h", "/model")
    check("cli-model", "default" in (p.stdout + p.stderr), (p.stdout, p.stderr))
    p = run("--json", "chat", "send", "h", "/nope")
    o = json.loads(p.stdout or "{}")
    check("cli-json-fail", o.get("ok") is False and "nope" in o.get("hint", ""), o)
    p = run("chat", "send", "h", "--command", "rev", "some args")
    check("cli-command-rejected", "opencode-only" in (p.stdout + p.stderr), (p.stdout, p.stderr))
    _, o = run("chat", "log", "h", "--limit", "20"), None
    log = json.loads((td / "chats" / "h.jsonl").read_text().splitlines()[-1])
    check("cli-log-has-slash", log["role"] in ("tool", "toolout"), log)

if __name__ == "__main__":
    test_parse_prefixes()
    test_backslash_preserves_normal_text()
    test_slash_never_calls_backend()
    test_unknown_command_errors_with_handoff()
    test_model_set_persists()
    test_switch_away_back_with_sid_segregation()
    test_busy_guard()
    test_handoff_has_no_bypass_flags()
    test_backend_opts_stable()
    test_opencode_command_argv_only()
    test_cli_headless_discovers_slash()
    print(f"{PASS} checks passed")
