#!/usr/bin/env python3
"""Outbound-only transfer acceptance tests; fake SSH, echo backend, no network."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

AG = Path(__file__).resolve().parents[1] / 'ag'
spec = importlib.util.spec_from_loader('ag_multihost', loader=None)
ag = importlib.util.module_from_spec(spec)
ag.__file__ = str(AG)
exec(AG.read_text(), ag.__dict__)

class MultiHost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source state'
        self.dest = self.root / 'dest state'
        self.project = self.root / "project ' $(false)"
        self.local = self.root / 'local project'
        for p in (self.source, self.dest, self.project, self.local): p.mkdir()
        (self.source / 'agents.json').write_text(json.dumps([dict(name='worker', backend='echo', model='default', dir=str(self.project), role='sub', profile='private', sid='SECRET')]))
        self.fake = self.root / 'fake ssh'
        self.fake.write_text('#!/usr/bin/env python3\nimport os,subprocess,sys\nmode=os.environ.get("AG_FAKE_SSH", "")\nif mode == "offline": sys.exit(255)\nif mode == "partial": print("{\\\"ok\\\":true"); sys.exit(0)\nsys.exit(subprocess.call(["/bin/sh","-c",sys.argv[-1]]))\n')
        self.fake.chmod(0o755)
        self.cli(self.source, 'context', 'assign', 'worker', '--task', 'finish feature', '--acceptance', 'tests pass', '--brief', 'work brief')
        self.cli(self.source, 'context', 'checkpoint', 'worker', 'done parsing; next tests')
        self.cli(self.source, 'chat', 'send', 'worker', 'implement the missing transfer')

    def cli(self, state, *args, ok=True, env=None):
        p = subprocess.run([sys.executable, str(AG), '--dir', str(state), '--json', *args], text=True, capture_output=True, env=env)
        if ok: self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        else: self.assertNotEqual(p.returncode, 0, p.stdout)
        try: return json.loads(p.stdout)
        except ValueError: return {'error': p.stderr}

    def host(self):
        return self.cli(self.dest, 'hosts', 'add', 'mac', '--ssh-json', json.dumps([str(self.fake), '-o', 'ProxyCommand=quoted proxy %h %p', 'user@mac']), '--remote-ag', str(AG), '--remote-state', str(self.source), '--remote-project', str(self.project), '--local-project', str(self.local))

    def pull(self, *args, **kwargs):
        return self.cli(self.dest, 'sync', 'pull', 'mac', 'worker', *args, **kwargs)

    def test_roundtrip_fresh_echo_and_dedup(self):
        self.host()
        self.assertTrue(self.cli(self.dest, 'hosts', 'check', 'mac')['data']['reachable'])
        (self.project / 'code.txt').write_bytes(b'hello\x00world')
        first = self.pull('--file', 'code.txt')['data']
        self.assertEqual(first['snapshot'], self.pull('--file', 'code.txt')['data']['snapshot'])
        record = json.loads(Path(first['path']).read_text())
        self.assertNotIn('sid', record['payload']['agent'])
        self.assertNotIn('profile', record['payload']['agent'])
        self.assertEqual(record['payload']['message'], 'implement the missing transfer')
        result = self.cli(self.dest, 'sync', 'resume', first['snapshot'], '--name', 'linux', '--project', str(self.local), '--source-stopped', '--run')['data']
        self.assertTrue(result['source_verified'])
        self.assertIn('done parsing; next tests', result['turn']['reply'])
        self.assertIn('implement the missing transfer', result['turn']['reply'])
        self.assertEqual((self.local / 'code.txt').read_bytes(), b'hello\x00world')
        self.assertEqual(ag.ctx_load_assignments(self.dest)['linux']['task'], 'finish feature')
        self.cli(self.dest, 'sync', 'resume', first['snapshot'], '--name', 'linux', '--project', str(self.local), '--source-stopped', ok=False)

    def test_interrupted_pull_keeps_last_snapshot(self):
        self.host()
        first = self.pull()['data']
        before = Path(first['path']).read_bytes()
        env = dict(os.environ, AG_FAKE_SSH='partial')
        self.pull(ok=False, env=env)
        self.assertEqual(Path(first['path']).read_bytes(), before)
        self.assertEqual(len(list((self.dest / 'sync' / 'snapshots').glob('*.json'))), 1)

    def test_resume_requires_ack_and_refuses_busy(self):
        self.host()
        snap = self.pull()['data']['snapshot']
        self.cli(self.dest, 'sync', 'resume', snap, '--name', 'linux', '--project', str(self.local), ok=False)
        lock = ag._acquire_agent_turn(self.source, 'worker')
        try:
            r = self.cli(self.dest, 'sync', 'resume', snap, '--name', 'linux', '--project', str(self.local), '--source-stopped', ok=False)
            self.assertIn('busy', r['error'])
        finally: ag._release_agent_turn(lock)
        self.assertFalse(any(r['name'] == 'linux' for r in ag.load_agents(self.dest)))

    def test_offline_resume_explicit_ack(self):
        self.host()
        snap = self.pull()['data']['snapshot']
        env = dict(os.environ, AG_FAKE_SSH='offline')
        r = self.cli(self.dest, 'sync', 'resume', snap, '--name', 'linux', '--project', str(self.local), '--source-stopped', env=env)['data']
        self.assertFalse(r['source_verified'])
        self.assertTrue(Path(r['message_path']).is_file())

    def test_project_mismatch_and_paths(self):
        self.host()
        for bad in ('../escape', '/etc/passwd', '.agent/token', '.env', '.ssh/key'):
            self.pull('--file', bad, ok=False)
        (self.project / 'link').symlink_to(self.root)
        self.pull('--file', 'link/escape', ok=False)
        (self.project / 'large').write_bytes(b'x' * (1024 * 1024 + 1))
        self.pull('--file', 'large', ok=False)
        snap = self.pull()['data']['snapshot']
        self.cli(self.dest, 'sync', 'resume', snap, '--name', 'linux', '--project', str(self.project), '--source-stopped', ok=False)
        agents = json.loads((self.source / 'agents.json').read_text())
        agents[0]['dir'] = str(self.local)
        (self.source / 'agents.json').write_text(json.dumps(agents))
        self.pull(ok=False)

    def test_existing_files_and_symlink_destination_refused(self):
        self.host()
        (self.project / 'code').write_text('new')
        snap = self.pull('--file', 'code')['data']['snapshot']
        (self.local / 'code').write_text('precious')
        self.cli(self.dest, 'sync', 'resume', snap, '--name', 'linux', '--project', str(self.local), '--source-stopped', ok=False)
        self.assertEqual((self.local / 'code').read_text(), 'precious')
        (self.local / 'code').unlink()
        (self.local / 'code').symlink_to(self.root / 'outside')
        self.cli(self.dest, 'sync', 'resume', snap, '--name', 'linux', '--project', str(self.local), '--source-stopped', ok=False)
        self.assertFalse((self.root / 'outside').exists())

    def test_modified_snapshot_rejected(self):
        self.host()
        info = self.pull()['data']
        path = Path(info['path'])
        data = json.loads(path.read_text())
        data['payload']['files'] = [{'path': '../outside', 'data': base64.b64encode(b'bad').decode()}]
        path.write_text(json.dumps(data))
        self.cli(self.dest, 'sync', 'resume', info['snapshot'], '--name', 'linux', '--project', str(self.local), '--source-stopped', ok=False)
        self.assertFalse((self.root / 'outside').exists())

    def test_relative_agent_dir_rejected_with_absolute_hint(self):
        (self.root / 'relwork').mkdir()
        self.cli(self.source, 'agents', 'add', 'rel', '--backend', 'echo', '--dir', 'relwork')
        r = self.cli(self.source, 'sync', '_export', 'rel', '--project', str(self.root / 'relwork'), ok=False)
        self.assertIn('absolute', r.get('error', ''))

    def test_cross_os_identity_without_local_resolve(self):
        import types
        cfg_proj, canon_proj = '/tmp/x-mac-proj', '/private/var/x-mac-proj'
        cfg_state, canon_state = '/tmp/x-mac-state', '/private/var/x-mac-state'
        if ag._sync_same_path(canon_proj, cfg_proj) or ag._sync_same_path(canon_state, cfg_state):
            self.skipTest('platform resolves fixture pair; no local disagreement to test')
        self.cli(self.dest, 'hosts', 'add', 'mac2', '--ssh-json', json.dumps([str(self.fake), 'user@mac2']),
            '--remote-ag', str(AG), '--remote-state', cfg_state, '--remote-project', cfg_proj, '--local-project', str(self.local))
        orig_remote = ag._sync_remote
        def fake_remote(host, args, timeout=30):
            if '_export' in args:
                payload = dict(schema=1, source=dict(agent='worker', project=canon_proj, state=canon_state),
                    agent=dict(backend='echo', model='default', role='sub'),
                    assignment=dict(scope='', task='t', acceptance='a'),
                    context='c', memories=dict(brief='b', checkpoint='k'),
                    message='m', files=[])
                return dict(snapshot=ag._sync_hash(payload), payload=payload, captured_at='t',
                    busy=False, warnings=[], requested_project=host['remote_project'])
            if '_probe' in args:
                return dict(protocol=1, project=canon_proj, state=canon_state, busy=False)
            raise AssertionError(args)
        ag._sync_remote = fake_remote
        try:
            a = types.SimpleNamespace(host='mac2', agent='worker', file=[], message=None, timeout=30)
            latest = ag._sync_pull(self.dest, a)
            record = json.loads(Path(latest['path']).read_text())
            self.assertEqual(record['requested_project'], cfg_proj)
            self.assertEqual(record['requested_state'], cfg_state)
            b = types.SimpleNamespace(source_stopped=True, name='xlinux', snapshot=latest['snapshot'],
                project=str(self.local), backend=None, model=None, timeout=30, max_runtime=0, run=False)
            result = ag._sync_resume(self.dest, b)
            self.assertTrue(result['source_verified'])
            self.assertEqual(result['agent'], 'xlinux')
        finally:
            ag._sync_remote = orig_remote

    def test_resume_backend_override_resets_foreign_model(self):
        agents = json.loads((self.source / 'agents.json').read_text())
        agents[0]['backend'] = 'echo'
        agents[0]['model'] = 'gpt-foreign'
        (self.source / 'agents.json').write_text(json.dumps(agents))
        self.host()
        snap = self.pull()['data']['snapshot']
        self.cli(self.dest, 'sync', 'resume', snap, '--name', 'plain', '--project', str(self.local), '--source-stopped')
        by_name = {r['name']: r for r in ag.load_agents(self.dest)}
        self.assertEqual(by_name['plain']['model'], 'gpt-foreign')
        self.cli(self.dest, 'sync', 'resume', snap, '--name', 'moved', '--project', str(self.local), '--source-stopped', '--backend', 'opencode')
        by_name = {r['name']: r for r in ag.load_agents(self.dest)}
        self.assertEqual(by_name['moved']['backend'], 'opencode')
        self.assertEqual(by_name['moved']['model'], 'default')
        self.cli(self.dest, 'sync', 'resume', snap, '--name', 'pinned', '--project', str(self.local), '--source-stopped', '--backend', 'opencode', '--model', 'my-custom')
        by_name = {r['name']: r for r in ag.load_agents(self.dest)}
        self.assertEqual(by_name['pinned']['model'], 'my-custom')

    def test_resume_run_backend_failure_is_nonzero_with_exit(self):
        import types
        self.host()
        snap = self.pull()['data']['snapshot']
        self.cli(self.dest, 'sync', 'resume', snap, '--name', 'badflag', '--project', str(self.local), '--source-stopped', '--run', '--max-runtime', 'nan', ok=False)
        orig = ag.run_turn
        try:
            ag.run_turn = lambda *args, **kwargs: {'exit': 1, 'reply': 'boom', 'sid': ''}
            a = types.SimpleNamespace(source_stopped=True, name='failed', snapshot=snap, project=str(self.local), backend=None, model=None, timeout=30, max_runtime=1800, run=True)
            with self.assertRaises(ValueError) as ctx:
                ag._sync_resume(self.dest, a)
            self.assertIn('exit 1', str(ctx.exception))
            # No rollback: prepared agent kept, saved message named for recovery.
            self.assertIn("'failed'", str(ctx.exception))
            self.assertIn('resumes/failed', str(ctx.exception))
            self.assertTrue(any(r['name'] == 'failed' for r in ag.load_agents(self.dest)))
            self.assertEqual(len(list((self.dest / 'sync' / 'resumes').glob('failed-*.txt'))), 1)
            ag.run_turn = lambda *args, **kwargs: {'exit': 124, 'reply': '', 'sid': '', 'timed_out': True, 'error': 'max-runtime 5s exceeded (backend killed)'}
            a = types.SimpleNamespace(source_stopped=True, name='stalled', snapshot=snap, project=str(self.local), backend=None, model=None, timeout=30, max_runtime=5, run=True)
            with self.assertRaises(ValueError) as ctx:
                ag._sync_resume(self.dest, a)
            self.assertIn('timed out', str(ctx.exception))
        finally:
            ag.run_turn = orig

if __name__ == '__main__': unittest.main()
