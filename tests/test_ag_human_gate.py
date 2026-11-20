#!/usr/bin/env python3
"""Human gate: risky grants need proof of a human (auth popup). An agent's call becomes a pending request;
`ag grant ID` pops the auth dialog (stubbed here via AG_AUTH_HELPER, never a real popup) and runs the stored action once.
Stdlib, temp dirs, no network. Run: python3 -m pytest tests/test_ag_human_gate.py"""
import importlib.machinery, importlib.util, json, os, re, shutil, stat, subprocess, sys, tempfile, time, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_gate_mod", AG)
ag = importlib.util.module_from_spec(importlib.util.spec_from_loader("ag_gate_mod", _l)); _l.exec_module(ag)

AGENT_KEYS = ("AG_JOB_ID", "AG_PARENT_AGENT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "OPENCODE", "OPENCODE_PID", "CODEX_SANDBOX", "CODEX_THREAD_ID", "CODEX_CI", "AG_HUMAN_GATE")
STUB = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_AUTH_LOG\"\nexit "
UI = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$FAKE_UI_LOG\"\n"


class Gate(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-gate-t-")); self.addCleanup(shutil.rmtree, self.td, True)
        self.state, self.cfg, self.cache = self.td / "state", self.td / "cfg", self.td / "cache"
        for d in (self.state, self.cfg, self.cache): d.mkdir()
        self.auth_log, self.ui_log = self.td / "auth.log", self.td / "ui.log"
        self.ok, self.bad = self.script("ok", STUB + "0\n"), self.script("bad", STUB + "1\n")
        self.ui = self.script("ui", UI)
        base = {k: v for k, v in os.environ.items() if k not in AGENT_KEYS}
        self.human = dict(base, AG_HOME=str(self.td / "home"), AG_CONFIG_HOME=str(self.cfg), AG_CACHE_HOME=str(self.cache), AG_AUTO_UPDATE="0",
            AG_PROBE="0", AG_HUMAN_GATE="off", FAKE_AUTH_LOG=str(self.auth_log), FAKE_UI_LOG=str(self.ui_log), AG_UI_CMD=str(self.ui), AG_AUTH_HELPER=self.bad, AG_NOTIFY_DIR=str(self.td / "notify"))
        self.agent = dict(self.human, AG_PARENT_AGENT="bot")

    def script(self, name, body):
        p = self.td / name; p.write_text(body); p.chmod(p.stat().st_mode | stat.S_IEXEC); return str(p)

    def run_ag(self, env, *a, helper=None):
        env = dict(env, **({"AG_AUTH_HELPER": helper} if helper else {}))
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a], capture_output=True, text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)

    def routing(self):
        f = self.cfg / "routing.json"; return json.loads(f.read_text()) if f.exists() else {}

    def req_id(self, p):
        m = re.search(r"pending approval (g-[0-9a-f]+); human runs: ag grant g-[0-9a-f]+", p.stderr + p.stdout); self.assertTrue(m, p.stderr + p.stdout); return m.group(1)

    def audit(self):
        f = self.state / "requests" / "audit.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def request(self, glob="opencode/*"):
        p = self.run_ag(self.agent, "access", "allow", glob)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        return self.req_id(p)

    def test_agent_request_is_pending_not_executed(self):
        p = self.run_ag(self.agent, "access", "allow", "opencode/*")
        rid = self.req_id(p)
        self.assertEqual(self.routing(), {})
        r = json.loads((self.state / "requests" / f"{rid}.json").read_text())
        self.assertEqual((r["status"], r["action"], r["agent"]), ("pending", "access allow", "bot"))
        self.assertTrue(86000 < r["expires"] - r["ts"] <= 86400)
        self.assertIn("ag: bot requests access allow", self.ui_log.read_text())  # native notification
        self.assertEqual([e["event"] for e in self.audit()], ["requested"])
        p2 = self.run_ag(self.agent, "access", "allow", "opencode/*")  # same ask = same request
        self.assertEqual(self.req_id(p2), rid)
        j = json.loads(self.run_ag(self.agent, "--json", "access", "allow", "x/*").stdout); self.assertFalse(j["ok"]); self.assertIn("grant", j["data"])

    def test_grant_with_auth_success_executes_and_audits(self):
        rid = self.request()
        p = self.run_ag(self.human, "grant", rid, helper=self.ok)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.routing()["access"]["allow"], ["opencode/*"])
        self.assertIn("allow model access opencode/*", self.auth_log.read_text())  # popup reason names the grant
        ev = {e["event"]: e for e in self.audit()}
        self.assertEqual((ev["executed"]["origin"], ev["executed"]["rc"], ev["executed"]["id"]), ("human", 0, rid))
        self.assertEqual(json.loads((self.state / "requests" / f"{rid}.json").read_text())["status"], "executed")

    def test_auth_failure_does_not_execute(self):
        rid = self.request()
        p = self.run_ag(self.human, "grant", rid, helper=self.bad)
        self.assertNotEqual(p.returncode, 0); self.assertIn("not approved", p.stderr)
        self.assertEqual(self.routing(), {})
        self.assertEqual(json.loads((self.state / "requests" / f"{rid}.json").read_text())["status"], "pending")
        self.assertIn("auth_failed", [e["event"] for e in self.audit()])
        self.assertEqual(self.run_ag(self.human, "grant", rid, helper=self.ok).returncode, 0)  # a retry can still succeed

    def test_expired_request_refused(self):
        rid = self.request(); f = self.state / "requests" / f"{rid}.json"
        r = json.loads(f.read_text()); r["expires"] = time.time() - 5; f.write_text(json.dumps(r))
        p = self.run_ag(self.human, "grant", rid, helper=self.ok)
        self.assertNotEqual(p.returncode, 0); self.assertIn("expired", p.stderr)
        self.assertEqual(self.routing(), {}); self.assertFalse(self.auth_log.exists())  # no popup for a dead request
        self.assertEqual(json.loads(f.read_text())["status"], "expired")

    def test_replay_refused(self):
        rid = self.request()
        self.assertEqual(self.run_ag(self.human, "grant", rid, helper=self.ok).returncode, 0)
        self.run_ag(self.human, "access", "rm", "opencode/*")  # undo, then replay the same id
        p = self.run_ag(self.human, "grant", rid, helper=self.ok)
        self.assertNotEqual(p.returncode, 0); self.assertIn("single-use", p.stderr)
        self.assertEqual(self.routing().get("access", {}).get("allow", []), [])
        self.assertIn("replay_refused", [e["event"] for e in self.audit()])
        (self.state / "requests" / f"{rid}.json").write_text(json.dumps({**json.loads((self.state / "requests" / f"{rid}.json").read_text()), "status": "pending"}))
        p = self.run_ag(self.human, "grant", rid, helper=self.ok)  # status forged back to pending: the claim file still blocks it
        self.assertNotEqual(p.returncode, 0); self.assertIn("already granted", p.stderr)

    def test_non_gated_actions_unaffected(self):
        for args in (("access", "show"), ("access", "deny", "x/*"), ("notes",), ("grant", "--list")):
            p = self.run_ag(self.agent, *args); self.assertEqual(p.returncode, 0, (args, p.stderr))
        self.assertEqual(self.routing()["access"]["deny"], ["x/*"])
        self.assertFalse((self.state / "requests").exists() and list((self.state / "requests").glob("g-*.json")))

    def test_deny_and_list(self):
        rid = self.request()
        self.assertIn(rid, self.run_ag(self.human, "grant", "--list").stdout)
        self.assertEqual(self.run_ag(self.human, "grant", "--deny", rid).returncode, 0)
        self.assertNotIn(rid, self.run_ag(self.human, "grant", "--list").stdout)
        p = self.run_ag(self.human, "grant", rid, helper=self.ok); self.assertNotEqual(p.returncode, 0); self.assertEqual(self.routing(), {})

    def test_gate_off_and_stub_ignored_in_agent_env(self):
        p = self.run_ag(self.agent, "access", "allow", "a/*")  # agent env inherits AG_HUMAN_GATE=off
        self.assertEqual(p.returncode, 3); self.assertEqual(self.routing(), {})  # an agent cannot switch the gate off
        self.assertEqual(self.run_ag(self.human, "access", "allow", "b/*").returncode, 0)  # test/CI switch works in a plain env
        os.environ["AG_PARENT_AGENT"] = "bot"; self.addCleanup(os.environ.pop, "AG_PARENT_AGENT", None)
        orig = (ag._gate_auth_helper, ag.ui_enabled); self.addCleanup(lambda: (setattr(ag, "_gate_auth_helper", orig[0]), setattr(ag, "ui_enabled", orig[1])))
        ag._gate_auth_helper = lambda: None; ag.ui_enabled = lambda: True
        os.environ["AG_AUTH_HELPER"] = self.ok; self.addCleanup(os.environ.pop, "AG_AUTH_HELPER", None)
        self.assertFalse(ag.human_confirm("access allow", "ag: x"))  # stub helper + weak dialog both refused for agent callers

    def test_no_gui_no_helper_refuses(self):
        orig = (ag._gate_auth_helper, ag.ui_enabled); self.addCleanup(lambda: (setattr(ag, "_gate_auth_helper", orig[0]), setattr(ag, "ui_enabled", orig[1])))
        ag._gate_auth_helper = lambda: None; ag.ui_enabled = lambda: False
        os.environ.pop("AG_AUTH_HELPER", None)
        self.assertFalse(ag.human_confirm("access allow", "ag: x"))

    def test_extra_gated_action_from_config(self):
        (self.cfg / "gate.json").write_text(json.dumps({"extra": ["access deny"]}))
        p = self.run_ag(self.agent, "access", "deny", "x/*"); self.assertEqual(p.returncode, 3); self.req_id(p)
        self.assertEqual(self.routing(), {})

    def test_relay_alias_add_becomes_pending_grant(self):
        mail, work = self.td / "mail", self.td / "work"; mail.mkdir(); work.mkdir()
        self.assertEqual(self.run_ag(self.human, "relay", "init", "--node", "mac", "--state", str(self.state), "--workdir", str(work), "--dir", str(mail)).returncode, 0)
        add = ("relay", "alias", "add", "build", "--backend", "claude", "--dir", str(work), "--from", "muse,muse2,ec2", "--mode", "edits", "--resume", "latest")
        p = self.run_ag(self.agent, *add); rid = self.req_id(p)
        self.assertNotIn("aliases", json.loads((self.cfg / "relay.json").read_text()))
        self.assertEqual(self.run_ag(self.human, "grant", rid, helper=self.ok).returncode, 0)
        self.assertIn("allow muse, muse2 and ec2 to start chats in session build (edits", self.auth_log.read_text())
        al = json.loads((self.cfg / "relay.json").read_text())["aliases"]["build"]
        self.assertEqual((al["from"], al["resume"]), (["muse", "muse2", "ec2"], "latest"))

    @unittest.skipUnless(shutil.which("swiftc") and sys.platform == "darwin", "needs macOS swiftc")
    def test_swift_helper_builds_once_and_is_cached(self):
        os.environ["AG_CACHE_HOME"] = str(self.cache); self.addCleanup(os.environ.pop, "AG_CACHE_HOME", None)
        exe = ag._gate_auth_helper()  # compiled only, never executed: no popup
        self.assertTrue(exe and os.access(exe, os.X_OK) and exe.endswith("bin/ag-auth"), exe)
        m = os.stat(exe).st_mtime_ns
        self.assertEqual(ag._gate_auth_helper(), exe); self.assertEqual(os.stat(exe).st_mtime_ns, m)  # cached by source hash
        (Path(exe).parent / "ag-auth.srchash").write_text("stale")
        ag._gate_auth_helper(); self.assertNotEqual((Path(exe).parent / "ag-auth.srchash").read_text(), "stale")  # source change rebuilds


if __name__ == "__main__":
    unittest.main()
