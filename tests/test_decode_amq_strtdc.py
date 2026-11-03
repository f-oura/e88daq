"""Offline tests for decode_amq_strtdc.py with hand-built v1 words."""
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import decode_amq_strtdc as dec  # noqa: E402


def hb1(frame, toffset=0, flag=0):
    return (0x1C << 58) | (flag << 40) | (toffset << 24) | frame


def hb2(trans=0, gene=0, user=0):
    return (0x1E << 58) | (user << 40) | (gene << 20) | trans


def hit(ch, tdc, tot):
    return (0x0B << 58) | (ch << 51) | (tot << 29) | tdc


class TestDecode(unittest.TestCase):
    def test_captured_word_bytes_from_2026_10_26(self):
        # First 16 bytes of the 2026-10-26 HR-TDC TCP capture.
        a, b = struct.unpack("<2Q", bytes.fromhex("19b4460000000070" "0000000000000078"))
        w1, w2 = dec.decode_word(a), dec.decode_word(b)
        self.assertEqual(w1["type"], "heartbeat1")
        self.assertEqual(w1["hbframe"], 0x46B419)
        self.assertEqual((w1["toffset"], w1["hbflag"]), (0, 0))
        self.assertEqual(w2["type"], "heartbeat2")

    def test_field_positions(self):
        w = dec.decode_word(hit(ch=0x55, tdc=0x1ABCDEF, tot=0x2AAAA))
        self.assertEqual((w["type"], w["hrch"], w["hrtdc"], w["hrtot"]), ("data", 0x55, 0x1ABCDEF, 0x2AAAA))
        w = dec.decode_word(hb1(0xFFFFFE, toffset=0x1234, flag=0x8001))
        self.assertEqual((w["hbframe"], w["toffset"], w["hbflag"]), (0xFFFFFE, 0x1234, 0x8001))
        w = dec.decode_word(hb2(trans=0xABCDE, gene=0x12345, user=0xBEEF))
        self.assertEqual((w["trans_size"], w["gene_size"], w["user_reg"]), (0xABCDE, 0x12345, 0xBEEF))

    def test_raw_stream_summary_and_wrap(self):
        words = [hb1(0xFFFFFF), hb2(), hit(3, 10, 20), hb1(0x000000), hb2(8, 8)]
        s = dec.Summary()
        dec.decode_raw_stream(struct.pack("<%dQ" % len(words), *words), s)
        r = s.result()
        self.assertEqual(r["heartbeat_frames"], 2)
        self.assertEqual(r["hbframe_steps"], {"1": 1})
        self.assertEqual(r["heartbeat_pair_errors"], 0)
        self.assertEqual(r["data_channels"], {"3": 1})

    def test_pair_error_is_counted(self):
        words = [hb1(1), hb1(2), hb2()]
        s = dec.Summary()
        dec.decode_raw_stream(struct.pack("<3Q", *words), s)
        self.assertEqual(s.result()["heartbeat_pair_errors"], 1)

    def test_filesink_walk(self):
        body = struct.pack("<4Q", hb1(7), hb2(), hb1(8), hb2())
        stf = struct.pack("<8Q", dec.MAGIC_STF, 7, 1, 0xC0A80A1E, 64 + len(body), 2, 0, 0) + body
        head = struct.pack("<2Q", dec.MAGIC_FS_HEAD, 16)
        trail = struct.pack("<2Q", dec.MAGIC_FS_TRAI, 16)
        s = dec.Summary()
        dec.decode_filesink(head + stf + trail, s)
        r = s.result()
        self.assertEqual(r["stf_headers"], 1)
        self.assertEqual(r["stf_header_sample"][0]["fem_id"], "0xc0a80a1e")
        self.assertEqual(r["heartbeat_frames"], 2)

    def test_filesink_v1_walk_as_written_on_2026_10_26(self):
        words = struct.pack("<2Q", hb1(4633625), hb2())
        hbf = struct.pack("<QIHH", dec.MAGIC_HBF_V1, 16 + len(words), 16, 0) + words
        stf = struct.pack("<QIHH4IQQ", dec.MAGIC_STF_V1, 48 + len(hbf), 48, 0, 4633625, 2, 0x7F000001, 1, 0, 0) + hbf
        head = struct.pack("<QIHH", dec.MAGIC_FS_HEAD_V1, 304, 304, 0) + b"\0" * 288
        trail = struct.pack("<QIHH", dec.MAGIC_FS_TRAI_V1, 304, 304, 0) + b"\0" * 288
        s = dec.Summary()
        dec.decode_filesink(head + stf + stf + trail, s)
        r = s.result()
        self.assertEqual(len(stf), 80)
        self.assertEqual(r["stf_headers"], 2)
        self.assertEqual(r["stf_header_sample"][0]["fem_type_name"], "TDC64H (HR-TDC)")
        self.assertEqual(r["hbf_headers"], 2)
        self.assertEqual(r["file_trailers"], 1)
        self.assertEqual(r["word_types"], {"heartbeat1": 2, "heartbeat2": 2})

    def test_lr_channel_field_follows_fem_type(self):
        # A Receiver word read with the HR layout as ch 40 is LR ch 81 ([57:50]).
        lr_hit = (0x0B << 58) | (81 << 50) | (0x1234 << 34) | (0x5678 << 15)
        w = dec.decode_word(lr_hit)
        self.assertEqual((w["lrch"], w["lrtot"], w["lrtdc"], w["hrch"]), (81, 0x1234, 0x5678, 40))
        words = struct.pack("<3Q", lr_hit, hb1(9), hb2(8, 8))
        hbf = struct.pack("<QIHH", dec.MAGIC_HBF_V1, 16 + len(words), 16, 0) + words

        def stf(fem_type):
            return struct.pack("<QIHH4IQQ", dec.MAGIC_STF_V1, 48 + len(hbf), 48, 0, 9, fem_type, 0, 1, 0, 0) + hbf
        for fem_type, layout, ch in ((3, "lr", "81"), (2, "hr", "40")):
            s = dec.Summary()
            dec.decode_filesink(stf(fem_type), s)
            r = s.result()
            self.assertEqual((r["data_channels"], r["data_channel_layouts"]), ({ch: 1}, [layout]))
        s = dec.Summary("hr")
        dec.decode_filesink(stf(3), s)
        self.assertEqual(s.result()["data_channels"], {"40": 1})
        s = dec.Summary()
        dec.decode_raw_stream(struct.pack("<Q", lr_hit), s)
        self.assertEqual(s.result()["data_channel_layouts"], ["hr"])
        with self.assertRaises(ValueError):
            dec.Summary("xx")

    def test_v1_missing_hrtbeat_raises(self):
        stf = struct.pack("<QIHH4IQQ", dec.MAGIC_STF_V1, 56, 48, 0, 1, 2, 0, 1, 0, 0) + struct.pack("<Q", hb1(1))
        with self.assertRaises(ValueError):
            dec.decode_filesink(stf, dec.Summary())

    def test_bad_length_raises(self):
        with self.assertRaises(ValueError):
            dec.decode_raw_stream(b"\x00" * 12, dec.Summary())


class TestCompare(unittest.TestCase):
    def test_compare_frames(self):
        import compare_hbframes as cmp
        r = cmp.compare([5, 6, 7, 8], [6, 7, 8, 9, 9])
        self.assertEqual((r["first_offset_b_minus_a"], r["last_offset_b_minus_a"]), (1, 1))
        self.assertEqual((r["common"], r["only_a"], r["only_b"], r["b_duplicates"]), (3, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
