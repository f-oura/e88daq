import importlib.util
import json
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


if __name__ == '__main__':
    unittest.main()
