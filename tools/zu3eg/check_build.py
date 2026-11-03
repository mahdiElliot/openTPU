"""Read a complete board build's timing, generated clocks and CDC diagnostics."""
import argparse
import csv
import json
import re
from pathlib import Path


def memory_path(directory, configuration):
    """Check the implemented BD, not only the width requested in metadata."""
    width = configuration.get('bridge_axi_bits', 128)
    if width == 128:
        return {'passed': True, 'bridge_bits': 128, 'hp_bits': 128}
    bd = directory / 'project/opentpu_zu3eg.srcs/sources_1/bd/system/system.bd'
    result = {'passed': False, 'bridge_bits': width, 'hp_bits': 128, 'errors': []}
    if not bd.is_file():
        result['errors'].append('Generated block design is missing')
        return result
    design = json.loads(bd.read_text())['design']
    components = design['components']
    errors = result['errors']
    if 'write_depth' in configuration:
        interface = components.get('accelerator', {}).get('interface_ports', {}).get('M_AXI', {})
        credits = interface.get('parameters', {})
        for key, wanted in [('NUM_READ_OUTSTANDING', configuration['read_depth']),
                            ('NUM_WRITE_OUTSTANDING', configuration['write_depth'])]:
            if int(credits.get(key, {}).get('value', '0')) < wanted:
                errors.append(f'AXI metadata {key} understates the bridge credits')

    def parameter(component, name):
        return components.get(component, {}).get('parameters', {}).get(name, {}).get('value')

    def connected(left, right, interfaces=False):
        key, ports = ('interface_nets', 'interface_ports') if interfaces else ('nets', 'ports')
        return any(left in net[ports] and right in net[ports] for net in design[key].values())

    if (parameter('memory_width_converter', 'SI_DATA_WIDTH') != str(width) or
        parameter('memory_width_converter', 'MI_DATA_WIDTH') != '128'):
        errors.append('Downsizer widths do not match the bridge and HP port')
    dual = configuration.get('mem_mhz', configuration['core_mhz']) != configuration['core_mhz']
    source = 'accelerator/M_AXI'
    if dual:
        if parameter('memory_clock_converter', 'DATA_WIDTH') != str(width):
            errors.append('Clock crossing is not wide enough')
        if not connected(source, 'memory_clock_converter/S_AXI', True):
            errors.append('The bridge must connect to the wide clock crossing first')
        source = 'memory_clock_converter/M_AXI'
    if not connected(source, 'memory_width_converter/S_AXI', True):
        errors.append('Downsizing must follow the wide crossing')
    if not connected('memory_width_converter/M_AXI', 'ps/S_AXI_HP0_FPD', True):
        errors.append('Downsizer output does not connect to HP0')
    clock = ('memory_clocks/clk_out1' if configuration.get('separate_mmcm') else
             'clocks/clk_out2') if dual else 'clocks/clk_out1'
    if not connected(clock, 'memory_width_converter/s_axi_aclk'):
        errors.append('Downsizer is not clocked in the HP domain')
    if not connected(clock, 'ps/saxihp0_fpd_aclk'):
        errors.append('HP0 uses the wrong clock')
    result['passed'] = not errors
    return result


def input_jitter_check(directory, configuration):
    required = configuration.get('input_jitter_ns')
    report = directory / 'clock_root.rpt'
    if not report.exists():
        report = directory / 'clock_root_100ps.rpt'
    match = (re.search(r'^INPUT_JITTER\s+\S+\s+\S+\s+([\d.]+)', report.read_text(), re.M)
             if report.exists() else None)
    actual = float(match[1]) if match else None
    return {'checked': required is not None, 'required_ns': required, 'reported_ns': actual,
            'passed': required is None or (actual is not None and actual >= required)}


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
    methodology = directory / 'methodology.rpt'
    critical_methodology = (re.findall(r'^\|\s*(\S+)\s*\|\s*(?:Critical Warning|Error)\s*\|',
                                      methodology.read_text(), re.M)
                            if methodology.exists() else [])
    result = {'target': configuration.get('target'), 'requested_clocks_mhz': requested,
              'slack': margins, 'clocks': clocks, 'requested_clocks_matched': clock_matches,
              'timing_passed': all(v >= 0 for v in margins.values()) and
                               all(int(match[i]) == 0 for i in (3, 6, 9)),
              'cdc_report': str(cdc), 'cdc_critical_summary_lines': critical_cdc,
              'methodology_report': str(methodology) if methodology.exists() else None,
              'methodology_critical_rules': critical_methodology,
              'bitstream_exists': (directory / 'opentpu_zu3eg.bit').exists()}
    result['memory_path'] = memory_path(directory, configuration)
    result['input_jitter'] = input_jitter_check(directory, configuration)
    result['passed'] = (result['timing_passed'] and clock_matches and
                        result['bitstream_exists'] and result['memory_path']['passed'] and
                        not critical_methodology and result['input_jitter']['passed'])
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
