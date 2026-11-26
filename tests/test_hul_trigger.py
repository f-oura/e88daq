"""Tests of hul/trigger/trgctl.py.

The check tests need nothing. The end-to-end tests need the programs built for the loopback address:
  HUL_TRIGGER_IP=127.0.0.1 hul/trigger/build-tools.sh <dir>
  HUL_TRIGGER_TEST_TOOLS=<dir> python3 -m unittest tests.test_hul_trigger
They answer RBCP on 127.0.0.1:4660 with tests/fake_rbcp.py; no board is involved.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
TRGCTL = HERE.parents[0] / 'hul' / 'trigger' / 'trgctl.py'
PARAMS = HERE.parents[0] / 'hul' / 'trigger' / 'param'
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('trgctl', TRGCTL)
trgctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trgctl)

REFERENCE = """\
RGN1::TOF_SEG1\t\t1      ________
RGN1::TOF_SEG2\t\t1      |      |
RGN2A::DELAY_BH1_BEAM\t\t1\t#  1-255
RGN2A::COIN_BH1\t\t2\t#  0: Veto,  1: On,  2: Off
"""
NIM_REFERENCE = """\
IOM::NIMOUT1  1
IOM::NIMOUT2  6
\t\t\t##\t0\tBH1_RGN2A\t\t##
\t\t\t##\t123\tLEVEL1_OR\t\t##
"""


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        (self.dir / 'ref.txt').write_text(REFERENCE)
        (self.dir / 'nimref.txt').write_text(NIM_REFERENCE)

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, text, kind='register', ref='ref.txt'):
        (self.dir / 'p.txt').write_text(text)
        return trgctl.check(kind, self.dir / 'p.txt', self.dir / ref)[1]

    def test_reference_passes(self):
        self.assertEqual(self.errors(REFERENCE), [])

    def test_missing_unknown_duplicate_and_range(self):
        errors = self.errors('rgn1::tof_seg1 2\nRGN1::TOF_SEG1 1\nRGN2A::DELAY_BH1_BEAM 0\n'
                             'RGN2A::COIN_BH1 3\nRGN2A::TYPO 1\n# RGN1::TOF_SEG2 1\n')
        text = '\n'.join(errors)
        self.assertIn('RGN1::TOF_SEG1: duplicated', text)
        self.assertIn('RGN1::TOF_SEG2: missing', text)
        self.assertIn('RGN2A::DELAY_BH1_BEAM = 0: allowed 1-255', text)
        self.assertIn('RGN2A::COIN_BH1 = 3: allowed [0, 1, 2]', text)
        self.assertIn('RGN2A::TYPO: unknown key', text)

    def test_segment_switch_is_zero_or_one(self):
        self.assertIn('RGN1::TOF_SEG2 = 5: allowed [0, 1]',
                      '\n'.join(self.errors(REFERENCE.replace('SEG2\t\t1', 'SEG2\t\t5'))))

    def test_nimio_signal_range(self):
        self.assertEqual(self.errors('IOM::NIMOUT1 0\nIOM::NIMOUT2 123\n', 'nimio', 'nimref.txt'), [])
        self.assertIn('IOM::NIMOUT2 = 124: allowed 0-123',
                      '\n'.join(self.errors('IOM::NIMOUT1 0\nIOM::NIMOUT2 124\n', 'nimio', 'nimref.txt')))

    def test_non_integer_value(self):
        self.assertIn('value is not a non-negative integer', '\n'.join(self.errors('RGN1::TOF_SEG1 x\n')))


TOOLS = os.environ.get('HUL_TRIGGER_TEST_TOOLS')


@unittest.skipUnless(TOOLS, 'set HUL_TRIGGER_TEST_TOOLS to programs built for 127.0.0.1')
class EndToEndTests(unittest.TestCase):
    def setUp(self):
        from fake_rbcp import FakeRbcp
        self.board = FakeRbcp()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.env = {**os.environ, 'HUL_TRIGGER_IP': '127.0.0.1', 'HUL_TRIGGER_TOOLS': TOOLS}
        self.ref = pathlib.Path(TOOLS) / 'hul-trigger' / 'src' / 'software'

    def tearDown(self):
        self.board.close()
        self.tmp.cleanup()

    def trgctl(self, *args, env=None):
        return subprocess.run([sys.executable, str(TRGCTL), *args], capture_output=True, text=True,
                              env=env or self.env, timeout=60)

    def report(self, out):
        return json.loads((self.dir / out / 'report.json').read_text())

    def test_version(self):
        p = self.trgctl('version', '--out', str(self.dir / 'v'))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.report('v')['version'], '0x21000000')

    def test_write_register_reference_and_readback(self):
        p = self.trgctl('write', 'register', str(self.ref / 'param' / 'example.txt'), '--out', str(self.dir / 'w'), '--yes')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        r = self.report('w')
        self.assertEqual(r['result'], 'OK')
        self.assertEqual(len(r['writes']), len(self.board.writes))
        self.assertTrue((self.dir / 'w' / 'last.log').exists())

    def test_write_e88_files(self):
        for kind, name in [('register', 'scor.txt'), ('register', 'scor-clock10k.txt'), ('register', 'clock1k.txt'), ('nimio', 'nimio.txt'), ('nimio', 'nimio-clock10k.txt')]:
            with self.subTest(name=name):
                p = self.trgctl('write', kind, str(PARAMS / name), '--out', str(self.dir / name), '--yes')
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertEqual(self.report(name)['result'], 'OK')

    def test_readback_mismatch_is_reported(self):
        self.board.stuck[(0x2, 0xa00)] = 7  # RGN2A::DELAY_BH1_BEAM
        p = self.trgctl('write', 'register', str(self.ref / 'param' / 'example.txt'), '--out', str(self.dir / 'm'), '--yes')
        self.assertEqual(p.returncode, 1)
        r = self.report('m')
        self.assertEqual(r['result'], 'READBACK_MISMATCH')
        self.assertIn('RGN2A::DELAY_BH1_BEAM', [m['label'] for m in r['mismatches']])

    def test_nimio_mismatch_is_reported(self):
        self.board.stuck[(0xa, 0x020)] = 9  # IOM::NIMOUT2
        p = self.trgctl('write', 'nimio', str(PARAMS / 'nimio.txt'), '--out', str(self.dir / 'n'), '--yes')
        self.assertEqual(p.returncode, 1)
        self.assertEqual([m['label'] for m in self.report('n')['mismatches']], ['IOM::NIMOUT2'])

    def test_check_errors_block_the_write(self):
        bad = self.dir / 'bad.txt'
        bad.write_text((self.ref / 'param' / 'example.txt').read_text().replace('RGN4::GATE', '# RGN4::GATE'))
        p = self.trgctl('write', 'register', str(bad), '--out', str(self.dir / 'b'), '--yes')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('RGN4::GATE: missing', p.stdout)
        self.assertEqual(self.board.writes, [])
        self.assertFalse((self.dir / 'b').exists())

    def test_address_mismatch_is_refused(self):
        p = self.trgctl('version', '--out', str(self.dir / 'x'), env={**self.env, 'HUL_TRIGGER_IP': '127.0.0.2'})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('rebuild', p.stderr)

    def test_no_answer_fails(self):
        self.board.close()
        self.board = type('Closed', (), {'close': lambda s: None, 'writes': []})()
        p = self.trgctl('write', 'nimio', str(self.ref / 'nim_param' / 'nimio.txt'), '--out', str(self.dir / 't'), '--yes')
        self.assertEqual(p.returncode, 1)
        self.assertEqual(self.report('t')['result'], 'FAILED')


if __name__ == '__main__':
    unittest.main()
