"""The ZU3EG's single-column MXU must elaborate and keep ACC/ASCALE/RMAX exact."""
import numpy as np
import pytest

from opentpu import Config, isa as I, rtlsim
from opentpu.isasim import Machine


@pytest.mark.parametrize('columns', [1, 2])
@pytest.mark.parametrize('tmem_impl', [0, 3])
def test_column_index_width_with_ascale_and_rmax(have_verilator, monkeypatch, columns, tmem_impl):
    one_copy = int(tmem_impl == 3)
    cfg = Config(S=1, D=128, MCOLS=columns, ACT_BLOCKS=8, LANES=4,
                 TMEM_WORDS=4096, IMEM_WORDS=2048, DRAM_BYTES=1 << 17)
    rng = np.random.default_rng(51)
    n, kb = 11, 2
    image = np.zeros(cfg.DRAM_BYTES, np.uint8)
    x = rng.standard_normal(kb * cfg.D).astype(np.float32)
    old = rng.standard_normal(n).astype(np.float32)
    weights = rng.integers(-127, 128, (n, kb * cfg.D), dtype=np.int8)
    image[:x.nbytes] = x.view(np.uint8)
    image[4096:4100] = np.array([0.375], np.float32).view(np.uint8)
    image[8192:8192 + old.nbytes] = old.view(np.uint8)
    image[16384:16384 + weights.nbytes] = weights.view(np.uint8).reshape(-1)
    program = [I.ld(0, 0, x.size), I.ld(4096, 2048, 1), I.ld(8192, 1024, n),
               I.qact(0, 1, 0, kb, kb * cfg.D),
               I.mm(16384, 0, 1024, n, kb, kb * cfg.D, n, 1, 0, 0,
                    unit=True, acc=True, ascale=2048, rmax=True),
               I.st(32768, 1024, n + 1), I.halt()]
    reference = Machine(cfg, [program], [image.copy()]).run().slices[0]
    flags = rtlsim.VFLAGS.copy()
    flags[flags.index('-j') + 1] = '2'
    monkeypatch.setattr(rtlsim, 'VFLAGS', flags)
    drams, tmems, _ = rtlsim.run(
        cfg, [program], [image], axi=False, max_cycles=20000,
        uarch={'WIN': 4, 'RPB': 32, 'WPB': 1, 'FIFO_DEPTH': 64,
               'TMEM_IMPL': tmem_impl, 'TMEM_NSB': 2, 'TMEM_SBP': 3,
               'TMEM_RPL': one_copy, 'TMEM_TDP': one_copy,
               'TMEM_GST': one_copy, 'TMEM_PMP': 0,
               'MXU_E': 1, 'VPU_CL': 1, 'ULANES': 4})
    assert np.array_equal(drams[0], reference.dram)
    assert np.array_equal(tmems[0], reference.tmem)
