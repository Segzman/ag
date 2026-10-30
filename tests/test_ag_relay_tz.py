"""Fixed-time and relay schedules carry a time zone (acceptance tests from bench v3)."""
import json, os, time, unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from tests.test_ag_relay import Env, ag


def utc(*t): return datetime(*t, tzinfo=timezone.utc).timestamp()


class TZEnv:
    def __init__(self, tz): self.tz, self.old = tz, os.environ.get("TZ")
    def __enter__(self): os.environ["TZ"] = self.tz; time.tzset()
    def __exit__(self, *a):
        if self.old is None: os.environ.pop("TZ", None)
        else: os.environ["TZ"] = self.old
        time.tzset()


class SchedNextTZ(unittest.TestCase):
    def test_iana_zone_independent_of_process_tz(self):
        with TZEnv("UTC"):
            n = ag._sched_next({"at": "09:00", "days": None, "tz": "America/Toronto"}, utc(2026, 10, 30, 12, 0))  # 08:00 EDT
        self.assertEqual(n, utc(2026, 10, 30, 13, 0))

    def test_fixed_offset(self):
        with TZEnv("America/Toronto"):
            n = ag._sched_next({"at": "09:00", "days": None, "tz": "+05:30"}, utc(2026, 10, 30, 0, 0))
        self.assertEqual(n, utc(2026, 10, 30, 3, 30))

    def test_days_use_zone_date(self):
        # Sat 2026-10-31 01:00 UTC is still Fri 21:00 in Toronto; Friday-only 22:00 Toronto -> 02:00 UTC Sat
        with TZEnv("UTC"):
            n = ag._sched_next({"at": "22:00", "days": [4], "tz": "America/Toronto"}, utc(2026, 10, 31, 1, 0))
        self.assertEqual(n, utc(2026, 10, 31, 2, 0))

    def test_no_tz_unchanged(self):
        with TZEnv("America/New_York"):
            f = lambda *t: datetime(*t).astimezone().timestamp()
            n = ag._sched_next({"at": "09:00", "days": [0, 1, 2, 3, 4]}, f(2026, 3, 6, 10))
            self.assertEqual(datetime.fromtimestamp(n).strftime("%a %H:%M"), "Mon 09:00")


class RelayTZ(Env):
    def pub_at(self, sid, origin_tz):
        self.init(self.t); self.init(self.o); self.add_agent(self.o, "w1")
        p, d = self.j(self.o, "schedule", "add", "w1", "--at", "09:00", "--id", sid, "ping")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = self.rows(self.o); r = next(x for x in rows if x["id"] == sid)
        r["on"], r["sdir"] = "muse", str(self.o["state"]); self.put_rows(self.o, rows)
        self.setenv(self.o)
        with TZEnv(origin_tz):
            return ag.relay_publish_schedule(ag.relay_cfg(), r, {"backend": "echo"})

    def test_publish_carries_origin_tz(self):
        f = self.pub_at("s1", "America/Toronto")
        self.assertEqual(json.loads(f.read_text())["tz"], "America/Toronto")

    def test_target_in_utc_fires_at_origin_wall_clock(self):
        self.pub_at("s1", "America/Toronto")
        self.t["env"]["TZ"] = "UTC"
        d = self.sync(self.t)
        self.assertEqual(d["materialized"], ["mac--s1"])
        r = self.row(self.t, "mac--s1")
        self.assertEqual(r["tz"], "America/Toronto")
        self.assertEqual(datetime.fromtimestamp(r["next_due"], ZoneInfo("America/Toronto")).strftime("%H:%M"), "09:00")

    def test_schedule_add_tz_flag(self):
        p, d = self.j(self.o, "schedule", "add", "w1", "--at", "09:00", "--tz", "Europe/Paris", "--id", "p1", "ping") if self.add_agent(self.o, "w1") is None else (None, None)
        self.assertEqual(p.returncode, 0, p.stderr)
        r = next(x for x in self.rows(self.o) if x["id"] == "p1")
        self.assertEqual(r["tz"], "Europe/Paris")
        self.assertEqual(datetime.fromtimestamp(r["next_due"], ZoneInfo("Europe/Paris")).strftime("%H:%M"), "09:00")
        p = self.run_ag(self.o, "schedule", "add", "w1", "--at", "09:00", "--tz", "Mars/Olympus", "--id", "p2", "ping", ok=False)
        self.assertNotEqual(p.returncode, 0); self.assertIn("invalid --tz", p.stdout + p.stderr)

    def test_local_at_without_tz_has_no_key(self):
        self.add_agent(self.o, "w1")
        p, d = self.j(self.o, "schedule", "add", "w1", "--at", "09:00", "--id", "l1", "ping")
        self.assertNotIn("tz", next(x for x in self.rows(self.o) if x["id"] == "l1"))


if __name__ == "__main__":
    unittest.main()
