import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('daqctl', pathlib.Path(__file__).resolve().parents[1] / 'run-control' / 'daqctl.py')
m = importlib.util.module_from_spec(spec)
SETUP = ['--manifest', str(pathlib.Path(__file__).resolve().parents[1] / 'config' / 'setup.yaml.example')]
spec.loader.exec_module(m)

class ReadinessTests(unittest.TestCase):
    def invoke_case(self, action, rows, extra=()):
        with tempfile.TemporaryDirectory() as d, patch.object(m, 'inspect', side_effect=rows), patch.object(m, 'read', return_value=0), patch.object(m, 'invoke', return_value='') as call:
            opts=['--run-number','42','--data-dir',d] if action=='run' else []
            rc=m.main(SETUP+[action, '--reader', '/fake/reader', '--out', d+'/run']+opts+list(extra))
            return rc,call.call_args_list
    def test_ready_prepare_skips_initialization(self):
        rc,calls=self.invoke_case('prepare',[{'linked':True,'ready':True}]*3,['--initialize','/fake/init'])
        self.assertEqual(rc,0)
        self.assertFalse(calls)
    def test_unready_run_never_starts_capture(self):
        rc,calls=self.invoke_case('run',[{'linked':True,'ready':True}]*2+[{'linked':True,'ready':False}],['--set-hbfstate','/fake/hbf','--writer','/fake/writer'])
        self.assertEqual(rc,2)
        self.assertFalse(calls)
    def test_bad_read_rejected(self):
        with patch.object(m,'invoke',return_value='#D: Read register: 4008636142 (0xeeeeeeee)'):
            with self.assertRaises(RuntimeError):m.read('reader','ip',0,1,[])

class OutputTests(unittest.TestCase):
    def test_enable_and_restore(self):
        with patch.object(m,'read',side_effect=[0,4,0,0]), patch.object(m,'invoke') as call:
            m.capture_with_receiver_output('r','w','receiver','root',['capture'],[])
            self.assertEqual([c.args[0] for c in call.call_args_list], [['w','receiver','30100000','04','1'],['capture'],['w','receiver','30100000','00','1']])
    def test_wrong_source_stops_before_write(self):
        with patch.object(m,'read',return_value=7),patch.object(m,'invoke') as call:
            with self.assertRaises(RuntimeError):m.capture_with_receiver_output('r','w','receiver','root',['capture'],[])
            call.assert_not_called()
    def test_uncertain_hbf_stops_restore(self):
        with patch.object(m,'read',side_effect=[0,4,1]),patch.object(m,'invoke') as call:
            with self.assertRaises(RuntimeError):m.capture_with_receiver_output('r','w','receiver','root',['capture'],[])
            self.assertEqual(len(call.call_args_list),2)

class BuilderSelectionTests(unittest.TestCase):
    def run_mode(self, extra):
        with tempfile.TemporaryDirectory() as d, patch.object(m,'inspect',return_value={'linked':True,'ready':True}), patch.object(m,'read',return_value=0), patch.object(m,'capture_with_receiver_output') as capture:
            opts=[] if extra else ['--run-number','42','--data-dir',d]
            rc=m.main(SETUP+['run','--reader','/fake/read','--writer','/fake/write','--set-hbfstate','/fake/hbf','--out',d+'/run']+opts+extra)
            self.assertEqual(rc,0)
            return capture.call_args.args[4]
    def test_default_two_source_timeframe(self):
        cmd=self.run_mode([])
        self.assertTrue(cmd[1].endswith('run-timeframe-chain.py'))
        self.assertIn('--live',cmd);self.assertIn('--set-hbfstate',cmd)
        self.assertEqual(cmd[cmd.index('--run-number')+1],'42')
    def test_explicit_legacy_stf_topology(self):
        cmd=self.run_mode(['--builder','separate-stf'])
        self.assertTrue(cmd[1].endswith('run-separate-stf.py'))
        self.assertEqual(cmd.count('--source'),2)

class RunStoragePreflightTests(unittest.TestCase):
    def test_missing_run_number_stops_before_contact(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'inspect') as inspect:
            rc=m.main(SETUP+['run','--reader','/fake/read','--writer','/fake/write','--set-hbfstate','/fake/hbf','--out',d+'/run'])
            self.assertEqual(rc,2);inspect.assert_not_called()
    def test_existing_run_stops_before_contact(self):
        with tempfile.TemporaryDirectory() as d,patch.object(m,'inspect') as inspect:
            pathlib.Path(d,'run000042.dat').write_bytes(b'keep')
            rc=m.main(SETUP+['run','--reader','/fake/read','--writer','/fake/write','--set-hbfstate','/fake/hbf','--run-number','42','--data-dir',d,'--out',d+'/run'])
            self.assertEqual(rc,2);inspect.assert_not_called()
            self.assertEqual(pathlib.Path(d,'run000042.dat').read_bytes(),b'keep')

if __name__=='__main__':unittest.main()
