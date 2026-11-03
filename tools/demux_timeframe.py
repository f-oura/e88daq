#!/usr/bin/env python3
"""Validate complete two-source TFs and extract unchanged STF streams per FEM.

Output .stf files have no FileSink wrapper; existing --format filesink parsers
accept these consecutive STF blocks. Does not repair or rebuild B2TT events.
"""
import argparse,json,sys
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'nestdaq/lib'))
from timeframe import blocks,parse_tf,TF_MAGIC,FS_HEAD,FS_TAIL

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('file');p.add_argument('--manifest',default=str(ROOT/'config/local/setup.yaml'));p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 if a.out.exists():p.error('output must be new')
 config=yaml.safe_load(Path(a.manifest).read_text());roles={int(b['fem_id']):role for role,b in config['data_sources'].items()}
 if len(roles)!=2:raise ValueError('two distinct manifest-bound FEM IDs required')
 a.out.mkdir(parents=True);outputs={fem:open(a.out/f'{role}.stf','wb') for fem,role in roles.items()};seen=set();trailer=0
 try:
  for magic,data in blocks(a.file):
   if magic==FS_HEAD:continue
   if magic==FS_TAIL:trailer+=1;continue
   if magic!=TF_MAGIC:raise ValueError('unexpected block')
   frame,src=parse_tf(data,set(roles))
   if frame in seen:raise ValueError('duplicate TF')
   seen.add(frame)
   for fem,block in src.items():outputs[fem].write(block)
  if not seen or trailer!=1:raise ValueError('empty capture or missing trailer')
 finally:
  for f in outputs.values():f.close()
 report={'complete_tf':len(seen),'first':min(seen),'last':max(seen),'sources':{role:hex(fem) for fem,role in roles.items()},'raw_stf_bytes_preserved':True}
 (a.out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
