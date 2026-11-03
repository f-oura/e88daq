import importlib.util
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('storage',Path(__file__).resolve().parents[1]/'nestdaq/lib/run_storage.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class RunStorageTests(unittest.TestCase):
 def test_exact_six_digits(self):
  self.assertEqual(m.run_filename(42),'run000042.dat');self.assertEqual(m.run_filename(999999),'run999999.dat')
 def test_out_of_range_and_noninteger_rejected(self):
  for value in [-1,1000000,1.5,True]:
   with self.assertRaises(ValueError):m.run_filename(value)
 def test_existing_file_not_reused(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d,'run000042.dat');p.write_bytes(b'original')
   with self.assertRaises(ValueError):m.output_path(d,42)
   self.assertEqual(p.read_bytes(),b'original')
 def test_new_run_path(self):
  with tempfile.TemporaryDirectory() as d:self.assertEqual(m.output_path(d,42),Path(d).resolve()/'run000042.dat')
if __name__=='__main__':unittest.main()
