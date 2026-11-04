"""Area candidates must retain exact memory semantics and float32 results."""
from dataclasses import replace

import numpy as np
import pytest

from opentpu import target as T
from test_zu3eg_ps import _compare_ps_core, test_ps_board_kernel_matches_isa as check_kernel
from test_vops import _vops_program, _direct_program, _special


@pytest.mark.parametrize('waitc,compact', [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize('fmt', ['int4', 'int8'])
def test_area_kernel_matches_isa(tmp_path, waitc, compact, fmt):
    target = 'mys-zu3eg:d64-m2-r6-wc' if waitc else 'mys-zu3eg:d64-m2-r6-cl2'
    check_kernel(tmp_path, target, fmt, True, True, hp_ports=2, compact_vpu=compact)


@pytest.mark.parametrize('waitc', [False, True])
@pytest.mark.parametrize('seed', [0, 1])
def test_shared_composites_and_dependencies_match_isa(tmp_path, waitc, seed):
    target = 'mys-zu3eg:d64-m2-r6-wc' if waitc else 'mys-zu3eg:d64-m2-r6-cl2'
    cfg = replace(T.config(target), DRAM_BYTES=1 << 20)
    rng = np.random.default_rng(510+seed)
    program = _vops_program(rng, cfg, n_ops=32) if seed == 0 else _direct_program(rng, n_ops=40)
    image = np.zeros(cfg.DRAM_BYTES, np.uint8)
    data = _special(rng, 8192)
    image[:data.nbytes] = data.view(np.uint8)
    _compare_ps_core(tmp_path, target, cfg, [program], [image], True, True,
                     hp_ports=2, compact_vpu=True)


@pytest.mark.parametrize('seed', [0, 1])
def test_wait_entries_cover_dynamic_register_loads_and_loops(tmp_path, seed):
    from test_zu3eg_ps import test_ps_scheduler_pipeline_hazards_and_control as check_control
    check_control(tmp_path, seed, True, hp_ports=2,
                  target='mys-zu3eg:d64-m2-r6-wc', compact_vpu=True)


@pytest.mark.parametrize('stages', [1, 2])
@pytest.mark.parametrize('seed', [0, 1])
def test_further_arithmetic_sharing_matches_isa(tmp_path, stages, seed):
    target = 'mys-zu3eg:d64-m2-r6-wc'
    cfg = replace(T.config(target), DRAM_BYTES=1 << 20)
    rng = np.random.default_rng(710+seed)
    program = _vops_program(rng, cfg, n_ops=40) if seed == 0 else _direct_program(rng, n_ops=48)
    image = np.zeros(cfg.DRAM_BYTES, np.uint8)
    data = _special(rng, 8192)
    image[:data.nbytes] = data.view(np.uint8)
    _compare_ps_core(tmp_path, target, cfg, [program], [image], True, True,
                     hp_ports=2, compact_vpu=True, compact_stages=stages)


@pytest.mark.parametrize('seed', [0, 1])
def test_storage_mapping_preserves_cycles_and_bits(tmp_path, seed):
    """BRAM command/OUTER buffers and narrower PCs cannot insert bubbles or change data."""
    target = 'mys-zu3eg:d64-m2-r6-cl2'
    cfg = replace(T.config(target), DRAM_BYTES=1 << 20)
    rng = np.random.default_rng(920+seed)
    program = _vops_program(rng, cfg, n_ops=40)
    image = np.zeros(cfg.DRAM_BYTES, np.uint8)
    data = _special(rng, 8192)
    image[:data.nbytes] = data.view(np.uint8)
    cycles = []
    for enabled in (False, True):
        case = tmp_path / str(enabled)
        case.mkdir()
        cycles.append(_compare_ps_core(case, target, cfg, [program], [image], True, True,
                                       hp_ports=2, area_buffers=enabled, narrow_pc=enabled))
    assert cycles[0] == cycles[1]


@pytest.mark.parametrize('seed', [0, 1])
def test_storage_mapping_covers_dynamic_register_loads_and_loops(tmp_path, seed):
    from test_zu3eg_ps import test_ps_scheduler_pipeline_hazards_and_control as check_control
    check_control(tmp_path, seed, True, hp_ports=2,
                  target='mys-zu3eg:d64-m2-r6-cl2', area_buffers=True, narrow_pc=True)


def test_narrow_pc_reaches_last_instruction_and_preserves_32bit_counts(tmp_path):
    from opentpu import isa as I
    target = 'mys-zu3eg:d64-m2-r6-cl2'
    cfg = replace(T.config(target), DRAM_BYTES=1 << 20)
    count = cfg.IMEM_WORDS // 8
    # The first count adds to zero in 32 bits and skips one word. The second loop
    # reaches IMEM's last row, twice, before HALT at the last instruction address.
    program = [I.li(1, 0x80000000), I.loop(1, 0x80000000, rcount=1), I.nop(),
               I.loop(count-5, 2)] + [I.nop()] * (count-5) + [I.halt()]
    assert len(program) == count
    image = np.zeros(cfg.DRAM_BYTES, np.uint8)
    cycles = []
    for enabled in (False, True):
        case = tmp_path / str(enabled)
        case.mkdir()
        cycles.append(_compare_ps_core(case, target, cfg, [program], [image], True, True,
                                       hp_ports=2, area_buffers=enabled, narrow_pc=enabled))
    assert cycles[0] == cycles[1]
