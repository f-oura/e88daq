#!/usr/bin/env python3
"""Compare the heartbeat-frame numbers of two decoded FileSink files.

Reads two FileSink files (inline v1 or v0 layout) with decode_amq_strtdc.py and
reports, for the frames each source delivered in the same HBF window, the first
and last frame numbers, the overlap, and any frame present in one but not the
other. Two boards that take their frame numbering from the same MIKUMARI Root
should show identical first/last numbers and an empty difference, up to the
window edges.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import decode_amq_strtdc as dec  # noqa: E402


def frames_of(path):
    s = dec.Summary()
    dec.decode_filesink(open(path, "rb").read(), s)
    return s.hbframes, s.result()


def compare(frames_a, frames_b):
    a, b = set(frames_a), set(frames_b)
    both = a & b
    return {
        "a_first": min(a) if a else None, "a_last": max(a) if a else None, "a_count": len(frames_a),
        "b_first": min(b) if b else None, "b_last": max(b) if b else None, "b_count": len(frames_b),
        "first_offset_b_minus_a": (min(b) - min(a)) if a and b else None,
        "last_offset_b_minus_a": (max(b) - max(a)) if a and b else None,
        "common": len(both),
        "only_a": len(a - b), "only_b": len(b - a),
        "only_a_sample": sorted(a - b)[:10], "only_b_sample": sorted(b - a)[:10],
        "a_duplicates": len(frames_a) - len(a), "b_duplicates": len(frames_b) - len(b),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file_a")
    ap.add_argument("file_b")
    ap.add_argument("--label-a", default="a")
    ap.add_argument("--label-b", default="b")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    fa, ra = frames_of(args.file_a)
    fb, rb = frames_of(args.file_b)
    out = {"a": {"label": args.label_a, "path": args.file_a, "stf_sample": ra["stf_header_sample"][:1],
                 "word_types": ra["word_types"], "hbframe_steps": ra["hbframe_steps"], "file_trailers": ra["file_trailers"]},
           "b": {"label": args.label_b, "path": args.file_b, "stf_sample": rb["stf_header_sample"][:1],
                 "word_types": rb["word_types"], "hbframe_steps": rb["hbframe_steps"], "file_trailers": rb["file_trailers"]},
           "comparison": compare(fa, fb)}
    text = json.dumps(out, indent=1)
    if args.json:
        open(args.json, "w").write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
