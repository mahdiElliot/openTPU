"""Collect the completed physical ZU3EG request and its validation evidence."""
import argparse
import json
import re
from pathlib import Path

from .settings import DEPLOY
from .sequence import save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEPLOY)
    parser.add_argument('--hardware', type=Path, help='override DIRECTORY/hardware')
    options = parser.parse_args()
    directory = options.directory.resolve()
    hardware = (options.hardware or directory / 'hardware').resolve()
    run = directory / 'run'
    result = json.loads((run / 'result.json').read_text())
    verification = json.loads((run / 'isa_verification.json').read_text())
    program = json.loads((directory / 'bringup/program_result.json').read_text())
    kernels = [json.loads((run / f'kernel_{fmt}_result.json').read_text())
               for fmt in ('int8', 'int4')]
    counts = {phase: sum(s['phase'] == phase for s in result['steps'])
              for phase in ('prefill', 'decode')}
    if not result['complete'] or counts != {'prefill': len(result['prefill_tokens']), 'decode': result['decode_steps']}:
        raise RuntimeError('All requested prefill and decode passes must finish')
    if not verification['passed'] or not all(k['passed'] for k in kernels):
        raise RuntimeError('Physical-board numerical validation must pass')
    resources = {}
    text = (hardware / 'utilization.rpt').read_text()
    for name in ('CLB LUTs', 'CLB Registers', 'CLB', 'Block RAM Tile', 'DSPs'):
        match = re.search(r'\|\s*' + re.escape(name) +
                          r'\s*\|\s*([\d.]+)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|', text)
        if not match:
            raise RuntimeError(f'Missing routed resource: {name}')
        resources[name] = {'used': float(match[1]), 'available': float(match[2]),
                           'percent': float(match[3])}
    keys = ('prefill_cycles', 'decode_cycles', 'prefill_wall_seconds', 'decode_wall_seconds',
            'request_wall_seconds', 'prefill_device_tokens_per_second',
            'decode_device_tokens_per_second', 'prefill_wall_tokens_per_second',
            'decode_wall_tokens_per_second', 'request_device_decode_steps_per_second',
            'request_wall_decode_steps_per_second', 'request_device_output_tokens_per_second',
            'request_wall_output_tokens_per_second', 'total_output_tokens_including_prefill_seed')
    from opentpu import target as T
    cfg = T.config(result['target'])
    report = {'complete': True, 'physical_board': 'MYIR MYS-ZU3EG K2, 2 GiB DDR4',
              'vivado_part': 'xczu3eg-sfvc784-1-i', 'target': result['target'],
              'PEs': cfg.S * cfg.D * cfg.MCOLS, 'geometry': f'{cfg.MCOLS} columns x {cfg.D} depth',
              'clock_mhz': result['core_khz'] / 1000,
              'mem_mhz': result.get('mem_mhz', result['core_khz'] / 1000),
              'weight_format': 'int4', 'head_format': 'int8', 'prefill_tokens': len(result['prefill_tokens']),
              'decode_steps': result['decode_steps'], 'context_capacity': result['context_capacity'], 'image_bytes': result['image_bytes'],
              'transport': result['transport'], 'programming': program,
              'routed_resources': resources, 'physical_kernels': kernels,
              'isa_verification': verification, 'performance': {k: result[k] for k in keys},
              'seed_token': result['seed_token'], 'generated_tokens': result['generated_tokens'],
              'generated_text': result['generated_text'],
              'limitations': ['PS DDR initialization preserved from the existing board bootloader',
                              'DDR throughput depends on burst scheduling and memory latency',
                              'wall rates include PC compilation, control and selected data transfers',
                              'device rates count accelerator RUN cycles only',
                              'model build and upload excluded from request timing']}
    save(directory / 'report.json', report)
    save(directory / 'status.json', {'complete': True, 'stage': 'physical request and ISA verification passed',
                                    'report': str(directory / 'report.json')})
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
