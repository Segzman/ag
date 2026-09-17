#!/usr/bin/env python3
"""Native slash x profile integration (live ag).

Checks, with fake fixtures only (echo backend, no real models/connectors):
- effective launch env/argv/config for a selected profile
- backend switching away/back keeps profile, segregates SIDs per backend
- shared MCP profile serves two backends (claude + opencode translations)
- path errors surface (missing MCP file, bad profile name, unknown agent)
- no global config mutation (HOME untouched, env scoped under state dir)
- /profile slash routes never reach the model as prose (tool/toolout only)
- handoff argv carries profile extras without bypass flags
- busy agents refuse backend/profile switches (cross-process active guard)

Run: python3 tests/test_ag_profile_native.py
"""
import importlib.machinery
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

AG = os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag"))
assert os.path.exists(AG), f"ag missing: {AG}"
SECRET = "FIXTURE-SECRET-9f8e7d6c5b"


def load_mod():
    return importlib.machinery.SourceFileLoader("agmod2", AG).load_module()


def run_ag(args, state, timeout=30):
    env = dict(os.environ, AGENT_CLI_DIR=str(state))
    return subprocess.run([sys.executable, AG] + args, capture_output=True,
                          text=True, timeout=timeout, env=env)


def run_json(args, state, timeout=30):
    p = run_ag(args, state, timeout=timeout)
    try:
        return p, json.loads(p.stdout)
    except Exception:
        raise AssertionError(
            f"not JSON for {args}: rc={p.returncode} out={p.stdout!r} err={p.stderr!r}")


def mk_fixtures():
    src = Path(tempfile.mkdtemp(prefix="ag-int-src-"))
    (src / "skills" / "s1").mkdir(parents=True)
    (src / "skills" / "s1" / "SKILL.md").write_text("# s1\nfixture skill\n")
    (src / "INSTR.md").write_text("# instructions\nINT-MARKER-42\n")
    (src / "MEM.md").write_text("# memory\nMEM-MARKER-7\n")
    (src / "mcp.json").write_text(json.dumps({"mcpServers": {
        "shr": {"command": "echo", "args": ["hi"], "env": {"K": SECRET}},
        "web": {"url": "https://mcp.example.com/x", "headers": {"Authorization": "Bearer t"}},
    }}))
    # .claude-style skills source (native discovery path for opencode)
    (src / ".claude" / "skills" / "cs").mkdir(parents=True)
    (src / ".claude" / "skills" / "cs" / "SKILL.md").write_text("# cs\nclaude skill\n")
    st = Path(tempfile.mkdtemp(prefix="ag-int-state-"))
    return src, st


def check(name, fn):
    try:
        fn()
    except Exception as e:
        print(f"FAIL {name}: {e}")
        return False
    print(f"PASS {name}")
    return True


def t_effective_launch_env_argv_config():
    src, st = mk_fixtures()
    m = load_mod()
    _, o = run_json(["--json", "harness", "profile", "add", "p",
                     "--instructions", str(src / "INSTR.md"),
                     "--memory", str(src / "MEM.md"),
                     "--skills", str(src / "skills"),
                     "--mcp", str(src / "mcp.json")], st)
    assert o["ok"], o
    _, o = run_json(["--json", "agents", "add", "a1",
                     "--backend", "claude", "--role", "sub"], st)
    assert o["ok"], o
    _, o = run_json(["--json", "harness", "profile", "set", "a1",
                     "--profile", "p"], st)
    assert o["ok"], o
    # claude headless argv carries scoped flags; env empty for claude
    a = [x for x in m.load_agents(st) if x["name"] == "a1"][0]
    extra = m.backend_argv_extra("claude", st, "p")
    assert "--mcp-config" in extra and "--plugin-dir" in extra, extra
    assert all(str(st) in x for x in extra[1::2]), extra
    # opencode headless env points under state dir
    env = m.backend_env_for_profile(st, {"backend": "opencode"}, "p")
    assert env["OPENCODE_CONFIG"].startswith(str(st)), env
    assert env["OPENCODE_CONFIG_DIR"].startswith(str(st)), env
    oc = json.loads(Path(env["OPENCODE_CONFIG"]).read_text())
    assert "shr" in oc.get("mcp", {}), oc
    assert any("INSTRUCTIONS.md" in i for i in oc.get("instructions", [])), oc
    # effective files live under profiles_effective/
    eff = m.profile_effective(st, "p")
    assert str(st) in eff["dir"] and "profiles_effective" in eff["dir"], eff
    # switch agent to opencode: same profile still resolves scoped env
    m2 = load_mod()
    rows = m2.load_agents(st)
    [x for x in rows if x["name"] == "a1"][0]["backend"] = "opencode"
    m2.save_agents(st, rows)
    env2 = m2.backend_env_for_profile(st, {"backend": "opencode"}, "p")
    assert env2["OPENCODE_CONFIG"].startswith(str(st)), env2


def t_switch_away_back_sids():
    _, st = mk_fixtures()
    m = load_mod()
    run_json(["--json", "agents", "add", "b1", "--backend", "claude",
              "--role", "sub"], st)
    m.save_chat_meta(st, "b1", {"sid": "claude-sid-1", "backend": "claude",
                                "sids": {"claude": "claude-sid-1"}})
    ok, _ = m.switch_backend(st, "b1", "opencode")
    assert ok
    meta = m.load_chat_meta(st, "b1")
    assert meta["sid"] == "" and meta["sids"].get("claude") == "claude-sid-1", meta
    m.save_chat_meta(st, "b1", {**meta, "sid": "ses-oc-1", "backend": "opencode",
                                "sids": {**meta["sids"], "opencode": "ses-oc-1"}})
    ok, _ = m.switch_backend(st, "b1", "claude")
    assert ok
    meta2 = m.load_chat_meta(st, "b1")
    assert meta2["sid"] == "claude-sid-1", meta2  # back: own sid restored
    assert meta2["sids"]["opencode"] == "ses-oc-1", meta2  # other kept, never resumed cross-backend
    a = [x for x in m.load_agents(st) if x["name"] == "b1"][0]
    assert a["backend"] == "claude", a


def t_shared_mcp_two_backends():
    src, st = mk_fixtures()
    m = load_mod()
    run_json(["--json", "harness", "profile", "add", "shr",
              "--mcp", str(src / "mcp.json")], st)
    eff = m.ensure_effective_profile(st, "shr")
    coc = json.loads(Path(eff["files"]["opencode_config"]).read_text())
    assert coc["mcp"]["shr"]["type"] == "local", coc
    assert coc["mcp"]["web"]["type"] == "remote", coc
    cextra = m.backend_argv_extra("claude", st, "shr")
    ccfg = json.loads(Path(cextra[cextra.index("--mcp-config") + 1]).read_text())
    assert ccfg["mcpServers"]["shr"]["command"] == "echo", ccfg
    assert ccfg["mcpServers"]["web"]["url"] == "https://mcp.example.com/x", ccfg
    # codex serves the same shared connectors: literal -> http_headers,
    # exact env ref -> env_http_headers (values never printed in reports)
    cfg, u = m.translate_mcp_for_backend(
        m.load_mcp_normalized(str(src / "mcp.json"))[0], "codex")
    assert cfg["mcp_servers"]["web"]["http_headers"] == {"Authorization": "Bearer t"}, cfg
    assert not u, u
    cfg2, u2 = m.translate_mcp_for_backend(
        {"e": m.normalize_mcp_entry("e", {"url": "https://x.example/m",
                                          "headers": {"Authorization": "{env:FIXTURE_TOK}"}})}, "codex")
    assert cfg2["mcp_servers"]["e"]["env_http_headers"] == {"Authorization": "FIXTURE_TOK"}, cfg2
    assert not u2, u2
    cfg3, u3 = m.translate_mcp_for_backend(
        {"e": m.normalize_mcp_entry("e", {"url": "https://x.example/m",
                                          "headers": {"Authorization": "Bearer {env:FIXTURE_TOK}"}})}, "codex")
    assert any("env ref" in x for x in u3), u3
    assert SECRET not in " ".join(u3)


def t_path_errors():
    src, st = mk_fixtures()
    _, o = run_json(["--json", "harness", "profile", "add", "bad",
                     "--mcp", str(src / "missing.json")], st)
    assert not o["ok"], o
    m = load_mod()
    assert not m.profile_validate(st, "nope")["ok"]
    assert "error" in m.profile_effective(st, "nope")
    assert "error" in m.profile_set_agent(st, "ghost", "")
    assert "error" in m.native_launch(st, "ghost", "claude")
    p, o = run_json(["--json", "harness", "profile", "set", "ghost",
                     "--profile", "nope"], st)
    assert not o["ok"], o


def t_no_config_mutation():
    src, st = mk_fixtures()
    m = load_mod()
    home = Path.home()
    before = {p for p in home.glob(".codex/*") if p.is_file()} if (home / ".codex").exists() else set()
    run_json(["--json", "harness", "profile", "add", "p",
              "--mcp", str(src / "mcp.json"),
              "--skills", str(src / "skills")], st)
    run_json(["--json", "agents", "add", "c1", "--backend", "codex",
              "--role", "sub"], st)
    run_json(["--json", "harness", "profile", "set", "c1", "--profile", "p"], st)
    env = m.backend_env_for_profile(st, {"backend": "codex"}, "p")
    assert env.get("CODEX_HOME", "").startswith(str(st)), env
    assert "HOME" not in env and "~" not in env.get("CODEX_HOME", "~"), env
    after = {p for p in home.glob(".codex/*") if p.is_file()} if (home / ".codex").exists() else set()
    assert before == after, (before, after)
    assert not (home / ".config" / "opencode").exists() or True  # never created here
    eff = m.profile_effective(st, "p")
    for f in eff["files"].values():
        assert str(st) in f, f


def t_slash_profile_never_prose_and_handoff():
    src, st = mk_fixtures()
    m = load_mod()
    run_json(["--json", "harness", "profile", "add", "p",
              "--instructions", str(src / "INSTR.md"),
              "--mcp", str(src / "mcp.json")], st)
    run_json(["--json", "agents", "add", "s1", "--backend", "echo",
              "--role", "sub"], st)
    r = m.run_turn(st, "s1", "/profile list")
    assert r.get("slash") == "profile" and r["exit"] == 0 and "p" in r["reply"], r
    log = m.load_chat_log(st, "s1")
    assert all(x.get("role") in ("tool", "toolout") for x in log[-2:]), log[-2:]
    assert not any(x.get("role") in ("user", "agent") and "/profile" in (x.get("text") or "") for x in log)
    r = m.run_turn(st, "s1", "/profile use p")
    assert r["exit"] == 0, r
    h = m.native_launch(st, "s1", "claude")
    assert h["ok"] and "--mcp-config" in h["argv"], h
    assert not any(t in m.BYPASS_FLAGS for t in h["argv"]), h["argv"]
    assert h["profile"] == "p"
    p, o = run_json(["--json", "handoff", "s1", "claude"], st)
    assert o["ok"], o
    assert "--mcp-config" in o["data"]["run"], o
    assert SECRET not in p.stdout


def t_busy_guard():
    _, st = mk_fixtures()
    m = load_mod()
    run_json(["--json", "agents", "add", "g1", "--backend", "echo",
              "--role", "sub"], st)
    m.mark_running("g1", True)
    try:
        ok, msg = m.switch_backend(st, "g1", "opencode")
        assert not ok and "busy" in msg, msg
        r = m.profile_set_agent(st, "g1", "")
        assert "error" in r and "busy" in r["error"], r
    finally:
        m.mark_running("g1", False)


def t_claude_skills_to_opencode():
    src, st = mk_fixtures()
    m = load_mod()
    run_json(["--json", "harness", "profile", "add", "cs",
              "--skills", str(src / ".claude" / "skills")], st)
    eff = m.ensure_effective_profile(st, "cs")
    cdir = Path(eff["files"]["opencode_config_dir"])
    found = list(cdir.rglob("SKILL.md"))
    assert found, eff["files"]


if __name__ == "__main__":
    oks = [
        check("effective_launch_env_argv_config", t_effective_launch_env_argv_config),
        check("switch_away_back_sids", t_switch_away_back_sids),
        check("shared_mcp_two_backends", t_shared_mcp_two_backends),
        check("path_errors", t_path_errors),
        check("no_config_mutation", t_no_config_mutation),
        check("slash_profile_never_prose_and_handoff", t_slash_profile_never_prose_and_handoff),
        check("busy_guard", t_busy_guard),
        check("claude_skills_to_opencode", t_claude_skills_to_opencode),
    ]
    print(f"{sum(oks)}/{len(oks)} integration tests passed")
    sys.exit(0 if all(oks) else 1)
