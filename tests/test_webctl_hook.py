import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fake_redis import FakeRedis  # noqa: E402

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


class RunInfoTests(unittest.TestCase):
    def test_post_run_records_run_number_and_comment(self):
        r = FakeRedis()
        try:
            r.data[b'run_info:run_number'] = b'18'
            r.data[b'run_info:run_comment'] = b'SC HV 1500 V'
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(hook(d, 'post-run', '--redis-port', str(r.port)).returncode, 0)  # replay: no HBF
                self.assertEqual(hook(d, 'pre-stop', '--redis-port', str(r.port)).returncode, 0)
                rows = [json.loads(x) for x in (pathlib.Path(d) / 'hooks.jsonl').read_text().splitlines()]
                self.assertEqual((rows[0]['run_number'], rows[0]['run_comment']), (18, 'SC HV 1500 V'))
                self.assertNotIn('run_comment', rows[1])  # only at post-run
        finally:
            r.close()

    def test_unreachable_redis_is_recorded_not_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(hook(d, 'post-run', '--redis-port', '1').returncode, 0)
            row = json.loads((pathlib.Path(d) / 'hooks.jsonl').read_text())
            self.assertEqual(row['status'], 'OK')
            self.assertIn('run_info_error', row)

if __name__ == '__main__':unittest.main()
