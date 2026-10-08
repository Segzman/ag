#!/usr/bin/env python3
"""Provider probe (auth/compat/cache/pre-flight) + idempotent receipts (--request-id).
Fake CLIs on a restricted PATH; AG_CACHE_HOME/AG_HOME point at temp dirs.
Run: python3 tests/test_ag_probe.py"""
import importlib.machinery, json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import os, sys
d = os.environ["FAKE_DIR"]; open(os.path.join(d, "calls.log"), "a").write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1:] == ["--version"]: print("2.1.289 (Claude Code)")
elif sys.argv[1:3] == ["auth", "status"]: print('{"loggedIn": %s}' % os.environ.get("FAKE_LOGGED", "true"))
else: print('{"type":"result"}')
'''
FAKE_CODEX = '#!/bin/sh\n[ "$1" = "--version" ] && echo "codex-cli 0.160.0" && exit 0\necho "Logged in using ChatGPT"\n'
FAKE_AGENT = '#!/bin/sh\n[ "$1" = "--version" ] && echo "2026.04.17-479fd04" && exit 0\nprintf "\\033[2K\\n Not logged in\\n"\n'
FAKE_OPENCODE = '#!/bin/sh\n[ "$1" = "--version" ] && echo "1.18.31" && exit 0\necho "1 credentials"\n'


def load_mod():
    return importlib.machinery.SourceFileLoader("agprobe", AG).load_module()


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="agprobe-"))
        self.bin = self.td / "bin"; self.bin.mkdir()
        self.state = self.td / ".agent"
        self.env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin", FAKE_DIR=str(self.td), AG_AUTO_UPDATE="0",
                        AG_HOME=str(self.td / "home"), AG_CACHE_HOME=str(self.td / "cache"), AG_RETRY="1")
        (self.td / "home").mkdir()

    def fake(self, name, body):
        p = self.bin / name; p.write_text(body); p.chmod(0o755)

    def ag(self, *args, **env):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), *args], env=dict(self.env, **env),
                           capture_output=True, text=True, timeout=60)
        return p

    def j(self, *args, **env):
        p = self.ag(*args, "--json", **env)
        return p.returncode, json.loads(p.stdout)

    def calls(self):
        f = self.td / "calls.log"
        return f.read_text().splitlines() if f.exists() else []


class Probe(Base):
    def doctor(self, **env):
        rc, o = self.j("agents", "doctor", "--refresh", **env)
        return {r["backend"]: r for r in o["data"]["backends"]}

    def test_parse_and_states(self):
        m = load_mod()
        self.assertEqual(m.auth_parse("claude", '{"loggedIn": false}'), "no")
        self.assertEqual(m.auth_parse("claude", "garbage"), "unknown")
        self.assertEqual(m.auth_parse("codex", "Not logged in"), "no")
        self.assertEqual(m.auth_parse("codex", "Logged in using ChatGPT"), "yes")
        self.assertEqual(m.auth_parse("cursor", "\x1b[2K\n Not logged in\n"), "no")
        self.assertEqual(m.auth_parse("opencode", "0 credentials"), "unknown")
        self.assertEqual(m.auth_parse("gemini", "x"), "unknown")

    def test_doctor_logged_in_out_missing(self):
        self.fake("claude", FAKE_CLAUDE); self.fake("codex", FAKE_CODEX); self.fake("agent", FAKE_AGENT)
        self.fake("opencode", FAKE_OPENCODE)
        r = self.doctor()
        self.assertEqual((r["claude"]["installed"], r["claude"]["auth"], r["claude"]["compat"]), (True, "yes", "ok"))
        self.assertEqual(r["codex"]["auth"], "yes")
        self.assertEqual(r["cursor"]["auth"], "no"); self.assertIn("agent login", r["cursor"]["hint"])
        self.assertEqual(r["opencode"]["auth"], "yes")
        self.assertEqual(r["gemini"]["installed"], False)  # not on restricted PATH
        self.assertEqual(r["gemini"]["auth"], "unknown")
        r = self.doctor(FAKE_LOGGED="false")
        self.assertEqual(r["claude"]["auth"], "no")
        txt = self.ag("agents", "doctor").stdout
        self.assertIn("auth=no", txt); self.assertIn("compat=ok", txt)

    def test_compat(self):
        m = load_mod()
        self.assertEqual(m.compat_verdict("claude", "2.1.289 (Claude Code)"), "ok")
        self.assertEqual(m.compat_verdict("claude", "2.2.0"), "ok")
        self.assertEqual(m.compat_verdict("claude", "2.0.9"), "graceful")
        self.assertEqual(m.compat_verdict("claude", "2.1.10"), "graceful")  # tuple, not string, compare
        self.assertEqual(m.compat_verdict("claude", "nonsense"), "unknown")
        self.assertEqual(m.compat_verdict("cursor", "2026.04.17-479fd04"), "ok")

    def test_cache_ttl(self):
        self.fake("claude", FAKE_CLAUDE)
        m = load_mod(); os.environ["AG_CACHE_HOME"] = str(self.td / "cache"); old = os.environ["PATH"]
        os.environ["PATH"] = self.env["PATH"]; os.environ["FAKE_DIR"] = str(self.td)
        try:
            m.providers_snapshot(["claude"]); n = len(self.calls())
            m.providers_snapshot(["claude"]); self.assertEqual(len(self.calls()), n)  # cached
            m.providers_snapshot(["claude"], refresh=True); self.assertGreater(len(self.calls()), n)
            n = len(self.calls()); c = json.loads(m._providers_path().read_text()); c["claude"]["ts"] = time.time() - 61
            m._providers_path().write_text(json.dumps(c))
            m.providers_snapshot(["claude"]); self.assertGreater(len(self.calls()), n)  # stale -> reprobe
        finally:
            os.environ["PATH"] = old

    def test_preflight_fast_fail_no_launch_no_retry(self):
        self.fake("claude", FAKE_CLAUDE)
        self.ag("agents", "add", "u1", "--backend", "claude", "--dir", str(self.td))
        rc, o = self.j("chat", "send", "u1", "hello", FAKE_LOGGED="false")
        self.assertEqual(rc, 1)
        self.assertEqual(o["error"], "not logged in to claude: run 'claude' once to log in")
        # only probe calls, never a launch (launch argv has -p / stream-json)
        self.assertTrue(all(c in ("--version", "auth status") for c in self.calls()), self.calls())
        self.assertEqual(self.calls().count("auth status"), 1)  # no retry => one probe only
        rc, o = self.j("chat", "send", "u1", "hello", FAKE_LOGGED="true", AG_PROBE="0")
        self.assertEqual(rc, 0, o)  # AG_PROBE=0 skips pre-flight and launches

    def test_unknown_auth_proceeds(self):
        self.fake("claude", FAKE_CLAUDE.replace("'{\"loggedIn\"", "'garbage{\"loggedIn\""))
        self.ag("agents", "add", "u2", "--backend", "claude", "--dir", str(self.td))
        rc, o = self.j("chat", "send", "u2", "hello")
        self.assertEqual(rc, 0, o)


class Receipts(Base):
    def setUp(self):
        super().setUp()
        self.ag("agents", "add", "e", "--backend", "echo", "--dir", str(self.td))

    def test_chat_replay_conflict(self):
        rc, a = self.j("chat", "send", "e", "ping", "--request-id", "k1")
        self.assertEqual(rc, 0); self.assertIn("ping", a["data"]["reply"])
        rc, b = self.j("chat", "send", "e", "ping", "--request-id", "k1")
        self.assertEqual(rc, 0); self.assertTrue(b["data"]["replayed"]); self.assertEqual(b["data"]["reply"], a["data"]["reply"])
        log = self.j("chat", "log", "e")[1]["data"]["log"]
        self.assertEqual(len([r for r in log if r["role"] == "user"]), 1)  # not re-run
        rc, c = self.j("chat", "send", "e", "different", "--request-id", "k1")
        self.assertEqual(rc, 1); self.assertEqual(c["error"], "request id conflict")
        rc, d = self.j("chat", "send", "e", "ping", "--request-id", "k2")  # new key runs
        self.assertNotIn("replayed", d["data"])

    def test_delegate_replay_and_in_progress(self):
        rc, a = self.j("delegate", "e", "task", "--request-id", "d1")
        rc, b = self.j("delegate", "e", "task", "--request-id", "d1")
        self.assertTrue(b["data"]["replayed"]); self.assertEqual(b["data"]["reply"], a["data"]["reply"])
        f = next((self.state / "receipts").glob("*.json")); rec = json.loads(f.read_text())
        rec["status"] = "in_progress"; rec.pop("reply"); f.write_text(json.dumps(rec))
        rc, c = self.j("delegate", "e", "task", "--request-id", "d1")
        self.assertEqual((c["data"]["status"], c["data"]["replayed"]), ("in_progress", True))

    def test_wake_replay_same_job(self):
        rc, a = self.j("wake", "e", "go", "--request-id", "w1")
        rc, b = self.j("wake", "e", "go", "--request-id", "w1")
        self.assertEqual(a["data"]["job"], b["data"]["job"]); self.assertTrue(b["data"]["replayed"])
        self.assertEqual(self.j("wake", "e", "other", "--request-id", "w1")[1]["error"], "request id conflict")
        self.assertEqual(len(self.j("wakes")[1]["data"]["wakes"]), 1)

    def test_failure_replay(self):
        rc, a = self.j("wake", "nobody", "go", "--request-id", "f1")
        self.assertEqual(rc, 1)
        self.ag("agents", "add", "nobody", "--backend", "echo", "--dir", str(self.td))  # now it would succeed
        rc, b = self.j("wake", "nobody", "go", "--request-id", "f1")
        self.assertEqual(rc, 1); self.assertEqual(b["error"], a["error"])  # original failure, not re-run

    def test_gc(self):
        self.j("chat", "send", "e", "a", "--request-id", "g1"); self.j("chat", "send", "e", "b", "--request-id", "g2")
        fs = sorted((self.state / "receipts").glob("*.json")); old = time.time() - 10 * 86400
        os.utime(fs[0], (old, old))
        rc, o = self.j("receipts", "gc")
        self.assertEqual(o["data"]["removed"], 1); self.assertEqual(len(list((self.state / "receipts").glob("*.json"))), 1)
        self.assertEqual(self.j("receipts", "gc", "--days", "0")[1]["data"]["removed"], 1)


if __name__ == "__main__":
    unittest.main()
