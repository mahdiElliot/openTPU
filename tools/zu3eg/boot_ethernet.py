"""Boot the Ethernet monitor on A53 core 0 without changing DDR or PL."""
import argparse
import os
from pathlib import Path
import subprocess
import time

from .build_ethernet import DEFAULT_WORKSPACE, TOOLS
from .evaluate import ROOT
from .jtag import Jtag, quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument('--out', type=Path, default=ROOT / 'build/zu3eg/ethernet_repair/boot')
    options = parser.parse_args()
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
        print('Starting A53 Ethernet monitor; preserving PS DDR and accelerator...', flush=True)
        link.command('targets -set -filter {name == "Cortex-A53 #0"}; rst -processor')
        link.command(f'dow -force {quote(elf)}')
        link.command('con; targets -set -filter {name == "PSU"}')
        for _ in range(15):
            time.sleep(1)
            state = link.words(address)[0]
            print('ETHERNET_BOOT_STATE', state, flush=True)
            if state == 6:
                break
        else:
            raise RuntimeError('Ethernet monitor did not reach the polling loop')
    finally:
        link.close()


if __name__ == '__main__':
    main()
