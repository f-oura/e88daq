import importlib.util
import io
import pathlib
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


if __name__ == '__main__':
    unittest.main()
