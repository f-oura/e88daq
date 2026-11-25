import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'nestdaq/lib'))
import run_settings as m


class RunSettingsTests(unittest.TestCase):
    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'run-settings.txt'
            m.write(p, 'prescale', 10)
            self.assertEqual(p.read_text(), 'kind prescale\nprescale 10\n')
            self.assertEqual(m.read(p), {'kind': 'prescale', 'prescale': 10})
            m.write(p, 'off', 10)
            self.assertEqual(m.read(p)['kind'], 'off')
            self.assertEqual(sorted(x.name for x in Path(d).iterdir()), ['run-settings.txt'])  # no temporary left

    def test_rejected_values(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'run-settings.txt'
            for kind, prescale in (('none', 10), ('sc', 0), ('sc', -1), ('sc', True), ('sc', '10'), ('sc', 10**9 + 1)):
                with self.assertRaises(ValueError):
                    m.write(p, kind, prescale)
            self.assertFalse(p.exists())
            for text in ('kind sc\nprescale 010\n', 'kind sc\nprescale x\n', 'kind sc\n', 'kind sc\nwindow 5\nprescale 1\n'):
                p.write_text(text)
                with self.assertRaises(ValueError):
                    m.read(p)


if __name__ == '__main__':
    unittest.main()
