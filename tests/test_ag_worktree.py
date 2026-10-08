#!/usr/bin/env python3
"""Per-agent git worktrees (`agents add --worktree`). Temp repo + temp state only.
Run: python3 tests/test_ag_worktree.py
"""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag"))
assert os.path.exists(AG), f"ag missing: {AG}"


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, check=True).stdout.strip()


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name).resolve()
        self.repo, self.state = root / "proj", root / "state"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.txt").write_text("a\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "init")

    def tearDown(self):
        self.td.cleanup()

    def ag(self, *args):
        env = dict(os.environ, AGENT_CLI_DIR=str(self.state))
        p = subprocess.run([sys.executable, AG, "--json", *args], capture_output=True, text=True, env=env, timeout=60)
        return p.returncode, json.loads(p.stdout)

    def add(self, name, *extra):
        return self.ag("agents", "add", name, "--backend", "echo", "--dir", str(self.repo), "--worktree", *extra)

    def agents(self):
        return {r["name"]: r for r in self.ag("agents", "list")[1]["data"]["agents"]}

    def test_two_agents_isolated(self):
        for n in ("w1", "w2"):
            rc, o = self.add(n)
            self.assertEqual(rc, 0, o)
            self.assertIn(f"ag/{n}", o["data"]["_text"])
        a = self.agents()
        p1, p2 = Path(a["w1"]["dir"]), Path(a["w2"]["dir"])
        self.assertNotEqual(p1, p2)
        self.assertEqual(p1, self.repo.parent / "proj.ag-wt" / "w1")
        self.assertEqual(a["w2"]["worktree"]["branch"], "ag/w2")
        self.assertEqual(git(p1, "branch", "--show-current"), "ag/w1")
        (p1 / "new.txt").write_text("x")
        self.assertFalse((p2 / "new.txt").exists())
        self.assertFalse((self.repo / "new.txt").exists())
        self.assertEqual(git(p2, "status", "--porcelain"), "")
        txt = subprocess.run([sys.executable, AG, "agents", "list"], capture_output=True, text=True,
                             env=dict(os.environ, AGENT_CLI_DIR=str(self.state))).stdout
        self.assertIn("[ag/w1]", txt)

    def test_rm_clean_keeps_branch(self):
        self.add("c1")
        path = Path(self.agents()["c1"]["dir"])
        rc, o = self.ag("agents", "rm", "c1")
        self.assertEqual(rc, 0, o)
        self.assertFalse(path.exists())
        self.assertIn("ag/c1", git(self.repo, "branch", "--list", "ag/c1"))
        self.assertNotIn("c1", self.agents())

    def test_rm_dirty_refused_then_force(self):
        self.add("d1")
        path = Path(self.agents()["d1"]["dir"])
        (path / "wip.txt").write_text("x")
        rc, o = self.ag("agents", "rm", "d1")
        self.assertNotEqual(rc, 0)
        self.assertIn("uncommitted", o["error"])
        self.assertIn("--force", o["hint"])
        self.assertTrue(path.exists())
        self.assertIn("d1", self.agents())
        rc, o = self.ag("agents", "rm", "d1", "--force")
        self.assertEqual(rc, 0, o)
        self.assertFalse(path.exists())
        self.assertIn("ag/d1", git(self.repo, "branch", "--list", "ag/d1"))

    def test_reuse_conflict_and_set(self):
        self.add("r1")
        self.ag("agents", "rm", "r1", "--force")
        rc, o = self.add("r1")  # branch kept, worktree gone, not same worktree -> error with hint
        self.assertNotEqual(rc, 0)
        self.assertTrue(o.get("hint"))
        self.ag("agents", "add", "s1", "--backend", "echo", "--dir", str(self.repo))
        rc, o = self.ag("agents", "set", "s1", "--worktree", "--dir", str(self.repo))
        self.assertEqual(rc, 0, o)
        self.assertEqual(self.agents()["s1"]["worktree"]["branch"], "ag/s1")
        rc, o = self.ag("agents", "add", "nr", "--backend", "echo", "--dir", self.td.name, "--worktree")
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
