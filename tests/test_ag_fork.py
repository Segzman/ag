#!/usr/bin/env python3
"""agents fork: clone config + history with fresh session, lineage. In-process, echo backend, no models.
Run: python3 tests/test_ag_fork.py"""
import importlib.machinery, io, json, os, shutil, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path

_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = importlib.machinery.SourceFileLoader("ag_fork_t", _AG).load_module()


class A:
    def __init__(self, **k): self.__dict__.update(k)


def fresh():
    td = Path(tempfile.mkdtemp(prefix="agfork-"))
    sdir = td / ".agent"
    AG.ensure_state(sdir)
    AG.save_agents(sdir, [{"name": "src", "backend": "echo", "model": "default", "dir": ".", "role": "sub",
        "persona": "reviewer", "system": "sys", "instructions": ["m.md"], "mode": "edits", "plan": True,
        "effort": "max", "env": {"K": "v"}, "env_unset": ["U"], "data": "work", "fallback": ["opencode/space-bunny-free"]}])
    (sdir / "chats").mkdir(parents=True, exist_ok=True)
    (sdir / "chats" / "src.jsonl").write_text(
        json.dumps({"role": "user", "text": "q1"}) + "\n" + json.dumps({"role": "agent", "text": "a1"}) + "\n" +
        json.dumps({"role": "user", "text": "q2"}) + "\n")
    (sdir / "chats" / "src.meta.json").write_text(json.dumps(
        {"backend": "echo", "sid": "old-sid", "sids": {"echo": "old-sid"}}))
    return td, sdir


def fork(sdir, src="src", new="new1", **k):
    a = A(dir=str(sdir), json=True, sub="fork", src=src, new=new, backend=k.get("backend"),
          model=k.get("model"), effort=k.get("effort"), worktree=k.get("worktree", False),
          base=k.get("base"), acp_cmd=k.get("acp_cmd"))
    buf = io.StringIO()
    with redirect_stdout(buf): rc = AG.do_agents(a)
    return rc, buf.getvalue()


def lst(sdir, as_json=False):
    a = A(dir=str(sdir), json=as_json, sub="list")
    buf = io.StringIO()
    with redirect_stdout(buf): AG.do_agents(a)
    out = buf.getvalue()
    return json.loads(out)["data"]["agents"] if as_json else out


class ForkTest(unittest.TestCase):
    def setUp(self):
        os.environ["AG_PROBE"] = "0"
        self.tds = []

    def tearDown(self):
        os.environ.pop("AG_PROBE", None)
        for td in self.tds: shutil.rmtree(td, ignore_errors=True)

    def test_keeps_privacy_class_and_fallback(self):  # swarm finding 2026-10-12: fork dropped `data`
        td, sdir = fresh(); self.tds.append(td)
        rc, _ = fork(sdir); self.assertEqual(rc, 0)
        n = {x["name"]: x for x in AG.load_agents(sdir)}["new1"]
        self.assertEqual(n.get("data"), "work"); self.assertEqual(n.get("fallback"), ["opencode/space-bunny-free"])

    def test_copies_config_history_not_sids(self):
        td, sdir = fresh(); self.tds.append(td)
        rc, _ = fork(sdir)
        self.assertEqual(rc, 0)
        rows = {r["name"]: r for r in AG.load_agents(sdir)}
        n = rows["new1"]
        self.assertEqual((n["backend"], n["model"], n["persona"], n["system"], n["instructions"],
                          n.get("mode"), n.get("plan"), n.get("effort"), n.get("env")), (
                          "echo", "default", "reviewer", "sys", ["m.md"], "edits", True, "max", {"K": "v"}))
        self.assertEqual((n["forked_from"], n["fork_turn"]), ("src", 2))
        self.assertTrue(n["forked_at"])
        self.assertEqual([json.loads(l)["text"] for l in (sdir / "chats" / "new1.jsonl").read_text().splitlines()],
                         ["q1", "a1", "q2"])
        meta = json.loads((sdir / "chats" / "new1.meta.json").read_text())
        self.assertEqual((meta["sid"], meta["sids"]), ("", {}))
        txt = lst(sdir)
        self.assertIn("<- src", [l for l in txt.splitlines() if l.startswith("new1")][0])
        j = lst(sdir, as_json=True)
        self.assertEqual([a for a in j if a["name"] == "new1"][0]["forked_from"], "src")

    def test_first_turn_replays_history(self):
        td, sdir = fresh(); self.tds.append(td)
        fork(sdir)
        r = AG.run_turn(sdir, "new1", "q3 here")
        self.assertEqual(r.get("exit"), 0, r)
        self.assertIn("user: q1", r["reply"])
        self.assertIn("agent: a1", r["reply"])
        self.assertIn("user: q2", r["reply"])
        self.assertTrue(r["reply"].rstrip().endswith("q3 here"))
        self.assertEqual(r["reply"].count("q3 here"), 1)

    def test_overrides(self):
        td, sdir = fresh(); self.tds.append(td)
        rc, _ = fork(sdir, new="n2", backend="echo", model="m1", effort="high")
        self.assertEqual(rc, 0)
        n = next(r for r in AG.load_agents(sdir) if r["name"] == "n2")
        self.assertEqual((n["backend"], n["model"], n["effort"]), ("echo", "m1", "high"))

    def test_refuses(self):
        td, sdir = fresh(); self.tds.append(td)
        rc, out = fork(sdir, src="nosuch", new="x")
        self.assertNotEqual(rc, 0); self.assertIn("no such agent", out)
        fork(sdir, new="dup")
        rc, out = fork(sdir, new="dup")
        self.assertNotEqual(rc, 0); self.assertIn("exists", out)
        guard = AG._acquire_agent_turn(sdir, "src", blocking=False)
        try:
            rc, out = fork(sdir, new="busy1")
            self.assertNotEqual(rc, 0); self.assertIn("busy", out)
            self.assertNotIn("busy1", [r["name"] for r in AG.load_agents(sdir)])
        finally:
            AG._release_agent_turn(guard)

    def test_resume_helper_shared(self):
        td, sdir = fresh(); self.tds.append(td)
        # drop_trailing_user=True (retry path): trailing q2 excluded; False (fork): kept
        self.assertNotIn("q2", AG.resume_replay_hist(sdir, "src"))
        self.assertIn("user: q2", AG.resume_replay_hist(sdir, "src", drop_trailing_user=False))
        self.assertIn("agent: a1", AG.resume_replay_hist(sdir, "src"))
        # _retry_plan uses it: bogus sid + resume_missing error -> hist without trailing user
        rs = AG._retry_plan(sdir, "src", "echo", "old-sid", 1, "session not found", False, False, None, None, 0.0, 0, None)
        self.assertTrue(rs and rs["resumed"] and "user: q1" in rs["hist"] and "q2" not in rs["hist"])

    def test_ps_includes_lineage(self):
        td, sdir = fresh(); self.tds.append(td)
        fork(sdir)
        # no live turns -> ps empty, but row-level lineage present in roster for ps join
        rows = AG.agents_ps(sdir, table={})
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
