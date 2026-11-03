"""Build a scaled MYIR core with optional independent core and AXI clocks."""
import argparse
import json
import math
import subprocess
from pathlib import Path

from opentpu import target as T

from .settings import AXI_DATA_WIDTH, CORE_MHZ, DEPLOY, HP_PORTS, MEM_MHZ, ROOT, TARGET


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
    parser.add_argument('--hp-ports', type=int, choices=(1, 2), default=HP_PORTS,
                        help='2: HP0 bulk + HP1 metadata (default); 1: legacy HP0; HP2/HP3 disabled')
    parser.add_argument('--prepare-only', action='store_true', help='write build parameters without starting Vivado')
    parser.add_argument('--memory-stats', action='store_true', help='add per-run memory demand-stall counters')
    parser.add_argument('--scheduler-pipeline', action='store_true',
                        help='register scheduler ranges before footprint compaction')
    parser.add_argument('--separate-mmcm', action='store_true',
                        help='generate core and HP clocks with independent MMCMs')
    parser.add_argument('--strategy', default='default', choices=(
        'default', 'Performance_Explore', 'Congestion_SpreadLogic_high',
        'Performance_ExplorePostRoutePhysOpt'), help='Vivado implementation strategy')
    parser.add_argument('--read-depth', type=int, choices=(8, 16, 32), default=8)
    parser.add_argument('--scale-cache-lines', type=int, choices=(1, 2, 4, 8), default=1,
                        help='adjacent 32-byte scale lines per cache fill')
    parser.add_argument('--write-depth', type=int, choices=(1, 2, 4, 8), default=1,
                        help='ordered AXI write-response credits; same-port reads remain fenced')
    parser.add_argument('--read-chunks', type=int, choices=(1, 2, 4, 8), default=1,
                        help='maximum contiguous D-byte reads combined into one burst')
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
    if 32 * options.scale_cache_lines // (options.axi_data_width//8) > 8:
        parser.error('eight scale-cache lines require the 256-bit AXI interface')
    files = T.CORE + T.TECH['xilinxus'] + ['asic/otpu_asic_top.sv', 'shell/otpu_ctrl.sv',
            'boards/mys-zu3eg/otpu_ps_dram.sv', 'boards/mys-zu3eg/otpu_ps_dram_multi.sv',
            'boards/mys-zu3eg/otpu_zu3eg_accel.sv',
            'boards/mys-zu3eg/otpu_zu3eg_wrapper.v']
    (out / 'sources.tcl').write_text('set rtl_sources {' + ' '.join(files) + '}\n')
    parameters = {k: T.rtl_params(target)[k] for k in (
        'D', 'MCOLS', 'ACT_BLOCKS', 'ACT_ROWS', 'TMEM_WORDS', 'IMEM_WORDS',
        'FIFO_DEPTH', 'LANES', 'WIN', 'MXU_CL', 'MXU_E', 'VPU_CL', 'ULANES',
        'RPB', 'WPB', 'TMEM_NSB', 'TMEM_SBP', 'TMEM_SBH', 'TMEM_RPL', 'TMEM_TDP',
        'TMEM_GST', 'TMEM_PMP', 'TMEM_PRK', 'TMEM_SDM') if k in T.rtl_params(target)}
    parameters.update(AXI_DATA_WIDTH=options.axi_data_width, CORE_KHZ=round(options.core_mhz * 1000), ARCH_HASH=T.arch_hash(cfg))
    parameters['HP_PORTS'] = options.hp_ports
    parameters['MEMORY_STATS'] = int(options.memory_stats)
    parameters['SEQ_FP_PIPE'] = int(options.scheduler_pipeline)
    parameters['BRIDGE_A_CACHE_LINES'] = options.scale_cache_lines
    parameters['BRIDGE_WRITE_DEPTH'] = options.write_depth
    parameters.update(BRIDGE_READ_DEPTH=options.read_depth, BRIDGE_B_DEPTH=options.read_depth,
                      BRIDGE_READ_CHUNKS=options.read_chunks)
    (out / 'build_params.tcl').write_text(
        f'set core_mhz {options.core_mhz}\nset mem_mhz {mem_mhz}\nset axi_data_width {options.axi_data_width}\nset hp_ports {options.hp_ports}\n' +
        f'set separate_mmcm {int(options.separate_mmcm)}\nset implementation_strategy {{{options.strategy}}}\n' +
        'set accelerator_parameters {' + ' '.join(
            f'CONFIG.{k} {int(v)}' for k, v in parameters.items()) + '}\n')
    metadata = target.to_dict() | {
        'target': options.target, 'core_mhz': options.core_mhz, 'mem_mhz': mem_mhz,
        'dual_clock': mem_mhz != options.core_mhz, 'rtl_parameters': parameters,
        'memory_stats': options.memory_stats,
        'scheduler_footprint_pipeline': options.scheduler_pipeline,
        'separate_mmcm': options.separate_mmcm,
        'implementation_strategy': options.strategy,
        'ps_pl0_input_mhz': 100, 'input_jitter_ns': 0.100, 'control_base': '0x80000000',
        'ddr_base': '0x10000000', 'ps_boot': 'preserve existing initialized PS DDR; PL-only programming',
        'memory_bridge': f'queued {options.axi_data_width}-bit bridge, 128-bit HP ports {[0, 1][:options.hp_ports]}, up to {options.read_depth} reads outstanding, up to {options.read_chunks} chunks per read burst, independent writes, {32*options.scale_cache_lines}-byte scale cache',
        'read_depth': options.read_depth, 'read_chunks': options.read_chunks,
        'hp_ports': [0, 1][:options.hp_ports],
        'scale_cache_lines': options.scale_cache_lines,
        'write_depth': options.write_depth,
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
