#!/usr/bin/env python3
"""ag schedule: parsing, next_after, tick idempotence, missed/overlap, install plist. Stdlib, echo backend, temp dirs."""
import importlib.machinery, importlib.util, json, os, plistlib, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timezone
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod", AG); _s = importlib.util.spec_from_loader("ag_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)


class Env(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-sched-t-"))
        self.home = self.td / "home"; self.sd = self.td / "state"; self.bin = self.td / "bin"
        for d in (self.home, self.bin): d.mkdir()
        self.env = dict(os.environ, AG_HOME=str(self.home), AG_CONFIG_HOME=str(self.home / "cfg"), AG_CACHE_HOME=str(self.home / "cache"),
            AG_AUTO_UPDATE="0", PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        self.addCleanup(__import__('shutil').rmtree, self.td, True)
        self.run_ag("agents", "add", "w1", "--backend", "echo", "--role", "sub")

    def run_ag(self, *a, env=None, sd=None):
        return subprocess.run([sys.executable, AG, "--dir", str(sd or self.sd), *a], capture_output=True, text=True, env=env or self.env, timeout=30)

    def j(self, *a, **k):
        p = self.run_ag("--json", *a, **k); return p, (json.loads(p.stdout) if p.stdout.strip() else {})

    def rows(self): return json.loads((self.sd / "schedules.json").read_text())
    def put(self, rows): (self.sd / "schedules.json").write_text(json.dumps(rows))
    def wakes(self): return len(list((self.sd / "wake" / "jobs").glob("*.json"))) if (self.sd / "wake" / "jobs").exists() else 0
    def iso(self, ts): return datetime.fromtimestamp(ts).isoformat()


class Parse(unittest.TestCase):
    def test_parse(self):
        self.assertEqual([ag._sched_every(x) for x in ("15m", "2h", "1d")], [900, 7200, 86400])
        for bad in ("30s", "x", "5"): self.assertRaises(ValueError, ag._sched_every, bad)
        self.assertEqual(ag._sched_at("9:00"), "09:00"); self.assertRaises(ValueError, ag._sched_at, "25:00")
        self.assertEqual(ag._sched_days("mon-fri"), [0, 1, 2, 3, 4]); self.assertEqual(ag._sched_days("mon,wed,fri"), [0, 2, 4])
        self.assertIsNone(ag._sched_days("daily")); self.assertEqual(ag._sched_days("weekends"), [5, 6]); self.assertEqual(ag._sched_days("sat-mon"), [0, 5, 6])

    def test_next_after(self):
        r = {"every_s": 900, "next_due": 1000.0}
        self.assertEqual(ag._sched_next(r, 1000.0), 1900.0)
        self.assertEqual(ag._sched_next(r, 1000.0 + 900 * 5 + 1), 1000.0 + 900 * 6)  # missed slots collapse
        self.assertEqual(ag._sched_next(r, 500.0), 1000.0)

    def test_weekday_and_dst(self):
        old = os.environ.get("TZ"); os.environ["TZ"] = "America/New_York"; time.tzset()
        try:
            f = lambda *t: datetime(*t).astimezone().timestamp()
            # Fri 2026-03-06 10:00 -> next mon-fri 09:00 is Mon 03-09
            n = ag._sched_next({"at": "09:00", "days": [0, 1, 2, 3, 4]}, f(2026, 3, 6, 10))
            self.assertEqual(datetime.fromtimestamp(n).strftime("%a %H:%M"), "Mon 09:00")
            # DST gap 2026-03-08 02:30 does not exist -> forward (03:30 EDT)
            n = ag._sched_next({"at": "02:30", "days": None}, f(2026, 3, 8, 0, 30))
            self.assertEqual(datetime.fromtimestamp(n).strftime("%d %H:%M"), "08 03:30")
            # ambiguous 2026-11-01 01:30 -> first occurrence (fold=0, EDT = 05:30 UTC)
            n = ag._sched_next({"at": "01:30", "days": None}, f(2026, 11, 1, 0, 0))
            self.assertEqual(datetime.fromtimestamp(n, timezone.utc).strftime("%H:%M"), "05:30")
        finally:
            if old is None: os.environ.pop("TZ", None)
            else: os.environ["TZ"] = old
            time.tzset()

    def test_update_skip(self): self.assertIn("schedule", ag.UPDATE_SKIP)


class Cli(Env):
    def add(self, *a): return self.j("schedule", "add", "w1", *a)

    def test_add_list_registry_rm(self):
        p, o = self.add("--every", "15m", "--id", "s1", "ping"); self.assertEqual(p.returncode, 0, p.stderr)
        reg = self.home / "cfg" / "schedule-dirs.json"
        self.assertEqual(json.loads(reg.read_text()), [str(self.sd.resolve())])
        self.assertNotEqual(self.add("--every", "15m", "--id", "s1", "dup")[0].returncode, 0)
        self.assertNotEqual(self.add("--every", "10s", "x")[0].returncode, 0)
        _, o = self.j("schedule", "list"); self.assertEqual(o["data"]["schedules"][0]["id"], "s1")
        self.assertIn("tick:", self.run_ag("schedule", "list").stdout)
        self.assertEqual(self.run_ag("schedule", "rm", "s1").returncode, 0)
        self.assertEqual(json.loads(reg.read_text()), [])

    def test_tick_double_fires_once(self):
        self.add("--every", "15m", "--id", "s1", "ping")
        t = self.rows()[0]["next_due"] + 1; ps = [subprocess.Popen([sys.executable, AG, "--dir", str(self.sd), "schedule", "tick", "--now", self.iso(t)],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for p in ps: p.communicate(); self.assertEqual(p.returncode, 0)
        self.assertEqual(self.wakes(), 1)
        r = self.rows()[0]; self.assertEqual(r["runs"], 1); self.assertGreater(r["next_due"], t)
        self.run_ag("schedule", "tick", "--now", self.iso(t)); self.assertEqual(self.wakes(), 1)

    def test_crash_between_receipt_and_state(self):
        self.add("--every", "15m", "--id", "s1", "ping"); r0 = self.rows(); due = r0[0]["next_due"]; t = due + 1
        self.run_ag("schedule", "tick", "--now", self.iso(t)); self.assertEqual(self.wakes(), 1)
        self.put(r0)  # simulate lost state write
        self.run_ag("schedule", "tick", "--now", self.iso(t)); self.assertEqual(self.wakes(), 1)
        self.assertEqual(self.rows()[0]["runs"], 1)

    def test_at_row_missed_and_catch_up(self):
        self.add("--at", "09:00", "--id", "a1", "m"); self.add("--at", "09:00", "--catch-up", "--id", "a2", "m")
        rows = self.rows(); due = rows[0]["next_due"]
        p, o = self.j("schedule", "tick", "--now", self.iso(due + 660))
        acts = {x["id"]: x["action"] for x in o["data"]["dirs"][0]["actions"]}
        self.assertEqual(acts, {"a1": "missed", "a2": "fire"}); self.assertEqual(self.wakes(), 1)
        r = {x["id"]: x for x in self.rows()}; self.assertEqual(r["a1"]["missed"], 1); self.assertGreater(r["a1"]["next_due"], due + 660)
        ev = (self.sd / "events.jsonl").read_text(); self.assertIn("schedule_missed", ev)

    def test_overlap_skip_then_dead_worker_recovers(self):
        self.add("--every", "15m", "--id", "s1", "ping"); due = self.rows()[0]["next_due"]
        jd = self.sd / "wake" / "jobs"; jd.mkdir(parents=True, exist_ok=True)
        (jd / "dead0001.json").write_text(json.dumps({"id": "dead0001", "agent": "w1", "status": "running", "session": "nosuchsess",
            "ts": "2020-01-01T00:00:00+00:00", "message": "x"}))
        rows = self.rows(); rows[0]["last_job"] = "dead0001"; self.put(rows)
        # dead worker: reconcile marks it failed, so due fires (not skipped)
        _, o = self.j("schedule", "tick", "--now", self.iso(due + 1))
        self.assertEqual(o["data"]["dirs"][0]["actions"][0]["action"], "fire")
        self.assertEqual(json.loads((jd / "dead0001.json").read_text())["status"], "failed")
        # overlap queue fires even if last job still queued
        rows = self.rows(); due2 = rows[0]["next_due"]; rows[0]["overlap"] = "queue"; self.put(rows)
        n = self.wakes(); self.run_ag("schedule", "tick", "--now", self.iso(due2 + 1)); self.assertEqual(self.wakes(), n + 1)

    def test_overlap_skip_counts(self):
        self.add("--every", "15m", "--id", "s1", "ping"); due = self.rows()[0]["next_due"]
        jd = self.sd / "wake" / "jobs"; jd.mkdir(parents=True, exist_ok=True)
        fresh = datetime.now().astimezone().isoformat()
        (jd / "q0000001.json").write_text(json.dumps({"id": "q0000001", "agent": "w1", "status": "queued", "ts": fresh, "message": "x"}))  # young, no session: starting up
        rows = self.rows(); rows[0]["last_job"] = "q0000001"; self.put(rows)
        _, o = self.j("schedule", "tick", "--now", self.iso(due + 1))
        self.assertEqual(o["data"]["dirs"][0]["actions"][0]["action"], "skipped"); self.assertEqual(self.rows()[0]["skipped"], 1)
        self.assertEqual(self.wakes(), 1)

    def test_corrupt_refuses_and_dry_run(self):
        self.add("--every", "15m", "--id", "s1", "ping"); due = self.rows()[0]["next_due"]
        _, o = self.j("schedule", "tick", "--dry-run", "--now", self.iso(due + 1))
        self.assertEqual(o["data"]["dirs"][0]["actions"][0]["action"], "fire"); self.assertEqual(self.wakes(), 0)
        (self.sd / "schedules.json").write_text("{nope")
        self.run_ag("schedule", "tick", "--now", self.iso(due + 1))
        self.assertEqual((self.sd / "schedules.json").read_text(), "{nope"); self.assertIn("schedule_fail", (self.sd / "events.jsonl").read_text())

    def test_pause_resume_run_and_removed_agent(self):
        self.add("--every", "15m", "--id", "s1", "ping"); due = self.rows()[0]["next_due"]
        self.run_ag("schedule", "pause", "s1"); self.run_ag("schedule", "tick", "--now", self.iso(due + 1)); self.assertEqual(self.wakes(), 0)
        self.run_ag("schedule", "resume", "s1"); self.assertTrue(self.rows()[0]["enabled"])
        p, o = self.j("schedule", "run", "s1"); self.assertEqual(p.returncode, 0, p.stderr); self.assertEqual(self.wakes(), 1)
        self.run_ag("agents", "rm", "w1"); rows = self.rows(); rows[0]["next_due"] = due; self.put(rows)
        self.run_ag("schedule", "tick", "--now", self.iso(due + 1)); self.assertTrue(self.rows()[0]["last_status"].startswith("error"))

    def test_tick_all_uses_registry(self):
        self.add("--every", "15m", "--id", "s1", "ping"); due = self.rows()[0]["next_due"]
        other = self.td / "elsewhere"; other.mkdir()
        p = self.run_ag("--json", "schedule", "tick", "--all", "--now", self.iso(due + 1), sd=other)
        self.assertEqual(json.loads(p.stdout)["data"]["dirs"][0]["dir"], str(self.sd.resolve())); self.assertEqual(self.wakes(), 1)

    def test_install_plist_and_write(self):
        p, o = self.j("schedule", "install"); self.assertEqual(p.returncode, 0)
        pl = o["data"]["plist"]; self.assertEqual(pl["Label"], "org.ag.schedule"); self.assertEqual(pl["StartInterval"], 60)
        self.assertEqual(pl["ProgramArguments"][2:], ["schedule", "tick", "--all", "--reap"])
        self.assertFalse((self.home / "Library").exists())
        self.assertIn("schedule tick --all --reap", self.run_ag("schedule", "install", "--cron").stdout)
        log = self.td / "lc.log"; fake = self.bin / "launchctl"
        fake.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\n[ \"$1\" = print ] && exit 0\nexit 0\n"); fake.chmod(0o755)
        p, o = self.j("schedule", "install", "--write"); self.assertEqual(p.returncode, 0, p.stderr)
        path = self.home / "Library" / "LaunchAgents" / "org.ag.schedule.plist"
        self.assertEqual(plistlib.loads(path.read_bytes())["Label"], "org.ag.schedule")
        calls = log.read_text().splitlines(); self.assertTrue(calls[1].startswith("bootout")); self.assertTrue(calls[2].startswith("bootstrap"))


if __name__ == "__main__": unittest.main()
