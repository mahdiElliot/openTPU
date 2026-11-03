"""Exercise queued PS reads, independent writes and ISA-exact board kernels."""
import re

import pytest

from opentpu import rtlsim


@pytest.mark.parametrize("depth", [32, 64, 128])
@pytest.mark.parametrize("width", [128, 256])
def test_ps_bridge_protocol(depth, width):
    _run_bridge(depth, width=width)


def _run_bridge(depth, read_depth=8, width=256, read_chunks=1):
    flags = list(rtlsim.VFLAGS)
    try:
        rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j') + 1] = '2'
        exe = rtlsim.build('tb_ps_dram', [
            rtlsim.RTL / 'boards/mys-zu3eg/otpu_ps_dram.sv',
            rtlsim.TB / 'otpu_ps_axi_mem.sv', rtlsim.TB / 'otpu_ps_test_mem.sv',
            rtlsim.TB / 'tb_ps_dram.sv'], {'D': depth, 'READ_DEPTH': read_depth,
                                       'READ_CHUNKS': read_chunks, 'AXI_DATA_WIDTH': width})
        result = rtlsim.run_sim([str(exe)], timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PS_DRAM_PROTOCOL_PASS' in result.stdout
        match = re.search(r'PS_DRAM_PIPELINE depth=(\d+) cycles=(\d+) max_reads=(\d+)', result.stdout)
        assert match, result.stdout
        bursts = re.search(r'chunks=(\d+) bursts=(\d+)', result.stdout)
        return {'read_depth': int(match[1]), 'cycles': int(match[2]), 'max_reads': int(match[3]),
                'chunks': int(bursts[1]), 'bursts': int(bursts[2])}
    finally:
        rtlsim.VFLAGS[:] = flags


def test_ps_bridge_hides_read_latency():
    single = _run_bridge(128, read_depth=1)
    queued = _run_bridge(128, read_depth=8)
    assert single['max_reads'] == 1
    assert queued['max_reads'] == 8
    assert queued['cycles'] < single['cycles'] * 0.75, (single, queued)
    print('PS read stream:', single, queued)


def test_wide_ps_bridge_moves_chunks_in_fewer_beats():
    narrow = _run_bridge(128, width=128)
    wide = _run_bridge(128, width=256)
    assert wide['cycles'] < narrow['cycles'] * 0.8, (narrow, wide)
    print('PS width comparison:', narrow, wide)


@pytest.mark.parametrize('depth,width', [(128, 256), (64, 256), (32, 128)])
def test_ps_bridge_combines_reads_and_keeps_sixteen_bursts_pending(depth, width):
    result = _run_bridge(depth, read_depth=16, width=width, read_chunks=4)
    assert result['max_reads'] == 16
    assert result['bursts'] < result['chunks']
    print('Combined reads:', depth, width, result)


@pytest.mark.parametrize("target,fmt", [("mys-zu3eg:d128-m1", "int8"),
                                        ("mys-zu3eg:d128-m1", "int4"),
                                        ("mys-zu3eg:d64-m2", "int4"),
                                        ("mys-zu3eg:d128-m1-r4", "int8"),
                                        ("mys-zu3eg:d128-m1-r4", "int4"),
                                        ("mys-zu3eg:d128-m1-r4-tmem4", "int8"),
                                        ("mys-zu3eg:d128-m1-r4-tmem4", "int4"),
                                        ("mys-zu3eg:d128-m1-r4-cl2-w16", "int8"),
                                        ("mys-zu3eg:d128-m1-r4-cl2-w16", "int4"),
                                        ("mys-zu3eg:d128-m1-r4-cl2", "int8"),
                                        ("mys-zu3eg:d128-m1-r4-cl2", "int4")])
@pytest.mark.parametrize('combined', [False, True])
@pytest.mark.parametrize('fp_pipe', [False, True])
def test_ps_board_kernel_matches_isa(tmp_path, target, fmt, combined, fp_pipe):
    import sys
    from dataclasses import replace

    import numpy as np

    from opentpu import isa as I, target as T
    from opentpu.isasim import Machine
    from opentpu.kernels import mlp
    from opentpu.runtime import Weight, compile_kernel

    sys.path.insert(0, str(rtlsim.ROOT / 'tests'))
    from test_kernels import mlp_args

    cfg = replace(T.config(target), DRAM_BYTES=1 << 20)
    arguments, _ = mlp_args(np.random.default_rng(42), M=4 if cfg.ACT_ROWS == 4 else 1, H=128, Fd=256)
    for value in arguments.values():
        if isinstance(value, Weight):
            value.fmt = fmt
    compiled, images = compile_kernel(mlp, cfg, **arguments)
    _compare_ps_core(tmp_path, target, cfg, compiled.programs, images, combined, fp_pipe)


def _compare_ps_core(tmp_path, target, cfg, programs, images, combined, fp_pipe, program_at=None):
    import numpy as np
    from opentpu import isa as I, target as T
    from opentpu.isasim import Machine
    program_at = cfg.DRAM_BYTES - 4 * cfg.IMEM_WORDS if program_at is None else program_at
    assert not np.any(images[0][program_at:])
    words = np.asarray(I.assemble(programs[0]), dtype='<u4')
    image = np.zeros(cfg.DRAM_BYTES, dtype=np.uint8)
    image[:len(images[0])] = images[0]
    image[program_at:program_at + words.nbytes] = words.view(np.uint8)
    reference = Machine(cfg, programs, [image]).run(max_steps=1 << 30)
    image.view('<u4').astype('>u4').tofile(tmp_path / 'input.bin')
    sources = [rtlsim.RTL / f for f in T.CORE + T.TECH['generic'] + [
        'asic/otpu_asic_top.sv', 'shell/otpu_ctrl.sv',
        'boards/mys-zu3eg/otpu_ps_dram.sv', 'boards/mys-zu3eg/otpu_zu3eg_accel.sv']]
    flags = list(rtlsim.VFLAGS)
    try:
        rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j') + 1] = '2'
        parameters = {k: getattr(cfg, k) for k in ('D', 'MCOLS', 'ACT_BLOCKS', 'ACT_ROWS')}
        parameters['MXU_E'] = T.load(target).uarch['MXU_E']
        parameters['TMEM_NSB'] = T.load(target).uarch['TMEM_NSB']
        parameters['TMEM_SBH'] = T.load(target).uarch['TMEM_SBH']
        parameters['VPU_CL'] = T.load(target).uarch['VPU_CL']
        parameters['WIN'] = T.load(target).uarch['WIN']
        parameters['SEQ_FP_PIPE'] = int(fp_pipe)
        if combined:
            parameters.update(READ_DEPTH=16, READ_CHUNKS=4)
        executable = rtlsim.build('tb_ps_core', sources + [
            rtlsim.TB / 'otpu_ps_axi_mem.sv', rtlsim.TB / 'tb_ps_core.sv'], parameters)
        result = rtlsim.run_sim([str(executable), f'+dir={tmp_path}',
                                f'+program_addr={program_at}',
                                f'+program_n={len(programs[0])}'], timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PS_CORE_PASS' in result.stdout
        stats = re.search(r'PS_CORE_PASS cycles=(\d+) max_reads=(\d+)', result.stdout)
        assert stats and int(stats[2]) > 1, result.stdout
        print(f'PS core {target} FP_PIPE={fp_pipe}: {stats[1]} cycles, {stats[2]} reads outstanding')
        actual = np.fromfile(tmp_path / 'dram_out.bin', dtype=np.uint8)
        assert np.array_equal(actual, reference.slices[0].dram)
        scratchpad = rtlsim._read_hex(tmp_path / 'tmem_0.hex', cfg.TMEM_WORDS)
        assert np.array_equal(scratchpad, reference.slices[0].tmem)
    finally:
        rtlsim.VFLAGS[:] = flags


@pytest.mark.parametrize('seed', range(4))
@pytest.mark.parametrize('fp_pipe', [False, True])
def test_ps_scheduler_pipeline_hazards_and_control(tmp_path, seed, fp_pipe):
    from dataclasses import replace
    import numpy as np
    from opentpu import isa as I, target as T
    from test_rtl import _hazard_program, _images, DATA, SCRATCH

    target = 'mys-zu3eg:d128-m1-r4-cl2'
    cfg = replace(T.config(target), DRAM_BYTES=1 << 20)
    rng = np.random.default_rng(8100 + seed)
    images = _images(rng, 1)
    images[0][0xf4000:0xf4004] = np.asarray([128], dtype='<u4').view(np.uint8)
    prefix = [I.li(1, 3), I.li(2, 0), I.loop(3, 0, rcount=1),
              I.ld(DATA, 8192, 8, ra=2), I.st(SCRATCH, 8192, 8, ra=2), I.addi(2, 2, 32),
              I.loop(1, 0), I.st(SCRATCH + 2048, 8192, 8)]
    body = _hazard_program(rng, cfg, n_ops=80, gather=False)[:-1]
    suffix = [I.ld(0xf4000, 12000, 1), I.rld(7, 12000, raw=True),
              I.st(SCRATCH + 4096, 8192, 8, ra=7), I.halt()]
    _compare_ps_core(tmp_path, target, cfg, [prefix + body + suffix], images, True, fp_pipe,
                     program_at=0xf8000)
