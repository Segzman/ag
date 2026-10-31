#!/usr/bin/env python3
"""ag channel: shared room, @mention wakes, replies posted back, hand-off chain + hop limit.
Fake spawn (no fork) + in-process wake worker over the keyless echo backend. All offline."""
import json, os, subprocess, sys, unittest
from tests.test_ag_verify import load, run, worker


def queued(m, sd):
    out = []
    for p in sorted(m._wake_dirs(sd)[1].glob("*.json")):
        j = json.loads(p.read_text())
        if j.get("status") == "queued": out.append(j)
    return out

def drain(m, sd, limit=50):
    """Run queued wake workers until none left; returns agents in run order."""
    order = []
    for _ in range(limit):
        q = queued(m, sd)
        if not q: break
        worker(m, sd, q[0]["id"]); order.append(q[0]["agent"])
    return order

def mk(*members):
    m, td, sd = load()
    rc, d, _, _ = run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "create", "room", "--agents", ",".join(members))
    assert rc == 0, d
    return m, sd

def post(m, sd, *argv):
    return run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "post", "room", *argv)

def log(m, sd):
    return run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "log", "room")[1]["data"]["messages"]


class TChannelCrud(unittest.TestCase):
    def test_create_list_rm(self):
        m, sd = mk("a", "b")
        rc, d, _, _ = run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "list")
        self.assertEqual(d["data"]["channels"], [{"name": "room", "members": ["a", "b"], "messages": 0}])
        self.assertEqual(run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "create", "room")[1]["ok"], False)
        self.assertFalse(run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "create", "Bad_Name")[1]["ok"])
        self.assertFalse(run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "create", "x", "--agents", "nope")[1]["ok"])
        self.assertTrue(run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "rm", "room")[1]["ok"])
        self.assertEqual(run(m, m.do_channel, "--dir", str(sd), "--json", "channel", "list")[1]["data"]["channels"], [])
        self.assertFalse(post(m, sd, "hi")[1]["ok"])

    def test_post_appends_and_log(self):
        m, sd = mk("a", "b")
        rc, d, _, _ = post(m, sd, "hello", "world")
        self.assertEqual((d["data"]["from"], d["data"]["woke"]), ("user", []))
        post(m, sd, "second", "--as", "a")
        rows = log(m, sd)
        self.assertEqual([(r["from"], r["text"]) for r in rows], [("user", "hello world"), ("a", "second")])
        self.assertTrue(all("ts" in r for r in rows))
        _, d, txt, _ = run(m, m.do_channel, "--dir", str(sd), "channel", "log", "room", "--tail", "1")
        self.assertIn("a: second", txt); self.assertNotIn("hello", txt)

    def test_default_sender_is_parent_agent(self):
        m, sd = mk("a")
        os.environ["AG_PARENT_AGENT"] = "a"
        try: post(m, sd, "from env")
        finally: os.environ.pop("AG_PARENT_AGENT")
        self.assertEqual(log(m, sd)[0]["from"], "a")
        self.assertFalse(post(m, sd, "x", "--as", "ghost")[1]["ok"])


class TChannelWake(unittest.TestCase):
    def test_mention_wakes_with_context_and_reply_posts_back(self):
        m, sd = mk("a", "b")
        post(m, sd, "earlier chatter")
        _, d, _, _ = post(m, sd, "@a please look")
        self.assertEqual([w["agent"] for w in d["data"]["woke"]], ["a"])
        j = queued(m, sd)[0]
        self.assertEqual(j["agent"], "a"); self.assertEqual(j["channel"]["name"], "room")
        for s in ("[channel room]", "earlier chatter", "@a please look", "posted back", "channel post room"):
            self.assertIn(s, j["message"])
        worker(m, sd, j["id"])
        last = log(m, sd)[-1]
        self.assertEqual(last["from"], "a"); self.assertIn("@a please look", last["text"])  # echo replies with its prompt
        self.assertEqual(queued(m, sd), [])  # self-mention in the reply wakes nobody

    def test_context_limit(self):
        m, sd = mk("a")
        for i in range(5): post(m, sd, f"msg{i}")
        post(m, sd, "@a go", "--context", "2")
        msg = queued(m, sd)[0]["message"]
        self.assertIn("msg4", msg); self.assertNotIn("msg3", msg)

    def test_self_mention_and_unknown(self):
        m, sd = mk("a", "b")
        _, d, _, _ = post(m, sd, "@a me @ghost @b", "--as", "a")
        self.assertEqual([w["agent"] for w in d["data"]["woke"]], ["b"])
        self.assertEqual(d["data"]["ignored"], ["ghost"])
        self.assertEqual([j["agent"] for j in queued(m, sd)], ["b"])

    def test_handoff_chain(self):
        m, sd = mk("a", "b", "boss")
        post(m, sd, "@a ask @b", "--max-hops", "2")
        order = drain(m, sd)
        # gen 1: a,b woken by the user; their echo replies hand off to each other (gen 2); budget then spent
        self.assertEqual(sorted(order), ["a", "a", "b", "b"])  # drain order follows job-id hashes
        froms = [r["from"] for r in log(m, sd)]
        self.assertEqual((froms[0], sorted(froms[1:])), ("user", ["a", "a", "b", "b"]))

    def test_hop_limit_stops_loop(self):
        m, sd = mk("a", "b")
        _, d, _, _ = post(m, sd, "@a @b ping", "--max-hops", "3")  # echoes keep re-mentioning both: would loop forever
        order = drain(m, sd)
        self.assertTrue(0 < len(order) <= 2 + 4 + 8)  # depth-bounded: <=3 generations of <=2 wakes
        self.assertEqual(queued(m, sd), [])
        # chain depth never exceeds max-hops: every wake job's budget stayed >= 0
        for p in m._wake_dirs(sd)[1].glob("*.json"):
            self.assertGreaterEqual(json.loads(p.read_text())["channel"]["left"], 0)

    def test_zero_hops_wakes_nothing(self):
        m, sd = mk("a")
        _, d, _, _ = post(m, sd, "@a hi", "--max-hops", "0")
        self.assertEqual(d["data"]["woke"], []); self.assertEqual(d["data"]["skipped"][0]["reason"], "hop limit")

    def test_failed_wake_posts_note(self):
        m, sd = mk("a")
        post(m, sd, "@a go")
        j = queued(m, sd)[0]
        m._wake_finish(sd, j["id"], "failed", error="boom", exit_code=1)
        last = log(m, sd)[-1]
        self.assertEqual(last["from"], "a"); self.assertIn("boom", last["text"])


class TChannelCli(unittest.TestCase):
    def test_subprocess_json(self):
        import tempfile
        from pathlib import Path
        sd = tempfile.mkdtemp(prefix="agch-")
        env = dict(os.environ, AG_AUTO_UPDATE="0")
        env.pop("AG_PARENT_AGENT", None)
        ag = str(Path(__file__).resolve().parent.parent / "ag")
        def go(*a): return json.loads(subprocess.run([sys.executable, ag, "--dir", sd, "--json", *a], capture_output=True, text=True, env=env).stdout)
        go("agents", "add", "x", "--backend", "echo")
        self.assertTrue(go("channel", "create", "r", "--agents", "x")["ok"])
        self.assertEqual(go("channel", "post", "r", "hello")["data"]["from"], "user")
        self.assertEqual(go("channel", "log", "r")["data"]["messages"][0]["text"], "hello")


if __name__ == "__main__":
    unittest.main()
