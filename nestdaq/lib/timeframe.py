"""Strict v1 NestDAQ TF/STF FileSink framing; no event-ID repair or live access.

Layouts: pinned nestdaq-user-impl TimeFrameHeader.h/SubTimeFrameHeader.h.
A complete TF must contain each expected FEM once, with matching TF IDs.
"""
import hashlib
import struct
TF_MAGIC=0x004D5246454D4954
STF_MAGIC=0x00454D4954425553
HBF_MAGIC=0x0054414542545248
FS_HEAD=0x004B4E53454C4946
FS_TAIL=0x004C5254454C4946
TF=struct.Struct('<QIHHII')
STF=struct.Struct('<QIHHIIIIQQ')


def blocks(path):
    with open(path,'rb') as f:
        while True:
            header=f.read(16)
            if not header:return
            if len(header)!=16:raise ValueError('truncated block prefix')
            magic,length,hsize,kind=struct.unpack('<QIHH',header)
            if length<hsize or hsize<16:raise ValueError('invalid block lengths')
            if length>128*1024*1024:raise ValueError('block exceeds 128 MiB parser limit')
            rest=f.read(length-16)
            if len(rest)!=length-16:raise ValueError('truncated block body')
            yield magic,header+rest


def stf_parts(data):
    if len(data)<STF.size:raise ValueError('short STF')
    magic,length,hsize,kind,tfid,ftype,fem,nmsg,sec,usec=STF.unpack_from(data)
    if magic!=STF_MAGIC or hsize!=STF.size or length!=len(data):raise ValueError('invalid STF header')
    parts=[data[:hsize]];offset=hsize
    while offset<len(data):
        if len(data)-offset<16:raise ValueError('short HBF header')
        hm,hl,hh,_=struct.unpack_from('<QIHH',data,offset)
        if hm!=HBF_MAGIC or hh!=16 or hl<16 or offset+hl>len(data) or (hl-16)%8:raise ValueError('invalid HBF length or magic')
        parts.append(data[offset:offset+hl]);offset+=hl
    if len(parts)-1!=nmsg:raise ValueError('STF message count mismatch')
    return tfid,fem,parts


def parse_tf(data,expected_fems):
    if len(data)<TF.size:raise ValueError('short TF')
    magic,length,hsize,kind,tfid,nsrc=TF.unpack_from(data)
    if magic!=TF_MAGIC or hsize!=TF.size or length!=len(data):raise ValueError('invalid TF header')
    if kind!=0:raise ValueError(f'incomplete/nonstandard TF type {kind:#x}')
    if nsrc!=len(expected_fems):raise ValueError('unexpected source count')
    offset=hsize;sources={}
    while offset<len(data):
        if len(data)-offset<STF.size:raise ValueError('truncated embedded STF')
        size=struct.unpack_from('<I',data,offset+8)[0]
        if size<STF.size or offset+size>len(data):raise ValueError('invalid embedded STF length')
        block=data[offset:offset+size];stid,fem,parts=stf_parts(block)
        if stid!=tfid:raise ValueError('TF/STF frame-ID disagreement')
        if fem not in expected_fems or fem in sources:raise ValueError('unexpected or duplicate FEM')
        sources[fem]=block;offset+=size
    if set(sources)!=set(expected_fems):raise ValueError('missing FEM')
    return tfid,sources
