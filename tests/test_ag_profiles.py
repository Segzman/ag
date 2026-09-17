#!/usr/bin/env python3
"""Persisted harness profile tests (live ag).

Opt-in named sources (instructions/memory/skills/MCP + backend config),
shared across agents, scoped runtime files, no global writes.
Fake fixtures only; echo backend; no real models/connectors.
No secrets in CLI output (fixture marker asserted absent).

Run: python3 tests/test_ag_profiles.py
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
assert SECRET not in Path(AG).read_text(), "secret marker must not be in ag"


def load_mod():
    return importlib.machinery.SourceFileLoader("agmod", AG).load_module()


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


def fixtures():
    src = Path(tempfile.mkdtemp(prefix="ag-prof-src-"))
    (src / "skills" / "demo-skill").mkdir(parents=True)
    (src / "skills" / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: fixture skill for profile tests\n---\n"
        "# demo\nUse me for fixtures.\n")
    (src / "INSTR.md").write_text("# instructions\nFollow repo style.\n")
    (src / "MEM.md").write_text("# memory\nRemember fixture context.\n")
    (src / "mcp.json").write_text(json.dumps({
        "mcpServers": {
            "local-one": {"command": "npx", "args": ["-y", "mcp-demo"],
                          "env": {"DEMO_KEY": SECRET}},
            "web-one": {"url": "https://mcp.example.com/mcp",
                        "headers": {"Authorization": "Bearer t",
                                    "X-Api-Key": "{env:FIXTURE_API_KEY}"}},
            "web-embedded": {"url": "https://mcp.example.com/other",
                             "headers": {"Authorization": "Bearer {env:FIXTURE_EMBEDDED}"}},
        }}))
    (src / "opencode.json").write_text(json.dumps(
        {"$schema": "https://opencode.ai/config.json",
         "model": "fixture/model"}))
    (src / "codex.toml").write_text('model = "fixture-model"\n')
    state = Path(tempfile.mkdtemp(prefix="ag-prof-state-"))
    return src, state


def check(name, fn):
    try:
        fn()
    except Exception as e:
        print(f"FAIL {name}: {e}")
        return False
    print(f"PASS {name}")
    return True


def t_add_validate_show():
    src, st = fixtures()
    m = load_mod()
    p, o = run_json(["--json", "harness", "profile", "add", "demo",
                     "--instructions", str(src / "INSTR.md"),
                     "--memory", str(src / "MEM.md"),
                     "--skills", str(src / "skills"),
                     "--mcp", str(src / "mcp.json"),
                     "--opencode-config", str(src / "opencode.json"),
                     "--codex-config", str(src / "codex.toml")], st)
    assert o["ok"], o
    assert SECRET not in p.stdout + p.stderr, "secret leaked in add output"
    v = m.profile_validate(st, "demo")
    assert v["ok"], v
    assert any("codex" in u and "env ref" in u for u in v["unsupported"]), v  # embedded ref bounded
    p, o = run_json(["--json", "harness", "profile", "show", "demo"], st)
    assert o["ok"], o
    assert SECRET not in p.stdout, "secret leaked in show output"
    files = o["data"]["effective"]["files"]
    assert "opencode_config" in files and "codex_config" in files, files
    # codex scoped home keeps skills access: profile skill mirrored in
    eff = m.ensure_effective_profile(st, "demo")
    assert (Path(eff["dir"]) / "codex-home" / "skills" / "demo-skill" / "SKILL.md").is_file(), eff


def t_opencode_translation():
    src, st = fixtures()
    m = load_mod()
    run_json(["--json", "harness", "profile", "add", "demo",
              "--mcp", str(src / "mcp.json"),
              "--skills", str(src / "skills"),
              "--instructions", str(src / "INSTR.md")], st)
    eff = m.ensure_effective_profile(st, "demo")
    oc = json.loads(Path(eff["files"]["opencode_config"]).read_text())
    assert oc["mcp"]["local-one"]["type"] == "local", oc
    assert oc["mcp"]["local-one"]["command"] == ["npx", "-y", "mcp-demo"], oc
    assert oc["mcp"]["local-one"]["environment"]["DEMO_KEY"] == SECRET
    assert oc["mcp"]["web-one"]["type"] == "remote", oc
    assert oc["mcp"]["web-one"]["url"] == "https://mcp.example.com/mcp", oc
    assert oc["mcp"]["web-one"]["headers"]["Authorization"] == "Bearer t", oc
    assert any("INSTRUCTIONS.md" in i for i in oc.get("instructions", [])), oc


def t_codex_translation():
    src, st = fixtures()
    m = load_mod()
    run_json(["--json", "harness", "profile", "add", "demo",
              "--mcp", str(src / "mcp.json")], st)
    eff = m.ensure_effective_profile(st, "demo")
    toml = Path(eff["files"]["codex_config"]).read_text()
    assert '[mcp_servers."local-one"]' in toml, toml
    assert 'command = "npx"' in toml, toml
    assert SECRET in toml  # env value preserved in scoped file
    assert '[mcp_servers."web-one"]' in toml and "https://mcp.example.com/mcp" in toml, toml
    # shared connectors work: literal -> http_headers, exact env ref -> env_http_headers
    assert 'http_headers = { "Authorization" = "Bearer t" }' in toml, toml
    assert 'env_http_headers = { "X-Api-Key" = "FIXTURE_API_KEY" }' in toml, toml
    assert any("codex" in u and "env ref" in u for u in eff["unsupported"]), eff
    assert SECRET not in " ".join(eff["unsupported"])


def t_claude_argv():
    src, st = fixtures()
    m = load_mod()
    run_json(["--json", "harness", "profile", "add", "demo",
              "--mcp", str(src / "mcp.json"),
              "--skills", str(src / "skills")], st)
    extra = m.backend_argv_extra("claude", st, "demo")
    assert "--mcp-config" in extra and "--plugin-dir" in extra, extra
    cfg = json.loads(Path(extra[extra.index("--mcp-config") + 1]).read_text())
    assert cfg["mcpServers"]["local-one"]["command"] == "npx", cfg
    assert cfg["mcpServers"]["local-one"]["env"]["DEMO_KEY"] == SECRET


def t_reaches_backend_and_shared():
    src, st = fixtures()
    run_json(["--json", "harness", "profile", "add", "demo",
              "--instructions", str(src / "INSTR.md"),
              "--memory", str(src / "MEM.md")], st)
    for a in ("w1", "w2"):
        p, o = run_json(["--json", "agents", "add", a,
                         "--backend", "echo", "--role", "sub"], st)
        assert o["ok"], o
        p, o = run_json(["--json", "harness", "profile", "set", a,
                         "--profile", "demo"], st)
        assert o["ok"] and o["data"]["profile"] == "demo", o
    p, o = run_json(["--json", "chat", "send", "w1", "hi"], st)
    assert o["ok"], o
    assert "Follow repo style" in o["data"]["reply"], o
    assert "Remember fixture context" in o["data"]["reply"], o


def t_sid_invalidated_persona_kept():
    src, st = fixtures()
    m = load_mod()
    run_json(["--json", "harness", "profile", "add", "demo",
              "--instructions", str(src / "INSTR.md")], st)
    run_json(["--json", "agents", "add", "w1", "--backend", "echo",
              "--role", "sub"], st)
    m.save_chat_meta(st, "w1", {"sid": "ses-fake", "sids": {"echo": "ses-fake"}})
    p, o = run_json(["--json", "harness", "profile", "set", "w1",
                     "--profile", "demo"], st)
    assert o["ok"] and o["data"]["sid_cleared"] is True, o
    got = m.load_chat_meta(st, "w1")
    assert got.get("sid") == "" and got.get("sids") == {}, got
    r = [x for x in m.load_agents(st) if x["name"] == "w1"][0]
    assert r["dir"] == "." and r["role"] == "sub", r


def t_scoped_no_globals():
    src, st = fixtures()
    m = load_mod()
    run_json(["--json", "harness", "profile", "add", "demo",
              "--mcp", str(src / "mcp.json"),
              "--skills", str(src / "skills")], st)
    env = m.backend_env_for_profile(st, {"backend": "opencode"}, "demo")
    assert env["OPENCODE_CONFIG"].startswith(str(st)), env
    assert env["OPENCODE_CONFIG_DIR"].startswith(str(st)), env
    assert Path.home().as_posix() not in env["OPENCODE_CONFIG"], env
    assert "~/.config" not in env["OPENCODE_CONFIG"], env
    cenv = m.backend_env_for_profile(st, {"backend": "codex"}, "demo")
    assert cenv.get("CODEX_HOME", "").startswith(str(st)), cenv


def t_no_secret_in_status():
    src, st = fixtures()
    run_json(["--json", "harness", "profile", "add", "demo",
              "--mcp", str(src / "mcp.json"),
              "--skills", str(src / "skills"),
              "--instructions", str(src / "INSTR.md")], st)
    for args in (["harness", "profile", "list"],
                 ["harness", "profile", "show", "demo"],
                 ["harness", "profile", "validate", "demo"],
                 ["harness", "profile", "status"]):
        p = run_ag(args, st)
        assert SECRET not in p.stdout + p.stderr, f"leak in {args}"
        p, o = run_json(["--json"] + args, st)
        assert o["ok"], o
        assert SECRET not in p.stdout, f"leak in --json {args}"


def t_invalid_and_rm_guard():
    src, st = fixtures()
    p, o = run_json(["--json", "harness", "profile", "add", "bad name!",
                     "--instructions", str(src / "INSTR.md")], st)
    assert not o["ok"], o
    p, o = run_json(["--json", "harness", "profile", "add", "demo",
                     "--mcp", str(src / "nope.json")], st)
    assert not o["ok"], o
    run_json(["--json", "harness", "profile", "add", "demo",
              "--instructions", str(src / "INSTR.md")], st)
    run_json(["--json", "agents", "add", "w1", "--backend", "echo",
              "--role", "sub"], st)
    run_json(["--json", "harness", "profile", "set", "w1",
              "--profile", "demo"], st)
    p, o = run_json(["--json", "harness", "profile", "rm", "demo"], st)
    assert not o["ok"] and "in use" in o["error"], o
    p, o = run_json(["--json", "harness", "profile", "rm", "demo", "--force"], st)
    assert o["ok"], o


def t_existing_harness_unaffected():
    _, st = fixtures()
    p = run_ag(["harness", "show"], st)
    assert p.returncode == 0, p.stderr
    src, _ = fixtures()
    (st / "fakehome" / ".claude" / "skills").mkdir(parents=True)
    (st / "fakehome" / ".claude" / "skills" / "s.md").write_text("hi")
    p = run_ag(["harness", "show", "--home", str(st / "fakehome")], st)
    assert p.returncode == 0, p.stderr
    p = run_ag(["harness", "link", "--to", str(st / "linked"),
                "--home", str(st / "fakehome")], st)
    assert p.returncode == 0, p.stderr


if __name__ == "__main__":
    oks = [
        check("add_validate_show", t_add_validate_show),
        check("opencode_translation", t_opencode_translation),
        check("codex_translation", t_codex_translation),
        check("claude_argv", t_claude_argv),
        check("reaches_backend_and_shared", t_reaches_backend_and_shared),
        check("sid_invalidated_persona_kept", t_sid_invalidated_persona_kept),
        check("scoped_no_globals", t_scoped_no_globals),
        check("no_secret_in_status", t_no_secret_in_status),
        check("invalid_and_rm_guard", t_invalid_and_rm_guard),
        check("existing_harness_unaffected", t_existing_harness_unaffected),
    ]
    print(f"{sum(oks)}/{len(oks)} profile tests passed")
    sys.exit(0 if all(oks) else 1)
