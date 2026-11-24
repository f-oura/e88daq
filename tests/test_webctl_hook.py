import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

HOOK = pathlib.Path(__file__).resolve().parents[1] / 'nestdaq' / 'scripts' / 'webctl-hook.py'

def hook(out, event, *extra):
    return subprocess.run([sys.executable, str(HOOK), event, '--output', str(out), *extra], capture_output=True, text=True)

class WebctlHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.out = pathlib.Path(self.tmp.name)
        self.hbf = self.out / 'hbf.sh'; self.calls = self.out / 'calls'
        self.hbf.write_text(f'#!/bin/sh\necho "$@" >> {self.calls}\necho "#D: ok"\n'); self.hbf.chmod(0o755)
        (self.out / 'AInput.log').write_text('TCP connected\n')
    def tearDown(self):self.tmp.cleanup()
    def live(self, event, timeout='0.5'):
        return hook(self.out, event, '--inputs', 'AInput', '--set-hbfstate', str(self.hbf), '--root', 'root', '--tcp-timeout', timeout)
    def records(self):return [json.loads(x) for x in (self.out / 'hooks.jsonl').read_text().splitlines()]
    def test_hbf_on_only_after_new_tcp_connection(self):
        self.live('pre-run')
        self.assertEqual(self.live('post-run').returncode, 1)          # no new connection yet
        self.assertFalse(self.calls.exists())
        self.live('pre-run')
        with open(self.out / 'AInput.log', 'a') as f:f.write('TCP connected\n')
        self.assertEqual(self.live('post-run').returncode, 0)
        self.assertEqual(self.calls.read_text().split(), ['root', 'on'])
    def test_failed_connection_keeps_hbf_off(self):
        self.live('pre-run')
        with open(self.out / 'AInput.log', 'a') as f:f.write('TCP connection error\n')
        self.assertEqual(self.live('post-run').returncode, 1)
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.records()[-1]['status'], 'FAILED')
    def test_pre_stop_switches_off(self):
        self.assertEqual(self.live('pre-stop').returncode, 0)
        self.assertEqual(self.calls.read_text().split(), ['root', 'off'])
    def test_replay_records_only(self):
        self.assertEqual(hook(self.out, 'post-run').returncode, 0)
        self.assertFalse(self.calls.exists())

if __name__ == '__main__':unittest.main()
