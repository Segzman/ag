#!/usr/bin/env python3
"""`ag events --follow`: tail, filters, --exit-on-match, wake-state changes.

Run: python3 tests/test_ag_events_follow.py
Target: live repo ag (override with AG_EVENTS_BIN).
"""
import importlib.machinery
import json
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

AG = os.environ.get("AG_EVENTS_BIN", str(Path(__file__).resolve().parent.parent / "ag"))
assert os.path.exists(AG), f"ag missing: {AG}"


def _load_ag(name):
    import importlib.util
    loader = importlib.machinery.SourceFileLoader(name, AG)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod


def _cmd(td, *args):
    return [sys.executable, AG, "--dir", str(td)] + list(args)


def _append(td, *rows):
    with open(Path(td) / "events.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _follow(td, *args):
    return subprocess.Popen(_cmd(td, "events", "--follow", *args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _rows(out):
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_follow_json_filters_and_exit_on_match():
    with tempfile.TemporaryDirectory(prefix="agev-") as td:
        (Path(td) / "events.jsonl").touch()
        p = _follow(td, "--json", "--agent", "w", "--type", "wake_done",
            "--exit-on-match", "--timeout", "5")
        time.sleep(0.5)
        _append(td,
            {"ts": "t1", "type": "spawn", "agent": "w"},
            {"ts": "t2", "type": "wake_fail", "agent": "w"},
            {"ts": "t3", "type": "wake_done", "agent": "other"},
            {"ts": "t4", "type": "wake_done", "agent": "w", "job": "j1"})
        out, err = p.communicate(timeout=10)
        assert p.returncode == 0, (p.returncode, out, err)
        rows = _rows(out)
        assert len(rows) == 1, rows
        assert rows[0]["type"] == "wake_done" and rows[0]["agent"] == "w"
        assert rows[0]["job"] == "j1", rows


def test_follow_timeout_exits_124():
    with tempfile.TemporaryDirectory(prefix="agev-") as td:
        (Path(td) / "events.jsonl").touch()
        p = _follow(td, "--exit-on-match", "--timeout", "0.6")
        out, err = p.communicate(timeout=10)
        assert p.returncode == 124, (p.returncode, out, err)
        assert out == "", out


def test_follow_terminal_wake_job_changes():
    with tempfile.TemporaryDirectory(prefix="agev-") as td:
        (Path(td) / "events.jsonl").touch()
        jobs = Path(td) / "wake" / "jobs"
        jobs.mkdir(parents=True)
        (jobs / "old.json").write_text(json.dumps(
            {"id": "old", "agent": "w", "status": "done"}))
        p = _follow(td, "--json", "--timeout", "2")
        time.sleep(0.5)
        (jobs / "j1.json").write_text(json.dumps(
            {"id": "j1", "agent": "w", "trigger": "s1", "status": "queued"}))
        time.sleep(0.7)
        (jobs / "j1.json").write_text(json.dumps(
            {"id": "j1", "agent": "w", "trigger": "s1", "status": "done"}))
        (jobs / "j2.json").write_text(json.dumps(
            {"id": "j2", "agent": "w", "trigger": "s2", "status": "failed",
             "cancelled": True}))
        out, err = p.communicate(timeout=10)
        assert p.returncode == 124, (p.returncode, out, err)
        rows = _rows(out)
        by_job = {r.get("job"): r for r in rows}
        assert "old" not in by_job, rows
        assert by_job["j1"]["type"] == "wake_done", rows
        assert by_job["j1"]["status"] == "done" and by_job["j1"]["agent"] == "w"
        assert by_job["j2"]["type"] == "wake_cancel", rows
        assert by_job["j2"]["status"] == "cancelled" and by_job["j2"]["cancelled"]


def test_events_filters_apply_without_follow():
    with tempfile.TemporaryDirectory(prefix="agev-") as td:
        _append(td,
            {"ts": "t1", "type": "spawn", "agent": "a"},
            {"ts": "t2", "type": "wake_done", "agent": "b"},
            {"ts": "t3", "type": "wake_done", "agent": "a"})
        p = subprocess.run(_cmd(td, "events", "--type", "wake_done", "--agent", "a",
            "--limit", "10"), capture_output=True, text=True, timeout=20)
        assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
        assert p.stdout.strip() == "t3 wake_done", p.stdout


def test_unchanged_wake_job_not_reparsed():
    ag = _load_ag("agev_reparse")
    with tempfile.TemporaryDirectory(prefix="agev-") as td:
        sdir = Path(td)
        ag.ensure_state(sdir)
        jobs = sdir / "wake" / "jobs"
        jobs.mkdir(parents=True)
        (jobs / "j1.json").write_text(json.dumps(
            {"id": "j1", "agent": "w", "status": "queued"}))
        (sdir / "events.jsonl").touch()
        a = types.SimpleNamespace(type=None, session=None, agent=None,
            timeout=1.1, json=True, exit_on_match=False, follow=True, dir=str(sdir))
        calls = []
        real = ag.json.loads
        def counting(*args, **kw):
            calls.append(args[0])
            return real(*args, **kw)
        ag.json.loads = counting
        try: rc = ag._follow_events(a, sdir)
        finally: ag.json.loads = real
        assert rc == 124, rc
        assert len(calls) == 1, calls
