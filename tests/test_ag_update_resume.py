#!/usr/bin/env python3
"""ag update download: chunked fetch resumes a dropped transfer with Range; 200-ignores-Range restarts clean.

Local http.server drops the connection after DROP_AFTER bytes on the first DROPS requests. Stdlib only."""
import os, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from tests.test_ag_relay import ag

SHA = "0123456789abcdef0123456789abcdef01234567"
BODY = ("".join(f"# line {i:05d} " + "x" * 80 + "\n" for i in range(4000)) + "print('ag ok')\n").encode()
DROP_AFTER = 100_000


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        srv = self.server
        rng = self.headers.get("Range")
        srv.ranges.append(rng)
        start = 0
        if rng and srv.honour_range:
            start = int(rng.split("=", 1)[1].rstrip("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")
        else:
            self.send_response(200)
        data = BODY[start:]
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if srv.drops_left > 0:  # connection closes short: client sees IncompleteRead
            srv.drops_left -= 1
            self.wfile.write(data[:srv.drop_after])
            return
        self.wfile.write(data)


class Flaky(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, drops, honour_range):
        super().__init__(("127.0.0.1", 0), Handler)
        self.drops_left, self.honour_range, self.ranges, self.drop_after = drops, honour_range, [], DROP_AFTER
        self.url = f"http://127.0.0.1:{self.server_address[1]}"
        threading.Thread(target=self.serve_forever, daemon=True).start()


class UpdateResume(unittest.TestCase):
    def serve(self, drops, honour_range):
        srv = Flaky(drops, honour_range)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        patcher = patch.object(ag.time, "sleep")  # skip backoff delays
        patcher.start(); self.addCleanup(patcher.stop)
        return srv

    def test_resume_with_range_is_byte_identical(self):
        srv = self.serve(drops=2, honour_range=True)
        got = ag._update_download(srv.url + "/ag")
        self.assertEqual(got, BODY)
        self.assertEqual(len(srv.ranges), 3)  # two drops + one clean finish
        self.assertIsNone(srv.ranges[0])
        offsets = [int(r.split("=", 1)[1].rstrip("-")) for r in srv.ranges[1:]]
        self.assertTrue(all(r.startswith("bytes=") for r in srv.ranges[1:]), srv.ranges)
        self.assertTrue(0 < offsets[0] < offsets[1] < len(BODY), offsets)

    def test_200_ignoring_range_restarts_clean(self):
        srv = self.serve(drops=2, honour_range=False)
        got = ag._update_download(srv.url + "/ag")
        self.assertEqual(got, BODY)  # no doubled prefix from the dropped attempts
        self.assertEqual(len(srv.ranges), 3)
        self.assertTrue(srv.ranges[1].startswith("bytes="))  # asked to resume, server said 200

    def test_gives_up_after_eight_attempts(self):
        srv = self.serve(drops=99, honour_range=True)
        srv.drop_after = 0  # every attempt makes no progress
        with self.assertRaises(OSError) as cm:
            ag._update_download(srv.url + "/ag")
        self.assertIn("after 8 attempts", str(cm.exception))
        self.assertEqual(len(srv.ranges), 8)

    def test_self_update_installs_resumed_download(self):
        srv = self.serve(drops=2, honour_range=True)
        td = Path(tempfile.mkdtemp(prefix="ag-update-resume-"))
        self.addCleanup(__import__("shutil").rmtree, td, True)
        src = td / "copy" / "ag"; src.parent.mkdir(); src.write_text("# old\n")
        env = dict(os.environ, AG_CACHE_HOME=str(td / "cache"))
        with patch.dict(os.environ, env), \
                patch.object(ag, "_update_remote_sha", return_value=SHA), \
                patch.object(ag, "UPDATE_RAW_PINNED", srv.url + "/{sha}/ag"):
            status, _ = ag.self_update(src)
            self.assertEqual(status, "updated")
            self.assertEqual(src.read_bytes(), BODY)
            self.assertEqual(ag._update_commit_read(), SHA)


if __name__ == "__main__":
    unittest.main()
