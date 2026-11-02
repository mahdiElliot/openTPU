"""Functionally validate the real Xilinx 256-bit CDC and 256-to-128 downsizer."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from .settings import DEPLOY, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEPLOY / 'memory_path_check')
    parser.add_argument('--axi-data-width', type=int, choices=(128, 256), default=256,
                        help='128 is the comparison baseline; 256 exercises the wide path')
    parser.add_argument('--read-depth', type=int, choices=(8, 16), default=8)
    parser.add_argument('--read-chunks', type=int, choices=(1, 2, 4, 8), default=1)
    parser.add_argument('--vivado', type=Path, default=Path('/home/mahdi/vivado/Vivado/2022.2/bin/vivado'))
    options = parser.parse_args()
    out, vivado = options.out.resolve(), options.vivado.resolve()
    out.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.pop('LD_PRELOAD', None)
    environment['PATH'] = str(vivado.parent) + ':' + environment['PATH']
    with (out / 'generate.log').open('w') as log:
        subprocess.run([str(vivado), '-mode', 'batch', '-notrace', '-source',
                        str(ROOT / 'boards/mys-zu3eg/check_memory_path.tcl'),
                        '-tclargs', str(ROOT), str(out), str(options.axi_data_width),
                        str(options.read_depth), str(options.read_chunks)], cwd=out, env=environment,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    simulation = out / 'project/memory_path.sim/sim_1/behav/xsim'
    # This converter-only test needs no encrypted device primitives. Some local
    # installations ship an empty secureip database, but generated scripts add it
    # unconditionally. Skip only that unused library when its database is empty.
    elaborate = (simulation / 'elaborate.sh').read_text()
    secureip = vivado.parent.parent / 'data/xsim/verilog/secureip/secureip.sdbx'
    skipped_empty_secureip = secureip.exists() and secureip.stat().st_size == 0
    if skipped_empty_secureip:
        elaborate = elaborate.replace(' -L secureip', '')
    (simulation / 'elaborate_memory.sh').write_text(elaborate)
    (simulation / 'validate.tcl').write_text('run all\nquit\n')
    commands = [('compile', ['bash', 'compile.sh']),
                ('elaborate', ['bash', 'elaborate_memory.sh']),
                ('simulate', [str(vivado.parent / 'xsim'), 'tb_ps_dram_behav',
                              '-tclbatch', 'validate.tcl', '-log', 'memory_path_sim.log'])]
    for name, command in commands:
        with (out / f'{name}.log').open('w') as log:
            subprocess.run(command, cwd=simulation, env=environment,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
    text = (out / 'simulate.log').read_text()
    match = re.search(r'PS_DRAM_PIPELINE depth=(\d+) cycles=(\d+) max_reads=(\d+) width=(\d+)', text)
    if 'PS_DRAM_PROTOCOL_PASS' not in text or not match:
        raise RuntimeError(f'Converter-path simulation failed: {out / "simulate.log"}')
    result = {'passed': True, 'core_mhz': 177.5, 'hp_mhz': 332.8125,
              'bridge_axi_bits': options.axi_data_width, 'crossing_axi_bits': options.axi_data_width,
              'hp_axi_bits': 128,
              'read_depth': int(match[1]), 'stream_memory_cycles': int(match[2]),
              'read_chunks': options.read_chunks,
              'max_hp_reads_pending': int(match[3]),
              'skipped_unused_empty_secureip_library': skipped_empty_secureip,
              'scope': 'functional vendor-IP simulation; CDC timing is checked by Vivado implementation'}
    if result['max_hp_reads_pending'] < 2:
        raise RuntimeError('The HP path did not overlap read transactions')
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
