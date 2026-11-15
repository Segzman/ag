#!/usr/bin/env python3
"""ag browser: WebSocket frame/handshake units, then open/click/upload/snap against a local page (skipped without Chrome).

Stdlib, temp dirs, localhost only. The integration case drives the real ag CLI and a real headless Chrome.
"""
import functools, http.server, importlib.machinery, importlib.util, json, os, shutil, socket, subprocess, sys, tempfile, threading, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod_browser", AG); _s = importlib.util.spec_from_loader("ag_mod_browser", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)

PAGE = """<!doctype html><title>smoke</title>
<button id="go" onclick="document.getElementById('out').textContent='clicked'">Go</button>
<button id="att">Attach</button>
<input type="file" id="f" style="display:none" onchange="document.getElementById('out').textContent='file:'+this.files[0].name">
<script>document.getElementById('att').onclick = () => document.getElementById('f').click();</script>
<select id="s"><option>one</option><option>two</option></select>
<input id="t"><div id="out"></div>"""


def srv_frame(fin, op, data):  # server frame: unmasked, payload < 126 bytes
    return bytes([(0x80 if fin else 0) | op, len(data)]) + data


class WsFrames(unittest.TestCase):
    def test_accept_key_rfc6455_example(self):
        self.assertEqual(ag.ws_accept("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masked_frame_roundtrip_all_length_forms(self):
        for n in (0, 5, 125, 126, 300, 65535, 65536, 70000):
            data = os.urandom(n)
            f = ag.ws_frame(data)
            self.assertTrue(f[1] & 0x80)  # client frames are masked
            self.assertEqual(ag.ws_parse(bytearray(f)), (True, 1, data, len(f)))

    def test_masked_payload_is_not_plaintext_on_the_wire(self):
        self.assertNotIn(b"secret-text", ag.ws_frame("secret-text"))

    def test_partial_frame_is_incomplete(self):
        f = ag.ws_frame("hello world" * 20)
        for cut in (1, 3, 10, len(f) - 1): self.assertIsNone(ag.ws_parse(bytearray(f[:cut])))

    def test_two_unmasked_frames_parse_in_order(self):
        buf = bytearray(srv_frame(True, 1, b"hi") + srv_frame(False, 0, b"you"))
        fin, op, payload, used = ag.ws_parse(buf)
        self.assertEqual((fin, op, payload), (True, 1, b"hi")); del buf[:used]
        self.assertEqual(ag.ws_parse(buf), (False, 0, b"you", len(buf)))


class WsReassembly(unittest.TestCase):
    def test_fragments_with_interleaved_ping_reassemble_and_pong(self):
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); srv.settimeout(10)
        got = {}

        def serve():
            conn, _ = srv.accept()
            req = b""
            while b"\r\n\r\n" not in req: req += conn.recv(1024)
            key = next(l.split(":", 1)[1].strip() for l in req.decode().split("\r\n") if l.lower().startswith("sec-websocket-key"))
            conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         f"Sec-WebSocket-Accept: {ag.ws_accept(key)}\r\n\r\n".encode())
            conn.sendall(srv_frame(False, 1, b"hel") + srv_frame(True, 9, b"p") + srv_frame(True, 0, b"lo"))
            got["pong"] = conn.recv(1024)
            conn.close()

        t = threading.Thread(target=serve, daemon=True); t.start()
        ws = ag.WS(f"ws://127.0.0.1:{srv.getsockname()[1]}/devtools/page/x")
        self.assertEqual(ws.recv(), "hello")
        ws.close(); t.join(10); srv.close()
        fin, op, payload, _ = ag.ws_parse(bytearray(got["pong"]))
        self.assertEqual((op, payload), (0xA, b"p"))


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


@unittest.skipUnless(ag._br_chrome(), "Google Chrome not installed")
class BrowserIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = Path(tempfile.mkdtemp(prefix="ag-browser-t-"))
        cls.site = cls.td / "site"; cls.site.mkdir()
        (cls.site / "index.html").write_text(PAGE); (cls.site / "up.txt").write_text("hello upload\n"); (cls.site / "up2.txt").write_text("second\n")
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(cls.site)))
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/index.html"
        cls.state = cls.td / "state"
        cls.env = dict(os.environ, AG_CACHE_HOME=str(cls.td / "cache"), AG_AUTO_UPDATE="0")

    @classmethod
    def tearDownClass(cls):
        cls.raw("--json", "browser", "close", "t1")  # no Chrome left behind if a case failed midway
        cls.httpd.shutdown(); shutil.rmtree(cls.td, True)

    @classmethod
    def raw(cls, *args):
        return subprocess.run([sys.executable, AG, "--dir", str(cls.state), *args], capture_output=True, text=True, env=cls.env, timeout=120)

    @classmethod
    def br(cls, *args, ok=True):
        p = cls.raw("--json", "browser", *args)
        d = json.loads(p.stdout) if p.stdout.strip() else {}
        if ok: self_check(p, d)
        return p, d

    def test_open_click_type_select_upload_snap_list_close(self):
        p, d = self.br("open", "t1", self.url)
        self.assertTrue(d["data"]["url"].endswith("/index.html"), p.stdout)
        p, d = self.br("click", "t1", "--role", "button", "--name", "Go")
        self.assertEqual(self.br("eval", "t1", "document.getElementById('out').textContent")[1]["data"]["value"], "clicked")
        self.br("click", "t1", "--css", "#t"); self.br("type", "t1", "hi there")
        self.assertEqual(self.br("eval", "t1", "document.getElementById('t').value")[1]["data"]["value"], "hi there")
        self.assertEqual(self.br("eval", "t1", "document.getElementById('s').value = 'two'; document.getElementById('s').value")[1]["data"]["value"], "two")
        p, d = self.br("upload", "t1", "--css", "#f", str(self.site / "up.txt"))
        self.assertEqual(d["data"]["uploaded"], "up.txt")
        self.assertEqual(self.br("eval", "t1", "document.getElementById('out').textContent")[1]["data"]["value"], "file:up.txt")
        self.br("eval", "t1", "document.getElementById('out').textContent = ''")
        self.br("upload", "t1", "--css", "#att", str(self.site / "up2.txt"))  # button opens a native chooser: intercepted
        self.assertEqual(self.br("eval", "t1", "document.getElementById('out').textContent")[1]["data"]["value"], "file:up2.txt")
        png = self.td / "shot.png"
        p, d = self.br("snap", "t1", "--png", str(png))
        self.assertEqual(png.read_bytes()[:4], b"\x89PNG")
        self.assertNotIn("cookie", p.stdout.lower())
        p = self.raw("browser", "snap", "t1", "--text")
        self.assertIn("Go", p.stdout)
        self.assertEqual(self.br("list")[1]["data"]["t1"]["alive"], True)
        self.assertEqual(self.br("close", "t1")[1]["data"], {"closed": "t1"})
        self.assertEqual(self.br("list")[1]["data"], {})

    def test_errors_are_reported_not_raised(self):
        self.assertEqual(self.br("list", ok=False)[0].returncode, 0)
        p, d = self.br("snap", "nobody", ok=False)
        self.assertEqual((p.returncode, d["ok"]), (1, False))
        p, d = self.br("snap", "bad/name", ok=False)
        self.assertFalse(d["ok"])


def self_check(p, d):
    if p.returncode != 0 or not d.get("ok"): raise AssertionError(p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
