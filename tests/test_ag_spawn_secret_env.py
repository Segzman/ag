"""Secret-looking env reaches a spawned session's process but is never written to its status.json."""
import json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")

class SpawnSecretEnv(unittest.TestCase):
    def test_secret_env_inherited_not_persisted(self):
        td = Path(tempfile.mkdtemp()); sd = td / ".agent"
        env = dict(os.environ, AG_AUTO_UPDATE="0", AG_SECRET_POPUP="0", AG_HOME=str(td))
        secret = "s3cr3t-" + "val-91a7"
        r = subprocess.run([sys.executable, AG, "--dir", str(sd), "--json", "spawn",
            "--env", f"MY_API_TOKEN={secret}", "--env", "PLAIN=ok", "--",
            "sh", "-c", 'echo "tok=$MY_API_TOKEN plain=$PLAIN"'], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        sid = json.loads(r.stdout)["data"]["id"]
        out = ""
        for _ in range(50):
            out = subprocess.run([sys.executable, AG, "--dir", str(sd), "snap", sid, "--clean"],
                capture_output=True, text=True, env=env).stdout
            if "tok=" in out: break
            time.sleep(0.1)
        self.assertIn(f"tok={secret} plain=ok", out)  # the program got the real value
        for p in sd.rglob("*.json"):
            self.assertNotIn(secret, p.read_text(errors="replace"), p)  # never at rest in state json
        st = json.loads((sd / "sessions" / sid / "status.json").read_text())
        self.assertEqual(st["env"]["MY_API_TOKEN"], "<redacted>")
        self.assertEqual(st["env"]["PLAIN"], "ok")

if __name__ == "__main__":
    unittest.main()
