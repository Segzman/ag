#!/usr/bin/env python3
"""Wake full reply bodies: sidecar beside the job JSON, JSON surfaces, relay fallback. Stdlib only.

Run: python3 tests/test_ag_wake_full_reply.py   (also pytest-compatible)
Covers:
  1. _wake_finish("done", long reply) keeps the 500-char preview in the job,
     writes the full body atomically to <jobs>/<id>.reply.md and points at it
     via reply_full; short replies write no sidecar; foreign reply_full paths
     are never read back.
  2. `wakes --json` (list and single --wait) swaps the preview for the full text.
  3. _relay_report publishes the full body when the chat log has no rows.
No models/network: echo roster, temp state, in-process module import.
"""
import contextlib
import importlib.machinery
import io
import json
import tempfile
import time
from pathlib import Path

def _load_src(loader):  # SourceFileLoader.load_module was removed in Python 3.15
    import importlib.util, sys
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

_AG = str(Path(__file__).resolve().parent.parent / "ag")
LONG = "x" * 700 + "TAIL"

def load_ag(name):
    return _load_src(importlib.machinery.SourceFileLoader(name, _AG))

def fresh_state(ag, prefix="agwfr-"):
    td = Path(tempfile.mkdtemp(prefix=prefix))
    sdir = td / "st"
    ag.ensure_state(sdir)
    ag.save_agents(sdir, [{"name": "w", "backend": "echo", "model": "default", "dir": ".", "role": "sub"}])
    return td, sdir

def seed_done(ag, sdir, jid, reply):
    ag._wake_write_job(sdir, {"id": jid, "agent": "w", "message": "m", "trigger": None,
        "exit": None, "status": "queued", "ts": ag.now_iso(), "session": "s",
        "notify": "", "on_done": "", "timeout_s": 0})
    return ag._wake_finish(sdir, jid, "done", reply=reply)

def wakes_ns(ag, sdir, **kw):
    base = dict(dir=str(sdir), json=True, reap=False, cancel=None, wait=None,
        wait_group=None, group=None, agent=None, limit=50, timeout=30)
    base.update(kw)
    return argparse_ns(ag, **base)

def argparse_ns(ag, **kw):
    import argparse
    return argparse.Namespace(**kw)

def emit_json(fn, *ar):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*ar)
    return json.loads(buf.getvalue())

def test_wake_finish_sidecar_and_preview():
    ag = load_ag("agwfr_a")
    td, sdir = fresh_state(ag)
    cur = seed_done(ag, sdir, "j-long", LONG)
    assert cur["reply"] == LONG[:500], cur["reply"]
    assert cur.get("reply_full"), cur
    p = Path(cur["reply_full"])
    assert p.name == "j-long.reply.md" and p.parent == sdir / "wake" / "jobs", p
    assert p.read_text() == LONG, "sidecar must hold the full body"
    job = ag._wake_read_job(sdir, "j-long")
    assert job["reply"] == LONG[:500] and job["reply_full"] == str(p), job
    assert ag._wake_full_reply(sdir, job) == LONG
    # short reply: preview only, no sidecar field/file
    cur2 = seed_done(ag, sdir, "j-short", "short-reply")
    assert cur2["reply"] == "short-reply" and "reply_full" not in cur2, cur2
    assert not (sdir / "wake" / "jobs" / "j-short.reply.md").exists()
    assert ag._wake_full_reply(sdir, cur2) == ""
    # tampered job pointing outside the jobs dir is not trusted
    bad = dict(cur, reply_full=str(td / "elsewhere.md"))
    (td / "elsewhere.md").write_text("nope")
    assert ag._wake_full_reply(sdir, bad) == ""
    print("ok test_wake_finish_sidecar_and_preview")

def test_wakes_json_full_reply():
    ag = load_ag("agwfr_b")
    td, sdir = fresh_state(ag)
    seed_done(ag, sdir, "j-long", LONG)
    seed_done(ag, sdir, "j-short", "short-reply")
    d = emit_json(ag.do_wakes, wakes_ns(ag, sdir))["data"]
    rows = {r["id"]: r for r in d["wakes"]}
    assert rows["j-long"]["reply"] == LONG, rows["j-long"]["reply"][:80]
    assert rows["j-short"]["reply"] == "short-reply"
    print("ok test_wakes_json_full_reply")

def test_wakes_wait_json_full_reply():
    ag = load_ag("agwfr_c")
    td, sdir = fresh_state(ag)
    seed_done(ag, sdir, "j-long", LONG)
    d = emit_json(ag.do_wakes, wakes_ns(ag, sdir, wait=["j-long"]))["data"]
    assert d["reply"] == LONG, str(d)[:120]
    assert d["reply"][:500] == LONG[:500]
    print("ok test_wakes_wait_json_full_reply")

def test_relay_report_publishes_full_body():
    ag = load_ag("agwfr_d")
    td, sdir = fresh_state(ag)
    ag._sched_save(sdir, [{"id": "mac--s1", "agent": "w", "relay_origin": "mac",
        "relay_id": "s1", "last_job": "job1", "last_due": 123}])
    seed_done(ag, sdir, "job1", LONG)  # no chat-log rows since job start -> sidecar fallback
    mail = td / "mail"; mail.mkdir()
    out, errs = {"reported": []}, []
    ag._relay_report({"node": "muse", "state": str(sdir)}, mail, time.time(), out, errs)
    assert not errs, errs
    res = json.loads((mail / "results" / "mac" / "muse--s1--123.json").read_text())
    assert res["reply"] == LONG, res["reply"][:80]
    # preview-only job (no sidecar) still publishes the short reply
    ag._sched_save(sdir, [{"id": "mac--s2", "agent": "w", "relay_origin": "mac",
        "relay_id": "s2", "last_job": "job2", "last_due": 124}])
    ag._wake_write_job(sdir, {"id": "job2", "agent": "w", "status": "done", "reply": "preview-only",
        "ts": ag.now_iso(), "ended_at": ag.now_iso()})
    out2, errs2 = {"reported": []}, []
    ag._relay_report({"node": "muse", "state": str(sdir)}, mail, time.time(), out2, errs2)
    res2 = json.loads((mail / "results" / "mac" / "muse--s2--124.json").read_text())
    assert res2["reply"] == "preview-only", res2
    print("ok test_relay_report_publishes_full_body")

if __name__ == "__main__":
    test_wake_finish_sidecar_and_preview()
    test_wakes_json_full_reply()
    test_wakes_wait_json_full_reply()
    test_relay_report_publishes_full_body()
    print("all wake full-reply tests passed")
