"""The wide bus must cross clocks before its fast-domain downsizer."""
import json

import pytest

from tools.zu3eg.check_build import input_jitter_check, memory_path
from tools.zu3eg.configure_hp import configure
from tools.zu3eg.jtag import CONTROL_BASE


@pytest.mark.parametrize('reported,passed', [(0.100, True), (0.071, False), (None, False)])
def test_clock_budget_requires_the_applied_input_jitter(tmp_path, reported, passed):
    if reported is not None:
        (tmp_path/'clock_root.rpt').write_text(
            f'INPUT_JITTER double true {reported}\nSYSTEM_JITTER double true 0.050\n')
    check = input_jitter_check(tmp_path, {'input_jitter_ns': 0.100})
    assert check['passed'] is passed


@pytest.mark.parametrize('fault', [None, 'slow_downsizer', 'narrow_crossing', 'reversed_path',
                                  'read_credit_metadata', 'write_credit_metadata'])
@pytest.mark.parametrize('separate', [False, True])
def test_wide_memory_path_rejects_bandwidth_bottlenecks(tmp_path, fault, separate):
    configuration = {'bridge_axi_bits': 256, 'core_mhz': 200, 'mem_mhz': 332.8125,
                     'separate_mmcm': separate, 'read_depth': 16, 'write_depth': 4}
    hp_clock = 'memory_clocks/clk_out1' if separate else 'clocks/clk_out2'
    design = {
        'components': {
            'accelerator': {'interface_ports': {'M_AXI': {'parameters': {
                'NUM_READ_OUTSTANDING': {'value': '16'}, 'NUM_WRITE_OUTSTANDING': {'value': '4'}}}}},
            'memory_clock_converter': {'parameters': {'DATA_WIDTH': {'value': '256'}}},
            'memory_width_converter': {'parameters': {'SI_DATA_WIDTH': {'value': '256'},
                                                       'MI_DATA_WIDTH': {'value': '128'}}}},
        'interface_nets': {
            'core': {'interface_ports': ['accelerator/M_AXI', 'memory_clock_converter/S_AXI']},
            'wide': {'interface_ports': ['memory_clock_converter/M_AXI', 'memory_width_converter/S_AXI']},
            'hp': {'interface_ports': ['memory_width_converter/M_AXI', 'ps/S_AXI_HP0_FPD']}},
        'nets': {'hp_clock': {'ports': [hp_clock, 'ps/saxihp0_fpd_aclk',
                                        'memory_width_converter/s_axi_aclk']}}}
    if fault == 'slow_downsizer':
        design['nets']['hp_clock']['ports'].remove('memory_width_converter/s_axi_aclk')
        design['nets']['core_clock'] = {'ports': ['clocks/clk_out1', 'memory_width_converter/s_axi_aclk']}
    elif fault == 'narrow_crossing':
        design['components']['memory_clock_converter']['parameters']['DATA_WIDTH']['value'] = '128'
    elif fault == 'reversed_path':
        design['interface_nets']['core']['interface_ports'] = ['accelerator/M_AXI', 'memory_width_converter/S_AXI']
    elif fault in ('read_credit_metadata', 'write_credit_metadata'):
        key = 'NUM_READ_OUTSTANDING' if fault == 'read_credit_metadata' else 'NUM_WRITE_OUTSTANDING'
        design['components']['accelerator']['interface_ports']['M_AXI']['parameters'][key]['value'] = '2'
    path = tmp_path / 'project/opentpu_zu3eg.srcs/sources_1/bd/system/system.bd'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'design': design}))
    result = memory_path(tmp_path, configuration)
    assert result['passed'] == (fault is None), result
    if fault:
        assert result['errors']


@pytest.mark.parametrize('ports', [[0, 1]])
@pytest.mark.parametrize('fault', [None, 'shared_crossing', 'hp2', 'wrong_clock', 'low_hp1_credits'])
def test_multiple_hp_paths_are_independent_and_leave_hp2_unused(tmp_path, ports, fault):
    configuration = {'hp_ports': ports, 'bridge_axi_bits': 256,
                     'core_mhz': 200, 'mem_mhz': 332.8125, 'separate_mmcm': True,
                     'stripe_weights': True, 'read_depth': 16, 'write_depth': 4}
    design = {'components': {}, 'interface_nets': {}, 'nets': {}}
    design['components']['accelerator'] = {'interface_ports': {}}
    for hp, bus in zip(ports, ('M_AXI', 'M_META_AXI', 'M_BULK_AXI')):
        suffix = '' if hp == 0 else f'_hp{hp}'
        cross, down = 'memory_clock_converter'+suffix, 'memory_width_converter'+suffix
        design['components'][cross] = {'parameters': {'DATA_WIDTH': {'value': '256'}}}
        design['components'][down] = {'parameters': {'SI_DATA_WIDTH': {'value': '256'},
                                                     'MI_DATA_WIDTH': {'value': '128'}}}
        design['interface_nets'][f'core{hp}'] = {'interface_ports': ['accelerator/'+bus, cross+'/S_AXI']}
        design['interface_nets'][f'wide{hp}'] = {'interface_ports': [cross+'/M_AXI', down+'/S_AXI']}
        design['interface_nets'][f'hp{hp}'] = {'interface_ports': [down+'/M_AXI', f'ps/S_AXI_HP{hp}_FPD']}
        design['nets'][f'clock{hp}'] = {'ports': ['memory_clocks/clk_out1', down+'/s_axi_aclk',
                                                 f'ps/saxihp{hp}_fpd_aclk']}
        design['components']['accelerator']['interface_ports'][bus] = {'parameters': {
            'NUM_READ_OUTSTANDING': {'value': '16'}, 'NUM_WRITE_OUTSTANDING': {'value': '4'}}}
    if fault == 'shared_crossing':
        design['interface_nets']['core1']['interface_ports'][1] = 'memory_clock_converter/S_AXI'
    elif fault == 'hp2':
        design['interface_nets']['hp1']['interface_ports'][1] = 'ps/S_AXI_HP2_FPD'
    elif fault == 'wrong_clock':
        design['nets']['clock1']['ports'][0] = 'clocks/clk_out1'
    elif fault == 'low_hp1_credits':
        design['components']['accelerator']['interface_ports']['M_META_AXI']['parameters']['NUM_READ_OUTSTANDING']['value'] = '2'
    path = tmp_path/'project/opentpu_zu3eg.srcs/sources_1/bd/system/system.bd'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'design': design}))
    result = memory_path(tmp_path, configuration)
    assert result['passed'] == (fault is None), result


@pytest.mark.parametrize('ports', [[0, 1]])
def test_hp_configuration_preserves_bus_width_and_never_touches_hp2(ports):
    class Link:
        registers = {CONTROL_BASE+0xc: 8}
        writes = []

        def words(self, address):
            return [self.registers.get(address, 0xa5a50103)]

        def write_word(self, address, value):
            self.writes.append(address)
            self.registers[address] = value

    link = Link()
    result = configure(link, {'hp_ports': ports, 'read_depth': 16, 'write_depth': 4}, 8)
    assert set(result) == {f'hp{hp}' for hp in ports}
    assert not any(0xfd3a0000 <= a < 0xfd3b0000 for a in link.writes)
    for hp in ports:
        assert link.words(0xfd380000+hp*0x10000)[0] & 3 == 3
        assert result[f'hp{hp}']['static_qos'] == (8 if hp == 1 else 0)


def test_hp_configuration_rejects_a_running_accelerator_before_any_write():
    class Link:
        def words(self, address):
            return [0x88]

        def write_word(self, address, value):
            pytest.fail('A busy accelerator must not be reconfigured')

    with pytest.raises(RuntimeError, match='Stop the accelerator'):
        configure(Link(), {'hp_ports': [0, 1], 'read_depth': 16, 'write_depth': 4})


def test_hp_configuration_rejects_the_wrong_programmed_port_count():
    class Link:
        def words(self, address, count=1):
            return [8] if address == CONTROL_BASE+0xc else [0x4d485031, 1]

        def write_word(self, address, value):
            pytest.fail('A mismatching shell must not be reconfigured')

    with pytest.raises(RuntimeError, match='HP-port count differs'):
        configure(Link(), {'hp_ports': [0, 1], 'read_depth': 16,
                           'write_depth': 4, 'memory_stats': True})


@pytest.mark.parametrize('striped', [False, True])
def test_specialized_axi_interfaces_match_real_queue_limits(tmp_path, striped):
    from tools.zu3eg.build_board import specialize_wrapper
    p = tmp_path/'wrapper.v'
    limits = specialize_wrapper(p, 256, 64, 16, 4, 8, 8, striped)
    assert limits['M_AXI'] == {'reads': 16, 'writes': 4, 'burst': 16}
    reads, burst = (16, 16) if striped else (2, 8)
    assert limits['M_META_AXI'] == {'reads': reads, 'writes': 4, 'burst': burst}
    text = p.read_text()
    assert f'M_META_AXI, NUM_READ_OUTSTANDING {reads}, NUM_WRITE_OUTSTANDING 4, MAX_BURST_LENGTH {burst}' in text


@pytest.mark.parametrize('width', [128, 256])
@pytest.mark.parametrize('ports', [1, 2])
def test_one_kib_scale_fill_advertises_the_correct_port_burst(tmp_path, monkeypatch, width, ports):
    import sys
    from tools.zu3eg.build_board import main
    monkeypatch.setattr(sys, 'argv', ['build_board', '--prepare-only', '--out', str(tmp_path),
        '--target', 'mys-zu3eg:d64-m2-r6-cl2', '--scale-cache-lines', '32',
        '--read-depth', '16', '--read-chunks', '8', '--hp-ports', str(ports),
        '--axi-data-width', str(width)])
    main()
    cfg = json.loads((tmp_path/'configuration.json').read_text())
    fill_beats = 1024//(width//8)
    bulk_beats = 512//(width//8)
    assert cfg['scale_cache_lines'] == cfg['rtl_parameters']['BRIDGE_A_CACHE_LINES'] == 32
    limits = cfg['axi_interface_limits']
    assert limits['M_META_AXI']['burst'] == fill_beats
    assert limits['M_AXI']['burst'] == (fill_beats if ports == 1 else bulk_beats)


def test_striped_hp_paths_use_equal_priority_and_full_read_credits():
    class Link:
        registers = {CONTROL_BASE+0xc: 8, CONTROL_BASE+0x318: 1}

        def words(self, address, count=1):
            if address == CONTROL_BASE+0x310:
                return [0x4d485031, 2]
            return [self.registers.get(address, 0xa5a50103)]

        def write_word(self, address, value):
            self.registers[address] = value

    link = Link()
    hardware = {'hp_ports': [0, 1], 'read_depth': 16, 'write_depth': 4,
                'stripe_weights': True, 'memory_stats': True}
    result = configure(link, hardware)
    assert [result[p]['read_credits'] for p in ('hp0', 'hp1')] == [16, 16]
    assert [result[p]['static_qos'] for p in ('hp0', 'hp1')] == [0, 0]
    link.registers[CONTROL_BASE+0x318] = 0
    with pytest.raises(RuntimeError, match='striped weight path'):
        configure(link, hardware)


@pytest.mark.parametrize('enabled', [False, True])
def test_build_defaults_select_cycle_preserving_area_settings(tmp_path, monkeypatch, enabled):
    import sys
    from tools.zu3eg.build_board import main
    options = ['build_board', '--prepare-only', '--out', str(tmp_path),
               '--target', 'mys-zu3eg:d64-m2-r6-cl2']
    if not enabled:
        options += ['--no-area-buffers', '--no-narrow-pc', '--synth-directive', 'default']
    monkeypatch.setattr(sys, 'argv', options)
    main()
    cfg = json.loads((tmp_path/'configuration.json').read_text())
    rtl = cfg['rtl_parameters']
    assert rtl['SEQ_CMD_BRAM'] == rtl['VPU_COL_BRAM'] == rtl['SEQ_NARROW_PC'] == int(enabled)
    assert rtl['D']*rtl['MCOLS'] == 128 and rtl['LANES'] == 4 and rtl['VPU_CL'] == 2
    assert rtl['SEQ_WC'] == rtl['VPU_COMPACT_COMP'] == 0
    assert cfg['synthesis_directive'] == ('AreaOptimized_high' if enabled else 'default')
