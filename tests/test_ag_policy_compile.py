#!/usr/bin/env python3
"""ag policy compile: .agent/policy.toml|json discovery, per-backend compile + --check drift,
ag-side model/mode/cmd gates, ledger events, wake/chat auto-compile env wiring. Stdlib, temp dirs, no network."""
import importlib.machinery, importlib.util, io, json, os, tempfile, unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod_policy_compile", AG)
_s = importlib.util.spec_from_loader("ag_mod_policy_compile", _l)
ag = importlib.util.module_from_spec(_s)
_l.exec_module(ag)

POLICY = {
    "version": 1,
    "permission_floor": "auto",
    "max_mode": "edits",
    "allow_cmds": ["git status", "pytest *"],
    "deny_cmds": ["rm -rf *", "sudo *"],
    "deny_paths": ["**/.env", "~/.ssh/**"],
    "network": "off",
    "models_allow": ["claude/*", "opencode/*"],
    "models_deny": ["*/gpt-5*"],
    "data_class": {"work": {"models_allow": ["claude/sonnet*"]}},
}

TOML = """version = 1
permission_floor = "auto"
max_mode = "edits"
network = "off"
allow_cmds = ["git status", "pytest *"]
deny_cmds = ["rm -rf *", "sudo *"]
deny_paths = ["**/.env", "~/.ssh/**"]
models_allow = ["claude/*", "opencode/*"]
models_deny = ["*/gpt-5*"]

[data_class.work]
models_allow = ["claude/sonnet*"]
"""


def _run_main(argv, cwd):
    """Run ag.main() in-process with cwd set; returns (rc, out, err)."""
    old = os.getcwd()
    out, err = io.StringIO(), io.StringIO()
    try:
        os.chdir(cwd)
        with redirect_stdout(out), redirect_stderr(err):
            rc = ag.main(argv)
    finally:
        os.chdir(old)
    return rc, out.getvalue(), err.getvalue()


class ValidateCompileEra(unittest.TestCase):
    def test_new_keys_validate_and_merge(self):
        clean, errs = ag.policy_validate(POLICY)
        self.assertEqual(errs, [])
        self.assertEqual(clean["permission_floor"], "edits")  # max_mode caps auto -> edits
        self.assertIn("**/.env", clean["secret_read_ban"])     # deny_paths merged for renderers
        self.assertEqual(clean["data_class"]["work"]["models_allow"], ["claude/sonnet*"])

    def test_bad_new_keys_rejected(self):
        _, errs = ag.policy_validate({"version": 1, "max_mode": "root"})
        self.assertTrue(any("max_mode" in e for e in errs))
        _, errs = ag.policy_validate({"version": 1, "data_class": {"secret": {}}})
        self.assertTrue(any("data_class" in e for e in errs))
        _, errs = ag.policy_validate({"version": 1, "bogus_key": 1})
        self.assertTrue(any("bogus_key" in e for e in errs))

    def test_toml_loads_same_schema(self):
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "policy.toml"
            fp.write_text(TOML)
            doc, err = ag.policy_load_file(fp)
            self.assertIsNone(err)
            clean, errs = ag.policy_validate(doc)
            self.assertEqual(errs, [])
            self.assertEqual(clean["allow_cmds"], POLICY["allow_cmds"])
            self.assertEqual(clean["permission_floor"], "edits")


class RenderCmds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p, errs = ag.policy_validate(POLICY)
        assert not errs, errs

    def test_claude_bash_rules(self):
        _, text = ag.policy_render(self.p, "claude")
        doc = json.loads(text)
        self.assertIn("Bash(git status:*)", doc["permissions"]["allow"])
        self.assertIn("Bash(rm -rf *)", doc["permissions"]["deny"])  # glob pattern verbatim
        self.assertIn("Read(//**/.env)", doc["permissions"]["deny"])

    def test_opencode_shell_rules_deny_last(self):
        _, text = ag.policy_render(self.p, "opencode")
        perms = json.loads(text)["permissions"]
        shell = [(x["resource"], x["effect"]) for x in perms if x["action"] == "shell"]
        self.assertIn(("git status", "allow"), shell)
        self.assertEqual(shell[-1], ("sudo *", "deny"))

    def test_codex_unverified_fields_are_todo_comments(self):
        _, text = ag.policy_render(self.p, "codex")
        self.assertIn("# TODO(policy): allow_cmds", text)
        self.assertIn("# TODO(policy): models_allow", text)
        self.assertIn('deny_read = ["**/.env", "~/.ssh/**"]', text)
        self.assertNotIn("allow_cmds =", text)


class Gates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p, _ = ag.policy_validate(POLICY)

    def test_model_gate_top_level(self):
        self.assertIsNone(ag.policy_model_error(self.p, "claude", "opus", "public"))
        self.assertIsNotNone(ag.policy_model_error(self.p, "codex", "default", "public"))
        self.assertIsNotNone(ag.policy_model_error(self.p, "claude", "gpt-5", "public"))  # models_deny wins

    def test_model_gate_data_class_overrides(self):
        self.assertIsNone(ag.policy_model_error(self.p, "claude", "sonnet", "work"))
        self.assertIsNotNone(ag.policy_model_error(self.p, "claude", "opus", "work"))  # work list is sonnet only
        self.assertIsNone(ag.policy_model_error(self.p, "claude", "opus", "public"))   # public falls back to top level

    def test_mode_gate(self):
        self.assertIsNone(ag.policy_mode_error(self.p, "edits"))
        self.assertIsNone(ag.policy_mode_error(self.p, "", plan=True))
        self.assertIsNotNone(ag.policy_mode_error(self.p, "auto"))
        self.assertIsNotNone(ag.policy_mode_error(self.p, "full"))

    def test_cmd_gate(self):
        self.assertIsNone(ag.policy_cmd_error(self.p, "git status"))
        self.assertIsNone(ag.policy_cmd_error(self.p, "pytest -q tests/"))
        self.assertIsNotNone(ag.policy_cmd_error(self.p, "rm -rf /tmp/x"))
        self.assertIsNotNone(ag.policy_cmd_error(self.p, "sudo ls"))
        self.assertIsNotNone(ag.policy_cmd_error(self.p, "make build"))  # allow_cmds non-empty, no match


class DiscoveryCompileLog(unittest.TestCase):
    def test_find_order_state_dir_first(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / ".agent"; sdir.mkdir()
            (sdir / "policy.json").write_text(json.dumps(POLICY))
            self.assertEqual(ag.policy_find(sdir, td), sdir / "policy.json")
            p, src, err = ag.policy_load(sdir, td)
            self.assertIsNone(err); self.assertIsNotNone(p); self.assertEqual(src, sdir / "policy.json")
            empty = Path(td) / "empty"; empty.mkdir()
            self.assertEqual(ag.policy_load(Path(td) / "nope", empty), (None, None, None))

    def test_compile_and_check_drift(self):
        with tempfile.TemporaryDirectory() as td:
            p, _ = ag.policy_validate(POLICY)
            out = Path(td) / "compiled"
            drift, _txt = ag.policy_compile_check(p, "claude", out)
            self.assertTrue(drift)  # nothing written yet
            self.assertFalse(out.exists())  # --check writes nothing
            written, _ = ag.policy_compile(p, "claude", out)
            self.assertEqual(written, [str(out / "claude" / "managed-settings.json")])
            drift, txt = ag.policy_compile_check(p, "claude", out)
            self.assertFalse(drift); self.assertIn("no changes", txt)
            written2, _ = ag.policy_compile(p, "codex", out)
            self.assertEqual(len(written2), 2)  # requirements.toml + codex-config.toml

    def test_unknown_backend_gets_todo_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            p, _ = ag.policy_validate(POLICY)
            written, _ = ag.policy_compile(p, "echo", Path(td))
            self.assertEqual(len(written), 1)
            self.assertIn("TODO(policy)", Path(written[0]).read_text())

    def test_ledger_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td)
            ag.policy_log_event(sdir, "deny", agent="w", backend="claude", reason="nope")
            ag.policy_log_event(sdir, "compile", agent="w", backend="claude", files=["x"])
            rows = ag.policy_read_log(sdir)
            self.assertEqual([r["action"] for r in rows], ["deny", "compile"])
            self.assertTrue(all(r["type"] == "policy" for r in rows))


class CliAndTurnWiring(unittest.TestCase):
    def test_cli_compile_check_log(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / ".agent"; sdir.mkdir()
            (sdir / "policy.toml").write_text(TOML)
            rc, out, _ = _run_main(["--dir", str(sdir), "policy", "compile", "--backend", "claude"], td)
            self.assertEqual(rc, 0, out)
            self.assertTrue((sdir / "policy-compiled" / "_cli" / "claude" / "managed-settings.json").is_file())
            rc, out, _ = _run_main(["--dir", str(sdir), "policy", "compile", "--backend", "claude", "--check"], td)
            self.assertEqual(rc, 0, out)  # no drift after compile
            (sdir / "policy.toml").write_text(TOML.replace('"off"', '"on"'))
            rc, out, _ = _run_main(["--dir", str(sdir), "policy", "compile", "--backend", "claude", "--check"], td)
            self.assertEqual(rc, 1)  # drift exits nonzero
            self.assertIn("WebFetch", out)
            rc, out, _ = _run_main(["--dir", str(sdir), "policy", "log"], td)
            self.assertEqual(rc, 0)
            self.assertIn("compile", out)

    def test_cli_run_denied_by_policy_and_logged(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / ".agent"; sdir.mkdir()
            (sdir / "policy.json").write_text(json.dumps(POLICY))
            rc, _out, err = _run_main(["--dir", str(sdir), "run", "--", "rm", "-rf", "/tmp/x"], td)
            self.assertEqual(rc, 1)
            self.assertIn("deny_cmds", err)
            rows = ag.policy_read_log(sdir)
            self.assertEqual(rows[-1]["action"], "deny")

    def test_turn_env_auto_compile_opencode(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / ".agent"; sdir.mkdir()
            (sdir / "policy.json").write_text(json.dumps(POLICY))
            env = ag._policy_turn_env(sdir, {"name": "w1", "backend": "opencode"}, "opencode", td, {})
            cfg = Path(env["OPENCODE_CONFIG"])
            self.assertTrue(cfg.is_file())
            perms = json.loads(cfg.read_text())["permissions"]
            self.assertIn({"action": "shell", "resource": "sudo *", "effect": "deny"}, perms)
            rows = ag.policy_read_log(sdir)
            self.assertEqual(rows[-1]["action"], "compile")

    def test_turn_env_no_policy_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / ".agent"; sdir.mkdir()
            env = {"A": "1"}
            self.assertEqual(ag._policy_turn_env(sdir, {"name": "w"}, "claude", td, env), env)
            self.assertEqual(ag.policy_read_log(sdir), [])

    def test_gate_uses_agent_workdir_policy(self):
        with tempfile.TemporaryDirectory() as td:
            sdir = Path(td) / "state"; sdir.mkdir()
            proj = Path(td) / "proj"; (proj / ".agent").mkdir(parents=True)
            (proj / ".agent" / "policy.json").write_text(json.dumps(POLICY))
            err, _p = ag._policy_gate(sdir, {"dir": str(proj), "data": "work", "mode": "edits"}, "claude", "opus")
            self.assertIsNotNone(err)  # work data_class allows sonnet only
            err, _p = ag._policy_gate(sdir, {"dir": str(proj), "data": "work", "mode": "edits"}, "claude", "sonnet")
            self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
