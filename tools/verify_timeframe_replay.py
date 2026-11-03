#!/usr/bin/env python3
"""Verify real TFB FileSink output against replay inputs, byte for byte per FEM/STF."""
import argparse,hashlib,json,struct,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'nestdaq/lib'))
from timeframe import blocks,parse_tf,TF_MAGIC,FS_HEAD,FS_TAIL

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('file');p.add_argument('--expected',required=True);p.add_argument('--json',required=True);p.add_argument('--run-number',type=int);a=p.parse_args()
 expected=json.loads(Path(a.expected).read_text());byfem={v['fem_id']:v['stf_sha256'] for v in expected.values()};seen=set();trailers=0
 for magic,data in blocks(a.file):
  if magic==FS_HEAD:
   if a.run_number is not None and struct.unpack_from('<Q',data,24)[0]!=a.run_number:raise ValueError('FileSink header run number mismatch')
   continue
  if magic==FS_TAIL:trailers+=1;continue
  if magic!=TF_MAGIC:raise ValueError('unexpected FileSink block')
  tfid,sources=parse_tf(data,set(byfem))
  if tfid in seen:raise ValueError('duplicate TF')
  for fem,block in sources.items():
   if byfem[fem].get(str(tfid))!=hashlib.sha256(block).hexdigest():raise ValueError('STF payload changed or unexpected TF ID')
  seen.add(tfid)
 wanted=set(map(int,next(iter(byfem.values()))))
 if seen!=wanted or trailers!=1:raise ValueError('missing/extra frames or missing trailer')
 report={'run_number_checked':a.run_number,'status':'PASS','complete_timeframes':len(seen),'sources_per_tf':len(byfem),'fem_ids':list(byfem),'first':min(seen),'last':max(seen),'all_input_stf_bytes_preserved':True,'file_trailers':trailers,'scope':'Saved real data replay through actual NestDAQ TimeFrameBuilder; no live board contact'}
 Path(a.json).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
