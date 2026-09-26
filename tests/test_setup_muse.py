#!/usr/bin/env python3
"""Offline acceptance for scripts/setup-muse.sh; fake curl, no network."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts' / 'setup-muse.sh'

FAKE_CURL = """#!/bin/sh
mode="${SETUP_TEST_CURL_MODE:-ok}"
if [ "$mode" = "fail" ]; then echo "fake curl: download failed" >&2; exit 22; fi
if [ "${SETUP_TEST_CURL_FORBID:-0}" = "1" ]; then echo "fake curl: must not be called" >&2; exit 99; fi
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
if [ -z "$out" ]; then echo "fake curl: no -o output" >&2; exit 2; fi
cat > "$out" <<'FAKE_INSTALLER_EOF'
#!/bin/sh
if [ "${1:-}" != "--no-modify-path" ]; then echo "fake installer: missing --no-modify-path" >&2; exit 3; fi
if [ -n "${SETUP_TEST_MARKER_ARGS:-}" ]; then printf '%s\\n' "$@" > "$SETUP_TEST_MARKER_ARGS"; fi
mkdir -p "$HOME/.opencode/bin"
cat > "$HOME/.opencode/bin/opencode" <<'FAKE_BIN_EOF'
#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "opencode-test 0.0.0"; exit 0; fi
echo "opencode-test"; exit 0
FAKE_BIN_EOF
chmod 755 "$HOME/.opencode/bin/opencode"
exit 0
FAKE_INSTALLER_EOF
exit 0
"""

FAKE_OPENCODE = """#!/bin/sh
echo "opencode-test 0.0.0"
exit 0
"""


class SetupMuse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.fakebin = self.root / 'fakebin'
        self.fakebin.mkdir()
        (self.fakebin / 'curl').write_text(FAKE_CURL)
        (self.fakebin / 'curl').chmod(0o755)
        self.rc_files = []
        for rc in ('.bashrc', '.zshrc', '.profile'):
            p = self.home / rc
            p.write_text(f'# sentinel {rc}\n')
            self.rc_files.append(p)

    def run_script(self, *args, curl_mode='ok', forbid_curl=False, extra_env=None):
        env = dict(os.environ)
        env['HOME'] = str(self.home)
        # Isolated PATH: fakebin + interpreter dir + base system dirs only,
        # so real backends (e.g. /opt/homebrew/bin/opencode) never leak in.
        env['PATH'] = os.pathsep.join([
            str(self.fakebin),
            os.path.dirname(sys.executable),
            '/usr/bin', '/bin', '/usr/sbin', '/sbin',
        ])
        env['SETUP_TEST_CURL_MODE'] = curl_mode
        env['SETUP_TEST_CURL_FORBID'] = '1' if forbid_curl else '0'
        env['OPENCODE_INSTALL_URL'] = 'http://127.0.0.1:9/fake-installer'
        env['TMPDIR'] = str(self.root / 'tmp')
        (self.root / 'tmp').mkdir(exist_ok=True)
        if extra_env:
            env.update(extra_env)
        p = subprocess.run(['sh', str(SCRIPT), *args], text=True,
                           capture_output=True, env=env, timeout=180)
        return p

    def assert_rc_untouched(self):
        for p in self.rc_files:
            self.assertTrue(p.is_file(), p)
            self.assertEqual(p.read_text(), f'# sentinel {p.name}\n', p)

    def test_prefix_with_spaces_ag_only(self):
        prefix = self.root / 'prefix with spaces'
        p = self.run_script('--prefix', str(prefix), '--skip-opencode', forbid_curl=True)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        ag_bin = prefix / 'bin' / 'ag'
        self.assertTrue(ag_bin.is_file(), p.stderr + p.stdout)
        self.assertTrue(bool(ag_bin.stat().st_mode & stat.S_IXUSR))
        v = subprocess.run([str(ag_bin), '--version'], text=True,
                           capture_output=True, timeout=60)
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assert_rc_untouched()

    def test_opencode_passes_no_modify_path_and_binary_runnable(self):
        marker = self.root / 'installer-args'
        p = self.run_script('--force', extra_env={'SETUP_TEST_MARKER_ARGS': str(marker)})
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertTrue(marker.is_file(), p.stderr + p.stdout)
        self.assertIn('--no-modify-path', marker.read_text())
        opencode = self.home / '.opencode' / 'bin' / 'opencode'
        self.assertTrue(opencode.is_file(), p.stderr + p.stdout)
        self.assertTrue(bool(opencode.stat().st_mode & stat.S_IXUSR))
        v = subprocess.run([str(opencode), '--version'], text=True,
                           capture_output=True, timeout=60)
        self.assertEqual(v.returncode, 0)
        self.assertIn('opencode-test', v.stdout)
        self.assert_rc_untouched()

    def test_curl_failure_is_fatal(self):
        p = self.run_script(curl_mode='fail')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('failed to download', p.stderr)
        self.assertFalse((self.home / '.opencode' / 'bin' / 'opencode').exists())

    def test_existing_opencode_uses_no_network(self):
        (self.fakebin / 'opencode').write_text(FAKE_OPENCODE)
        (self.fakebin / 'opencode').chmod(0o755)
        p = self.run_script('--force', '--skip-opencode', forbid_curl=True)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        p = self.run_script('--force', forbid_curl=True)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertIn('already installed', p.stdout)


if __name__ == '__main__':
    unittest.main()
