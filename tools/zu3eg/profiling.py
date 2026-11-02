"""Optional host/link timings for physical-board inference steps."""
import time

import numpy as np


class ProfiledLink:
    def __init__(self, link):
        self.link = link
        self.samples = []

    def _call(self, name, method, *args, nbytes=0):
        begin = time.perf_counter()
        try:
            return method(*args)
        finally:
            self.samples.append((name, time.perf_counter() - begin, nbytes))

    def words(self, address, count=1):
        return self._call('control', self.link.words, address, count)

    def write_word(self, address, value):
        return self._call('control', self.link.write_word, address, value)

    def command(self, code):
        kind = 'run_control' if code.startswith('otpu_run ') else 'control'
        return self._call(kind, self.link.command, code)

    def start_program(self, address, instructions, arguments):
        return self._call('run_start', self.link.start_program, address, instructions, arguments)

    def wait_program(self):
        return self._call('run_control', self.link.wait_program)

    def write(self, address, data):
        return self._call('write', self.link.write, address, data, nbytes=np.asarray(data).nbytes)

    def read(self, address, nbytes):
        return self._call('read', self.link.read, address, nbytes, nbytes=nbytes)

    def reset(self):
        self.samples.clear()

    def take(self, wall_seconds, cycles, frequency):
        totals = {}
        for kind, seconds, nbytes in self.samples:
            for key, value in ((kind + '_seconds', seconds), (kind + '_bytes', nbytes),
                               (kind + '_calls', 1)):
                totals[key] = totals.get(key, 0) + value
        totals['fpga_seconds'] = cycles / frequency
        totals['control_overhead_seconds'] = max(
            0.0, totals.get('run_start_seconds', 0) + totals.get('run_control_seconds', 0) - totals['fpga_seconds'])
        totals['host_seconds'] = max(0.0, wall_seconds - sum(s[1] for s in self.samples))
        self.reset()
        return totals

    def close(self):
        return self.link.close()
