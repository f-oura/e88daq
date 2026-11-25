"""Minimal Redis client (RESP over TCP) for the few keys the run-control scripts read and write.

The session Redis of a DAQ chain listens on 127.0.0.1 at a port chosen when the chain starts (redis.conf in the
chain's output directory). Only simple commands are needed (GET, SET), so no client library is required.
"""
import socket
from pathlib import Path


class RedisError(Exception):
    pass


def encode(*args):
    """A command as a RESP array of bulk strings."""
    out = [b'*%d\r\n' % len(args)]
    for a in args:
        data = a if isinstance(a, bytes) else str(a).encode()
        out.append(b'$%d\r\n%s\r\n' % (len(data), data))
    return b''.join(out)


def _line(f):
    line = f.readline()
    if not line.endswith(b'\r\n'):
        raise RedisError('connection closed')
    return line[:-2]


def decode(f):
    """One reply from a binary file object: str (simple), bytes or None (bulk), int, or list."""
    line = _line(f)
    kind, rest = line[:1], line[1:]
    if kind == b'+':
        return rest.decode()
    if kind == b'-':
        raise RedisError(rest.decode(errors='replace'))
    if kind == b':':
        return int(rest)
    if kind == b'$':
        n = int(rest)
        if n < 0:
            return None
        data = f.read(n + 2)
        if len(data) != n + 2:
            raise RedisError('connection closed')
        return data[:-2]
    if kind == b'*':
        n = int(rest)
        return None if n < 0 else [decode(f) for _ in range(n)]
    raise RedisError(f'unexpected reply {line[:40]!r}')


def command(port, *args, host='127.0.0.1', timeout=2.0):
    with socket.create_connection((host, int(port)), timeout=timeout) as sock:
        sock.sendall(encode(*args))
        with sock.makefile('rb') as f:
            return decode(f)


def chain_port(output_dir):
    """Port of the session Redis from <output>/redis.conf (written by run-timeframe-chain.py)."""
    for line in (Path(output_dir) / 'redis.conf').read_text().splitlines():
        words = line.split()
        if len(words) == 2 and words[0] == 'port':
            return int(words[1])
    raise RedisError(f'no port in {output_dir}/redis.conf')
