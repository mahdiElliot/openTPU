"""JTAG bring-up transport for the MYIR PS-DDR system (no board OS needed)."""
from __future__ import annotations

import subprocess
import os
import signal
import time
from pathlib import Path

import numpy as np

from opentpu import isa as I
from opentpu import waits as W
from opentpu.isasim import Machine

ROOT = Path(__file__).resolve().parents[2]
DDR_BASE = 0x10000000
CONTROL_BASE = 0x80000000


def program_words(cfg, program):
    """Transport words, including compiler dependency entries when required."""
    words = np.asarray(I.assemble(program), dtype='<u4')
    if words.size > cfg.IMEM_WORDS:
        raise ValueError('Program exceeds IMEM')
    if not getattr(cfg, 'SEQ_WC', False):
        return words
    from .wait_cache import entries as cached_entries
    entries = cached_entries(words, cfg, program)
    return W.loadable(words, entries, cfg.D)


def quote(value):
    value = str(value)
    if any(c in value for c in '{}\n\r'):
        raise ValueError('Tcl paths must not contain braces or newlines')
    return '{' + value + '}'


class Jtag:
    def __init__(self, out, xsdb='/home/mahdi/vivado/Vitis/2022.2/bin/xsdb'):
        self.out = Path(out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.log = (self.out / 'jtag.log').open('a')
        self.process = subprocess.Popen([xsdb, str(ROOT / 'boards/mys-zu3eg/jtag_server.tcl')],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1,
                                        start_new_session=True)
        self.serial = 0
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError('XSDB exited before connecting; see jtag.log')
            self.log.write(line)
            self.log.flush()
            if line.strip() == 'OTPU_READY':
                break

    def command(self, code):
        self.serial += 1
        self.process.stdin.write(f'{self.serial} {code.encode().hex()}\n')
        self.process.stdin.flush()
        prefix = f'OTPU_RESPONSE {self.serial} '
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError('XSDB exited; see jtag.log')
            self.log.write(line)
            self.log.flush()
            if line.startswith(prefix):
                status, _, value = line[len(prefix):].strip().partition(' ')
                result = bytes.fromhex(value).decode()
                if status != '0':
                    raise RuntimeError(result)
                return result

    def words(self, address, count=1):
        return [int(v, 0) for v in self.command(f'mrd -value {address:#x} {count}').split()]

    def write_word(self, address, value):
        self.command(f'mwr {address:#x} {int(value) & 0xffffffff}')

    def upload(self, path, address):
        self.command(f'dow -data {quote(Path(path).resolve())} {address:#x}')

    def write(self, address, data):
        path = self.out / 'write.bin'
        np.ascontiguousarray(data).view(np.uint8).tofile(path)
        self.upload(path, address)

    def read(self, address, nbytes):
        if address % 4 or nbytes % 4:
            raise ValueError('JTAG binary reads must be word aligned')
        path = self.out / 'read.bin'
        self.command(f'mrd -force -bin -file {quote(path)} {address:#x} {nbytes // 4}')
        result = np.fromfile(path, dtype=np.uint8)
        if len(result) != nbytes:
            raise RuntimeError(f'Short JTAG read: {len(result)} / {nbytes}')
        return result

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=10)
        self.log.close()


class JtagBackend:
    args = True

    def __init__(self, cfg, images, link, verify=True, upload=True):
        if cfg.S != 1:
            raise ValueError('Only one slice is supported')
        self.cfg, self.link = cfg, link
        self.reference = Machine(cfg, [[]], images) if verify else None
        self.run_index = 0
        self.checks = []
        self.image_bytes = len(images[0])
        self.program_at = (self.image_bytes + 4095) & ~4095
        if DDR_BASE + self.program_at + W.area(cfg.IMEM_WORDS, cfg.SEQ_WC) >= 0x80000000:
            raise ValueError('Image and program must fit the physical 2-GiB DDR')
        image = np.asarray(images[0], dtype=np.uint8)
        if not upload:
            actual = link.read(DDR_BASE, min(4096, len(image)))
            if not np.array_equal(actual, image[:len(actual)]):
                raise RuntimeError('Preloaded DDR image does not match its first page')
            return
        start = time.monotonic()
        for at in range(0, len(image), 8 << 20):
            end = min(len(image), at + (8 << 20))
            link.write(DDR_BASE + at, image[at:end])
            print(f'JTAG model loaded {end}/{len(image)} bytes ({time.monotonic()-start:.1f} s)', flush=True)

    def write(self, s, addr, data):
        if s != 0:
            raise ValueError('Invalid slice')
        self.link.write(DDR_BASE + addr, data)
        if self.reference is not None:
            values = np.ascontiguousarray(data).view(np.uint8).reshape(-1)
            self.reference.slices[s].dram[addr:addr + len(values)] = values

    def read(self, s, addr, nbytes):
        if s != 0:
            raise ValueError('Invalid slice')
        result = self.link.read(DDR_BASE + addr, nbytes)
        if self.reference is not None:
            expected = self.reference.slices[s].dram[addr:addr + nbytes]
            different = int(np.count_nonzero(result != expected))
            self.checks.append({'address': addr, 'bytes': nbytes, 'different_bytes': different})
            if different:
                raise RuntimeError(f'Board/ISA mismatch: {different} bytes at {addr:#x}')
        return result

    def slot_words(self, program):
        return program_words(self.cfg, program)

    def run(self, programs, args=None):
        words = self.slot_words(programs[0])
        if self.reference is not None:
            self.reference.load(programs, args).run(max_steps=1 << 40)
        self.link.write(DDR_BASE + self.program_at, words)
        arguments = ' '.join(str(int(v) & 0xffffffff) for v in (args or []))
        values = self.link.command(f'otpu_run {self.program_at} {len(programs[0])} {{{arguments}}}')
        cycles, instructions, status = map(int, values.split())
        self.run_index += 1
        return {'cycles': cycles, 'instructions': [instructions], 'status': status}
