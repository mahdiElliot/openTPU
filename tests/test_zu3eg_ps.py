"""Exercise the real PS bridge RTL under AXI backpressure and partial stores."""
import pytest

from opentpu import rtlsim


@pytest.mark.parametrize("depth", [32, 64, 128])
def test_ps_bridge_protocol(depth):
    flags = list(rtlsim.VFLAGS)
    try:
        rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j') + 1] = '2'
        exe = rtlsim.build('tb_ps_dram', [
            rtlsim.RTL / 'boards/mys-zu3eg/otpu_ps_dram.sv',
            rtlsim.TB / 'tb_ps_dram.sv'], {'D': depth})
        result = rtlsim.run_sim([str(exe)], timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PS_DRAM_PROTOCOL_PASS' in result.stdout
    finally:
        rtlsim.VFLAGS[:] = flags


@pytest.mark.parametrize("target,fmt", [("mys-zu3eg:d128-m1", "int8"),
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
        executable = rtlsim.build('tb_ps_core', sources + [rtlsim.TB / 'tb_ps_core.sv'], parameters)
        result = rtlsim.run_sim([str(executable), f'+dir={tmp_path}',
                                f'+program_addr={program_at}',
                                f'+program_n={len(compiled.programs[0])}'], timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'PS_CORE_PASS' in result.stdout
        actual = np.fromfile(tmp_path / 'dram_out.bin', dtype=np.uint8)
        assert np.array_equal(actual, reference.slices[0].dram)
        scratchpad = rtlsim._read_hex(tmp_path / 'tmem_0.hex', cfg.TMEM_WORDS)
        assert np.array_equal(scratchpad, reference.slices[0].tmem)
    finally:
        rtlsim.VFLAGS[:] = flags
