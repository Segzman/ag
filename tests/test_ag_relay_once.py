"""One-shot cloud wakes over the relay dir mailbox."""
import json
import unittest

from tests.test_ag_relay import Env


class RelayOnce(Env):
    def test_remote_wake_runs_once_reports_and_cleans_mail(self):
        self.init(self.o)
        self.init(self.t)
        self.add_agent(self.o, "w1")

        p, d = self.j(self.o, "wake", "w1", "cloud ping", "--on", "muse", "--request-id", "once-key")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        rid = d["data"]["job"]
        p, replay = self.j(self.o, "wake", "w1", "cloud ping", "--on", "muse", "--request-id", "once-key")
        self.assertTrue(replay["data"]["replayed"])
        self.assertEqual(replay["data"]["job"], rid)
        p = self.run_ag(self.o, "--json", "wake", "w1", "cloud ping", "--on", "other", "--request-id", "once-key", ok=False)
        self.assertIn("request id conflict", p.stdout)
        mail = self.root / "schedules" / "muse" / f'mac--{rid}.json'
        self.assertTrue(mail.exists(), p.stdout + p.stderr)
        m = json.loads(mail.read_text())
        self.assertTrue(m["once"])
        self.assertIsInstance(m["at_epoch"], (int, float))
        self.assertEqual(m["message"], "cloud ping")

        pending = self.j(self.o, "wakes")[1]["data"]["wakes"]
        self.assertTrue(any(x.get("id") == rid and x.get("status") == "remote@muse" for x in pending))

        self.sync(self.t)
        self.run_ag(self.t, "schedule", "tick", "--all", timeout=90)
        row = next(r for r in self.rows(self.t) if r.get("relay_id") == rid)
        self.run_ag(self.t, "wakes", "--wait", str(row["last_job"]), "--timeout", "45", timeout=90)
        self.sync(self.t)
        self.assertNotIn(f'mac--{rid}', [r["id"] for r in self.rows(self.t)])
        self.sync(self.t)  # still-published mail must not materialise or fire twice
        self.assertNotIn(f'mac--{rid}', [r["id"] for r in self.rows(self.t)])

        self.sync(self.o)
        self.assertFalse(mail.exists())
        self.assertFalse(any(x.get("id") == rid for x in self.j(self.o, "wakes")[1]["data"]["wakes"]))
        p, d = self.j(self.o, "relay", "results", "--id", rid)
        self.assertEqual(d["data"]["results"][0]["id"], rid)
        self.assertIn("cloud ping", d["data"]["results"][0]["reply"])


if __name__ == "__main__":
    unittest.main()
