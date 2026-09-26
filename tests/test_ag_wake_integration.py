#!/usr/bin/env python3
"""Cross-track wake races and process cleanup; no model calls."""
import importlib.machinery
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time

AG = Path(__file__).resolve().parents[1] / 'ag'

def module():
    return importlib.machinery.SourceFileLoader('ag_wake_integration', str(AG)).load_module()

def test_exited_parent_descendant_timeout():
    """A successful parent exit must not disable a descendant pipe deadline."""
    with tempfile.TemporaryDirectory(prefix='ag-wake-orphan-') as tmp:
        td = Path(tmp)
        binary = td / 'printf'
        pidfile = td / 'child.pid'
        binary.write_text('#!' + sys.executable + '\n' +
            'import os, signal, subprocess, sys\n' +
            'child = subprocess.Popen([sys.executable, "-c", '
            '"import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])\n' +
            f'open({str(pidfile)!r}, "w").write(str(child.pid))\n' +
            'os._exit(0)\n')
        binary.chmod(0o755)
        state = td / 'state'
        subprocess.run([sys.executable, str(AG), '--dir', str(state),
            'agents', 'add', 'orphan', '--backend', 'echo'], check=True, capture_output=True)
        try:
            result = subprocess.run([sys.executable, str(AG), '--dir', str(state),
                '--json', 'chat', 'send', 'orphan', 'hello', '--timeout', '0.5'],
                env=dict(os.environ, PATH=str(td) + os.pathsep + os.environ.get('PATH', '')),
                text=True, capture_output=True, timeout=10)
            assert '"timed_out": true' in result.stdout, (result.stdout, result.stderr)
        finally:
            if pidfile.exists():
                try: os.kill(int(pidfile.read_text()), signal.SIGKILL)
                except ProcessLookupError: pass


def test_backend_ownership_is_thread_local():
    """Finishing another TUI agent must not erase this turn's cleanup handle."""
    m = module()
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
        start_new_session=True)
    try:
        m._backend_note(child.pid)
        def other_turn():
            m._backend_note(99999999)
            m._backend_clear()
        t = threading.Thread(target=other_turn)
        t.start(); t.join()
        m._kill_active_backend(grace_s=0.05)
        child.wait(timeout=3)
    finally:
        m._backend_clear()
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def test_start_callback_aborts_without_backend():
    m = module()
    with tempfile.TemporaryDirectory(prefix='ag-wake-start-') as tmp:
        state = Path(tmp)
        subprocess.run([sys.executable, str(AG), '--dir', str(state),
            'agents', 'add', 'stopped', '--backend', 'echo'], check=True, capture_output=True)
        called = []
        original = m.subprocess.Popen
        def forbidden(*args, **kwargs):
            called.append(True)
            raise AssertionError('cancelled callback still launched backend')
        def stop():
            raise RuntimeError('wake already cancelled')
        m.subprocess.Popen = forbidden
        try:
            try: result = m.run_turn(state, 'stopped', 'hello', on_start=stop)
            except RuntimeError: pass
            else: assert 'error' in result, result
            assert not called
            assert not m.agent_busy(state, 'stopped')
        finally:
            m.subprocess.Popen = original

if __name__ == '__main__':
    for test in (test_exited_parent_descendant_timeout,
                 test_backend_ownership_is_thread_local,
                 test_start_callback_aborts_without_backend):
        test()
        print('ok', test.__name__)
