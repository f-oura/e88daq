import importlib.util
import io
import pathlib
import subprocess
import sys
import unittest

spec = importlib.util.spec_from_file_location('chain', pathlib.Path(__file__).resolve().parents[1] / 'nestdaq' / 'scripts' / 'run-timeframe-chain.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

DROP = '[ERROR] Failed to send time frame after checking all destinations.Discarding data. Direction = 0\n'


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class RelayTests(unittest.TestCase):
    def test_lines_left_out_and_counted(self):
        stream = io.BytesIO((DROP * 5 + '[STATE] READY ---> RUNNING\n' + DROP * 2 + '[INFO] out[0]: in: 0\n').encode())
        log, counts = io.StringIO(), {'dropped': 0}
        m.relay_log(stream, log, m.DISCARD_LINE, counts)
        text = log.getvalue()
        self.assertEqual(counts['dropped'], 7)
        self.assertNotIn('Discarding data. Direction', text)
        self.assertIn('[STATE] READY ---> RUNNING', text)
        self.assertIn('[INFO] out[0]: in: 0', text)
        self.assertTrue(text.rstrip().endswith("lines with 'Discarding data' left out (total 7)"))

    def test_summary_once_per_period(self):
        clock = Clock()

        class Stream:
            lines = [DROP.encode()] * 6

            def readline(self):
                if not self.lines:
                    return b''
                clock.t += 25
                return self.lines.pop()
        log, counts = io.StringIO(), {'dropped': 0}
        m.relay_log(Stream(), log, m.DISCARD_LINE, counts, period=60, clock=clock)
        summaries = [x for x in log.getvalue().splitlines() if x.startswith('[launcher]')]
        # lines at 25, 50, ..., 150 s: summaries at 75 s (3 lines) and 150 s (3 lines), nothing left for the end
        self.assertEqual(len(summaries), 2)
        self.assertIn('3 lines', summaries[0])
        self.assertIn('(total 6)', summaries[-1])

    def test_failing_log_does_not_stop_reading(self):
        class BadLog:
            def write(self, text):
                raise OSError('No space left on device')

            def flush(self):
                raise OSError('No space left on device')
        stream = io.BytesIO(('[INFO] a\n' + DROP + '[INFO] b\n').encode())
        counts = {'dropped': 0}
        m.relay_log(stream, BadLog(), m.DISCARD_LINE, counts)
        self.assertEqual(stream.read(), b'')  # everything was read
        self.assertEqual(counts['dropped'], 1)
        self.assertGreaterEqual(counts['errors'], 2)


class FloodLinesTests(unittest.TestCase):
    def test_several_patterns_are_left_out(self):
        flood = ['[DEBUG] #D Output incomplete TF: 12\n', '[DEBUG] #D TFB Fail. TFid: 3(0x3), 1: 0/2, FEid: 5\n']
        stream = io.BytesIO((''.join(flood * 3) + '[INFO] number of source = 9\n' + m.DISCARD_LINE + '\n').encode())
        log, counts = io.StringIO(), {'dropped': 0}
        m.relay_log(stream, log, (m.DISCARD_LINE, *m.FLOOD_LINES), counts)
        self.assertEqual(counts['dropped'], 7)
        text = log.getvalue()
        body = [line for line in text.splitlines() if not line.startswith('[launcher]')]
        self.assertEqual(body, ['[INFO] number of source = 9'])  # no dropped line is kept
        self.assertIn('[launcher] 7 lines with', text)  # the summary line stays

    def test_other_debug_lines_stay(self):
        stream = io.BytesIO(b'[DEBUG] #D DB Server URI: tcp://127.0.0.1:1/0\n[DEBUG]  number of source = 9\n')
        log, counts = io.StringIO(), {'dropped': 0}
        m.relay_log(stream, log, m.FLOOD_LINES, counts)
        self.assertEqual(counts['dropped'], 0)
        self.assertIn('number of source = 9', log.getvalue())


class AnalyzerLimitTests(unittest.TestCase):
    def run_child(self, gigabytes, allocate_gb):
        code = f'b = bytearray(int({allocate_gb} * 2**30)); print("allocated")'
        return subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                              preexec_fn=m.analyzer_limits(gigabytes))

    def test_allocation_beyond_the_limit_fails_in_the_child_only(self):
        r = self.run_child(2, 4)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('MemoryError', r.stderr)

    def test_allocation_inside_the_limit_works(self):
        r = self.run_child(2, 0.25)
        self.assertEqual(r.returncode, 0)
        self.assertIn('allocated', r.stdout)

    def test_zero_means_no_limit(self):
        r = self.run_child(0, 0.25)
        self.assertEqual(r.returncode, 0)

    def test_default_is_set(self):
        self.assertGreater(m.ANALYZER_MEMORY_GB, 0)


if __name__ == '__main__':
    unittest.main()
