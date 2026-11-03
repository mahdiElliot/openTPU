"""The report accepts both per-token and aggregated device-decode records."""
import json
import sys

import pytest

from tools.zu3eg import deployment_report


@pytest.mark.parametrize('aggregated', [False, True])
@pytest.mark.parametrize('complete_decode', [False, True])
def test_report_counts_decoded_tokens(tmp_path, monkeypatch, aggregated, complete_decode):
    run, hw, bringup = (tmp_path / n for n in ('run', 'hardware', 'bringup'))
    for p in (run, hw, bringup):
        p.mkdir()
    count = 16 if complete_decode else 15
    steps = [{'phase': 'prefill', 'tokens': 4} for _ in range(32)]
    steps += ([{'phase': 'decode', 'tokens': count}] if aggregated else
              [{'phase': 'decode'} for _ in range(count)])
    result = dict(complete=True, steps=steps, prefill_tokens=list(range(128)), decode_steps=16,
                  target='mys-zu3eg:d64-m2-r6-cl2', core_khz=200000, mem_mhz=332.8125,
                  context_capacity=256, image_bytes=1596454400, transport='Ethernet',
                  seed_token=279, generated_tokens=list(range(count)), generated_text='test',
                  total_output_tokens_including_prefill_seed=17)
    for key in ('prefill_cycles', 'decode_cycles', 'prefill_wall_seconds', 'decode_wall_seconds',
                'request_wall_seconds', 'prefill_device_tokens_per_second',
                'decode_device_tokens_per_second', 'prefill_wall_tokens_per_second',
                'decode_wall_tokens_per_second', 'request_device_decode_steps_per_second',
                'request_wall_decode_steps_per_second', 'request_device_output_tokens_per_second',
                'request_wall_output_tokens_per_second'):
        result[key] = 1
    (run/'result.json').write_text(json.dumps(result))
    for name in ('isa_verification.json', 'kernel_int8_result.json', 'kernel_int4_result.json'):
        (run/name).write_text(json.dumps({'passed': True}))
    (bringup/'program_result.json').write_text('{}')
    (hw/'utilization.rpt').write_text('\n'.join(
        f'| {name} | 1 | 0 | 0 | 10 | 10.00 |'
        for name in ('CLB LUTs', 'CLB Registers', 'CLB', 'Block RAM Tile', 'DSPs')))
    monkeypatch.setattr(sys, 'argv', ['deployment_report', '--directory', str(tmp_path)])
    if complete_decode:
        deployment_report.main()
        report = json.loads((tmp_path/'report.json').read_text())
        assert report['complete'] and report['decode_steps'] == 16
        assert report['PEs'] == 128
    else:
        with pytest.raises(RuntimeError, match='All requested prefill and decode'):
            deployment_report.main()
