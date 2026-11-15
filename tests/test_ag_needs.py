#!/usr/bin/env python3
"""ag needs queue: ui dialogs that time out stay answerable via <state>/needs.jsonl. Stdlib, temp dirs."""
import importlib.util, json
from pathlib import Path

_T = Path(__file__).resolve().parent / "test_ag_relay.py"
_s = importlib.util.spec_from_file_location("_ag_needs_support", _T)
_sup = importlib.util.module_from_spec(_s); _s.loader.exec_module(_sup)
Env, ag = _sup.Env, _sup.ag

TOUT = "#!/bin/sh\necho 'ag-timeout' >&2\nexit 1\n"          # ui_run -> timeout
OK = "#!/bin/sh\nprintf '%s\\n' \"$AG_UI_ANSWER\"\n"          # ui_run -> ok with $AG_UI_ANSWER


class Needs(Env):
    def dialog(self, n, script):
        p = n["state"].parent / "bin" / ("dialog-" + script)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TOUT if script == "timeout" else OK)
        p.chmod(0o755)
        return str(p)

    def ui(self, n, *a, ok=True, script="timeout", answer=None, timeout=60):
        e = dict(n["env"], AG_UI_CMD=self.dialog(n, script))
        if answer is not None: e["AG_UI_ANSWER"] = answer
        d = dict(n); d["env"] = e
        return self.run_ag(d, "ui", *a, ok=ok)

    def rows(self, n):
        p = n["state"] / "needs.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.is_file() else []

    def need_id(self, n):
        return self.rows(n)[-1]["id"]

    def notify_files(self, n):
        d = Path(n["env"]["AG_NOTIFY_DIR"])
        return sorted(x.name for x in d.glob("ag-*.json")) if d.is_dir() else []

    def test_timeout_queues_and_answer_reaches_recheck(self):
        self.init(self.o)
        n = dict(self.o); n["env"] = dict(self.o["env"], AG_PARENT_AGENT="worker-1", AG_NOTIFY_DIR=str(self.td / "inbox"))
        p = self.ui(n, "ask", "Deploy tag?", "--timeout", "1", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("queued", p.stdout)
        r = self.rows(n)[-1]
        self.assertEqual(r["status"], "open"); self.assertEqual(r["kind"], "ask")
        self.assertEqual(r["agent"], "worker-1"); self.assertEqual(r["question"], "Deploy tag?")
        self.assertEqual(self.notify_files(n), self.notify_files(n))  # inbox written
        self.assertEqual(len(self.notify_files(n)), 1)                # one 'things need you' ping
        p, d = self.j(n, "needs", "list")
        self.assertEqual(d["data"]["count"], 1)
        self.assertEqual(d["data"]["needs"][0]["id"], r["id"])
        self.assertIn(r["id"], p.stdout)                              # text mode lists the id
        p = self.run_ag(n, "needs", "answer", r["id"], "do it")
        self.assertEqual(self.rows(n)[-1]["status"], "answered")
        self.assertEqual(self.rows(n)[-1]["answer"], "do it")
        p = self.ui(n, "ask", "Deploy tag?", "--needs-id", r["id"], ok=False)  # dialog would time out
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "do it")
        self.assertEqual(len(self.rows(n)), 1)                        # re-check stores nothing
        p, d = self.j(n, "needs", "done", r["id"])
        self.assertEqual(d["data"]["need"]["status"], "closed")
        p, d = self.j(n, "needs", "list")
        self.assertEqual(d["data"]["count"], 0)
        self.assertIn("no open needs", p.stdout)
        p = self.run_ag(n, "needs", "answer", r["id"], "x", ok=False)
        self.assertIn("is closed", p.stdout + p.stderr)
        p = self.run_ag(n, "needs", "answer", "nope", "x", ok=False)
        self.assertIn("no such need", p.stdout + p.stderr)

    def test_confirm_and_choose_queue_shape(self):
        self.init(self.o)
        n = dict(self.o); n["env"] = dict(self.o["env"], AG_NOTIFY_DIR=str(self.td / "inbox"))
        p = self.ui(n, "confirm", "Deploy?", "--timeout", "1", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("yes|no", p.stdout)
        r = self.rows(n)[-1]
        self.assertEqual(r["kind"], "confirm"); self.assertEqual(r["options"], ["Yes", "No"])
        self.run_ag(n, "needs", "answer", r["id"], "yes")
        p = self.ui(n, "confirm", "Deploy?", "--needs-id", r["id"], ok=False)
        self.assertEqual(p.returncode, 0); self.assertEqual(p.stdout.strip(), "yes")
        p = self.ui(n, "choose", "Which?", "a", "b", "--timeout", "1", "--agent", "worker-2", ok=False)
        r = self.rows(n)[-1]
        self.assertEqual(r["agent"], "worker-2"); self.assertEqual(r["options"], ["a", "b"])
        self.assertEqual(r["status"], "open")

    def test_queue_flag_records_answered_dialog(self):
        self.init(self.o)
        self.ui(self.o, "ask", "Name?", "--queue", script="ok", answer="ace", ok=True)
        r = self.rows(self.o)[-1]
        self.assertEqual(r["status"], "answered"); self.assertEqual(r["answer"], "ace")
        p = self.ui(self.o, "ask", "Name?", "--needs-id", r["id"], script="timeout", ok=False)
        self.assertEqual(p.returncode, 0); self.assertEqual(p.stdout.strip(), "ace")
        self.ui(self.o, "ask", "Name?", script="ok", answer="noq", ok=True)   # no --queue: nothing stored
        self.assertEqual(len(self.rows(self.o)), 1)

    def test_secret_never_stored(self):
        self.init(self.o)
        p = self.ui(self.o, "ask", "Token?", "--secret", "--queue", script="timeout", ok=False)
        self.assertEqual(p.returncode, 2)
        self.assertIn("never touches the needs queue", p.stdout + p.stderr)
        self.assertEqual(self.rows(self.o), [])
        n = dict(self.o); n["env"] = dict(self.o["env"], AG_SECRET_POPUP="0")
        p = self.ui(n, "ask", "Token?", "--secret", "--stdout", ok=False)  # piped stdout passes the refusal gate
        self.assertEqual(p.returncode, 3)
        self.assertIn("secret dialog unavailable", p.stdout + p.stderr)
        self.assertEqual(self.rows(self.o), [])

    def test_notify_ping_throttled_to_one_per_10min(self):
        self.init(self.o)
        n = dict(self.o); n["env"] = dict(self.o["env"], AG_NOTIFY_DIR=str(self.td / "inbox"))
        self.ui(n, "ask", "one?", "--timeout", "1", ok=False)
        self.assertEqual(len(self.notify_files(n)), 1)
        self.ui(n, "choose", "two?", "a", "b", "--timeout", "1", ok=False)
        self.assertEqual(len(self.notify_files(n)), 1)                # inside the 10 min window
        mark = n["state"] / "needs_notify.json"
        mark.write_text(json.dumps({"last": 0}))
        self.ui(n, "ask", "three?", "--timeout", "1", ok=False)
        self.assertEqual(len(self.notify_files(n)), 2)                # window expired -> one more

    def test_unit_helpers(self):
        self.init(self.o)
        sdir = self.o["state"]
        self.assertEqual(ag._needs_load(sdir), [])                    # missing file
        self.assertTrue(ag._needs_new_id().startswith("n"))
        self.assertIsNone(ag._needs_stored_answer(sdir, "gone"))
        env = dict(self.o["env"], AG_SECRET_POPUP="0")
        self.assertFalse(ag.secret_enabled(env))                      # secrets never reach the queue


if __name__ == "__main__":
    import unittest
    unittest.main()
