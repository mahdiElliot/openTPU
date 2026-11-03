"""The wide bus must cross clocks before its fast-domain downsizer."""
import json

import pytest

from tools.zu3eg.check_build import memory_path


@pytest.mark.parametrize('fault', [None, 'slow_downsizer', 'narrow_crossing', 'reversed_path'])
@pytest.mark.parametrize('separate', [False, True])
def test_wide_memory_path_rejects_bandwidth_bottlenecks(tmp_path, fault, separate):
    configuration = {'bridge_axi_bits': 256, 'core_mhz': 200, 'mem_mhz': 332.8125,
                     'separate_mmcm': separate}
    hp_clock = 'memory_clocks/clk_out1' if separate else 'clocks/clk_out2'
    design = {
        'components': {
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
    path = tmp_path / 'project/opentpu_zu3eg.srcs/sources_1/bd/system/system.bd'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'design': design}))
    result = memory_path(tmp_path, configuration)
    assert result['passed'] == (fault is None), result
    if fault:
        assert result['errors']
