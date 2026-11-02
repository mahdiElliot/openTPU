"""Boot the Ethernet monitor on A53 core 0 without changing DDR or PL."""
import argparse
import math
import os
from pathlib import Path
import subprocess
import time

from .build_ethernet import DEFAULT_WORKSPACE, TOOLS
from .settings import DEPLOY
from .jtag import Jtag, quote


NETWORK_STAGES = {
    0: 'before main (CPU startup, caches, or BSS initialization)',
    1: 'platform/UART/interrupt setup',
    2: 'startup banner or lwIP memory initialization; Ethernet is not initialized yet',
    3: 'GEM3/AR8035 interface and PHY initialization',
    4: 'interface/interrupt activation and TCP listener setup',
    5: 'entering the service loop',
    6: 'Ethernet service loop ready',
}


def wait_for_network(link, address, timeout):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        time.sleep(1)
        state = link.words(address)[0]
        print('ETHERNET_BOOT_STATE', state, NETWORK_STAGES.get(state, 'unknown startup state'), flush=True)
        if state == 6:
            return
    raise RuntimeError(
        f'Ethernet monitor startup timed out after {timeout:g} s: state={state} '
        f'({NETWORK_STAGES.get(state, "no valid startup marker")}); '
        f'marker={address:#x}; debugger log: {link.out / "jtag.log"}. '
        'Ping cannot work before state 6. If the CPU cannot halt or XSDB reports '
        'EDITR errors, power-cycle the board, let its normal firmware initialize '
        'DDR, then program the accelerator and boot the monitor again.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument('--out', type=Path, default=DEPLOY / 'ethernet/boot')
    parser.add_argument('--timeout', type=float, default=30,
                        help='seconds to wait for the Ethernet service loop (default: 30)')
    parser.add_argument('--reset-apu', action='store_true',
                        help='reset all A53 cores before a firmware update; preserve DDR and PL')
    options = parser.parse_args()
    if not math.isfinite(options.timeout) or options.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    elf = options.workspace.resolve() / 'monitor/Debug/monitor.elf'
    env = os.environ.copy()
    env.pop('LD_PRELOAD', None)
    symbols = subprocess.check_output([str(TOOLS / 'aarch64-none-elf-nm'), str(elf)], env=env, text=True)
    symbol = next(line.split()[0] for line in symbols.splitlines() if line.endswith(' otpu_network_state'))
    address = int(symbol, 16)
    link = Jtag(options.out)
    try:
        if link.words(0xFD070004)[0] & 7 != 1:
            raise RuntimeError('PS DDR must already be initialized')
        if link.words(0xFF5E0020)[0] != 0x15A00:
            raise RuntimeError('The Ethernet firmware clock divisors require the verified 1.5-GHz IOPLL setup')
        if link.words(0x80000000)[0] != 0x4f545055:
            raise RuntimeError('Program the accelerator before starting its Ethernet loader')
        print(f'Starting A53 Ethernet monitor: {elf}; preserving PS DDR and accelerator...', flush=True)
        if options.reset_apu:
            # Stop the old monitor's GEM DMA before its buffers are overwritten.
            # The group reset also clears shared APU/debug state that a single
            # processor reset can leave stuck after changing the firmware ELF.
            link.write_word(0xff0e002c, 0xffffffff)
            link.write_word(0xff0e0000, link.words(0xff0e0000)[0] & ~0xc)
            link.command('targets -set -filter {name == "APU"}; '
                         'rst -cores -clear-registers -isa A64')
        # XSDB warns that a processor reset without cleared registers can leave
        # unpredictable state. The standalone monitor is an AArch64 ELF.
        link.command('targets -set -filter {name == "Cortex-A53 #0"}; '
                     'rst -processor -clear-registers -isa A64')
        link.command(f'dow -force {quote(elf)}')
        link.command('con; targets -set -filter {name == "PSU"}')
        wait_for_network(link, address, options.timeout)
    finally:
        link.close()


if __name__ == '__main__':
    main()
