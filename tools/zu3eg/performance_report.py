"""Compare measured 128-PE candidates at a fixed core clock, excluding board I/O."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build/zu3eg/perf128'


def resources(path):
    text = path.read_text()
    result = {}
    for key, name in [('luts', 'CLB LUTs'), ('ffs', 'CLB Registers'), ('clbs', 'CLB'),
                      ('bram36', 'Block RAM Tile'), ('dsps', 'DSPs')]:
        match = re.search(r'^\|\s*' + re.escape(name) + r'\*?\s*\|\s*([\d.]+)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)', text, re.M)
        if match:
            used, available, percent = map(float, match.groups())
            result[key] = dict(used=used, available=available, percent=percent)
    return result


def timing(path):
    text = path.read_text()
    match = re.search(r'\n\s*(-?[\d.]+)\s+(-?[\d.]+)\s+\d+\s+\d+\s+(-?[\d.]+)\s+(-?[\d.]+)\s+\d+\s+\d+\s+(-?[\d.]+)', text)
    if not match:
        raise ValueError(f'No timing summary in {path}')
    setup, setup_total, hold, hold_total, pulse = map(float, match.groups())
    return dict(setup_slack_ns=setup, hold_slack_ns=hold, pulse_slack_ns=pulse,
                passed=min(setup, hold, pulse) >= 0 and setup_total == hold_total == 0)


def main():
    rows = []
    for directory in sorted(OUT.iterdir()):
        if not directory.is_dir():
            continue
        model, physical = directory / 'model', directory / 'vivado'
        if not (model / 'preflight.json').exists():
            continue
        preflight = json.loads((model / 'preflight.json').read_text())
        row = {'candidate': directory.name, 'config': preflight['config'],
               'uarch': preflight['uarch'], 'clock_mhz': 112.5,
               'pe_count': preflight['config']['D'] * preflight['config']['MCOLS']}
        assert row['pe_count'] == 128
        if (physical / 'utilization_route.rpt').exists():
            row['resources'] = resources(physical / 'utilization_route.rpt')
            row['timing'] = timing(physical / 'timing_route.rpt')
            implementation = json.loads((physical / 'configuration.json').read_text())
            row['part'] = implementation['tech']['part']
            row['clock_mhz'] = implementation['implementation_mhz']
            assert row['clock_mhz'] == 112.5
            assert implementation['arch'] == row['config']
            generics = implementation['core_generics']
            for key, value in row['uarch'].items():
                if key in generics:
                    assert generics[key] == value, f'{directory.name}: {key} differs between RTL and synthesis'
        elif (physical / 'utilization_synth.rpt').exists():
            row['synthesis_resources'] = resources(physical / 'utilization_synth.rpt')
        if (model / 'verilator_result.json').exists():
            rtl = json.loads((model / 'verilator_result.json').read_text())
            assert rtl['config'] == row['config'] and rtl['uarch'] == row['uarch']
            row.update(cycles_per_token=rtl['stats']['cycles'],
                       simulated_core_tokens_per_second=112_500_000 / rtl['stats']['cycles'],
                       rtl_passed=rtl['passed'], comparisons=rtl['comparisons'],
                       context_position=rtl['snapshot_position'])
        if (model / 'isa_result.json').exists():
            isa = json.loads((model / 'isa_result.json').read_text())
            row.update(prompt=isa['prompt'], generated_text=isa['generated_text'],
                       image_bytes=isa['image_bytes'])
        if (model / 'snapshot_replay.json').exists():
            row['snapshot_replay'] = json.loads((model / 'snapshot_replay.json').read_text())
        row['eligible'] = (row.get('rtl_passed', False) and row.get('timing', {}).get('passed', False)
                           and row.get('snapshot_replay', {}).get('passed', False))
        if row['eligible']:
            row['eligible'] = all(r['used'] <= r['available'] for r in row['resources'].values())
        rows.append(row)
    eligible = [r for r in rows if r['eligible']]
    report = {'clock_mhz': 112.5, 'pe_count': 128, 'weight_format': 'int4', 'head_format': 'int8',
              'scope': 'One full decode token after an ISA-prefilled short prompt; ideal behavioral memory',
              'board_end_to_end_tokens_per_second': None, 'candidates': rows,
              'best_measured_candidate': max(eligible, key=lambda r: r['simulated_core_tokens_per_second'])['candidate'] if eligible else None}
    report['all_candidates_rtl_passed'] = bool(rows) and all(r.get('rtl_passed', False) for r in rows)
    report['all_candidates_scratchpad_passed'] = bool(rows) and all(r.get('snapshot_replay', {}).get('passed', False) for r in rows)
    report['physical_reports_complete'] = bool(rows) and all('timing' in r for r in rows)
    (OUT / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
