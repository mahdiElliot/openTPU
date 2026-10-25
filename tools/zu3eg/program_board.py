"""Program the volatile PL and preserve the board's working DDR initialization."""
import argparse
import json
import re
from dataclasses import asdict
from pathlib import Path

import numpy as np

from opentpu import isa as I
from opentpu import target as T

from .jtag import CONTROL_BASE, Jtag, quote


def program(link, hardware):
    hardware = Path(hardware).resolve()
    configuration = json.loads((hardware / 'configuration.json').read_text())
    cfg = T.config(configuration.get('target', 'mys-zu3eg:d128-m1'))
    if configuration['arch'] != asdict(cfg):
        raise RuntimeError('Target file changed after building; rebuild before programming')
    bit = hardware / 'opentpu_zu3eg.bit'
    ps_init = hardware / 'project/opentpu_zu3eg.gen/sources_1/bd/system/ip/system_ps_0/psu_init.tcl'
    if not bit.is_file() or not ps_init.is_file():
        raise FileNotFoundError('The complete board bitstream and generated PS configuration are required')
    timing = (hardware / 'timing.rpt').read_text()
    summary = re.search(r'WNS\(ns\).*?\n\s*-+.*?\n\s*([-\d.]+)\s+([-\d.]+)\s+\d+\s+\d+\s+([-\d.]+)', timing, re.S)
    pulse = re.search(r'\n\s*([-\d.]+)\s+([-\d.]+)\s+\d+\s+\d+\s+([-\d.]+)\s+([-\d.]+)\s+\d+\s+\d+\s+([-\d.]+)\s+([-\d.]+)\s+\d+\s+\d+', timing)
    if summary is None or pulse is None or any(float(pulse[k]) < 0 for k in (1, 3, 5)):
        raise RuntimeError('Full board implementation must pass setup and hold timing before programming')
    if link.words(0xFD070004)[0] & 7 != 1:
        raise RuntimeError('DDR must already be initialized by the board bootloader')
    clocks = {hex(a): link.words(a)[0] for a in (0xFF5E0020, 0xFF5E0030, 0xFF5E00C0)}
    # Verified on the attached board: 33.333333-MHz reference, RPLL 800 MHz / 8.
    if clocks['0xff5e0030'] != 0x13000 or clocks['0xff5e00c0'] != 0x01010802:
        raise RuntimeError(f'Unexpected PS reference/PL0 clock setup: {clocks}; refusing a guessed PL clock')
    print('Stopping Cortex-A53 #0 for exclusive accelerator DDR use...', flush=True)
    link.command('targets -set -filter {name =~ "Cortex-A53 #0"}; '
                 'if {[catch {stop} message] && $message ne "Already stopped"} {error $message}; '
                 'targets -set -filter {name =~ "PSU"}')
    print('Programming volatile PL bitstream...', flush=True)
    link.command(f'targets -set -filter {{name =~ "PL"}}; fpga -file {quote(bit)}; targets -set -filter {{name =~ "PSU"}}')
    # Apply ONLY PS-PL port widths, isolation and PL-reset controls. In particular,
    # do not call psu_init: its DDR defaults are not a MYIR hardware preset.
    link.command(f'source {quote(ps_init)}; init_ps $psu_afi_config; psu_ps_pl_isolation_removal; psu_ps_pl_reset_config')
    identity = link.words(CONTROL_BASE, 2)
    if identity != [0x4F545055, (cfg.D << 16) | (cfg.MCOLS << 8) | cfg.LANES]:
        raise RuntimeError(f'Unexpected accelerator ID/version: {identity}')
    khz = link.words(CONTROL_BASE + 0x44)[0]
    if khz != round(configuration['core_mhz'] * 1000):
        raise RuntimeError(f'Unexpected core clock metadata: {khz} kHz')
    if 'rtl_parameters' in configuration and link.words(CONTROL_BASE + 0x9c)[0] != T.arch_hash(cfg):
        raise RuntimeError('The programmed architecture hash differs from the selected target')
    link.write_word(CONTROL_BASE + 0x38, 0x1234ABCD)
    if link.words(CONTROL_BASE + 0x38) != [0x1234ABCD]:
        raise RuntimeError('AXI control-register readback failed')
    return {'id': hex(identity[0]), 'version': hex(identity[1]), 'core_khz': khz,
            'target': configuration.get('target', 'mys-zu3eg:d128-m1'),
            'mem_mhz': configuration.get('mem_mhz', configuration['core_mhz']),
            'clock_registers': clocks, 'ddr_status': link.words(0xFD070004)[0],
            'setup_slack_ns': float(summary[1]), 'hold_slack_ns': float(summary[3]),
            'pulse_width_slack_ns': float(pulse[5]),
            'volatile_programming': True, 'cpu0_halted': True, 'register_test_passed': True}


def measure_clock(link, expected_mhz=112.5):
    words = np.asarray(I.assemble([I.loop(1, 0xffffffff), I.nop(), I.halt()]), dtype='<u4')
    logical_address = 0x3f000000
    link.write(0x10000000 + logical_address, words)
    code = '''
mwr 0x80000008 4
mwr 0x80000010 %d
mwr 0x80000014 3
mwr 0x80000008 2
otpu_wait 4 0 30000
mwr 0x80000008 5
set first [lindex [mrd -value 0x80000018] 0]
set start [clock milliseconds]
after 2000
set last [lindex [mrd -value 0x80000018] 0]
set elapsed [expr {[clock milliseconds] - $start}]
mwr 0x80000008 0
list $first $last $elapsed
''' % logical_address
    first, last, milliseconds = map(int, link.command(code).split())
    mhz = (last - first) / (milliseconds * 1000)
    result = {'estimated_mhz_against_host_timer': mhz, 'measurement_ms': milliseconds,
              'expected_mhz': expected_mhz,
              'cycles': last - first, 'within_two_percent': abs(mhz / expected_mhz - 1) < 0.02}
    if not result['within_two_percent']:
        raise RuntimeError(f'Physical clock check failed: {result}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', type=Path, default=Path('build/zu3eg/deployment512_16/hardware'))
    parser.add_argument('--out', type=Path, default=Path('build/zu3eg/deployment512_16/bringup'))
    options = parser.parse_args()
    link = Jtag(options.out)
    try:
        result = program(link, options.hardware)
        result['clock_measurement'] = measure_clock(link, result['core_khz'] / 1000)
        (options.out / 'program_result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result, indent=2), flush=True)
    finally:
        link.close()


if __name__ == '__main__':
    main()
