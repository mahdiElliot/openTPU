"""Exercise queued PS reads, independent writes and ISA-exact board kernels."""
import re

import pytest

from opentpu import rtlsim


@pytest.mark.parametrize("depth", [32, 64, 128])
@pytest.mark.parametrize("width", [128, 256])
def test_ps_bridge_protocol(depth, width):
    _run_bridge(depth, width=width)


def _run_bridge(depth, read_depth=8, width=256):
    flags = list(rtlsim.VFLAGS)
    try:
        rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j') + 1] = '2'
        exe = rtlsim.build('tb_ps_dram', [
            rtlsim.RTL / 'boards/mys-zu3eg/otpu_ps_dram.sv',
            rtlsim.TB / 'otpu_ps_axi_mem.sv', rtlsim.TB / 'otpu_ps_test_mem.sv',
            rtlsim.TB / 'tb_ps_dram.sv'], {'D': depth, 'READ_DEPTH': read_depth, 'AXI_DATA_WIDTH': width})
        result = rtlsim.run_sim([str(exe)], timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PS_DRAM_PROTOCOL_PASS' in result.stdout
        match = re.search(r'PS_DRAM_PIPELINE depth=(\d+) cycles=(\d+) max_reads=(\d+)', result.stdout)
        assert match, result.stdout
        return {'read_depth': int(match[1]), 'cycles': int(match[2]), 'max_reads': int(match[3])}
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


@pytest.mark.parametrize("target,fmt", [("mys-zu3eg:d128-m1", "int8"),
                                        ("mys-zu3eg:d128-m1", "int4"),
                                        ("mys-zu3eg:d64-m2", "int4")])
def test_ps_board_kernel_matches_isa(tmp_path, target, fmt):
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
    arguments, _ = mlp_args(np.random.default_rng(42), M=1, H=128, Fd=256)
    for value in arguments.values():
        if isinstance(value, Weight):
            value.fmt = fmt
    compiled, images = compile_kernel(mlp, cfg, **arguments)
    program_at = cfg.DRAM_BYTES - 4 * cfg.IMEM_WORDS
    assert not np.any(images[0][program_at:])
    words = np.asarray(I.assemble(compiled.programs[0]), dtype='<u4')
    image = np.zeros(cfg.DRAM_BYTES, dtype=np.uint8)
    image[:len(images[0])] = images[0]
    image[program_at:program_at + words.nbytes] = words.view(np.uint8)
    reference = Machine(cfg, compiled.programs, [image]).run(max_steps=1 << 30)
    image.view('<u4').astype('>u4').tofile(tmp_path / 'input.bin')
    sources = [rtlsim.RTL / f for f in T.CORE + T.TECH['generic'] + [
        'asic/otpu_asic_top.sv', 'shell/otpu_ctrl.sv',
        'boards/mys-zu3eg/otpu_ps_dram.sv', 'boards/mys-zu3eg/otpu_zu3eg_accel.sv']]
    flags = list(rtlsim.VFLAGS)
    try:
        rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j') + 1] = '2'
        parameters = {k: getattr(cfg, k) for k in ('D', 'MCOLS', 'ACT_BLOCKS', 'ACT_ROWS')}
        parameters['MXU_E'] = T.load(target).uarch['MXU_E']
        executable = rtlsim.build('tb_ps_core', sources + [
            rtlsim.TB / 'otpu_ps_axi_mem.sv', rtlsim.TB / 'tb_ps_core.sv'], parameters)
        result = rtlsim.run_sim([str(executable), f'+dir={tmp_path}',
                                f'+program_addr={program_at}',
                                f'+program_n={len(compiled.programs[0])}'], timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PS_CORE_PASS' in result.stdout
        stats = re.search(r'PS_CORE_PASS cycles=(\d+) max_reads=(\d+)', result.stdout)
        assert stats and int(stats[2]) > 1, result.stdout
        print(f'PS kernel {target} {fmt}: {stats[1]} cycles, {stats[2]} reads outstanding')
        actual = np.fromfile(tmp_path / 'dram_out.bin', dtype=np.uint8)
        assert np.array_equal(actual, reference.slices[0].dram)
        scratchpad = rtlsim._read_hex(tmp_path / 'tmem_0.hex', cfg.TMEM_WORDS)
        assert np.array_equal(scratchpad, reference.slices[0].tmem)
    finally:
        rtlsim.VFLAGS[:] = flags
