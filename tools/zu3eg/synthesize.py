"""Reproduce out-of-context synthesis and routing on the exact MYIR ZU3EG part."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from opentpu import target as T

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--vivado', default='/home/mahdi/vivado/Vivado/2022.2/bin/vivado')
    parser.add_argument('--out', type=Path, default=ROOT / 'build/zu3eg/vivado_win8')
    parser.add_argument('--target', default='mys-zu3eg')
    parser.add_argument('--mhz', type=float)
    options = parser.parse_args()
    t = T.load(options.target)
    mhz = options.mhz or t.platform['core_mhz']
    if mhz <= 0:
        parser.error('--mhz must be positive')
    core = T.rtl_params(t)
    keys = ['D', 'MCOLS', 'ACT_BLOCKS', 'ACT_ROWS', 'TMEM_WORDS', 'IMEM_WORDS',
            'FIFO_DEPTH', 'LANES', 'WIN', 'MXU_IMPL', 'MXU_CL', 'MXU_E', 'VPU_CL',
            'ULANES', 'TMEM_IMPL', 'RPB', 'WPB', 'SEQ_WC', *T.TMEM3_KEYS]
    generics = {k: core[k] for k in keys if k in core}
    generics.update(HAS_DSTEP=0, COLL_GATHER=0)
    split = T.CORE.index('mxu/otpu_mxu.sv') + 1
    files = T.CORE[:split] + T.TECH['xilinxus'] + T.CORE[split:] + ['asic/otpu_asic_top.sv']
    out = options.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    params = out / 'params.tcl'
    params.write_text(f'set target_part {{{t.tech["part"]}}}\n'
                      + f'set core_period_ns {1000 / mhz:.9f}\n'
                      + 'set rtl_sources {' + ' '.join(files) + '}\n'
                      + 'set core_generics {' + ' '.join(f'{k}={v}' for k, v in generics.items()) + '}\n')
    command = [options.vivado, '-mode', 'batch', '-source', str(ROOT / 'tools/zu3eg/synth.tcl'),
               '-log', 'flow.log', '-journal', 'flow.jou']
    (out / 'configuration.json').write_text(json.dumps(t.to_dict() | {'core_generics': generics, 'implementation_mhz': mhz}, indent=2) + '\n')
    print('Vivado part:', t.tech['part'], 'WIN:', generics['WIN'], flush=True)
    with (out / 'console.log').open('w') as log:
        result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT,
                                env=os.environ | {'OTPU_ROOT': str(ROOT), 'OTPU_SYNTH_PARAMS': str(params)})
    if result.returncode:
        raise SystemExit(f'Vivado failed ({result.returncode}); see {out / "flow.log"}')
    print('SYNTHESIS_AND_ROUTE_COMPLETE', out, flush=True)


if __name__ == '__main__':
    main()
