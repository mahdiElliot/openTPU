"""Build the MYIR AR8035 Ethernet monitor using a generated standalone lwIP BSP."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess

from .evaluate import ROOT
from .settings import DEPLOY

DEFAULT_WORKSPACE = DEPLOY / 'ethernet/workspace'
TOOLS = Path('/home/mahdi/vivado/Vitis/2022.2/gnu/aarch64/lin/aarch64-none/bin')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument('--create', action='store_true', help='generate the Vivado handoff and Vitis lwIP platform first')
    options = parser.parse_args()
    work = options.workspace.resolve()
    environment = os.environ.copy()
    environment.pop('LD_PRELOAD', None)
    environment['PATH'] = str(TOOLS) + ':' + environment['PATH']
    if options.create:
        hardware = work.parent / 'hardware_export'
        hardware.mkdir(parents=True, exist_ok=True)
        command = ['/home/mahdi/vivado/Vivado/2022.2/bin/vivado', '-mode', 'batch', '-source',
                   str(ROOT / 'boards/mys-zu3eg/ethernet/export.tcl'), '-tclargs', str(hardware)]
        with (hardware / 'export.log').open('w') as log:
            subprocess.run(command, cwd=hardware, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        command = ['/home/mahdi/vivado/Vitis/2022.2/bin/xsct', '-nodisp', '-log', str(hardware / 'xsct.history'),
                   str(ROOT / 'boards/mys-zu3eg/ethernet/create.tcl'), str(work), str(hardware / 'ethernet.xsa')]
        with (hardware / 'platform.log').open('w') as log:
            subprocess.run(command, cwd=hardware, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    bsp = work / 'myir_runtime/psu_cortexa53_0/standalone_domain/bsp/psu_cortexa53_0'
    export = work / 'myir_runtime/export/myir_runtime/sw/myir_runtime/standalone_domain'
    source = work / 'monitor/src'
    if not (bsp / 'lib/liblwip4.a').is_file():
        raise FileNotFoundError('Generate the standalone MYIR lwIP platform first')
    for original, destination in (('monitor.c', 'echo.c'), ('platform.c', 'platform_zynqmp.c'), ('main.c', 'main.c')):
        shutil.copyfile(ROOT / 'boards/mys-zu3eg/ethernet' / original, source / destination)
    linker = source / 'lscript.ld'
    if not linker.exists():
        # Vitis's echo template creates this at app build, but we use the direct
        # compiler below to avoid its unused TTC timer source and GUI launcher.
        template = ROOT / 'boards/mys-zu3eg/ethernet/lscript.ld'
        shutil.copyfile(template, linker)
    text = linker.read_text().replace('ORIGIN = 0x0, LENGTH = 0x7FF00000',
                                     'ORIGIN = 0x100000, LENGTH = 0x0EF00000')
    text = text.replace('_HEAP_SIZE : 0xA000', '_HEAP_SIZE : 0x800000')
    linker.write_text(text)
    for header in (bsp / 'include/xparameters.h', export / 'bspinclude/include/xparameters.h'):
        text = header.read_text()
        divisors = {'1000MBPS': (12, 1), '100MBPS': (12, 5), '10MBPS': (12, 50)}
        def replace_divisor(match):
            return match[1] + str(divisors[match[2]][int(match[3])])
        text = re.sub(r'(#define\s+\w+_ENET_SLCR_(1000MBPS|100MBPS|10MBPS)_DIV([01])\s+)\d+', replace_divisor, text)
        header.write_text(text)
    phy = bsp / 'libsrc/lwip211_v1_8/src/contrib/ports/xilinx/netif/xemacpsif_physpeed.c'
    text = phy.read_text()
    branch = '''\tif (phy_identity == 0x004d) {
        u16_t state, control;
        /* Preserve PHY reset/AN state when a cable has already negotiated. */
        XEmacPs_PhyRead(xemacpsp, phy_addr, 17, &state);
        if (!(state & 0x0400)) {
            XEmacPs_PhyRead(xemacpsp, phy_addr, 0, &control);
            XEmacPs_PhyWrite(xemacpsp, phy_addr, 0, (control & ~0x0c00) | 0x1200);
            for (unsigned retry = 0; retry < 100; retry++) {
                usleep(100000);
                XEmacPs_PhyRead(xemacpsp, phy_addr, 17, &state);
                if (state & 0x0400) break;
            }
        }
        /* AR8035 RX/TX internal delays for the board's RGMII-ID connection. */
        XEmacPs_PhyWrite(xemacpsp, phy_addr, 0x1d, 0);
        XEmacPs_PhyRead(xemacpsp, phy_addr, 0x1e, &control);
        XEmacPs_PhyWrite(xemacpsp, phy_addr, 0x1e, control | 0x8000);
        XEmacPs_PhyWrite(xemacpsp, phy_addr, 0x1d, 5);
        XEmacPs_PhyRead(xemacpsp, phy_addr, 0x1e, &control);
        XEmacPs_PhyWrite(xemacpsp, phy_addr, 0x1e, control | 0x0100);
        xil_printf("AR8035 state 0x%x at PHY %d\\r\\n", state, phy_addr);
        if (!(state & 0x0400)) return XST_FAILURE;
        return ((state >> 14) & 3) == 2 ? 1000 : ((((state >> 14) & 3) == 1) ? 100 : 10);
    } else if (phy_identity == PHY_TI_IDENTIFIER) {'''
    text, count = re.subn(r'\tif \(phy_identity == 0x004d\) \{.*?\} else if \(phy_identity == PHY_TI_IDENTIFIER\) \{',
                          lambda m: branch, text, count=1, flags=re.S)
    if not count:
        marker = '\tif (phy_identity == PHY_TI_IDENTIFIER) {'
        if marker not in text:
            raise RuntimeError('Unexpected lwIP PHY source')
        text = text.replace(marker, branch, 1)
    phy.write_text(text)
    obj = bsp / 'lib/xemacpsif_physpeed.o'
    subprocess.run(['aarch64-none-elf-gcc', '-O2', '-g', '-c', str(phy), '-o', str(obj),
                    '-I', str(bsp / 'include'), '-I', str(bsp / 'include/lwip')], env=environment, check=True)
    subprocess.run(['aarch64-none-elf-ar', 'r', str(bsp / 'lib/liblwip4.a'), str(obj)], env=environment, check=True)
    shutil.copyfile(bsp / 'lib/liblwip4.a', export / 'bsplib/lib/liblwip4.a')
    config_source = bsp / 'libsrc/emacps_v3_17/src/xemacps_g.c'
    obj = bsp / 'lib/xemacps_g.o'
    subprocess.run(['aarch64-none-elf-gcc', '-O2', '-c', str(config_source), '-o', str(obj),
                    '-I', str(bsp / 'include')], env=environment, check=True)
    subprocess.run(['aarch64-none-elf-ar', 'r', str(bsp / 'lib/libxil.a'), str(obj)], env=environment, check=True)
    shutil.copyfile(bsp / 'lib/libxil.a', export / 'bsplib/lib/libxil.a')
    output = work / 'monitor/Debug'
    output.mkdir(parents=True, exist_ok=True)
    objects = []
    for name in ('main.c', 'echo.c', 'platform_zynqmp.c'):
        obj = output / (Path(name).stem + '.monitor.o')
        subprocess.run(['aarch64-none-elf-gcc', '-O2', '-g', '-Wall', '-c', str(source / name),
                        '-I', str(source), '-I', str(bsp / 'include'), '-o', str(obj)], env=environment, check=True)
        objects.append(str(obj))
    subprocess.run(['aarch64-none-elf-gcc', '-Wl,-T,' + str(linker),
                    '-L', str(bsp / 'lib'), '-o', str(output / 'monitor.elf'), *objects,
                    '-Wl,--start-group', '-lxil', '-llwip4', '-lgcc', '-lc', '-Wl,--end-group'],
                   env=environment, check=True)
    print('ETHERNET_FIRMWARE_BUILT', work / 'monitor/Debug/monitor.elf', flush=True)


if __name__ == '__main__':
    main()
