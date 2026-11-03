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
@pytest.mark.parametrize('fault', [None, 'shared_crossing', 'hp2', 'wrong_clock'])
def test_multiple_hp_paths_are_independent_and_leave_hp2_unused(tmp_path, ports, fault):
    configuration = {'hp_ports': ports, 'bridge_axi_bits': 256,
                     'core_mhz': 200, 'mem_mhz': 332.8125, 'separate_mmcm': True}
    design = {'components': {}, 'interface_nets': {}, 'nets': {}}
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
    if fault == 'shared_crossing':
        design['interface_nets']['core1']['interface_ports'][1] = 'memory_clock_converter/S_AXI'
    elif fault == 'hp2':
        design['interface_nets']['hp1']['interface_ports'][1] = 'ps/S_AXI_HP2_FPD'
    elif fault == 'wrong_clock':
        design['nets']['clock1']['ports'][0] = 'clocks/clk_out1'
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
