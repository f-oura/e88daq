import importlib.util
from pathlib import Path
import struct
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('timeframe',ROOT/'nestdaq/lib/timeframe.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def stf(frame,fem):
 body=struct.pack('<QIHHQQ',m.HBF_MAGIC,32,16,0,0x7000000000000000,0x7800000000000000)
 return m.STF.pack(m.STF_MAGIC,48+len(body),48,0,frame,3,fem,1,0,0)+body

def tf(parts,frame=7,kind=0):
 return m.TF.pack(m.TF_MAGIC,24+sum(map(len,parts)),24,kind,frame,2)+b''.join(parts)

class TimeframeTests(unittest.TestCase):
 def test_complete_two_distinct_sources(self):
  frame,parts=m.parse_tf(tf([stf(7,18),stf(7,30)]),{18,30});self.assertEqual(frame,7);self.assertEqual(parts[18],stf(7,18))
 def test_two_copies_of_one_source_are_not_complete(self):
  with self.assertRaisesRegex(ValueError,'duplicate'):m.parse_tf(tf([stf(7,18),stf(7,18)]),{18,30})
 def test_frame_id_mismatch(self):
  with self.assertRaisesRegex(ValueError,'frame-ID'):m.parse_tf(tf([stf(7,18),stf(8,30)]),{18,30})
 def test_incomplete_flag_is_rejected(self):
  with self.assertRaisesRegex(ValueError,'incomplete'):m.parse_tf(tf([stf(7,18)],kind=0xc000),{18,30})
 def test_missing_source(self):
  with self.assertRaisesRegex(ValueError,'missing'):m.parse_tf(tf([stf(7,18)]),{18,30})
 def test_truncation(self):
  with self.assertRaisesRegex(ValueError,'invalid TF'):m.parse_tf(tf([stf(7,18),stf(7,30)])[:-1],{18,30})
 def test_bad_hbf_is_rejected(self):
  block=bytearray(stf(7,18));struct.pack_into('<I',block,56,4096)
  with self.assertRaisesRegex(ValueError,'HBF'):m.parse_tf(tf([bytes(block),stf(7,30)]),{18,30})
if __name__=='__main__':unittest.main()
