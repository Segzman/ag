"""wake --escalate: worker ladder, CLI, receipts (acceptance tests from bench v3)."""
import unittest
from tests.test_ag_verify import load, run, worker


def pass_on(td, n):
    c = td / "n"
    return f'k=$(( $(cat {c} 2>/dev/null || echo 0) + 1 )); echo $k > {c}; [ $k -ge {n} ] && exit 0; echo FAILMARK$k; exit 1'


class WakeEscalate(unittest.TestCase):
    def test_cli_records_ladder(self):
        m, td, sd = load()
        rc, d, _, _ = run(m, m.do_wake, "--dir", str(sd), "--json", "wake", "a", "do it", "--verify", "true", "--escalate", "m2,m3")
        self.assertEqual(rc, 0)
        self.assertEqual(m._wake_read_job(sd, d["data"]["job"])["escalate"], ["m2", "m3"])

    def test_cli_requires_verify(self):
        m, td, sd = load()
        rc, d, out, err = run(m, m.do_wake, "--dir", str(sd), "wake", "a", "go", "--escalate", "m2")
        self.assertNotEqual(rc, 0)
        self.assertIn("--escalate needs --verify", out + err)

    def test_worker_escalates_until_pass(self):
        m, td, sd = load()
        r = m.launch_wake_chat(sd, "a", "fix the thing", verify=pass_on(td, 3), escalate=["m2", "m3"])
        self.assertEqual(worker(m, sd, r["job"]), 0)
        j = m._wake_read_job(sd, r["job"])
        self.assertEqual(j["status"], "done")
        self.assertEqual([x["model"] for x in j["escalation"]], ["default", "m2", "m3"])
        self.assertEqual([x["verify_exit"] for x in j["escalation"]], [1, 1, 0])
        self.assertEqual(j["verify"]["exit"], 0)
        self.assertIn("fix the thing", j["reply"])
        log = "\n".join(x["text"] for x in m.load_chat_log(sd, "a"))
        self.assertIn("FAILMARK2", log)  # the m3 attempt was told why m2 failed
        self.assertIn("[escalate] -> m2", log); self.assertIn("[escalate] -> m3", log)

    def test_worker_all_fail(self):
        m, td, sd = load()
        r = m.launch_wake_chat(sd, "a", "x", verify="echo nope; exit 2", escalate=["m2"])
        worker(m, sd, r["job"])
        j = m._wake_read_job(sd, r["job"])
        self.assertEqual([(x["model"], x["verify_exit"]) for x in j["escalation"]], [("default", 2), ("m2", 2)])
        self.assertEqual(j["verify"]["exit"], 2)

    def test_stops_at_first_pass_and_roster_unchanged(self):
        m, td, sd = load()
        r = m.launch_wake_chat(sd, "a", "x", verify="true", escalate=["m2"])
        worker(m, sd, r["job"])
        j = m._wake_read_job(sd, r["job"])
        self.assertEqual([x["model"] for x in j["escalation"]], ["default"])
        self.assertEqual(next(x for x in m.load_agents(sd) if x["name"] == "a")["model"], "default")

    def test_no_escalate_no_key(self):
        m, td, sd = load()
        r = m.launch_wake_chat(sd, "a", "x", verify="true")
        worker(m, sd, r["job"])
        self.assertNotIn("escalation", m._wake_read_job(sd, r["job"]))


if __name__ == "__main__":
    unittest.main()
