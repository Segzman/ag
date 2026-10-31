#!/usr/bin/env python3
"""agents create: one sentence -> proposal -> (--yes) agents add. In-process, offline.
Run: python3 tests/test_ag_agents_create.py"""
import importlib.machinery, io, json, os, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

def _load_src(loader):  # SourceFileLoader.load_module was removed in Python 3.15
    import importlib.util, sys
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

AG = _load_src(importlib.machinery.SourceFileLoader("ag_create_t", str(Path(__file__).resolve().parent.parent / "ag")))
DESC = "a careful reviewer for the payments service that never edits files"


class A:
    def __init__(self, **k): self.__dict__.update(k)


def run(sdir, desc=DESC, proposal=None, **k):
    """-> (rc, parsed --json stdout)."""
    a = A(dir=str(sdir), json=True, sub="create", description=desc, name=k.get("name"), adir=".", using=k.get("using"),
          yes=k.get("yes", False), dry_run=k.get("dry_run", False))
    old = os.environ.pop("AG_CREATE_PROPOSAL_JSON", None)
    if proposal is not None: os.environ["AG_CREATE_PROPOSAL_JSON"] = proposal
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(io.StringIO()): rc = AG.do_agents(a)
    finally:
        os.environ.pop("AG_CREATE_PROPOSAL_JSON", None)
        if old is not None: os.environ["AG_CREATE_PROPOSAL_JSON"] = old
    return rc, json.loads(buf.getvalue())


def names(sdir): return [r["name"] for r in AG.load_agents(sdir)]


class T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(prefix="agcreate-")
        self.sdir = Path(self.td) / ".agent"
        AG.ensure_state(self.sdir)
        AG.save_agents(self.sdir, [{"name": "e", "backend": "echo", "model": "default", "dir": ".", "role": "orchestrator"}])

    def test_parse_messy_text(self):
        o = AG._first_json_obj('Sure! {not json} here you go:\n```json\n{"name": "rev", "job": "review", "system": "x {y}"}\n```\nthanks')
        self.assertEqual(o["name"], "rev")
        self.assertIsNone(AG._first_json_obj("no braces at all"))
        self.assertIsNone(AG._first_json_obj("[1, 2]"))

    def test_clamp(self):
        p, _ = AG.create_clamp({"job": "wizard", "mode": "full", "name": "Bad Name!!", "system": "hi"}, DESC, set())
        self.assertEqual((p["job"], p["mode"], p["name"]), ("implement", "auto", "Bad-Name"))
        self.assertEqual(p["system"], DESC)  # too-short instructions fall back to the description
        p, _ = AG.create_clamp({"mode": "full"}, "give it full access to the repo", set())
        self.assertEqual(p["mode"], "full")
        p, _ = AG.create_clamp({"mode": "bogus", "system": "s" * 5000, "worktree": "yes"}, DESC, set())
        self.assertEqual((p["mode"], len(p["system"]), p["worktree"]), ("ro", 1200, False))
        p, _ = AG.create_clamp({"name": "rev"}, DESC, {"rev", "rev-2"})
        self.assertEqual(p["name"], "rev-3")
        _, err = AG.create_clamp({"name": "x"}, DESC, {"mine"}, want="mine")
        self.assertIn("exists", err)
        _, err = AG.create_clamp({}, DESC, set(), want="!!!")
        self.assertIn("bad --name", err)

    def test_no_yes_creates_nothing_prints_command(self):
        rc, out = run(self.sdir, proposal='{"name": "payrev", "job": "review", "mode": "ro", "system": "Review payments code. Never edit."}')
        self.assertEqual(rc, 0)
        d = out["data"]
        self.assertFalse(d["created"])
        self.assertIn("agents add payrev --job review --mode ro", d["command"])
        self.assertEqual(names(self.sdir), ["e"])

    def test_dry_run_with_yes_creates_nothing(self):
        rc, out = run(self.sdir, proposal='{"name": "payrev", "job": "review"}', yes=True, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertFalse(out["data"]["created"])
        self.assertEqual(names(self.sdir), ["e"])

    def test_yes_creates_routed_agent(self):
        rc, out = run(self.sdir, proposal='noise {"name": "payrev", "job": "review", "mode": "ro", "system": "Review payments code. Never edit."} noise', yes=True)
        self.assertEqual(rc, 0, out)
        self.assertTrue(out["data"]["created"])
        row = next(r for r in AG.load_agents(self.sdir) if r["name"] == "payrev")
        ra = AG.routing_now()[0]["review"]["ag"]
        self.assertEqual(row["backend"], ra["backend"])
        self.assertEqual(row["model"], ra["model"] if ra["model"] not in ("", "default") else row["model"])
        self.assertEqual(row["mode"], "ro")
        self.assertEqual(row["system"], "Review payments code. Never edit.")
        self.assertEqual(out["data"]["proposal"]["backend"], ra["backend"])

    def test_yes_name_collision_errors(self):
        rc, out = run(self.sdir, proposal='{"name": "e"}', name="e", yes=True)
        self.assertEqual(rc, 1)
        self.assertIn("exists", out["error"])

    def test_echo_end_to_end(self):
        # echo replies with the prompt, which embeds the JSON schema; "..."-ish placeholders must clamp, not crash
        rc, out = run(self.sdir, using="e")
        self.assertEqual(rc, 0, out)
        p = out["data"]["proposal"]
        self.assertIn(p["job"], AG.JOB_KEYS)
        self.assertIn(p["mode"], AG.MODES)
        self.assertEqual(names(self.sdir), ["e"])
        rc, out = run(self.sdir, using="e", yes=True)
        self.assertEqual((rc, out["data"]["created"]), (0, True))
        self.assertEqual(len(names(self.sdir)), 2)

    def test_unknown_using(self):
        rc, out = run(self.sdir, using="nope")
        self.assertEqual(rc, 1)
        self.assertIn("no such agent", out["error"])


if __name__ == "__main__":
    unittest.main()
