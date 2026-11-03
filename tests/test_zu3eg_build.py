"""The wide bus must cross clocks before its fast-domain downsizer."""
import json

import pytest

from tools.zu3eg.check_build import input_jitter_check, memory_path


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
