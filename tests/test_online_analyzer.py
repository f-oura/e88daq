import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'nestdaq/lib'))
import online_analyzer as m


class FakeProc:
    def __init__(self, command):
        self.command = command
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class FollowerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.started = time.time() - 1
        self.spawned = []
        self.port_ok = True

    def tearDown(self):
        self.tmp.cleanup()

    def follower(self, with_param=True):
        def command_for(f, use_param):
            return ['nda-replay', '--input', str(f)] + (['--param', 'index.txt'] if use_param else [])

        def spawn(command, log):
            proc = FakeProc(command)
            self.spawned.append(proc)
            return proc
        return m.RunFileFollower(self.dir, self.started, command_for, spawn, lambda: self.port_ok, self.dir,
                                 with_param=with_param)

    def write_run(self, number, mtime=None):
        path = self.dir / f'run{number:06d}.dat'
        path.write_bytes(b'header')
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_waits_for_the_file(self):
        f = self.follower()
        f.poll(18)
        self.assertEqual(self.spawned, [])
        self.write_run(18)
        f.poll(18)
        self.assertEqual(len(self.spawned), 1)
        self.assertIn(str(self.dir / 'run000018.dat'), self.spawned[0].command)
        f.poll(18)
        self.assertEqual(len(self.spawned), 1)

    def test_empty_file_waits_for_data(self):
        f = self.follower()
        path = self.dir / 'run000018.dat'
        path.write_bytes(b'')
        f.poll(18)
        self.assertEqual(self.spawned, [])
        path.write_bytes(b'header')
        f.poll(18)
        self.assertEqual(len(self.spawned), 1)

    def test_old_file_is_not_followed(self):
        self.write_run(16, mtime=self.started - 3600)
        f = self.follower()
        f.poll(16)
        self.assertEqual(self.spawned, [])

    def test_next_run_replaces_the_analyzer(self):
        f = self.follower()
        self.write_run(18)
        f.poll(18)
        self.write_run(19)
        f.poll(19)
        self.assertEqual(len(self.spawned), 2)
        self.assertTrue(self.spawned[0].terminated)
        self.assertFalse(self.spawned[1].terminated)
        records = f.close()
        self.assertTrue(self.spawned[1].terminated)
        self.assertEqual([r['run_number'] for r in records], [18, 19])
        self.assertEqual(records[0]['stopped'], 'next run')
        self.assertEqual(records[1]['stopped'], 'session end')

    def test_parameter_error_retried_once_without_parameters(self):
        f = self.follower()
        self.write_run(18)
        f.poll(18)
        self.spawned[0].returncode = 1
        f.poll(18)
        self.assertEqual(len(self.spawned), 2)
        self.assertNotIn('--param', self.spawned[1].command)
        self.spawned[1].returncode = 1
        f.poll(18)
        f.poll(18)
        self.assertEqual(len(self.spawned), 2)  # no endless restarts
        self.assertIn('note', f.records[1])

    def test_no_retry_without_parameters(self):
        f = self.follower(with_param=False)
        self.write_run(18)
        f.poll(18)
        self.spawned[0].returncode = 1
        f.poll(18)
        f.poll(18)
        self.assertEqual(len(self.spawned), 1)

    def test_next_run_after_an_exited_analyzer(self):
        f = self.follower(with_param=False)
        self.write_run(18)
        f.poll(18)
        self.spawned[0].returncode = 0
        f.poll(18)
        f.poll(18)
        self.assertEqual(len(self.spawned), 1)  # the finished run is not started again
        self.write_run(19)
        f.poll(19)
        self.assertEqual(len(self.spawned), 2)
        self.assertIn(str(self.dir / 'run000019.dat'), self.spawned[1].command)
        records = f.close()
        self.assertEqual([r['run_number'] for r in records], [18, 19])
        self.assertEqual(records[0]['stopped'], 'exited')

    def test_busy_port_is_recorded_not_fatal(self):
        self.port_ok = False
        f = self.follower()
        self.write_run(18)
        f.poll(18)
        self.assertEqual(self.spawned, [])
        self.assertIn('port in use', f.records[0]['error'])

    def test_unknown_run_number_is_ignored(self):
        f = self.follower()
        for value in (None, -1, 1000000):
            f.poll(value)
        self.assertEqual(self.spawned, [])


    def test_restart_button_starts_the_same_run_again(self):
        f = self.follower()
        self.write_run(18)
        f.poll(18)
        self.spawned[0].returncode = m.RESTART_STATUS
        f.poll(18)
        self.assertEqual(len(self.spawned), 2)
        self.assertEqual(self.spawned[1].command, self.spawned[0].command)  # same file, parameters kept
        self.assertEqual(f.records[0]['stopped'], 'restart requested from the page')
        self.assertEqual(f.records[1]['note'], 'restarted from the analyzer page')
        self.spawned[1].returncode = m.RESTART_STATUS  # may be pressed again
        f.poll(18)
        self.assertEqual(len(self.spawned), 3)


class HelperTests(unittest.TestCase):
    def test_command(self):
        c = m.analyzer_command('/x/nda-replay', '/d/run000018.dat', '127.0.0.1:18090', '/p/index.txt', ['192.0.2.30', '192.0.2.31'])
        self.assertEqual(c[:2], ['/x/nda-replay', '--input'])
        for flag in ('--follow', '--keep-alive', '--allow-restart'):
            self.assertIn(flag, c)
        self.assertEqual(c[c.index('--http') + 1], 'http:127.0.0.1:18090')
        self.assertEqual(c[c.index('--param') + 1], '/p/index.txt')
        self.assertEqual(c[c.index('--expect-fem') + 1], '192.0.2.30,192.0.2.31')
        self.assertNotIn('--param', m.analyzer_command('/x', '/d', '127.0.0.1:1'))

    def test_parse_http(self):
        self.assertEqual(m.parse_http('127.0.0.1:18090'), '127.0.0.1:18090')
        for bad in ('18090', '127.0.0.1:', ':18090', '127.0.0.1:0', '127.0.0.1:70000', '127.0.0.1:x'):
            with self.assertRaises(ValueError):
                m.parse_http(bad)

    def test_port_free(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            s.listen()
            port = s.getsockname()[1]
            self.assertFalse(m.port_free(f'127.0.0.1:{port}'))
        self.assertTrue(m.port_free(f'127.0.0.1:{port}'))


if __name__ == '__main__':
    unittest.main()
