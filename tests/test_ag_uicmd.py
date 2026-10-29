#!/usr/bin/env python3
"""`ag ui`: notify/confirm/choose/ask + secret ask. Never pops a real dialog:
AG_UI_CMD / AG_ASKPASS_CMD point at a fake that records its argv.
Run: python3 tests/test_ag_uicmd.py
"""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag"))
SECRET = "s3cr3t-value"
FAKE = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_LOG"
if [ -n "$FAKE_ERR" ]; then echo "$FAKE_ERR" >&2; exit 1; fi
printf '%s\\n' "$FAKE_OUT"
"""


class UiTest(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-ui-"))
        self.state = self.td / ".agent"; self.state.mkdir()
        self.log = self.td / "fake.log"
        fake = self.td / "fake.sh"; fake.write_text(FAKE); fake.chmod(0o755)
        self.env = dict(os.environ, AG_UI_CMD=str(fake), FAKE_LOG=str(self.log), FAKE_OUT="")
        for k in ("FAKE_ERR", "AG_ASKPASS_CMD", "AG_SECRET_POPUP"): self.env.pop(k, None)

    def ag(self, *args, **env):
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *args], capture_output=True,
            text=True, timeout=30, env=dict(self.env, **env))

    def events(self):
        f = self.state / "events.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines() if '"ui"' in l] if f.exists() else []

    def test_notify(self):
        p = self.ag("ui", "notify", 'he said "hi"', "--title", "T", "--sound")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "sent"))
        args = self.log.read_text().splitlines()
        self.assertEqual(args[-3:], ['he said "hi"', "T", "1"])  # text travels as argv, not script source
        self.assertEqual(self.events()[-1]["outcome"], "sent")

    def test_confirm_exit_codes(self):
        for out, rc, word in (("Yes", 0, "yes"), ("No", 1, "no")):
            p = self.ag("ui", "confirm", "ok?", FAKE_OUT=out)
            self.assertEqual((p.returncode, p.stdout.strip()), (rc, word))
        p = self.ag("ui", "confirm", "ok?", "--yes", "Go", FAKE_OUT="Go")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "yes"))
        p = self.ag("ui", "confirm", "ok?", FAKE_ERR="ag-timeout")
        self.assertEqual((p.returncode, p.stdout.strip()), (2, "timeout"))
        self.assertEqual(self.events()[-1]["outcome"], "timeout")

    def test_choose(self):
        p = self.ag("ui", "choose", "which?", "a", "b", "c", "--multi", FAKE_OUT="a\nc")
        self.assertEqual((p.returncode, p.stdout.split()), (0, ["a", "c"]))
        self.assertEqual(self.log.read_text().splitlines()[-6:], ["which?", "ag", "1", "a", "b", "c"])
        p = self.ag("ui", "choose", "which?", "a", FAKE_ERR="execution error: User canceled. (-128)")
        self.assertEqual((p.returncode, p.stdout), (2, ""))
        self.assertEqual(self.events()[-1]["outcome"], "cancelled")

    def test_ask_and_json(self):
        p = self.ag("--json", "ui", "ask", "name?", "--default", "x", FAKE_OUT="bob")
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertEqual((d["ok"], d["data"]["answer"]), (True, "bob"))
        p = self.ag("--json", "ui", "ask", "name?", FAKE_ERR="-128")
        self.assertEqual((p.returncode, json.loads(p.stdout)["data"]["outcome"]), (2, "cancelled"))

    def test_events_never_hold_values(self):
        self.ag("ui", "ask", "pin?", FAKE_OUT="hunter2")
        self.assertTrue(self.events())
        self.assertNotIn("hunter2", (self.state / "events.jsonl").read_text())

    def test_non_macos_exit_3(self):
        env = {k: v for k, v in self.env.items() if k != "AG_UI_CMD"}
        code = ("import sys, runpy; sys.platform='linux'; sys.argv=['ag','--dir',%r,'ui','notify','x']; "
                "runpy.run_path(%r, run_name='__main__')" % (str(self.state), AG))
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(p.returncode, 3, p.stderr)
        self.assertIn("macOS", p.stderr)

    def test_secret_refuses_without_to_env(self):
        p = self.ag("ui", "ask", "pw?", "--secret", AG_ASKPASS_CMD=f"printf {SECRET}")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn(SECRET, p.stdout + p.stderr)
        self.assertIn("refusing", p.stderr)

    def test_secret_to_env(self):
        p = self.ag("ui", "ask", "pw?", "--secret", "--to-env", "MY_PW", "--", "sh", "-c",
            'test "$MY_PW" = "%s" && echo matched' % SECRET, AG_ASKPASS_CMD=f"printf {SECRET}")
        self.assertEqual((p.returncode, p.stdout.strip()), (0, "matched"))
        self.assertNotIn(SECRET, p.stdout + p.stderr)
        self.assertEqual(self.events()[-1]["outcome"], "answered")
        self.assertNotIn(SECRET, (self.state / "events.jsonl").read_text())
        p = self.ag("ui", "ask", "pw?", "--secret", "--to-env", "MY_PW", "--", "true", AG_ASKPASS_CMD="false")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(self.events()[-1]["outcome"], "cancelled")

    def test_secret_stdout_needs_pipe_and_flag(self):
        p = self.ag("ui", "ask", "pw?", "--secret", "--stdout", AG_ASKPASS_CMD=f"printf {SECRET}")  # piped here
        self.assertEqual((p.returncode, p.stdout.strip()), (0, SECRET))
        p = self.ag("--json", "ui", "ask", "pw?", "--secret", "--stdout", AG_ASKPASS_CMD=f"printf {SECRET}")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn(SECRET, p.stdout)

    def test_secret_bad_args(self):
        for extra in (["--to-env", "1BAD", "--", "true"], ["--to-env", "OK"], ["--stdout", "--", "true"]):
            p = self.ag("ui", "ask", "pw?", "--secret", *extra, AG_ASKPASS_CMD=f"printf {SECRET}")
            self.assertEqual(p.returncode, 2, extra)


if __name__ == "__main__":
    unittest.main()
