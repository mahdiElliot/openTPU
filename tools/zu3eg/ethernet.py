"""TCP transport for the bare-metal MYIR DDR transfer monitor."""
import socket
import struct
import re

import numpy as np
from opentpu import isa as I

from .jtag import DDR_BASE, JtagBackend


class Ethernet:
    def __init__(self, address='192.168.200.2', port=8020, *, timeout=15, read_retries=2,
                 read_idle_timeout=1):
        self.address, self.port = address, port
        self.timeout, self.read_retries = timeout, read_retries
        self.read_idle_timeout = read_idle_timeout
        self.socket = None
        self.connect()

    def connect(self):
        self.socket = socket.create_connection((self.address, self.port), timeout=10)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.socket.settimeout(self.timeout)

    def receive(self, size):
        data = bytearray(size)
        view = memoryview(data)
        while view:
            n = self.socket.recv_into(view)
            if not n:
                raise ConnectionError('Ethernet service closed the connection')
            view = view[n:]
        return data

    def send_request(self, operation, address, size, data=b''):
        self.socket.sendall(struct.pack('<4I', operation, address, size, 0) + data)

    def response(self):
        error, length = struct.unpack('<2I', self.receive(8))
        if length > 16 << 20:
            raise RuntimeError('Invalid Ethernet response size')
        result = self.receive(length)
        if error:
            raise RuntimeError(f'Ethernet monitor error {error}: {result.hex()}')
        return result

    def request(self, operation, address, size, data=b''):
        self.send_request(operation, address, size, data)
        return self.response()

    def write(self, address, data):
        data = np.ascontiguousarray(data).view(np.uint8).reshape(-1).tobytes()
        self.request(1, address, len(data), data)

    def read(self, address, size):
        for attempt in range(self.read_retries + 1):
            try:
                if self.socket is None:
                    self.connect()
                # Healthy logit reads take milliseconds. A stalled data stream
                # must not add 15 seconds per retry to a token's latency.
                self.socket.settimeout(self.read_idle_timeout)
                data = self.request(2, address, size)
                if len(data) != size:
                    raise RuntimeError('Short Ethernet read')
                return np.frombuffer(data, dtype=np.uint8).copy()
            except OSError:
                # A partial reply leaves the TCP byte stream unusable. Discard
                # it and repeat only this idempotent read on a fresh connection.
                self.close()
                if attempt == self.read_retries:
                    raise
                print(f'ETHERNET_READ_RETRY {attempt+1}/{self.read_retries} '
                      f'address={address:#x} bytes={size}', flush=True)
            finally:
                if self.socket is not None:
                    self.socket.settimeout(self.timeout)

    def run(self, address, instructions, arguments):
        self.begin_run(address, instructions, arguments)
        return self.finish_run()

    def begin_run(self, address, instructions, arguments):
        if len(arguments) > 8:
            raise ValueError('The accelerator accepts at most eight arguments')
        payload = struct.pack('<8I', *(arguments + [0] * (8 - len(arguments))))
        self.socket.settimeout(190)
        self.send_request(3, address, instructions, payload)

    def finish_run(self):
        try:
            # A run changes model state: never replay it after a timeout.
            result = self.response()
            lo, hi, count, status = struct.unpack('<4I', result)
            return [(hi << 32) | lo, count, status]
        finally:
            self.socket.settimeout(self.timeout)

    def crc32(self, address, size):
        return struct.unpack('<I', self.request(5, address, size))[0]

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class EthernetDataLink:
    """Ethernet DDR transfers and optional run control; JTAG checks identity."""

    def __init__(self, control, data, *, run_control='jtag'):
        self.control, self.data = control, data
        self.run_control = run_control

    def words(self, address, count=1):
        return self.control.words(address, count)

    def write_word(self, address, value):
        return self.control.write_word(address, value)

    def command(self, code):
        if self.run_control == 'ethernet':
            match = re.fullmatch(r'otpu_run (\d+) (\d+) \{([\d ]*)\}', code)
            if match:
                values = self.data.run(int(match[1]), int(match[2]),
                                       [int(v) for v in match[3].split()])
                return ' '.join(map(str, values))
        return self.control.command(code)

    def write(self, address, data):
        return self.data.write(address, data)

    def start_program(self, address, instructions, arguments):
        return self.data.begin_run(address, instructions, arguments)

    def wait_program(self):
        return self.data.finish_run()

    def read(self, address, nbytes):
        return self.data.read(address, nbytes)

    def close(self):
        try:
            self.data.close()
        finally:
            self.control.close()


class EthernetBackend(JtagBackend):
    """JTAG backend's checked DDR layout, with asynchronous Ethernet run control."""
    on_started = None

    def prepare(self, programs):
        # Engine calls this on its compile worker. Keep the reference with the
        # words so object IDs cannot be reused while a prepared program waits.
        if not hasattr(self, '_prepared'):
            self._prepared = {}
        program = programs[0]
        self._prepared[id(program)] = (program, self.slot_words(program))

    def start(self, programs, args=None):
        if self.reference is not None:
            raise ValueError('Use the separate ISA reference for asynchronous board verification')
        prepared = getattr(self, '_prepared', {}).pop(id(programs[0]), None)
        words = prepared[1] if prepared is not None else self.slot_words(programs[0])
        if len(programs[0])*8 > self.cfg.IMEM_WORDS:
            raise ValueError('Program exceeds IMEM')
        self.link.write(DDR_BASE + self.program_at, words)
        self.link.start_program(self.program_at, len(programs[0]), list(args or []))

    def wait(self):
        cycles, instructions, status = self.link.wait_program()
        self.run_index += 1
        return {'cycles': cycles, 'instructions': [instructions], 'status': status}

    def run(self, programs, args=None):
        self.start(programs, args)
        if self.on_started is not None:
            self.on_started()
        return self.wait()
