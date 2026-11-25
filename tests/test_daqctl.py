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

class MultiSourceTests(unittest.TestCase):
    def manifest(self, d, with_board=True):
        import yaml
        data = yaml.safe_load(pathlib.Path(SETUP[1]).read_text())
        data['data_sources']['hrtdc2'] = dict(data['data_sources']['hrtdc'], endpoint='192.0.2.31')
        if with_board:
            data['operational_control']['boards']['hrtdc2'] = dict(data['operational_control']['boards']['hrtdc'])
        path = pathlib.Path(d, 'setup.yaml'); path.write_text(yaml.safe_dump(data)); return str(path)
    def run_with(self, with_board):
        with tempfile.TemporaryDirectory() as d, patch.object(m,'inspect',return_value={'linked':True,'ready':True}), patch.object(m,'read',return_value=0), patch.object(m,'capture_with_receiver_output') as capture:
            rc=m.main(['--manifest',self.manifest(d,with_board),'run','--reader','/r','--writer','/w','--set-hbfstate','/h','--run-number','42','--data-dir',d,'--out',d+'/run'])
            return rc,capture
    def test_three_sources_start_timeframe_chain(self):
        rc,capture=self.run_with(True)
        self.assertEqual(rc,0);capture.assert_called_once()
    def test_source_without_board_check_never_starts(self):
        rc,capture=self.run_with(False)
        self.assertEqual(rc,2);capture.assert_not_called()
    def test_unknown_link_mask_is_not_linked(self):
        board={'endpoint':'ip','serial':'s','interface':'d','source':'src','mac':'aa','identity_address':0,'identity_value':1,'link_mask':None,'role':'root'}
        with patch.object(m,'invoke',side_effect=['dev d src src','ip lladdr aa REACHABLE']), patch.object(m,'read',side_effect=[1,7,7,7]):
            self.assertFalse(m.inspect(board,'r',[])['linked'])

class WebctlTests(unittest.TestCase):
    def test_webctl_passed_to_timeframe_chain(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m,'inspect',return_value={'linked':True,'ready':True}), patch.object(m,'read',return_value=0), patch.object(m,'capture_with_receiver_output') as capture:
            rc=m.main(SETUP+['run','--reader','/r','--writer','/w','--set-hbfstate','/h','--run-number','42','--data-dir',d,'--out',d+'/run','--webctl','127.0.0.1:8080'])
            self.assertEqual(rc,0)
            cmd=capture.call_args.args[4]
            self.assertEqual(cmd[cmd.index('--webctl')+1],'127.0.0.1:8080')
    def outcome(self, webctl, hooks=None, data=b''):
        with tempfile.TemporaryDirectory() as d:
            out=pathlib.Path(d)/'run';(out/'timeframe').mkdir(parents=True)
            if hooks:(out/'timeframe'/'hooks.jsonl').write_text(''.join(m.json.dumps(h)+'\n' for h in hooks))
            data_file=pathlib.Path(d)/'run000042.dat'
            if data:data_file.write_bytes(data)
            return m.capture_outcome(out,data_file,webctl)
    def test_session_without_run_has_no_data(self):
        r=self.outcome('127.0.0.1:8080',hooks=[])
        self.assertEqual(r['result'],'NO_DATA');self.assertEqual(r['runs_started'],0);self.assertFalse(r['data_file_exists'])
    def test_failed_run_hook_does_not_count(self):
        r=self.outcome('127.0.0.1:8080',hooks=[{'event':'pre-run','status':'OK'},{'event':'post-run','status':'FAILED'}])
        self.assertEqual(r['result'],'NO_DATA')
    def test_data_file_means_capture(self):
        r=self.outcome('127.0.0.1:8080',hooks=[{'event':'post-run','status':'OK'}],data=b'x'*16)
        self.assertEqual(r['result'],'CAPTURE_COMPLETED_REQUIRES_DECODE');self.assertEqual(r['data_file_bytes'],16)
    def test_run_under_another_number_is_noted(self):
        r=self.outcome('127.0.0.1:8080',hooks=[{'event':'post-run','status':'OK'}]*2)
        self.assertEqual(r['result'],'CAPTURE_COMPLETED_REQUIRES_DECODE');self.assertEqual(r['runs_started'],2);self.assertIn('note',r)
    def test_fixed_capture_without_file_has_no_data(self):
        self.assertEqual(self.outcome(None)['result'],'NO_DATA')
        self.assertEqual(self.outcome(None,data=b'x')['result'],'CAPTURE_COMPLETED_REQUIRES_DECODE')
    def test_report_of_run_17(self):
        """Session of run 17 (2026-11-24): devices stayed IDLE, no hook, no file -> NO_DATA."""
        self.assertEqual(self.outcome('127.0.0.1:18080',hooks=[])['result'],'NO_DATA')
    def test_webctl_rejected_outside_timeframe_run(self):
        with tempfile.TemporaryDirectory() as d, patch.object(m,'inspect') as inspect:
            rc=m.main(SETUP+['status','--reader','/r','--out',d+'/s','--webctl','127.0.0.1:8080'])
            self.assertEqual(rc,2);inspect.assert_not_called()

if __name__=='__main__':unittest.main()
