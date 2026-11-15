#!/usr/bin/env python3
"""ag relay inbox persistent log: sync-appended rows survive pending-file cleanup. Stdlib, temp dirs."""
import importlib.util, json, time
from pathlib import Path

_T = Path(__file__).resolve().parent / "test_ag_relay.py"
_s = importlib.util.spec_from_file_location("_ag_relay_support", _T)
_sup = importlib.util.module_from_spec(_s); _s.loader.exec_module(_sup)
Env, ag = _sup.Env, _sup.ag


class InboxLog(Env):
    def log_path(self, n, bucket):
        return n["cache"] / "relay" / "inbox" / f"{bucket}.jsonl"

    def log_rows(self, n, bucket):
        f = self.log_path(n, bucket)
        if not f.exists(): return []
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()]

    def put_mail(self, mid, agent, message, at_epoch=None, origin="muse"):
        d = self.root / "schedules" / "mac"
        d.mkdir(parents=True, exist_ok=True)
        m = {"origin": origin, "target": "mac", "id": mid, "agent": agent, "message": message}
        if at_epoch is not None:
            m["once"] = True; m["at_epoch"] = int(at_epoch)
        (d / f"{origin}--{mid}.json").write_text(json.dumps(m))

    def test_logged_survives_cleanup_and_marks_seen(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        self.put_mail("m1", "coop", "persist me", at_epoch=int(time.time()) + 1)
        self.sync(self.o)
        rows = self.log_rows(self.o, "coop")
        self.assertEqual([(r["id"], r["agent"], r["origin"], r["message"]) for r in rows],
                         [("m1", "coop", "muse", "persist me")])
        self.assertEqual(rows[0]["seen"], False)
        (self.root / "schedules" / "mac" / "muse--m1.json").unlink()  # cleanup removed the pending file
        p, d = self.j(self.o, "relay", "inbox", "--name", "coop")
        self.assertEqual([m["id"] for m in d["data"]], ["m1"])  # still shown, from the log
        self.assertEqual(self.log_rows(self.o, "coop")[0]["seen"], True)  # cursor advance marked it seen
        p, d = self.j(self.o, "relay", "inbox", "--name", "coop")
        self.assertEqual(d["data"], [])
        p, d = self.j(self.o, "relay", "inbox", "--name", "coop", "--all")
        self.assertEqual([m["id"] for m in d["data"]], ["m1"])

    def test_dedupe_by_id(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        self.put_mail("m1", "coop", "once only", at_epoch=int(time.time()) + 1)
        self.sync(self.o); self.sync(self.o)
        self.assertEqual([r["id"] for r in self.log_rows(self.o, "coop")], ["m1"])

    def test_unaddressed_bucket(self):
        self.init(self.o)
        self.put_mail("u1", "not a name!", "nobody owns me", at_epoch=int(time.time()) + 1)
        self.sync(self.o)
        self.assertEqual([r["id"] for r in self.log_rows(self.o, "_unaddressed")], ["u1"])
        p = self.run_ag(self.o, "relay", "inbox")
        self.assertIn("1 unaddressed relay message", p.stdout)  # hint survives cleanup
        (self.root / "schedules" / "mac" / "muse--u1.json").unlink()
        p = self.run_ag(self.o, "relay", "inbox")
        self.assertIn("1 unaddressed relay message", p.stdout)

    def test_since_filters(self):
        self.init(self.o)
        self.j(self.o, "relay", "name", "claim", "coop", "--session", "s1")
        now = int(time.time())
        self.put_mail("old", "coop", "stale", at_epoch=now - 3 * 3600)
        self.put_mail("new", "coop", "fresh", at_epoch=now + 1)
        p, d = self.j(self.o, "relay", "inbox", "--name", "coop", "--since", "1", "--peek")
        self.assertEqual([m["id"] for m in d["data"]], ["new"])
        p, d = self.j(self.o, "relay", "inbox", "--name", "coop", "--since", "5", "--peek")
        self.assertEqual([m["id"] for m in d["data"]], ["new", "old"])


if __name__ == "__main__":
    import unittest
    unittest.main()
