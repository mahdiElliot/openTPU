"""Verify the complete RTL DRAM and TMEM result from an independent ISA snapshot replay."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from opentpu import isa as I, rtlsim
from opentpu.isasim import Config, Machine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    options = parser.parse_args()
    out = options.directory
    snapshot = json.loads((out / 'rtl_snapshot.json').read_text())
    cfg = Config(**snapshot['config'])
    programs, images = [], []
    for s in range(cfg.S):
        words = np.fromfile(out / f'rtl_program_{s}.bin', dtype='<u4').reshape(-1, 8)
        programs.append([I.Instr.decode(w) for w in words])
        images.append(np.memmap(out / f'rtl_input_{s}.bin', mode='r', dtype=np.uint8))
    # The RTL starts each standalone snapshot with zeroed TMEM. Replay with that
    # same state and also check the original persistent-engine DRAM reference.
    machine = Machine(cfg, programs, images, snapshot['args']).run(max_steps=1 << 40)
    checks = []
    for s, part in enumerate(machine.slices):
        expected = np.memmap(out / f'rtl_expected_{s}.bin', mode='r', dtype=np.uint8)
        dram_differences = 0
        for at in range(0, len(expected), 1 << 20):
            end = min(len(expected), at + (1 << 20))
            dram_differences += int(np.count_nonzero(part.dram[at:end] != expected[at:end]))
        rtl_tmem = rtlsim._read_hex(out / 'verilator_run' / f'tmem_{s}.hex', cfg.TMEM_WORDS)
        checks.append({'slice': s, 'dram_compared_bytes': len(expected),
                       'dram_different_bytes': dram_differences,
                       'tmem_compared_words': cfg.TMEM_WORDS,
                       'tmem_different_words': int(np.count_nonzero(part.tmem != rtl_tmem))})
    result = {'initial_tmem': 'zero in both RTL and ISA replay', 'comparisons': checks,
              'passed': all(c['dram_different_bytes'] == c['tmem_different_words'] == 0 for c in checks)}
    (out / 'snapshot_replay.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)
    assert result['passed'], checks


if __name__ == '__main__':
    main()
