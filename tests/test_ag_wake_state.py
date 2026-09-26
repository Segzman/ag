#!/usr/bin/env python3
"""Wake-state reliability: enqueue/finish races only. Stdlib only.

Run: python3 tests/test_ag_wake_state.py
Covers:
  1. launch_wake_chat publishes job+session after the session status exists
     but before the worker forks (pre-fork), so a fast worker's
     running/done update is never clobbered and reconcile never sees a
     startup gap (deterministic via fake spawn / fork hooks).
  2. _wake_reconcile spares young jobs whose session is not visible yet.
  3. _wake_finish concurrent callers emit exactly one terminal event/hook
     (per-job crash-released flock; events+notify fire outside the guard);
     lock-acquire failure leaves the job untouched and persistence failure
     emits nothing.
No models/network: echo roster, temp state, in-process module import.
"""
import importlib.machinery
import json
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_AG = str(Path(__file__).resolve().parent.parent / "ag")


def load_ag(name):
    return importlib.machinery.SourceFileLoader(name, _AG).load_module()


def fresh_state(ag, prefix="agws-"):
    td = Path(tempfile.mkdtemp(prefix=prefix))
    sdir = td / "st"
    ag.ensure_state(sdir)
    ag.save_agents(sdir, [
        {"name": "w", "backend": "echo", "model": "default",
         "dir": ".", "role": "sub"},
    ])
    return sdir


def events_for(sdir, jid=None):
    p = sdir / "events.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text().splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if jid is None or e.get("job") == jid:
            rows.append(e)
    return rows


def test_launch_session_prepublished_survives_fast_worker():
    ag = load_ag("agws_a")
    sdir = fresh_state(ag)
    seen = {}

    def fast_spawn(sdir_, agent, jid, sid=None, pre_fork=None):
        if pre_fork is not None:
            pre_fork(sid, None)  # real path: session status already prepared
        cur = ag._wake_read_job(sdir_, jid)
        seen["session_at_spawn"] = cur.get("session")
        seen["spawn_sid"] = sid
        assert cur.get("session") == sid, (
            "job/session must be published before worker runs: %r vs %r"
            % (cur.get("session"), sid))
        assert cur.get("status") == "queued", cur
        cur["status"] = "running"
        ag._wake_write_job(sdir_, cur)
        ag._wake_finish(sdir_, jid, "done", reply="fast-reply")
        return sid

    real = ag._spawn_wake_session
    ag._spawn_wake_session = fast_spawn
    try:
        r = ag.launch_wake_chat(sdir, "w", "hi")
    finally:
        ag._spawn_wake_session = real
    assert "job" in r, r
    jid = r["job"]
    final = ag._wake_read_job(sdir, jid)
    assert final.get("status") == "done", final
    assert final.get("reply") == "fast-reply", final
    assert final.get("session") == seen["spawn_sid"] == r["session"], (
        final, seen, r)
    n_wake = sum(1 for e in events_for(sdir, jid) if e.get("type") == "wake")
    assert n_wake == 1, events_for(sdir, jid)
    print("ok test_launch_session_prepublished_survives_fast_worker")


def test_launch_spawn_failure_marks_failed_once():
    ag = load_ag("agws_b")
    sdir = fresh_state(ag)

    def boom(sdir_, agent, jid, sid=None, pre_fork=None):
        if pre_fork is not None:
            pre_fork(sid, None)  # session prepared + job published, then fork dies
        raise RuntimeError("no pty")

    real = ag._spawn_wake_session
    ag._spawn_wake_session = boom
    try:
        r = ag.launch_wake_chat(sdir, "w", "hi")
    finally:
        ag._spawn_wake_session = real
    assert "error" in r, r
    jobs = list((sdir / "wake" / "jobs").glob("*.json"))
    assert len(jobs) == 1, jobs
    final = json.loads(jobs[0].read_text())
    assert final.get("status") == "failed", final
    assert final.get("session"), final  # pre-published sid kept
    assert final.get("backend_exit"), final  # nonzero spawn-failure code
    fails = [e for e in events_for(sdir, final["id"])
             if e.get("type") == "wake_fail"]
    assert len(fails) == 1, events_for(sdir, final["id"])
    print("ok test_launch_spawn_failure_marks_failed_once")


def _queued_job(ag, sdir, jid, **kw):
    job = {"id": jid, "agent": "w", "message": "m", "trigger": None,
           "exit": None, "status": "queued", "ts": ag.now_iso(),
           "session": "s", "notify": "", "on_done": "",
           "timeout_s": 0}
    job.update(kw)
    ag._wake_write_job(sdir, job)
    return job


def test_wake_finish_concurrent_exactly_once():
    ag = load_ag("agws_c")
    sdir = fresh_state(ag)
    jid = "job-conc"
    _queued_job(ag, sdir, jid)
    base = len(events_for(sdir))
    ths = [threading.Thread(
        target=lambda: ag._wake_finish(sdir, jid, "done", reply="r"))
        for _ in range(8)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    final = ag._wake_read_job(sdir, jid)
    assert final.get("status") == "done", final
    new = events_for(sdir)[base:]
    n = sum(1 for e in new
            if e.get("type") == "wake_done" and e.get("job") == jid)
    assert n == 1, new
    print("ok test_wake_finish_concurrent_exactly_once")


def test_wake_finish_second_caller_no_duplicate_hook():
    ag = load_ag("agws_d")
    sdir = fresh_state(ag)
    marker = sdir / "hook.log"
    jid = "job-hook"
    _queued_job(ag, sdir, jid, on_done="echo hooked >> %s" % marker)
    base = len(events_for(sdir))
    ths = [threading.Thread(
        target=lambda: ag._wake_finish(sdir, jid, "done", reply="r"))
        for _ in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    t0 = time.time()
    while time.time() - t0 < 5:
        if marker.exists():
            break
        time.sleep(0.1)
    assert marker.exists(), "on-done hook never ran"
    time.sleep(0.5)  # let any duplicate hook land
    lines = marker.read_text().splitlines()
    assert lines == ["hooked"], lines
    new = events_for(sdir)[base:]
    n = sum(1 for e in new
            if e.get("type") == "wake_done" and e.get("job") == jid)
    assert n == 1, new
    # Sequential loser after terminal: untouched, no new event.
    before = len(events_for(sdir))
    cur = ag._wake_finish(sdir, jid, "failed", error="late", exit_code=1)
    assert cur.get("status") == "done", cur
    assert len(events_for(sdir)) == before, events_for(sdir)[before:]
    print("ok test_wake_finish_second_caller_no_duplicate_hook")


def test_job_lock_distinct_from_agent_lock():
    ag = load_ag("agws_e")
    sdir = fresh_state(ag)
    assert (ag._wake_job_lock_path(sdir, "j1")
            != ag._wake_lock_path(sdir, "w")), "job guard must not be the turn guard"
    f = ag._acquire_wake_job(sdir, "j1")
    try:
        assert f is not None
    finally:
        ag._release_wake_job(f)
    print("ok test_job_lock_distinct_from_agent_lock")


def test_launch_no_startup_gap_at_fork():
    """Real prepare path with fork stubbed: at fork time the job is already
    published and the session live, so a concurrent reconcile spares it."""
    ag = load_ag("agws_f")
    sdir = fresh_state(ag)
    seen = {}
    real_fork = ag._fork_tracked_daemon

    def no_fork(sp):
        jobs = list((sdir / "wake" / "jobs").glob("*.json"))
        seen["job_at_fork"] = len(jobs) == 1
        if jobs:
            j = json.loads(jobs[0].read_text())
            seen["jid"] = j.get("id")
            seen["reconciled"] = ag._wake_reconcile(sdir, j).get("status")

    ag._fork_tracked_daemon = no_fork
    try:
        r = ag.launch_wake_chat(sdir, "w", "hi")
    finally:
        ag._fork_tracked_daemon = real_fork
    assert "job" in r, r
    assert seen.get("job_at_fork"), seen
    assert seen.get("reconciled") == "queued", seen
    final = ag._wake_read_job(sdir, r["job"])
    assert final.get("status") == "queued", final
    assert final.get("session") == r["session"], (final, r)
    print("ok test_launch_no_startup_gap_at_fork")


def test_reconcile_spares_young_missing_session():
    ag = load_ag("agws_g")
    sdir = fresh_state(ag)
    _queued_job(ag, sdir, "young-ghost", session="ghost")
    out = ag._wake_reconcile(sdir, ag._wake_read_job(sdir, "young-ghost"))
    assert out.get("status") == "queued", out
    assert not [e for e in events_for(sdir, "young-ghost")
                if e.get("type") == "wake_fail"], events_for(sdir)
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    _queued_job(ag, sdir, "old-ghost", session="ghost", ts=old_ts)
    out = ag._wake_reconcile(sdir, ag._wake_read_job(sdir, "old-ghost"))
    assert out.get("status") == "failed", out
    assert "worker gone" in (out.get("error") or ""), out
    print("ok test_reconcile_spares_young_missing_session")


def test_wake_finish_lock_failure_leaves_untouched():
    ag = load_ag("agws_h")
    sdir = fresh_state(ag)
    _queued_job(ag, sdir, "job-locked")
    base = len(events_for(sdir))
    real_acq = ag._acquire_wake_job
    ag._acquire_wake_job = lambda *a: (_ for _ in ()).throw(OSError("flock busy"))
    try:
        cur = ag._wake_finish(sdir, "job-locked", "done", reply="r")
    finally:
        ag._acquire_wake_job = real_acq
    assert cur.get("status") == "queued", cur
    assert len(events_for(sdir)) == base, events_for(sdir)[base:]
    print("ok test_wake_finish_lock_failure_leaves_untouched")


def test_wake_finish_persist_failure_emits_nothing():
    ag = load_ag("agws_i")
    sdir = fresh_state(ag)
    marker = sdir / "nopersist.log"
    _queued_job(ag, sdir, "job-nopersist",
                on_done="echo hooked >> %s" % marker)
    base = len(events_for(sdir))
    real_write = ag._wake_write_job

    def boom_write(sdir_, job):
        raise OSError("disk gone")

    ag._wake_write_job = boom_write
    try:
        cur = ag._wake_finish(sdir, "job-nopersist", "done", reply="r")
    finally:
        ag._wake_write_job = real_write
    assert cur.get("status") == "queued", cur
    assert len(events_for(sdir)) == base, events_for(sdir)[base:]
    time.sleep(0.5)
    assert not marker.exists(), "hook must not fire when terminal did not persist"
    print("ok test_wake_finish_persist_failure_emits_nothing")


if __name__ == "__main__":
    test_launch_session_prepublished_survives_fast_worker()
    test_launch_spawn_failure_marks_failed_once()
    test_launch_no_startup_gap_at_fork()
    test_reconcile_spares_young_missing_session()
    test_wake_finish_concurrent_exactly_once()
    test_wake_finish_second_caller_no_duplicate_hook()
    test_wake_finish_lock_failure_leaves_untouched()
    test_wake_finish_persist_failure_emits_nothing()
    test_job_lock_distinct_from_agent_lock()
    print("all wake-state tests passed")
