"""TCP transport for the bare-metal MYIR DDR transfer monitor."""
import socket
import struct

import numpy as np


class Ethernet:
    def __init__(self, address='192.168.200.2', port=8020):
        self.socket = socket.create_connection((address, port), timeout=10)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.socket.settimeout(120)

    def receive(self, size):
        data = bytearray(size)
        view = memoryview(data)
        while view:
            n = self.socket.recv_into(view)
            if not n:
                raise RuntimeError('Ethernet service closed the connection')
            view = view[n:]
        return data

    def request(self, operation, address, size, data=b''):
        self.socket.sendall(struct.pack('<4I', operation, address, size, 0))
        if data:
            self.socket.sendall(data)
        error, length = struct.unpack('<2I', self.receive(8))
        if length > 16 << 20:
            raise RuntimeError('Invalid Ethernet response size')
        result = self.receive(length)
        if error:
            raise RuntimeError(f'Ethernet monitor error {error}: {result.hex()}')
        return result

    def write(self, address, data):
        data = np.ascontiguousarray(data).view(np.uint8).reshape(-1).tobytes()
        self.request(1, address, len(data), data)

    def read(self, address, size):
        data = self.request(2, address, size)
        if len(data) != size:
            raise RuntimeError('Short Ethernet read')
        return np.frombuffer(data, dtype=np.uint8).copy()

    def crc32(self, address, size):
        return struct.unpack('<I', self.request(5, address, size))[0]

    def close(self):
        self.socket.close()
