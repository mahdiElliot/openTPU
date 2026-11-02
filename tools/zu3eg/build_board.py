"""Build a scaled MYIR core with optional independent core and AXI clocks."""
import argparse
import json
import math
import subprocess
from pathlib import Path

from opentpu import target as T

from .settings import AXI_DATA_WIDTH, CORE_MHZ, DEPLOY, MEM_MHZ, ROOT, TARGET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEPLOY / 'hardware')
    parser.add_argument('--vivado', default='/home/mahdi/vivado/Vivado/2022.2/bin/vivado')
    parser.add_argument('--loader', action='store_true', help='build a temporary JTAG-to-AXI DDR loader')
    parser.add_argument('--target', default=TARGET)
    parser.add_argument('--core-mhz', type=float, default=CORE_MHZ)
    parser.add_argument('--mem-mhz', type=float, default=MEM_MHZ,
                        help='HP AXI clock; physical DDR is unchanged')
    parser.add_argument('--axi-data-width', type=int, choices=(128, 256), default=AXI_DATA_WIDTH,
                        help='bridge AXI width; downsizing to the 128-bit HP port runs at the HP clock')
    parser.add_argument('--prepare-only', action='store_true', help='write build parameters without starting Vivado')
    options = parser.parse_args()
    out = options.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    mem_mhz = options.mem_mhz
    if any(not math.isfinite(f) or not 0 < f <= 333 for f in (options.core_mhz, mem_mhz)):
        parser.error('core and HP AXI clocks must be positive and at most 333 MHz')
    target = T.load(options.target)
    cfg = T.config(target)
    if cfg.S != 1 or cfg.D not in (32, 64, 128) or cfg.DSTEP or cfg.STREAM or cfg.SEQ_WC:
        parser.error('this shell supports S=1, D=32/64/128, DSTEP=STREAM=SEQ_WC=false')
    if cfg.DRAM_BYTES != 1 << 31 or target.tech.get('part') != 'xczu3eg-sfvc784-1-i':
        parser.error('this board shell requires the exact ZU3EG part and a logical 2-GiB DDR configuration')
    if target.uarch['MXU_IMPL'] != 2:
        parser.error('this shell uses the systolic MXU_IMPL=2')
    if target.uarch['TMEM_IMPL'] != 3 or target.uarch.get('TMEM_PMP', 0) != 0:
        parser.error('this shell requires single-copy TMEM_IMPL=3 with TMEM_PMP=0')
    files = T.CORE + T.TECH['xilinxus'] + ['asic/otpu_asic_top.sv', 'shell/otpu_ctrl.sv',
            'boards/mys-zu3eg/otpu_ps_dram.sv', 'boards/mys-zu3eg/otpu_zu3eg_accel.sv',
            'boards/mys-zu3eg/otpu_zu3eg_wrapper.v']
    (out / 'sources.tcl').write_text('set rtl_sources {' + ' '.join(files) + '}\n')
    parameters = {k: T.rtl_params(target)[k] for k in (
        'D', 'MCOLS', 'ACT_BLOCKS', 'ACT_ROWS', 'TMEM_WORDS', 'IMEM_WORDS',
        'FIFO_DEPTH', 'LANES', 'WIN', 'MXU_CL', 'MXU_E', 'VPU_CL', 'ULANES',
        'RPB', 'WPB', 'TMEM_NSB', 'TMEM_SBP', 'TMEM_SBH', 'TMEM_RPL', 'TMEM_TDP',
        'TMEM_GST', 'TMEM_PMP', 'TMEM_PRK', 'TMEM_SDM') if k in T.rtl_params(target)}
    parameters.update(AXI_DATA_WIDTH=options.axi_data_width, CORE_KHZ=round(options.core_mhz * 1000), ARCH_HASH=T.arch_hash(cfg))
    (out / 'build_params.tcl').write_text(
        f'set core_mhz {options.core_mhz}\nset mem_mhz {mem_mhz}\nset axi_data_width {options.axi_data_width}\n' +
        'set accelerator_parameters {' + ' '.join(
            f'CONFIG.{k} {int(v)}' for k, v in parameters.items()) + '}\n')
    metadata = target.to_dict() | {
        'target': options.target, 'core_mhz': options.core_mhz, 'mem_mhz': mem_mhz,
        'dual_clock': mem_mhz != options.core_mhz, 'rtl_parameters': parameters,
        'ps_pl0_input_mhz': 100, 'control_base': '0x80000000',
        'ddr_base': '0x10000000', 'ps_boot': 'preserve existing initialized PS DDR; PL-only programming',
        'memory_bridge': f'queued {options.axi_data_width}-bit bridge, 128-bit HP0, up to 8 reads outstanding, independent writes, 32-byte scale-line cache',
        'bridge_axi_bits': options.axi_data_width, 'hp_axi_bits': 128,
        'width_conversion_clock': 'HP AXI' if options.axi_data_width != 128 else 'none'}
    if options.loader:
        metadata = {'purpose': 'temporary DDR bulk loader; no accelerator core',
                    'part': 'xczu3eg-sfvc784-1-i', 'clock_mhz': 112.5,
                    'axi_data_bits': 64, 'write_queue': 16, 'read_queue': 16,
                    'preserve_initialized_ps_ddr': True}
    (out / 'configuration.json').write_text(json.dumps(metadata, indent=2) + '\n')
    if options.prepare_only:
        print('BUILD_PARAMETERS_PREPARED', out)
        return
    script = 'build_loader.tcl' if options.loader else 'build.tcl'
    command = [options.vivado, '-mode', 'batch', '-source', str(ROOT / 'boards/mys-zu3eg' / script),
               '-tclargs', str(ROOT), str(out), '-notrace']
    with (out / 'console.log').open('w') as log:
        result = subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit(f'Board build failed: {out / "console.log"}')


if __name__ == '__main__':
    main()
