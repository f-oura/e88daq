"""A tiny Redis stand-in for tests: GET and SET on a dict, over RESP on 127.0.0.1."""
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'nestdaq/lib'))
import redis_lite  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.data = {}
        store = self.data

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                while True:
                    try:
                        args = redis_lite.decode(self.rfile)
                    except redis_lite.RedisError:
                        return
                    cmd = args[0].upper()
                    if cmd == b'SET':
                        store[args[1]] = args[2]
                        self.wfile.write(b'+OK\r\n')
                    elif cmd == b'GET':
                        v = store.get(args[1])
                        self.wfile.write(b'$-1\r\n' if v is None else b'$%d\r\n%s\r\n' % (len(v), v))
                    else:
                        self.wfile.write(b'-ERR unknown command\r\n')

        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
