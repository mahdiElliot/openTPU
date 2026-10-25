"""Read a complete board build's timing, generated clocks and CDC diagnostics."""
import argparse
import csv
import json
import re
from pathlib import Path


def inspect(directory):
    configuration = json.loads((directory / 'configuration.json').read_text())
    text = (directory / 'timing.rpt').read_text()
    match = re.search(r'\n\s*([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+\d+\s+'
                      r'([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+\d+\s+'
                      r'([-\d.]+)\s+([-\d.]+)\s+(\d+)\s+\d+', text)
    if match is None:
        raise ValueError('Timing summary is missing')
    margins = {name: float(match[index]) for name, index in
               (('setup_ns', 1), ('hold_ns', 4), ('pulse_width_ns', 7))}
    clocks_path = directory / 'clocks.csv'
    clocks = list(csv.DictReader(clocks_path.open())) if clocks_path.exists() else []
    requested = {'core': configuration['core_mhz'], 'axi': configuration.get('mem_mhz', configuration['core_mhz'])}
    clock_matches = all(any(abs(float(c['mhz']) / value - 1) < 0.001 for c in clocks)
                        for value in requested.values()) if clocks else False
    cdc = directory / 'cdc.rpt'
    # Known XPM synchronizers are reported as Info/Warning. Unknown crossings
    # require a human review of the named path, not a blanket false-path waiver.
    critical_cdc = re.findall(r'^.*Critical.*$', cdc.read_text(), re.M) if cdc.exists() else []
    result = {'target': configuration.get('target'), 'requested_clocks_mhz': requested,
              'slack': margins, 'clocks': clocks, 'requested_clocks_matched': clock_matches,
              'timing_passed': all(v >= 0 for v in margins.values()) and
                               all(int(match[i]) == 0 for i in (3, 6, 9)),
              'cdc_report': str(cdc), 'cdc_critical_summary_lines': critical_cdc,
              'bitstream_exists': (directory / 'opentpu_zu3eg.bit').exists()}
    result['passed'] = result['timing_passed'] and clock_matches and result['bitstream_exists']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('hardware', type=Path)
    args = parser.parse_args()
    result = inspect(args.hardware.resolve())
    (args.hardware / 'build_check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit('Build timing, generated frequency or bitstream check failed')


if __name__ == '__main__':
    main()
