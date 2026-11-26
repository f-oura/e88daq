"""A loopback SiTCP RBCP responder that behaves like the HUL trigger register bus, for tests.

The HUL programs send the bits above the lowest byte of a register value in the low 16 bits
of the RBCP address; read-back reads byte i of a register at local address + i. Registers are
stored that way, so a write followed by a read returns the written value. `stuck` maps
(module id, local address) to a value that read-back returns instead, to simulate a mismatch.
"""
import socket
import struct
import threading


class FakeRbcp:
    def __init__(self, host='127.0.0.1', port=4660, version=0x21000000):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.settimeout(0.2)
        self.mem, self.stuck, self.writes = {}, {}, []
        self.set_register(0xe, 0x010, version, 4)
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def set_register(self, mid, local, value, nbytes=3):
        for i in range(nbytes):
            self.mem[(mid, local + i)] = (value >> (8 * i)) & 0xff

    def _serve(self):
        while self.running:
            try:
                packet, peer = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            ver, cmd, pid, length, addr = struct.unpack('>BBBBI', packet[:8])
            mid, local = addr >> 28, (addr >> 16) & 0xfff
            if cmd == 0x80:
                value = ((addr & 0xffff) << 8) | packet[8]
                self.writes.append((mid, local, value))
                self.set_register(mid, local, self.stuck.get((mid, local), value))
                reply = struct.pack('>BBBBI', ver, cmd | 0x08, pid, length, addr) + packet[8:8 + length]
            else:
                data = bytes(self.mem.get((mid, local + i), 0) for i in range(length))
                reply = struct.pack('>BBBBI', ver, cmd | 0x08, pid, length, addr) + data
            self.sock.sendto(reply, peer)

    def close(self):
        self.running = False
        self.thread.join()
        self.sock.close()
