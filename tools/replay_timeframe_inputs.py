#!/usr/bin/env python3
"""Extract saved STF multipart messages for hardware-free TimeFrameBuilder replay."""
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'nestdaq/lib'))
from timeframe import blocks,stf_parts,STF_MAGIC,FS_HEAD,FS_TAIL

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('receiver');p.add_argument('hrtdc');p.add_argument('--out',type=Path,required=True);p.add_argument('--frames',type=int,default=20000);a=p.parse_args()
 if a.frames<=0 or a.out.exists():p.error('positive frames and new output required')
 a.out.mkdir(parents=True);report={};ids=[]
 for role,path in [('receiver',a.receiver),('hrtdc',a.hrtdc)]:
  seen=[];fems=set();digests={}
  with open(a.out/f'{role}.txt','w') as out:
   for magic,block in blocks(path):
    if magic in (FS_HEAD,FS_TAIL):continue
    if magic!=STF_MAGIC:raise ValueError('input must contain STF blocks')
    tfid,fem,parts=stf_parts(block)
    if seen and tfid!=seen[-1]+1:raise ValueError('input STF frame IDs not consecutive')
    if tfid in digests:raise ValueError('duplicate frame')
    seen.append(tfid);fems.add(fem);digests[tfid]=hashlib.sha256(block).hexdigest();out.write(' '.join(x.hex() for x in parts)+'\n')
    if len(seen)>=a.frames:break
  if len(fems)!=1 or len(seen)!=a.frames:raise ValueError('unexpected FEMs or insufficient input')
  report[role]={'source':str(path),'fem_id':next(iter(fems)),'frames':len(seen),'first':seen[0],'last':seen[-1],'stf_sha256':digests};ids.append(seen)
 if ids[0]!=ids[1]:raise ValueError('source frame windows differ')
 if report['receiver']['fem_id']==report['hrtdc']['fem_id']:raise ValueError('same FEM in both inputs')
 (a.out/'expected.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:{x:y for x,y in v.items() if x!='stf_sha256'} for k,v in report.items()},indent=2))
if __name__=='__main__':main()
