import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'nestdaq/lib'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import redis_lite as m  # noqa: E402
from fake_redis import FakeRedis  # noqa: E402


class RespTests(unittest.TestCase):
    def test_encode(self):
        self.assertEqual(m.encode('SET', 'k', 'é'), b'*3\r\n$3\r\nSET\r\n$1\r\nk\r\n$2\r\n\xc3\xa9\r\n')

    def test_decode(self):
        for raw, value in ((b'+OK\r\n', 'OK'), (b':42\r\n', 42), (b'$-1\r\n', None), (b'$3\r\na\r\nb\r\n'[:0] + b'$5\r\na\r\nbc\r\n', b'a\r\nbc'),
                           (b'*2\r\n$1\r\nx\r\n:1\r\n', [b'x', 1])):
            self.assertEqual(m.decode(io.BytesIO(raw)), value)
        with self.assertRaises(m.RedisError):
            m.decode(io.BytesIO(b'-ERR wrong\r\n'))
        with self.assertRaises(m.RedisError):
            m.decode(io.BytesIO(b'$5\r\nab'))

    def test_round_trip_and_port(self):
        r = FakeRedis()
        try:
            self.assertEqual(m.command(r.port, 'SET', 'run_info:run_comment', 'HV 1500 V'), 'OK')
            self.assertEqual(m.command(r.port, 'GET', 'run_info:run_comment'), b'HV 1500 V')
            self.assertIsNone(m.command(r.port, 'GET', 'missing'))
        finally:
            r.close()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'redis.conf').write_text('bind 127.0.0.1\nport 34567\nsave ""\n')
            self.assertEqual(m.chain_port(d), 34567)


if __name__ == '__main__':
    unittest.main()
