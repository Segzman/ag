#!/usr/bin/env python3
"""ag policy: pure renderers (golden text), validator errors, dry-run diffs. Stdlib, temp dirs."""
import importlib.machinery, importlib.util, io, json, tempfile, types, unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod_policy", AG)
_s = importlib.util.spec_from_loader("ag_mod_policy", _l)
ag = importlib.util.module_from_spec(_s)
_l.exec_module(ag)

SAMPLE = {
    "version": 1,
    "permission_floor": "edits",
    "allowed_models": ["claude-sonnet-4-5"],
    "denied_models": ["gpt-5"],
    "mcp_allow": ["github"],
    "secret_read_ban": ["~/.ssh/**", "**/.env"],
    "network": "on",
}

CLAUDE_GOLDEN = """{
  "permissions": {
    "defaultMode": "acceptEdits",
    "disableBypassPermissionsMode": "disable",
    "disableAutoMode": "disable",
    "deny": [
      "Read(~/.ssh/**)",
      "Read(//**/.env)"
    ]
  },
  "allowManagedMcpServersOnly": true,
  "allowedMcpServers": [
    {
      "serverName": "github"
    }
  ],
  "enabledMcpjsonServers": [
    "github"
  ],
  "availableModels": [
    "claude-sonnet-4-5"
  ],
  "deniedModels": [
    "gpt-5"
  ]
}
"""

CODEX_GOLDEN = """# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
allowed_approval_policies = ["on-request"]
allowed_sandbox_modes = ["read-only", "workspace-write"]

[mcp_servers.github]
# identity missing: set mcp_identity (command or url) or Codex cannot match this server

[permissions.filesystem]
deny_read = ["~/.ssh/**", "**/.env"]
"""

CODEX_CONFIG_GOLDEN = """# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
approval_policy = "on-request"
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = true
"""


def ns(**kw):
    base = dict(polsub="render", pfile="ag-policy.json", target="all", out=None, dry_run=False, json=False)
    base.update(kw)
    return types.SimpleNamespace(**base)


class Policy(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-policy-t-"))
        self.addCleanup(__import__("shutil").rmtree, self.td, True)

    def write_policy(self, doc, name="ag-policy.json"):
        p = self.td / name
        p.write_text(json.dumps(doc))
        return p

    def run_do(self, a):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = ag.do_policy(a)
        return rc, out.getvalue(), err.getvalue()

    def errs(self, doc):
        return ag.policy_validate(doc)[1]

    def test_render_golden(self):
        p = self.write_policy(SAMPLE)
        rc, out, err = self.run_do(ns(pfile=str(p), target="claude"))
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out, CLAUDE_GOLDEN)
        rc, out, err = self.run_do(ns(pfile=str(p), target="codex"))
        self.assertEqual((rc, err), (0, ""))
        self.assertEqual(out, CODEX_GOLDEN)
        rc, out, err = self.run_do(ns(pfile=str(p), target="codex-config"))
        self.assertEqual((rc, err, out), (0, "", CODEX_CONFIG_GOLDEN))

    def test_renderers_pure_and_targeted(self):
        clean, errs = ag.policy_validate(SAMPLE)
        self.assertEqual(errs, [])
        self.assertEqual(ag.claude_policy_render(clean), CLAUDE_GOLDEN)
        self.assertEqual(ag.codex_policy_render(clean), CODEX_GOLDEN)
        self.assertEqual(ag.codex_config_render(clean), CODEX_CONFIG_GOLDEN)
        self.assertEqual(ag.policy_render(clean, "claude"), ("managed-settings.json", CLAUDE_GOLDEN))
        self.assertEqual(ag.policy_render(clean, "codex"), ("requirements.toml", CODEX_GOLDEN))
        self.assertEqual(ag.policy_render(clean, "codex-config"), ("codex-config.toml", CODEX_CONFIG_GOLDEN))
        with self.assertRaises(ValueError):
            ag.policy_render(clean, "gemini")

    def test_floor_mapping_uses_mode_rank(self):
        ranks = {}
        for floor in ("ro", "edits", "auto"):
            clean, errs = ag.policy_validate({"version": 1, "permission_floor": floor})
            self.assertEqual(errs, [])
            ranks[floor] = ag._mode_rank(floor)
            self.assertEqual(ag.POLICY_CLAUDE_MODE[ranks[floor]], {"ro": "plan", "edits": "acceptEdits", "auto": "bypassPermissions"}[floor])
            approval, sandbox = ag.POLICY_CODEX_DEFAULT[ranks[floor]]
            self.assertEqual((approval, sandbox), {
                "ro": ("on-request", "read-only"),
                "edits": ("on-request", "workspace-write"),
                "auto": ("never", "workspace-write")}[floor])
            self.assertEqual(ag.POLICY_CODEX_ALLOW[ranks[floor]][0], {"ro": ["on-request"], "edits": ["on-request"], "auto": ["never"]}[floor])
        self.assertEqual([ranks[f] for f in ("ro", "edits", "auto")], [0, 1, 2])

    def test_network_off_adds_fetch_denies(self):
        clean, errs = ag.policy_validate({"version": 1, "secret_read_ban": ["**/.env"], "network": "off"})
        self.assertEqual(errs, [])
        doc = json.loads(ag.claude_policy_render(clean))
        self.assertIn("Read(//**/.env)", doc["permissions"]["deny"])
        self.assertIn("WebFetch", doc["permissions"]["deny"])
        self.assertIn("WebSearch", doc["permissions"]["deny"])
        self.assertEqual(doc["sandbox"]["network"], {"allowedDomains": []})
        self.assertTrue(doc["sandbox"]["failIfUnavailable"])
        self.assertIn("network_access = false", ag.codex_config_render(clean))

    def test_defaults(self):
        clean, errs = ag.policy_validate({"version": 1})
        self.assertEqual(errs, [])
        self.assertEqual(clean, {"version": 1, "permission_floor": "edits", "allowed_models": [],
            "denied_models": [], "mcp_allow": [], "mcp_identity": {}, "secret_read_ban": [], "network": "on"})

    def test_validator_unknown_keys_and_bad_values(self):
        self.assertIn("policy must be a JSON object", self.errs([])[0])
        self.assertTrue(any("unknown key(s): bogus" in e for e in self.errs({"version": 1, "bogus": 1})))
        self.assertTrue(any("version must be 1" in e for e in self.errs({"version": 2})))
        self.assertTrue(any("version must be 1" in e for e in self.errs({"version": True})))
        self.assertTrue(any("permission_floor must be ro|edits|auto" in e
            for e in self.errs({"version": 1, "permission_floor": "full"})))
        self.assertTrue(any("network must be on|off" in e for e in self.errs({"version": 1, "network": "maybe"})))
        self.assertTrue(any("allowed_models must be an array" in e
            for e in self.errs({"version": 1, "allowed_models": "x"})))
        self.assertTrue(any("denied_models[0] must be a non-empty string" in e
            for e in self.errs({"version": 1, "denied_models": [""]})))
        self.assertTrue(any("secret_read_ban[0] must be one line" in e
            for e in self.errs({"version": 1, "secret_read_ban": ["a\nb"]})))
        self.assertTrue(any("mcp_allow[0] has a bad value" in e
            for e in self.errs({"version": 1, "mcp_allow": ["bad name"]})))
        self.assertTrue(any("both allowed and denied" in e
            for e in self.errs({"version": 1, "allowed_models": ["m"], "denied_models": ["m"]})))

    def test_mcp_names_and_identity(self):
        self.assertTrue(any("mcp_allow[0] has a bad value" in e
            for e in self.errs({"version": 1, "mcp_allow": ["a.b"]})))  # serverName charset is [A-Za-z0-9_-]
        clean, errs = ag.policy_validate({"version": 1, "mcp_allow": ["gh"], "mcp_identity": {"gh": {"command": "npx"}}})
        self.assertEqual((errs, clean["mcp_identity"]), ([], {"gh": {"command": "npx"}}))
        self.assertIn('identity = { command = "npx" }', ag.codex_policy_render(clean))
        self.assertTrue(any("has no mcp_allow entry" in e
            for e in self.errs({"version": 1, "mcp_identity": {"gh": {"url": "https://x"}}})))
        self.assertTrue(any("must be {command} or {url}" in e
            for e in self.errs({"version": 1, "mcp_allow": ["gh"], "mcp_identity": {"gh": {"url": "u", "command": "c"}}})))
        self.assertTrue(any("mcp_identity must be an object" in e
            for e in self.errs({"version": 1, "mcp_identity": []})))

    def test_duplicates_are_deduped(self):
        clean, errs = ag.policy_validate({"version": 1, "allowed_models": ["m", "m"], "mcp_allow": ["x", "x"]})
        self.assertEqual(errs, [])
        self.assertEqual(clean["allowed_models"], ["m"])
        self.assertEqual(clean["mcp_allow"], ["x"])

    def test_check_ok_and_failure(self):
        p = self.write_policy(SAMPLE)
        rc, out, err = self.run_do(ns(polsub="check", pfile=str(p)))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("policy ok:", out)
        self.assertIn("floor=edits", out)
        bad = self.write_policy({"version": 1, "nope": True}, "bad.json")
        rc, out, err = self.run_do(ns(polsub="check", pfile=str(bad)))
        self.assertEqual(rc, 1)
        self.assertIn("unknown key(s): nope", err)
        rc, out, err = self.run_do(ns(polsub="check", pfile=str(self.td / "missing.json")))
        self.assertEqual(rc, 1)
        self.assertIn("no policy file:", err)

    def test_render_out_and_dry_run_diff(self):
        p = self.write_policy(SAMPLE)
        outdir = self.td / "out"
        rc, out, err = self.run_do(ns(pfile=str(p), out=str(outdir)))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("wrote", out)
        self.assertEqual((outdir / "managed-settings.json").read_text(), CLAUDE_GOLDEN)
        self.assertEqual((outdir / "requirements.toml").read_text(), CODEX_GOLDEN)
        self.assertEqual((outdir / "codex-config.toml").read_text(), CODEX_CONFIG_GOLDEN)

        # unchanged policy -> no diff, files untouched
        rc, out, err = self.run_do(ns(pfile=str(p), out=str(outdir), dry_run=True))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("no changes:", out)

        # network flip -> unified diff, nothing written
        self.write_policy(dict(SAMPLE, network="off"))
        before = (outdir / "requirements.toml").read_text()
        rc, out, err = self.run_do(ns(pfile=str(p), out=str(outdir), dry_run=True))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("--- a/", out)
        self.assertIn("+++ b/", out)
        self.assertIn('"WebFetch"', out)
        self.assertIn('"sandbox"', out)
        self.assertEqual((outdir / "requirements.toml").read_text(), before)

    def test_policy_diff_pure(self):
        self.assertEqual(ag.policy_diff("a\n", "a\n", "f"), "")
        d = ag.policy_diff("a\n", "b\n", "f")
        self.assertIn("--- a/f", d)
        self.assertIn("+++ b/f", d)
        self.assertIn("-a", d)
        self.assertIn("+b", d)


if __name__ == "__main__":
    unittest.main()
