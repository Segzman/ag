#!/usr/bin/env python3
"""Secret input: askpass, PTY prompt detection, send --secret, sudo shim.
Never shows a real dialog (AG_ASKPASS_CMD fake) and never runs real sudo
(a fake `sudo` sits ahead of it on PATH).
Run: python3 tests/test_ag_secret.py
"""
import hashlib, importlib.machinery, json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path

AG = os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag"))
SECRET = "s3cret"
# compare against a hash so the plaintext never appears in the logged command
_H = hashlib.sha256(SECRET.encode()).hexdigest()
GETPASS = ("import getpass, hashlib; print('got', hashlib.sha256("
    f"getpass.getpass('Password: ').encode()).hexdigest()=='{_H}')")


class SecretTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-secret-"))
        self.state = self.td / ".agent"
        self.env = dict(os.environ, AG_ASKPASS_CMD=f"printf {SECRET}")
        self.env.pop("AG_SECRET_POPUP", None)
        for k in ("SUDO_ASKPASS", "SSH_ASKPASS", "GIT_ASKPASS", "SSH_ASKPASS_REQUIRE"):
            self.env.pop(k, None)

    def ag(self, *args, env=None, timeout=30):
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *args],
            capture_output=True, text=True, timeout=timeout, env=env or self.env)

    def spawn(self, *cmd, flags=()):
        p = self.ag("--json", "spawn", *flags, "--", *cmd)
        o = json.loads(p.stdout)
        self.assertTrue(o["ok"], o)
        return o["data"]["id"]

    def sp(self, sid): return self.state / "sessions" / sid

    def wait(self, fn, timeout=15):
        t0 = time.time()
        while time.time() - t0 < timeout:
            v = fn()
            if v: return v
            time.sleep(0.1)
        return None

    def out(self, sid):
        try: return (self.sp(sid) / "output.log").read_text(errors="replace")
        except OSError: return ""

    def exited(self, sid):
        st = json.loads((self.sp(sid) / "status.json").read_text())
        return not st.get("running")

    def assert_no_secret_on_disk(self):
        for p in self.state.rglob("*"):
            if p.is_file() and not p.is_fifo():
                self.assertNotIn(SECRET.encode(), p.read_bytes(), str(p))

    def test_askpass_cmd(self):
        p = self.ag("askpass", "Password for x:")
        self.assertEqual((p.returncode, p.stdout), (0, SECRET + "\n"))
        p = self.ag("askpass", "x", env=dict(self.env, AG_ASKPASS_CMD="false"))
        self.assertEqual((p.returncode, p.stdout), (1, ""))
        p = self.ag("askpass", "x", env=dict(self.env, AG_SECRET_POPUP="0"))
        self.assertEqual((p.returncode, p.stdout), (1, ""))
        self.assertIn("AG_SECRET_POPUP", p.stderr)

    def test_pty_prompt_detected_and_redacted(self):
        sid = self.spawn(sys.executable, "-c", GETPASS)
        self.assertTrue(self.wait(lambda: "got" in self.out(sid)), self.out(sid))
        self.assertIn("got True", self.out(sid))
        self.assertTrue(self.wait(lambda: self.exited(sid)))
        inp = self.sp(sid) / "input.log"
        self.assertIn(b"[secret input redacted]", inp.read_bytes())
        ev = (self.state / "events.jsonl").read_text()
        self.assertIn('"secret_prompt"', ev)
        self.assertIn('"outcome": "ok"', ev)
        self.assert_no_secret_on_disk()

    def test_echo_on_keyword_prompt_not_echoed(self):
        code = "x=input('Enter OTP code: '); print('len', len(x))"
        sid = self.spawn(sys.executable, "-c", code)
        self.assertTrue(self.wait(lambda: "len" in self.out(sid)), self.out(sid))
        self.assertIn("len 6", self.out(sid))
        self.wait(lambda: self.exited(sid))
        self.assert_no_secret_on_disk()

    def test_send_secret(self):
        sid = self.spawn(sys.executable, "-c", GETPASS, flags=("--no-secret-popup",))
        self.assertTrue(self.wait(lambda: "Password:" in self.out(sid)))
        time.sleep(0.8)  # opted out: no dialog may fire on its own
        self.assertNotIn("got", self.out(sid))
        p = self.ag("send", sid, "--secret")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn(SECRET, p.stdout)
        self.assertTrue(self.wait(lambda: "got" in self.out(sid)), self.out(sid))
        self.assertIn("got True", self.out(sid))
        self.assertIn(b"[secret input redacted]", (self.sp(sid) / "input.log").read_bytes())
        self.assert_no_secret_on_disk()
        p = self.ag("send", sid, "--secret", "inline")
        self.assertNotEqual(p.returncode, 0)

    def _fake_sudo(self):
        fake = self.td / "fakebin"
        fake.mkdir()
        f = fake / "sudo"
        f.write_text('#!/bin/sh\necho "FAKE $* ASKPASS=${SUDO_ASKPASS##*/}"\n')
        f.chmod(0o755)
        return f"PATH={fake}{os.pathsep}{os.environ.get('PATH', '')}"

    def test_sudo_shim_adds_askpass_flag(self):
        path = self._fake_sudo()
        sid = self.spawn("sh", "-c", "sudo ls /; sudo -n id", flags=("--env", path))
        self.assertTrue(self.wait(lambda: self.exited(sid)))
        out = self.out(sid)
        self.assertIn("FAKE -A ls / ASKPASS=ag-askpass", out)
        self.assertIn("FAKE -n id", out)
        bindir = self.state / "bin"
        self.assertEqual(oct(bindir.stat().st_mode & 0o777), "0o700")
        self.assertEqual(oct((bindir / "ag-askpass").stat().st_mode & 0o777), "0o700")
        sid = self.spawn("sh", "-c", "sudo ls /", flags=("--env", path, "--no-secret-popup"))
        self.assertTrue(self.wait(lambda: self.exited(sid)))
        self.assertIn("FAKE ls / ASKPASS=", self.out(sid))
        self.assertNotIn("-A", self.out(sid))

    def test_backend_toolout_note_once(self):
        os.environ["AG_ASKPASS_CMD"] = "true"
        try:
            m = importlib.machinery.SourceFileLoader("ag_secret_mod", AG).load_module()
            self.state.mkdir(parents=True)
            class Buf: pass
            buf, seen = Buf(), []
            on = lambda n, k, t: seen.append((k, t))
            m.secret_toolout(self.state, "a1", "ok", buf, on)
            self.assertEqual(seen, [])
            for _ in range(2):
                m.secret_toolout(self.state, "a1",
                    "sudo: a terminal is required to read the password", buf, on)
            self.assertEqual(len(seen), 1)
            self.assertIn("retry", seen[0][1])
            ev = (self.state / "events.jsonl").read_text()
            self.assertEqual(ev.count('"secret_needed"'), 1)
            env = m.secret_env(self.state, {"PATH": "/bin", "GIT_ASKPASS": "/mine"})
            self.assertEqual(env["GIT_ASKPASS"], "/mine")
            self.assertTrue(env["SUDO_ASKPASS"].endswith("/bin/ag-askpass"))
            self.assertEqual(env["SSH_ASKPASS_REQUIRE"], "force")
            self.assertTrue(env["PATH"].startswith(str((self.state / "bin").resolve())))
        finally:
            os.environ.pop("AG_ASKPASS_CMD", None)


if __name__ == "__main__":
    unittest.main()
