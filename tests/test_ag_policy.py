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

TARGET_GOLDEN = {
    "claude": ("managed-settings.json", r"""{
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
"""),
    "codex": ("requirements.toml", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
allowed_approval_policies = ["on-request"]
allowed_sandbox_modes = ["read-only", "workspace-write"]

[mcp_servers.github]
# identity missing: set mcp_identity (command or url) or Codex cannot match this server

[permissions.filesystem]
deny_read = ["~/.ssh/**", "**/.env"]
"""),
    "codex-config": ("codex-config.toml", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
approval_policy = "on-request"
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
network_access = true
"""),
    "gemini": ("settings.json", r"""{
  "tools": {
    "sandbox": true,
    "sandboxNetworkAccess": true
  },
  "model": {
    "name": "claude-sonnet-4-5"
  }
}
"""),
    "gemini-policy": ("ag.toml", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.

[[rule]]
toolName = "write_file"
decision = "allow"
priority = 100

[[rule]]
toolName = "run_shell_command"
decision = "ask_user"
priority = 100

[[rule]]
toolName = "read_file"
argsPattern = ".*(\\~/\\.ssh/.*|(.*/)?\\.env).*"
decision = "deny"
priority = 900

[[rule]]
toolName = "mcp_*"
decision = "deny"
priority = 800

[[rule]]
toolName = "mcp_github_*"
decision = "allow"
priority = 900
"""),
    "qwen": ("settings.json", r"""{
  "tools": {
    "approvalMode": "auto-edit"
  },
  "model": {
    "name": "claude-sonnet-4-5"
  },
  "permissions": {
    "deny": [
      "Read(~/.ssh/**)",
      "Read(**/.env)"
    ]
  },
  "mcp": {
    "allowed": [
      "github"
    ]
  }
}
"""),
    "cursor": ("cli-config.json", r"""{
  "permissions": {
    "allow": [
      "Read(**)",
      "Write(**)",
      "Edit(**)"
    ],
    "deny": [
      "Read(~/.ssh/**)",
      "Read(**/.env)"
    ]
  },
  "sandbox": {
    "networkAccess": true
  },
  "model": "claude-sonnet-4-5"
}
"""),
    "copilot": ("managed-settings.json", r"""{
  "permissions": {
    "allow": [
      "Read(**)",
      "Edit(**)",
      "Write(**)"
    ],
    "deny": [
      "Read(~/.ssh/**)",
      "Read(**/.env)"
    ]
  },
  "allowedMcpServers": [
    {
      "serverName": "github"
    }
  ],
  "model": "claude-sonnet-4-5"
}
"""),
    "amp": ("settings.json", r"""{}
"""),
    "goose": ("config.yaml", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
GOOSE_MODE: "approve"
GOOSE_MODEL: "claude-sonnet-4-5"
"""),
    "aider": (".aider.conf.yml", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
model: "claude-sonnet-4-5"
"""),
    "aider-ignore": (".aiderignore", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
# not an access boundary: Aider still reads ignored files when asked
~/.ssh/**
**/.env
"""),
    "cline": (".clineignore", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
# not a security boundary: enforce with a PreToolUse hook
~/.ssh/**
**/.env
"""),
    "roo": (".rooignore", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
# file tools only: shell commands are not covered
~/.ssh/**
**/.env
"""),
    "roo-modes": (".roomodes", r"""{
  "customModes": [
    {
      "slug": "ag-edits",
      "name": "ag edits",
      "roleDefinition": "Read and edit files; no shell commands.",
      "groups": [
        "read",
        "edit"
      ]
    }
  ]
}
"""),
    "kilo": ("kilo.json", r"""{
  "permission": {
    "edit": "allow",
    "bash": "ask",
    "read": {
      "*": "allow",
      "~/.ssh/**": "deny",
      "**/.env": "deny"
    }
  },
  "model": "claude-sonnet-4-5"
}
"""),
    "continue": ("permissions.yaml", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
allow:
  - "Read(*)"
  - "Write"
  - "Edit"
ask:
  - "Bash"
exclude:
  - "Read(~/.ssh/**)"
  - "Read(**/.env)"
"""),
    "windsurf": (".devinignore", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
# Cascade file access only: the terminal can still read these paths
~/.ssh/**
**/.env
"""),
    "crush": ("crush.json", r"""{
  "options": {
    "disabled_tools": [
      "bash"
    ]
  }
}
"""),
    "augment": ("settings.json", r"""{
  "toolPermissions": [
    {
      "toolName": "edit",
      "permission": {
        "type": "allow"
      }
    },
    {
      "toolName": "write",
      "permission": {
        "type": "allow"
      }
    },
    {
      "toolName": "terminal",
      "permission": {
        "type": "ask-user"
      }
    }
  ],
  "defaultModel": "claude-sonnet-4-5"
}
"""),
    "factory": (".factory/settings.json", r"""{
  "sandbox": {
    "filesystem": {
      "denyRead": [
        "~/.ssh/**",
        "**/.env"
      ],
      "denyWrite": [
        "~/.ssh/**",
        "**/.env"
      ]
    },
    "enabled": true
  },
  "modelPolicy": {
    "allowCustomModels": false,
    "allowedModelIds": [
      "claude-sonnet-4-5"
    ],
    "blockedModelIds": [
      "gpt-5"
    ]
  }
}
"""),
    "grok": ("config.toml", r"""# ag policy v0: field names follow vendor docs as of 2026-11; re-check them against each release.
[ui]
permission_mode = "ask"

[permission]
allow = ["Edit"]
deny = ["Read(~/.ssh/**)", "Read(**/.env)", "Edit(~/.ssh/**)", "Edit(**/.env)"]

[models]
default = "claude-sonnet-4-5"
allowed_models = ["claude-sonnet-4-5"]
disabled_models = ["gpt-5"]

[sandbox]
profile = "workspace"
"""),
    "kiro": ("managed-settings.json", r"""{
  "rules": [
    {
      "capability": "fs_read",
      "match": [
        "~/.ssh/**",
        "**/.env"
      ],
      "effect": "deny"
    },
    {
      "capability": "fs_write",
      "match": [
        "~/.ssh/**",
        "**/.env"
      ],
      "effect": "deny"
    },
    {
      "capability": "mcp",
      "match": [
        "*"
      ],
      "exclude": [
        "github"
      ],
      "effect": "deny"
    }
  ]
}
"""),
    "zed": ("settings.json", r"""{
  "agent": {
    "tool_permissions": {
      "default": "confirm",
      "tools": {
        "terminal": {
          "default": "confirm"
        },
        "edit_file": {
          "default": "allow",
          "always_deny": [
            {
              "pattern": "(\\~/\\.ssh/.*|(.*/)?\\.env)"
            }
          ]
        },
        "write_file": {
          "default": "allow",
          "always_deny": [
            {
              "pattern": "(\\~/\\.ssh/.*|(.*/)?\\.env)"
            }
          ]
        }
      }
    }
  }
}
"""),
    "opencode": ("opencode.json", r"""{
  "$schema": "https://opencode.ai/config.json",
  "permissions": [
    {
      "action": "read",
      "resource": "**",
      "effect": "allow"
    },
    {
      "action": "read",
      "resource": "~/.ssh/**",
      "effect": "deny"
    },
    {
      "action": "read",
      "resource": "**/.env",
      "effect": "deny"
    },
    {
      "action": "edit",
      "resource": "**",
      "effect": "allow"
    },
    {
      "action": "shell",
      "resource": "*",
      "effect": "ask"
    }
  ],
  "model": "claude-sonnet-4-5",
  "policies": [
    {
      "action": "mcp.use",
      "resource": "github",
      "effect": "allow"
    }
  ]
}
"""),
}


CLAUDE_GOLDEN = TARGET_GOLDEN["claude"][1]
CODEX_GOLDEN = TARGET_GOLDEN["codex"][1]
CODEX_CONFIG_GOLDEN = TARGET_GOLDEN["codex-config"][1]


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
        self.assertEqual(ag.claude_policy_render(clean), ("managed-settings.json", CLAUDE_GOLDEN))
        self.assertEqual(ag.codex_policy_render(clean), ("requirements.toml", CODEX_GOLDEN))
        self.assertEqual(ag.codex_config_render(clean), ("codex-config.toml", CODEX_CONFIG_GOLDEN))
        self.assertEqual(ag.policy_render(clean, "claude"), ("managed-settings.json", CLAUDE_GOLDEN))
        self.assertEqual(ag.policy_render(clean, "codex"), ("requirements.toml", CODEX_GOLDEN))
        self.assertEqual(ag.policy_render(clean, "codex-config"), ("codex-config.toml", CODEX_CONFIG_GOLDEN))
        self.assertEqual(ag.policy_render(clean, "gemini"), TARGET_GOLDEN["gemini"])
        with self.assertRaises(ValueError):
            ag.policy_render(clean, "nope")

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
        doc = json.loads(ag.claude_policy_render(clean)[1])
        self.assertIn("Read(//**/.env)", doc["permissions"]["deny"])
        self.assertIn("WebFetch", doc["permissions"]["deny"])
        self.assertIn("WebSearch", doc["permissions"]["deny"])
        self.assertEqual(doc["sandbox"]["network"], {"allowedDomains": []})
        self.assertTrue(doc["sandbox"]["failIfUnavailable"])
        self.assertIn("network_access = false", ag.codex_config_render(clean)[1])

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
        self.assertIn('identity = { command = "npx" }', ag.codex_policy_render(clean)[1])
        self.assertTrue(any("has no mcp_allow entry" in e
            for e in self.errs({"version": 1, "mcp_identity": {"gh": {"url": "https://x"}}})))
        self.assertTrue(any("must be {command} or {url}" in e
            for e in self.errs({"version": 1, "mcp_allow": ["gh"], "mcp_identity": {"gh": {"url": "u", "command": "c"}}})))
        self.assertTrue(any("mcp_identity must be an object" in e
            for e in self.errs({"version": 1, "mcp_identity": []})))

    def test_every_target_golden_for_sample(self):
        clean, errs = ag.policy_validate(SAMPLE)
        self.assertEqual(errs, [])
        self.assertEqual(sorted(TARGET_GOLDEN), sorted(ag.POLICY_TARGETS))
        for t, want in TARGET_GOLDEN.items():
            self.assertEqual(ag.policy_render(clean, t), want, t)

    def test_coverage_table_consistent(self):
        levels = {"native", "approx", "none"}
        for t, spec in ag.POLICY_TARGETS.items():
            self.assertEqual(len(spec["levels"]), len(ag.POLICY_FIELDS), t)
            self.assertTrue(set(spec["levels"]) <= levels, t)
            self.assertTrue(spec["doc"].startswith("https://"), t)
            self.assertTrue(spec["scope"], t)
        rows = ag.policy_coverage()
        self.assertEqual([r["target"] for r in rows], list(ag.POLICY_TARGETS))
        for r in rows:
            self.assertEqual(set(r["fields"]), set(ag.POLICY_FIELDS), r["target"])
            self.assertEqual(r["file"], ag.policy_render(ag.policy_validate({"version": 1})[0], r["target"])[0])
        text = ag.policy_coverage_text(rows)
        for t in ag.POLICY_TARGETS: self.assertIn(t, text)

    def test_coverage_notes_for_policy_fields(self):
        clean, _ = ag.policy_validate(dict(SAMPLE, network="off"))
        notes = {n for r in ag.policy_coverage(clean) for n in r["notes"]}
        self.assertIn("codex: network not enforced", notes)  # requirements.toml has no network key
        self.assertIn("codex-config: network approximated", notes)
        self.assertIn("amp: mcp_allow not enforced", notes)
        self.assertIn("claude: network approximated", notes)
        self.assertFalse([n for n in notes if n.startswith("claude: permission_floor")])  # native floor: no note

    def test_targets_command_text_and_json(self):
        rc, out, err = self.run_do(ns(polsub="targets", pfile=None))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("permission_floor", out.splitlines()[0])
        self.assertIn("gemini-policy", out)
        p = self.write_policy(dict(SAMPLE, network="off"))
        rc, out, err = self.run_do(ns(polsub="targets", pfile=str(p)))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("coverage notes:", out)
        self.assertIn("codex: network not enforced", out)
        rc, out, err = self.run_do(ns(polsub="targets", pfile=None, json=True))
        obj = json.loads(out)
        self.assertEqual((obj["ok"], obj["cmd"]), (True, "policy"))
        self.assertEqual(len(obj["data"]["targets"]), len(ag.POLICY_TARGETS))

    def test_render_every_target_by_name_and_all(self):
        p = self.write_policy(SAMPLE)
        for t in ag.POLICY_TARGETS:
            rc, out, err = self.run_do(ns(pfile=str(p), target=t))
            self.assertEqual((rc, err), (0, ""), t)
            self.assertEqual(out, TARGET_GOLDEN[t][1], t)
        outdir = self.td / "all"
        rc, out, err = self.run_do(ns(pfile=str(p), out=str(outdir)))
        self.assertEqual((rc, err), (0, ""))
        written = sorted(str(f.relative_to(outdir)) for f in outdir.rglob("*") if f.is_file())
        self.assertEqual(written, sorted(f"{t}/{ag.policy_render(ag.policy_validate(SAMPLE)[0], t)[0]}" for t in ag.POLICY_TARGETS))

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
        self.assertEqual((outdir / "claude" / "managed-settings.json").read_text(), CLAUDE_GOLDEN)
        self.assertEqual((outdir / "codex" / "requirements.toml").read_text(), CODEX_GOLDEN)
        self.assertEqual((outdir / "codex-config" / "codex-config.toml").read_text(), CODEX_CONFIG_GOLDEN)

        # unchanged policy -> no diff, files untouched
        rc, out, err = self.run_do(ns(pfile=str(p), out=str(outdir), dry_run=True))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("no changes:", out)

        # network flip -> unified diff, nothing written
        self.write_policy(dict(SAMPLE, network="off"))
        before = (outdir / "claude" / "managed-settings.json").read_text()
        rc, out, err = self.run_do(ns(pfile=str(p), out=str(outdir), dry_run=True))
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("--- a/", out)
        self.assertIn("+++ b/", out)
        self.assertIn('"WebFetch"', out)
        self.assertIn('"sandbox"', out)
        self.assertEqual((outdir / "claude" / "managed-settings.json").read_text(), before)

    def test_policy_diff_pure(self):
        self.assertEqual(ag.policy_diff("a\n", "a\n", "f"), "")
        d = ag.policy_diff("a\n", "b\n", "f")
        self.assertIn("--- a/f", d)
        self.assertIn("+++ b/f", d)
        self.assertIn("-a", d)
        self.assertIn("+b", d)


if __name__ == "__main__":
    unittest.main()
