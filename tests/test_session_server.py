import importlib.util
import json
import os
import pathlib
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('session_server', pathlib.Path(__file__).resolve().parents[1] / 'run-control' / 'session-server.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from fake_redis import FakeRedis  # noqa: E402


class SessionCommandTests(unittest.TestCase):
    T = 'exec python3 run-control/daqctl.py run --run-number {run_number} --out {out}'

    def test_substitution(self):
        self.assertEqual(m.session_command(self.T, 18, '/s/a b'), "exec python3 run-control/daqctl.py run --run-number 18 --out '/s/a b'")

    def test_no_file_appended(self):
        self.assertTrue(m.session_command(self.T, 18, '/s/x', True).endswith("--out /s/x --no-file"))

    def test_no_file_placeholder(self):
        t = 'exec python3 daqctl.py run {no_file} --run-number {run_number} --out {out}; echo done'
        self.assertEqual(m.session_command(t, 18, '/o', True), 'exec python3 daqctl.py run --no-file --run-number 18 --out /o; echo done')
        self.assertNotIn('no_file', m.session_command(t, 18, '/o', False))
        self.assertNotIn('--no-file', m.session_command(t, 18, '/o', False))


class StartTests(unittest.TestCase):
    def test_checkbox_reaches_the_command(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'busy_processes', return_value=[]), patch.object(m, 'listening', return_value=False):
            env = {'SESSION_OUT_BASE': f'{d}/out', 'SESSION_DATA_DIR': d, 'SESSION_WEBCTL': '127.0.0.1:1',
                   'SESSION_COMMAND': 'echo run {run_number} out {out}'}
            s = m.Sessions(env, None)
            s.start(7, no_file=True)
            s.proc.wait()
            time.sleep(0.2)
            s.start(8)
            s.proc.wait()
            rows = [json.loads(x) for x in (pathlib.Path(d) / 'out' / 'sessions.jsonl').read_text().splitlines()]
            self.assertEqual([r['no_file'] for r in rows], [True, False])
            self.assertTrue(rows[0]['command'].endswith(' --no-file'))
            self.assertNotIn('--no-file', rows[1]['command'])
            log = pathlib.Path(rows[0]['out'] + '.log').read_text()
            self.assertTrue(log.strip().endswith('--no-file'))


class PrepareTests(unittest.TestCase):
    REPORT = {'action': 'prepare', 'result': 'FAIL_STOP', 'error': 'hrtdc31 initialize failed',
              'boards': {'root': {'ready': True}, 'hrtdc30': {'ready': True}, 'hrtdc31': {'ready': False}}}

    def sessions(self, d, command):
        env = {'SESSION_OUT_BASE': f'{d}/out', 'SESSION_DATA_DIR': d, 'SESSION_WEBCTL': '127.0.0.1:1',
               'SESSION_COMMAND': 'sleep 30'}
        if command is not None:
            env['SESSION_PREPARE_COMMAND'] = command
        return m.Sessions(env, None)

    def test_prepare_runs_and_reports(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'busy_processes', return_value=[]), \
                patch.object(m, 'listening', return_value=False):
            report = json.dumps(self.REPORT).replace("'", "'\\''")
            s = self.sessions(d, f"sleep 0.3; mkdir -p {{out}}; echo '{report}' > {{out}}/report.json")
            proc = s.prepare()
            self.assertTrue(s.prepare_state()['running'])
            self.assertIn('Preparing the boards', m.render(s))
            with self.assertRaises(ValueError):
                s.prepare()  # already running
            with self.assertRaises(ValueError):
                s.start(5)  # no session while the boards are prepared
            proc.wait()
            time.sleep(0.2)
            st = s.prepare_state()
            self.assertFalse(st['running'])
            self.assertEqual(st['report'], {'result': 'FAIL_STOP', 'error': 'hrtdc31 initialize failed',
                                            'not_ready': ['hrtdc31'], 'boards': 3})
            page = m.render(s)
            self.assertIn('Prepare boards', page)
            self.assertIn('Not READY: hrtdc31', page)
            rows = [json.loads(x) for x in (pathlib.Path(d) / 'out' / 'sessions.jsonl').read_text().splitlines()]
            self.assertEqual(rows[-1]['action'], 'prepare')
            self.assertTrue(st['out'].endswith('-prepare'))

    def test_refused_while_a_session_runs(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'busy_processes', return_value=[]), \
                patch.object(m, 'listening', return_value=False):
            s = self.sessions(d, 'true')
            s.start(7)
            try:
                with self.assertRaises(ValueError):
                    s.prepare()
                self.assertNotIn('action="prepare"', m.render(s))
            finally:
                s.proc.kill(); s.proc.wait()

    def test_no_button_without_command(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'listening', return_value=False):
            s = self.sessions(d, None)
            self.assertNotIn('action="prepare"', m.render(s))
            with self.assertRaises(ValueError):
                s.prepare()


class ControllerApiTests(unittest.TestCase):
    def session(self, d, no_file=False, with_settings=True):
        env = {'SESSION_OUT_BASE': f'{d}/out', 'SESSION_DATA_DIR': f'{d}/data', 'SESSION_WEBCTL': '127.0.0.1:1',
               'SESSION_COMMAND': 'sleep 30'}
        pathlib.Path(f'{d}/data').mkdir()
        s = m.Sessions(env, None)
        with patch.object(m, 'busy_processes', return_value=[]), patch.object(m, 'listening', return_value=False):
            s.start(18, no_file=no_file)
        out = pathlib.Path(json.loads(s.current.read_text())['out'])
        (out / 'timeframe').mkdir(parents=True)
        if with_settings:
            (out / 'timeframe' / 'run-settings.txt').write_text('kind sc\nprescale 100\n')
        return s, out

    def test_recording(self):
        with tempfile.TemporaryDirectory() as d:
            s, out = self.session(d)
            try:
                data = pathlib.Path(d) / 'data'
                old = data / 'run000016.dat'
                old.write_bytes(b'o')
                os.utime(old, (1, 1))  # written before the session: never reported
                r = s.recording()
                self.assertTrue(r['session_running']); self.assertFalse(r['no_file']); self.assertFalse(r['exists'])
                (data / 'run000018.dat').write_bytes(b'x' * 100)
                (data / 'run000018_sc.dat').write_bytes(b'y' * 999)  # side output: not the run file
                r = s.recording()
                self.assertEqual((r['file'], r['run_number'], r['bytes']), ('run000018.dat', 18, 100))
                self.assertLess(r['seconds_since_write'], 10)
                time.sleep(0.05)
                (data / 'run000019.dat').write_bytes(b'z' * 7)  # the next run of the same session
                self.assertEqual(s.recording()['file'], 'run000019.dat')
            finally:
                s.proc.kill(); s.proc.wait()
            time.sleep(0.2)
            self.assertFalse(s.recording()['session_running'])

    def test_recording_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            s, out = self.session(d, no_file=True, with_settings=False)  # the launcher skips the filter
            try:
                r = s.recording()
                self.assertTrue(r['no_file']); self.assertNotIn('file', r)
                g = s.get_settings()
                self.assertFalse(g['available']); self.assertIn('writes no run files', g['reason'])
            finally:
                s.proc.kill(); s.proc.wait()

    def test_settings(self):
        with tempfile.TemporaryDirectory() as d:
            s, out = self.session(d)
            try:
                self.assertEqual(s.get_settings(), {'available': True, 'kind': 'sc', 'prescale': 100, 'latest': None})
                self.assertEqual(s.set_settings('prescale', 10)['kind'], 'prescale')
                self.assertEqual((out / 'timeframe' / 'run-settings.txt').read_text(), 'kind prescale\nprescale 10\n')
                with self.assertRaises(ValueError):
                    s.set_settings('none', 10)
                self.assertEqual(len((out / 'run-settings.jsonl').read_text().splitlines()), 1)
            finally:
                s.proc.kill(); s.proc.wait()
            time.sleep(0.2)
            self.assertEqual(s.get_settings(), {'available': False, 'reason': 'no session is running'})
            with self.assertRaises(ValueError):
                s.set_settings('sc', 10)

    def test_no_filter_no_settings(self):
        with tempfile.TemporaryDirectory() as d:
            s, out = self.session(d, with_settings=False)
            try:
                r = s.get_settings()
                self.assertFalse(r['available']); self.assertIn('runs no side-output filter', r['reason'])
            finally:
                s.proc.kill(); s.proc.wait()


class LatestTests(unittest.TestCase):
    def test_latest_entry(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(m.latest_entry(d, 'run-comments.jsonl'))
            for name, rows in (('a', [{'time': 1, 'comment': 'one'}, {'time': 5, 'comment': 'five'}]),
                               ('b', [{'time': 3, 'comment': 'three'}]), ('c', [])):
                pathlib.Path(d, name).mkdir()
                pathlib.Path(d, name, 'run-comments.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
            self.assertEqual(m.latest_entry(d, 'run-comments.jsonl')['comment'], 'five')
            with open(pathlib.Path(d, 'b', 'run-comments.jsonl'), 'a') as f:
                f.write(json.dumps({'time': 9, 'comment': ''}) + '\n' + 'not json\n')  # a broken last line is skipped
            self.assertEqual(m.latest_entry(d, 'run-comments.jsonl'), {'time': 9, 'comment': ''})

    def test_latest_settings_from_an_earlier_session(self):
        with tempfile.TemporaryDirectory() as d:
            old = pathlib.Path(d, 'out', '20260101-000000-run000001')
            old.mkdir(parents=True)
            (old / 'run-settings.jsonl').write_text(json.dumps({'time': 1, 'kind': 'prescale', 'prescale': 7}) + '\n')
            s, out = ControllerApiTests.session(None, d)
            try:
                self.assertEqual(s.get_settings()['latest'], {'time': 1, 'kind': 'prescale', 'prescale': 7})
                s.set_settings('sc', 50)
                self.assertEqual({k: v for k, v in s.get_settings()['latest'].items() if k != 'time'}, {'kind': 'sc', 'prescale': 50})
            finally:
                s.proc.kill(); s.proc.wait()


class RunCommentTests(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(m.clean_comment('  SC HV 1500 V\r\nthreshold 30 mV\t '), 'SC HV 1500 V threshold 30 mV')
        self.assertEqual(m.clean_comment('é' * 127), 'é' * 127)  # 254 bytes
        with self.assertRaises(ValueError):
            m.clean_comment('é' * 128)  # 256 bytes
        with self.assertRaises(ValueError):
            m.clean_comment('bell\x07')

    def test_comment_in_session_redis(self):
        r = FakeRedis()
        with tempfile.TemporaryDirectory() as d:
            s, out = ControllerApiTests.session(None, d, with_settings=False)
            try:
                (out / 'timeframe' / 'redis.conf').write_text(f'bind 127.0.0.1\nport {r.port}\n')
                self.assertEqual(s.get_comment(), {'available': True, 'comment': '', 'latest': None})
                self.assertEqual(s.set_comment('beam on, SC HV 1500 V')['comment'], 'beam on, SC HV 1500 V')
                self.assertEqual(s.get_comment()['latest']['comment'], 'beam on, SC HV 1500 V')
                self.assertEqual(r.data[b'run_info:run_comment'], b'beam on, SC HV 1500 V')
                with self.assertRaises(ValueError):
                    s.set_comment('x' * 256)
                self.assertEqual(len((out / 'run-comments.jsonl').read_text().splitlines()), 1)
            finally:
                s.proc.kill(); s.proc.wait(); r.close()
            time.sleep(0.2)
            self.assertFalse(s.get_comment()['available'])


if __name__ == '__main__':
    unittest.main()
