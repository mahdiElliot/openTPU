"""Collect measured simulation and exact-part Vivado results without area scaling."""
from __future__ import annotations

import json
import re
import importlib.metadata
import hashlib
from pathlib import Path

from opentpu import target as T

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build/zu3eg'


def utilization(path):
    text = path.read_text()
    names = {'luts': 'CLB LUTs', 'registers': 'CLB Registers', 'clbs': 'CLB',
             'bram36_tiles': 'Block RAM Tile', 'dsps': 'DSPs'}
    result = {}
    for key, name in names.items():
        match = re.search(r'^\|\s*' + re.escape(name) + r'\s*\|\s*([\d.]+)\s*\|\s*0\s*\|\s*0\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)', text, re.M)
        if not match:
            raise ValueError(f'Missing {name} in {path}')
        used, available, percent = map(float, match.groups())
        result[key] = {'used': used, 'available': available, 'percent': percent,
                       'fits': used <= available}
    return result


def main():
    t = T.load('mys-zu3eg')
    route = OUT / 'vivado_win8'
    timing = (route / 'timing_route.rpt').read_text()
    used = utilization(route / 'utilization_route.rpt')
    part_text = (route / 'utilization_route.rpt').read_text()
    actual_part = re.search(r'^\| Device\s*:\s*(\S+)', part_text, re.M).group(1)
    assert actual_part == t.tech['part']
    slack = re.search(r'\n\s*(-?[\d.]+)\s+(-?[\d.]+)\s+\d+\s+\d+\s+(-?[\d.]+)\s+(-?[\d.]+)\s+\d+\s+\d+\s+(-?[\d.]+)', timing)
    assert slack
    wns, tns, whs, ths, wpws = map(float, slack.groups())
    report = {'board': 'MYIR MYS-ZU3EG-8E2D-EDGE-K2', 'ddr_bytes': 2 << 30,
              'target': t.to_dict(), 'actual_synthesis_part': actual_part,
              'fallback_part_used': False, 'scope': 'Out-of-context accelerator core',
              'resources': used, 'clock_mhz': t.platform['core_mhz'],
              'timing': {'setup_wns_ns': wns, 'setup_tns_ns': tns, 'hold_whs_ns': whs,
                         'hold_ths_ns': ths, 'pulse_width_slack_ns': wpws,
                         'passed': min(wns, whs, wpws) >= 0 and tns == ths == 0},
              'limitations': [
                  'PS-to-PL AXI bridge, board software and a board-level bitstream are not included.',
                  'The RTL DRAM is a behavioral memory model, not a timing model of PS DDR4.',
                  'Physical CLB occupancy must be considered as well as LUT percentage.',
                  'Questa 2021 did not pass the kernel check; native Verilator is the validated path.',
              ]}
    report['model_source'] = json.loads((OUT / 'model_source.json').read_text())
    report['python_packages'] = {name: importlib.metadata.version(name) for name in
                                 ('numpy', 'torch', 'transformers', 'safetensors', 'huggingface_hub', 'verilator')}
    files = {'checkpoint': 'checkpoint_integrity.json',
             'kernel': 'verilator_smoke.json', 'preflight': 'qwen35_int4_h8/preflight.json',
             'isa': 'qwen35_int4_h8/isa_result.json', 'rtl': 'qwen35_int4_h8/verilator_result.json'}
    for stage, name in files.items():
        path = OUT / name
        report[stage] = json.loads(path.read_text()) if path.exists() else {'status': 'pending'}
    report['pending_stages'] = [stage for stage in files if report[stage].get('status') == 'pending']
    report['complete'] = not report['pending_stages']
    report['passed'] = report['complete'] and all(r['fits'] for r in used.values()) and report['timing']['passed'] \
        and report['checkpoint'].get('verified', False) \
        and all(report[stage].get('passed', False) for stage in ('preflight', 'isa', 'rtl'))
    source_files = [ROOT / 'rtl' / name for name in
                    set(T.CORE + T.TECH['generic'] + T.TECH['xilinxus']
                        + ['asic/otpu_asic_top.sv', 'top/otpu_top.sv', 'mem/otpu_dram.sv'])]
    source_files += [ROOT / 'sim/verilator/tb_top.sv', ROOT / 'targets/mys-zu3eg.toml']
    report['source_sha256'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(source_files)}
    (OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'part': actual_part, 'resources': used, 'timing': report['timing'],
                      'isa': report['isa'].get('passed', 'pending'),
                      'rtl': report['rtl'].get('passed', 'pending')}, indent=2))


if __name__ == '__main__':
    main()
