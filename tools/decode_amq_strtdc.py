#!/usr/bin/env python3
"""Decode AMANEQ StrTdc (v1) 64-bit words from a raw SiTCP TCP capture or a NestDAQ FileSink file.

Word layout follows nestdaq-user-impl dc2985f7 AmQStrTdcData.h namespace v1
(the inline namespace); the file layout follows the inline v1 headers in
FileSinkHeader.h, FileSinkTrailer.h, SubTimeFrameHeader.h and
HeartbeatFrameHeader.h. The older data format note describes the v0
headers ("@FS-HEAD", "STF-HEAD"), which the 2026-10-26 FileSink output does not
use; v0 is still accepted.

  head [63:58]   0x0B data, 0x1C heartbeat 1st, 0x1E heartbeat 2nd,
                 0x0D trailer, 0x19/0x11 throttling T1 start/end,
                 0x1A/0x12 throttling T2 start/end
  HR-TDC data    hrtdc [28:0], hrtot [50:29], hrch [57:51]
  LR-TDC data    tdc [33:15], tot [49:34], ch [57:50]
  heartbeat 1st  hbframe [23:0], toffset [39:24], hbflag [55:40]
  heartbeat 2nd  transSize [19:0], geneSize [39:20], userReg [55:40]

The data-word channel field differs between the HR and LR layouts. With
--tdc-layout auto (the default) a FileSink v1 file takes the layout from each
STF header's femType (2, 5: HR; 1, 3, 6: LR); a raw stream, which has no
header, uses HR unless --tdc-layout lr is given. The summary names the layout
it used for each channel tally.

Words are little-endian 8-byte units on the wire. Nothing here talks to
hardware; it reads files only.
"""
import argparse
import collections
import json
import struct
import sys

HEAD_NAMES = {
    0x0B: "data", 0x1C: "heartbeat1", 0x1E: "heartbeat2", 0x0D: "trailer",
    0x19: "throttle_t1_start", 0x11: "throttle_t1_end",
    0x1A: "throttle_t2_start", 0x12: "throttle_t2_end",
    0x18: "spill_on_compat", 0x14: "spill_end_compat",
}

MAGIC_FS_HEAD = 0x444145482D534640  # "@FS-HEAD"
MAGIC_FS_TRAI = 0x494152542D534640  # "@FS-TRAI"
MAGIC_STF = 0x444145482D465453      # "STF-HEAD"
MAGIC_TF = 0x444145482D465440       # "@TF-HEAD"
MAGIC_FLT = 0x4E494F432D544C46      # "FLT-COIN"
STF_HEADER_BYTES = 64
# inline v1 headers: magic, then u32 length (whole block), u16 hLength, u16 type
MAGIC_FS_HEAD_V1 = 0x004B4E53454C4946  # "FILESNK"
MAGIC_FS_TRAI_V1 = 0x004C5254454C4946  # "FILETRL"
MAGIC_STF_V1 = 0x00454D4954425553      # "SUBTIME"
MAGIC_HBF_V1 = 0x0054414542545248      # "HRTBEAT"
FEM_TYPES_V1 = {2: "TDC64H (HR-TDC)", 1: "TDC64L_V1", 3: "TDC64L_V2", 5: "TDC64H_V3", 6: "TDC64L_V3", 21: "INPUT_REG"}
FEM_TYPE_LAYOUT = {2: "hr", 5: "hr", 1: "lr", 3: "lr", 6: "lr"}


def bits(value, lo, width):
    return (value >> lo) & ((1 << width) - 1)


def decode_word(raw):
    head = bits(raw, 58, 6)
    out = {"head": head, "type": HEAD_NAMES.get(head, "unknown")}
    if head == 0x0B:
        out.update(hrtdc=bits(raw, 0, 29), hrtot=bits(raw, 29, 22), hrch=bits(raw, 51, 7),
                   lrtdc=bits(raw, 15, 19), lrtot=bits(raw, 34, 16), lrch=bits(raw, 50, 8))
    elif head == 0x1C:
        out.update(hbframe=bits(raw, 0, 24), toffset=bits(raw, 24, 16), hbflag=bits(raw, 40, 16))
    elif head == 0x1E:
        out.update(trans_size=bits(raw, 0, 20), gene_size=bits(raw, 20, 20), user_reg=bits(raw, 40, 16))
    return out


class Summary:
    def __init__(self, layout="auto"):
        if layout not in ("auto", "hr", "lr"):
            raise ValueError("unknown TDC layout %r" % layout)
        self.layout_option = layout
        self.layout = "hr" if layout == "auto" else layout
        self.types = collections.Counter()
        self.unknown_heads = collections.Counter()
        self.hbframes = []
        self.hbflags = collections.Counter()
        self.toffsets = collections.Counter()
        self.trans_sizes = collections.Counter()
        self.gene_sizes = collections.Counter()
        self.channels = collections.Counter()
        self.pair_errors = 0
        self._expect_hb2 = False
        self.stf_headers = []

    def add(self, raw):
        w = decode_word(raw)
        self.types[w["type"]] += 1
        if w["type"] == "unknown":
            self.unknown_heads[hex(w["head"])] += 1
        if w["type"] == "heartbeat1":
            if self._expect_hb2:
                self.pair_errors += 1
            self._expect_hb2 = True
            self.hbframes.append(w["hbframe"])
            self.hbflags[hex(w["hbflag"])] += 1
            self.toffsets[w["toffset"]] += 1
        elif w["type"] == "heartbeat2":
            if not self._expect_hb2:
                self.pair_errors += 1
            self._expect_hb2 = False
            self.trans_sizes[w["trans_size"]] += 1
            self.gene_sizes[w["gene_size"]] += 1
        elif w["type"] == "data":
            self.channels[(self.layout, w["lrch"] if self.layout == "lr" else w["hrch"])] += 1

    def set_fem_type(self, fem_type):
        if self.layout_option == "auto" and fem_type in FEM_TYPE_LAYOUT:
            self.layout = FEM_TYPE_LAYOUT[fem_type]

    def result(self):
        frames = self.hbframes
        steps = collections.Counter((b - a) % (1 << 24) for a, b in zip(frames, frames[1:]))
        return {
            "word_types": dict(self.types),
            "unknown_heads": dict(self.unknown_heads),
            "heartbeat_frames": len(frames),
            "hbframe_first": frames[0] if frames else None,
            "hbframe_last": frames[-1] if frames else None,
            "hbframe_steps": {str(k): v for k, v in sorted(steps.items())},
            "heartbeat_pair_errors": self.pair_errors,
            "hbflag_values": dict(self.hbflags),
            "toffset_values": {str(k): v for k, v in sorted(self.toffsets.items())},
            "trans_size_values": {str(k): v for k, v in sorted(self.trans_sizes.items())[:20]},
            "gene_size_values": {str(k): v for k, v in sorted(self.gene_sizes.items())[:20]},
            "data_channels": {str(ch): v for (_, ch), v in sorted(self.channels.items())},
            "data_channel_layouts": sorted({lay for lay, _ in self.channels}),
            "stf_headers": len(self.stf_headers),
            "stf_header_sample": self.stf_headers[:3],
            "stf_time_frame_id_steps": {str(k): v for k, v in sorted(collections.Counter(
                b["time_frame_id"] - a["time_frame_id"] for a, b in zip(self.stf_headers, self.stf_headers[1:])).items())},
            "hbf_headers": getattr(self, "hbf_headers", 0),
            "file_trailers": getattr(self, "trailers", 0),
        }


def decode_raw_stream(data, summary):
    if len(data) % 8:
        raise ValueError("raw stream length %d is not a multiple of 8" % len(data))
    for (raw,) in struct.iter_unpack("<Q", data):
        summary.add(raw)


def _v1_block(data, pos):
    length, h_length, typ = struct.unpack_from("<IHH", data, pos + 8)
    return length, h_length, typ


def decode_filesink(data, summary):
    """Walk FileSink header, STF headers and their bodies (v1 or v0); other headers are skipped by length."""
    pos = 0
    n = len(data)
    summary.hbf_headers = getattr(summary, "hbf_headers", 0)
    summary.trailers = getattr(summary, "trailers", 0)
    while pos + 8 <= n:
        (magic,) = struct.unpack_from("<Q", data, pos)
        if magic in (MAGIC_FS_HEAD_V1, MAGIC_FS_TRAI_V1):
            length, _, _ = _v1_block(data, pos)
            summary.trailers += magic == MAGIC_FS_TRAI_V1
            pos += length
        elif magic == MAGIC_STF_V1:
            length, h_length, _ = _v1_block(data, pos)
            tf_id, fem_type, fem_id, n_msg = struct.unpack_from("<4I", data, pos + 16)
            summary.stf_headers.append({"time_frame_id": tf_id, "fem_type": fem_type,
                                        "fem_type_name": FEM_TYPES_V1.get(fem_type, "unknown"),
                                        "fem_id": hex(fem_id), "length": length, "num_messages": n_msg})
            summary.set_fem_type(fem_type)
            end = pos + length
            if length < h_length or end > n:
                raise ValueError("bad v1 STF length %d at offset %d" % (length, pos))
            q = pos + h_length
            while q < end:
                (m2,) = struct.unpack_from("<Q", data, q)
                if m2 != MAGIC_HBF_V1:
                    raise ValueError("expected HRTBEAT at offset %d, got 0x%016x" % (q, m2))
                hb_len, hb_h, _ = _v1_block(data, q)
                if hb_len < hb_h or q + hb_len > end:
                    raise ValueError("bad HRTBEAT length %d at offset %d" % (hb_len, q))
                summary.hbf_headers += 1
                decode_raw_stream(data[q + hb_h:q + hb_len], summary)
                q += hb_len
            pos = end
        elif magic in (MAGIC_FS_HEAD, MAGIC_FS_TRAI):
            (size,) = struct.unpack_from("<Q", data, pos + 8)
            summary.trailers += magic == MAGIC_FS_TRAI
            pos += size
        elif magic == MAGIC_STF:
            tf_id, fem_type, fem_id, length, n_msg, t_sec, t_usec = struct.unpack_from("<7Q", data, pos + 8)
            summary.stf_headers.append({"time_frame_id": tf_id, "fem_type": fem_type, "fem_id": hex(fem_id),
                                        "length": length, "num_messages": n_msg})
            body_end = pos + length
            if length < STF_HEADER_BYTES or body_end > n:
                raise ValueError("bad STF length %d at offset %d" % (length, pos))
            decode_raw_stream(data[pos + STF_HEADER_BYTES:body_end], summary)
            pos = body_end
        elif magic == MAGIC_TF:
            pos += 24
        elif magic == MAGIC_FLT:
            pos += 32
        else:
            raise ValueError("unexpected word 0x%016x at offset %d" % (magic, pos))
    if pos != n:
        raise ValueError("trailing %d bytes after the last block" % (n - pos))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path")
    ap.add_argument("--format", choices=("raw", "filesink"), required=True)
    ap.add_argument("--json", help="write the summary here as well")
    ap.add_argument("--tdc-layout", choices=("auto", "hr", "lr"), default="auto",
                    help="data-word channel field: auto (femType, else HR), hr [57:51], lr [57:50]")
    args = ap.parse_args(argv)
    data = open(args.path, "rb").read()
    summary = Summary(args.tdc_layout)
    (decode_raw_stream if args.format == "raw" else decode_filesink)(data, summary)
    result = {"path": args.path, "format": args.format, "bytes": len(data), **summary.result()}
    text = json.dumps(result, indent=1)
    if args.json:
        open(args.json, "w").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
