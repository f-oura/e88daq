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
            s, out = self.session(d, no_file=True)
            try:
                r = s.recording()
                self.assertTrue(r['no_file']); self.assertNotIn('file', r)
            finally:
                s.proc.kill(); s.proc.wait()

    def test_settings(self):
        with tempfile.TemporaryDirectory() as d:
            s, out = self.session(d)
            try:
                self.assertEqual(s.get_settings(), {'available': True, 'kind': 'sc', 'prescale': 100})
                self.assertEqual(s.set_settings('prescale', 10)['kind'], 'prescale')
                self.assertEqual((out / 'timeframe' / 'run-settings.txt').read_text(), 'kind prescale\nprescale 10\n')
                with self.assertRaises(ValueError):
                    s.set_settings('none', 10)
                self.assertEqual(len((out / 'run-settings.jsonl').read_text().splitlines()), 1)
            finally:
                s.proc.kill(); s.proc.wait()
            time.sleep(0.2)
            self.assertEqual(s.get_settings(), {'available': False})
            with self.assertRaises(ValueError):
                s.set_settings('sc', 10)

    def test_no_filter_no_settings(self):
        with tempfile.TemporaryDirectory() as d:
            s, out = self.session(d, with_settings=False)
            try:
                self.assertEqual(s.get_settings(), {'available': False})
            finally:
                s.proc.kill(); s.proc.wait()


if __name__ == '__main__':
    unittest.main()
