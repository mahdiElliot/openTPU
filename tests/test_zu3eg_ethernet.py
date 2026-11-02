"""Ethernet DDR transfers must preserve bytes and retain JTAG-only control."""
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import asdict

import numpy as np
import pytest

from opentpu import Config, isa as I, target as T
from opentpu.isasim import Machine
from tools.zu3eg.ethernet import EthernetDataLink
from tools.zu3eg.jtag import CONTROL_BASE, DDR_BASE, JtagBackend


def test_ethernet_read_reconnects_and_discards_partial_reply(monkeypatch):
    from tools.zu3eg import ethernet

    class Socket:
        def __init__(self, pieces):
            self.pieces, self.sent, self.closed = pieces, [], False
        def setsockopt(self, *args):
            pass
        def settimeout(self, timeout):
            pass
        def sendall(self, data):
            self.sent.append(data)
        def recv_into(self, view):
            piece = self.pieces.pop(0)
            if isinstance(piece, Exception):
                raise piece
            view[:len(piece)] = piece
            return len(piece)
        def close(self):
            self.closed = True

    prefix = struct.pack('<2I', 0, 4)
    first = Socket([prefix, b'ab', TimeoutError('stalled read')])
    second = Socket([prefix, b'wxyz'])
    connections = iter([first, second])
    monkeypatch.setattr(ethernet.socket, 'create_connection', lambda *a, **kw: next(connections))
    link = ethernet.Ethernet()
    try:
        assert link.read(DDR_BASE, 4).tobytes() == b'wxyz'
        assert first.closed
        assert first.sent == second.sent == [struct.pack('<4I', 2, DDR_BASE, 4, 0)]
    finally:
        link.close()
    assert second.closed


def test_ethernet_write_timeout_is_not_replayed(monkeypatch):
    from tools.zu3eg import ethernet

    connections = []
    class Socket:
        def setsockopt(self, *args):
            pass
        def settimeout(self, timeout):
            pass
        def sendall(self, data):
            raise TimeoutError('write state unknown')
        def close(self):
            pass
    def connect(*args, **kwargs):
        connections.append(Socket())
        return connections[-1]
    monkeypatch.setattr(ethernet.socket, 'create_connection', connect)
    link = ethernet.Ethernet()
    try:
        with pytest.raises(TimeoutError):
            link.write(DDR_BASE, np.zeros(4, np.uint8))
        assert len(connections) == 1
    finally:
        link.close()


def test_ethernet_run_packs_arguments_and_is_not_replayed():
    from tools.zu3eg.ethernet import Ethernet

    class Socket:
        def settimeout(self, seconds):
            self.timeout = seconds

    link = Ethernet.__new__(Ethernet)
    link.socket, link.timeout = Socket(), 15
    requests = []
    def send(op, at, count, payload):
        requests.append((op, at, count, struct.unpack('<8I', payload)))
    link.send_request = send
    link.response = lambda: struct.pack('<4I', 7, 1, 23, 233)
    assert link.run(4096, 23, [0xffffffff]) == [(1 << 32) | 7, 23, 233]
    assert requests == [(3, 4096, 23, (0xffffffff, 0, 0, 0, 0, 0, 0, 0))]
    assert link.socket.timeout == 15

    def failed():
        raise TimeoutError('run result unknown')
    link.response = failed
    with pytest.raises(TimeoutError):
        link.run(4096, 23, [])
    assert len(requests) == 2
    assert link.socket.timeout == 15


def test_ethernet_run_control_bypasses_jtag_commands():
    class Control:
        def command(self, code):
            raise AssertionError('run command must use Ethernet')
    class Data:
        def run(self, address, instructions, arguments):
            assert (address, instructions, arguments) == (4096, 23, [8, 0xffffffff])
            return [101, 23, 233]
    link = EthernetDataLink(Control(), Data(), run_control='ethernet')
    assert link.command('otpu_run 4096 23 {8 4294967295}') == '101 23 233'


def test_async_backend_keeps_program_and_input_bytes_exact():
    from tools.zu3eg.ethernet import EthernetBackend

    cfg = Config(S=1, D=32, MCOLS=1, DRAM_BYTES=1 << 16, TMEM_WORDS=1024, IMEM_WORDS=64)
    program = [I.ld(64, 0, 4), I.st(128, 0, 4), I.halt()]
    events = []
    class Link(MemoryData):
        def start_program(self, address, instructions, arguments):
            words = np.asarray(I.assemble(program), dtype='<u4').view(np.uint8)
            assert np.array_equal(self.memory[address:address + len(words)], words)
            assert instructions == 3 and arguments == [7]
            events.append('start')
        def wait_program(self):
            events.append('wait')
            self.memory[:] = Machine(cfg, [program], [self.memory.copy()]).run().slices[0].dram
            return [101, 3, 233]
    link = Link()
    backend = EthernetBackend(cfg, [np.zeros(512, np.uint8)], link, verify=False)
    inputs = np.array([0x01020304, 0x7fc01234, 0x80000000, 0xffffffff], dtype='<u4')
    backend.write(0, 64, inputs)
    backend.prepare([program])
    backend.on_started = lambda: events.append('compile next')
    assert backend.run([program], args=[7])['cycles'] == 101
    assert events == ['start', 'compile next', 'wait']
    assert np.array_equal(backend.read(0, 128, inputs.nbytes), inputs.view(np.uint8))
    assert not backend._prepared


class MemoryData:
    def __init__(self, size=1 << 16):
        self.memory = np.zeros(size, np.uint8)
        self.calls = []
        self.closed = False

    def write(self, address, data):
        data = np.ascontiguousarray(data).view(np.uint8).reshape(-1)
        offset = address - DDR_BASE
        self.memory[offset:offset + len(data)] = data
        self.calls.append(('write', address, len(data)))

    def read(self, address, size):
        offset = address - DDR_BASE
        self.calls.append(('read', address, size))
        return self.memory[offset:offset + size].copy()

    def close(self):
        self.closed = True


def test_inference_inputs_program_and_outputs_use_ethernet():
    cfg = Config(S=1, D=32, MCOLS=1, DRAM_BYTES=1 << 16, TMEM_WORDS=1024, IMEM_WORDS=64)
    data = MemoryData(cfg.DRAM_BYTES)
    program = [I.ld(64, 0, 4), I.st(128, 0, 4), I.halt()]

    class Control:
        closed = False

        def command(self, code):
            # No read/write data API exists on this control object: the backend
            # must put both inputs and the assembled program through Ethernet.
            assert code.startswith('otpu_run 4096 3 ')
            words = np.asarray(I.assemble(program), dtype='<u4').view(np.uint8)
            assert np.array_equal(data.memory[4096:4096 + len(words)], words)
            machine = Machine(cfg, [program], [data.memory.copy()]).run()
            data.memory[:] = machine.slices[0].dram
            return '101 3 9'

        def words(self, address, count=1):
            assert address == CONTROL_BASE
            return [0x4f545055][:count]

        def close(self):
            self.closed = True

    control = Control()
    link = EthernetDataLink(control, data)
    backend = JtagBackend(cfg, [np.zeros(512, np.uint8)], link, verify=False)
    inputs = np.array([0x01020304, 0x7fc01234, 0x80000000, 0xffffffff], dtype='<u4')
    backend.write(0, 64, inputs)
    assert backend.run([program])['cycles'] == 101
    assert np.array_equal(backend.read(0, 128, inputs.nbytes), inputs.view(np.uint8))
    assert ('write', DDR_BASE + 64, inputs.nbytes) in data.calls
    assert ('write', DDR_BASE + 4096, len(I.assemble(program)) * 4) in data.calls
    assert ('read', DDR_BASE + 128, inputs.nbytes) in data.calls
    assert link.words(CONTROL_BASE) == [0x4f545055]
    link.close()
    assert data.closed and control.closed


def test_ethernet_data_link_always_closes_control():
    class Control:
        closed = False
        def close(self):
            self.closed = True

    class BrokenData:
        def close(self):
            raise RuntimeError('data close failed')

    control = Control()
    with pytest.raises(RuntimeError, match='data close failed'):
        EthernetDataLink(control, BrokenData()).close()
    assert control.closed


@pytest.mark.parametrize('keep_running', [False, True])
@pytest.mark.parametrize('verify_only', [False, True])
def test_model_loader_keeps_monitor_alive_only_when_requested(tmp_path, monkeypatch, keep_running, verify_only):
    from tools.zu3eg import load_ethernet as loader

    cfg = T.config('mys-zu3eg:d128-m1')
    image = tmp_path / 'model_image.bin'
    payload = np.arange(512, dtype=np.uint8)
    image.write_bytes(payload.tobytes())
    image.with_suffix('.json').write_text(json.dumps({
        'config': asdict(cfg), 'target': 'mys-zu3eg:d128-m1',
        'sha256': hashlib.sha256(payload).hexdigest()}))
    hw = tmp_path / 'hardware'
    hw.mkdir()
    (hw / 'configuration.json').write_text(json.dumps({'arch': asdict(cfg), 'core_mhz': 177.5}))
    (hw / 'opentpu_zu3eg.bit').write_bytes(b'test hardware identity')

    class Monitor(MemoryData):
        def read(self, address, size):
            registers = {CONTROL_BASE: [0x4f545055, (cfg.D << 16) | (cfg.MCOLS << 8) | cfg.LANES],
                         CONTROL_BASE + 0xc: [8], CONTROL_BASE + 0x44: [177500]}
            if address in registers:
                return np.frombuffer(struct.pack('<' + 'I' * len(registers[address]),
                                                 *registers[address]), dtype=np.uint8).copy()
            return super().read(address, size)

        def crc32(self, address, size):
            return zlib.crc32(self.read(address, size))

    monitor = Monitor()
    if verify_only:
        monitor.memory[:len(payload)] = payload
    stops = []

    class Debug:
        def __init__(self, out):
            pass
        def command(self, code):
            stops.append(code)
        def close(self):
            pass

    monkeypatch.setattr(loader, 'Ethernet', lambda address: monitor)
    monkeypatch.setattr(loader, 'Jtag', Debug)
    argv = ['load_ethernet', '--image', str(image), '--hardware', str(hw)]
    if keep_running:
        argv.append('--keep-running')
    if verify_only:
        argv.append('--verify-only')
    monkeypatch.setattr(sys, 'argv', argv)
    loader.main()
    result = json.loads((tmp_path / 'model_loaded.json').read_text())
    assert result['pristine'] and result['full_readback_passed']
    assert result['cpu0_halted'] == (not keep_running)
    assert bool(stops) == (not keep_running)
    assert monitor.closed
    if verify_only:
        assert not any(call[0] == 'write' for call in monitor.calls)


def test_boot_wait_reports_stage_and_debug_recovery(tmp_path, monkeypatch):
    from tools.zu3eg import boot_ethernet as boot

    clock = [0.0]
    monkeypatch.setattr(boot.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(boot.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    class Stalled:
        out = tmp_path
        def words(self, address):
            assert address == 0x200038
            return [2]

    with pytest.raises(RuntimeError) as error:
        boot.wait_for_network(Stalled(), 0x200038, 2)
    message = str(error.value)
    assert 'state=2' in message and 'lwIP memory initialization' in message
    assert 'power-cycle' in message and str(tmp_path / 'jtag.log') in message


@pytest.mark.parametrize('reset_apu', [False, True])
def test_boot_clears_registers_selects_a64_and_closes_link(tmp_path, monkeypatch, reset_apu):
    from tools.zu3eg import boot_ethernet as boot

    commands = []
    class Debug:
        out = tmp_path
        closed = False
        def __init__(self, out):
            pass
        def words(self, address):
            return [{0xfd070004: 1, 0xff5e0020: 0x15a00,
                     0x80000000: 0x4f545055, 0x200038: 6, 0xff0e0000: 28}[address]]
        def write_word(self, address, value):
            commands.append(f'mwr {address:#x} {value:#x}')
        def command(self, command):
            commands.append(command)
        def close(self):
            self.closed = True

    debug = Debug(tmp_path)
    monkeypatch.setattr(boot, 'Jtag', lambda out: debug)
    monkeypatch.setattr(boot.subprocess, 'check_output',
                        lambda *args, **kwargs: '0000000000200038 B otpu_network_state\n')
    monkeypatch.setattr(boot.time, 'sleep', lambda seconds: None)
    argv = ['boot_ethernet', '--workspace', str(tmp_path)]
    if reset_apu:
        argv.append('--reset-apu')
    monkeypatch.setattr(sys, 'argv', argv)
    boot.main()
    assert any('rst -processor -clear-registers -isa A64' in command for command in commands)
    assert not any('psu_init' in command or 'rst -system' in command for command in commands)
    assert debug.closed
    assert any('rst -cores' in command for command in commands) == reset_apu
