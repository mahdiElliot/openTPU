"""Run Qwen3.5 on MYIR with JTAG control and JTAG or Ethernet DDR transfers."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from opentpu import imgcache, progcache, target as T
from opentpu.llm import load_spec
from opentpu.llm.qwen3 import Engine, load_weights
from .evaluate import MODEL, ROOT
from .jtag import CONTROL_BASE, Jtag, JtagBackend
from .sequence import save

from .settings import CAPACITY, DECODE, DEPLOY, PREFILL, TARGET as DEFAULT_TARGET


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1 << 20), b''):
            h.update(data)
    return h.hexdigest()


def model_layout(model, target, capacity):
    return {'target': target, 'config': asdict(T.config(target)), 'capacity': capacity,
            'rows': 1, 'wformat': 'int4', 'head_format': 'int8', 'lookup': False,
            'checkpoint': imgcache.checkpoint(model), 'source': progcache.source()}


def engine(model, out, backend, target=DEFAULT_TARGET, capacity=CAPACITY, pipeline=False):
    cfg, spec = T.config(target), load_spec(model)
    layout = digest(model_layout(model, target, capacity))

    def build(image, weights, unused_layout):
        path, manifest = out / 'model_image.bin', out / 'model_image.json'
        if path.exists():
            if not manifest.exists():
                raise RuntimeError('Image lacks a layout manifest; use a new output directory')
            metadata = json.loads(manifest.read_text())
            if metadata['layout_digest'] != layout or path.stat().st_size != image.nbytes:
                raise RuntimeError('Cached image configuration changed; use a new output directory')
            if metadata['sha256'] != file_digest(path):
                raise RuntimeError('Cached model image checksum differs')
            return [np.fromfile(path, dtype=np.uint8)]
        images = image.build(weights)
        temporary = path.with_suffix('.tmp')
        images[0].tofile(temporary)
        temporary.replace(path)
        save(manifest, {'layout_digest': layout, 'sha256': file_digest(path),
                        'image_bytes': image.nbytes, 'target': target,
                        'config': asdict(cfg), 'capacity': capacity})
        return images

    with patch.object(imgcache, 'build', build):
        return Engine(spec, load_weights(model), cfg=cfg, cap=capacity, rows=1,
                      backend=backend, pipeline=pipeline, resident=False,
                      wformat='int4', head_format='int8', prog_cache=False, snapshots=0)


def kernel_check(link, out, target, hardware_id, fmt='int8'):
    from opentpu.isasim import Machine
    from opentpu.kernels import mlp
    from opentpu.runtime import Weight, compile_kernel
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_kernels import mlp_args
    cfg = replace(T.config(target), DRAM_BYTES=1 << 20)
    args, _ = mlp_args(np.random.default_rng(42), M=1, H=128, Fd=256)
    for value in args.values():
        if isinstance(value, Weight):
            value.fmt = fmt
    compiled, images = compile_kernel(mlp, cfg, **args)
    reference = Machine(cfg, compiled.programs, images).run(max_steps=1 << 30)
    backend = JtagBackend(cfg, images, link, verify=False)
    stats = backend.run(compiled.programs)
    actual = backend.read(0, 0, cfg.DRAM_BYTES)
    different = int(np.count_nonzero(actual != reference.slices[0].dram))
    result = {'passed': different == 0, 'target': target, 'hardware_id': hardware_id,
              'weight_format': fmt, 'dram_compared_bytes': cfg.DRAM_BYTES,
              'different_bytes': different, 'stats': stats}
    save(out / f'kernel_{fmt}_result.json', result)
    if different:
        raise RuntimeError(f'Board kernel differs in {different} DRAM bytes')
    print('BOARD_KERNEL_BIT_EXACT', fmt, stats, flush=True)


def preflight(model, target, capacity, prompt_count, decode_count, out):
    cfg = T.config(target)
    image = load_spec(model).image(cfg, capacity, rows=1, wformat='int4', head_format='int8', lookup=False)
    checks = []
    positions = sorted({0, min(3, prompt_count - 1), prompt_count - 1,
                        prompt_count, prompt_count + decode_count - 1})
    for position in positions:
        programs = image.compile_step(position)
        counts = [len(p) for p in programs]
        checks.append({'position': position, 'kind': 'step', 'instructions': counts})
        for head in ([], [0]):
            counts = [len(p) for p in image.compile_rows([(0, position)], head)]
            checks.append({'position': position, 'kind': 'rows_head' if head else 'rows_no_head',
                           'instructions': counts})
    if any(max(c['instructions']) * 8 > cfg.IMEM_WORDS for c in checks):
        raise RuntimeError('At least one program exceeds the target instruction memory')
    if 0x10000000 + image.nbytes + cfg.IMEM_WORDS * 4 >= 0x80000000:
        raise RuntimeError('Image and programs exceed the physical DDR window')
    result = {'target': target, 'config': asdict(cfg), 'capacity': capacity,
              'prefill': prompt_count, 'decode': decode_count, 'image_bytes': image.nbytes,
              'compiled_programs': checks, 'passed': True}
    save(out / 'preflight.json', result)
    print('PREFLIGHT_PASS', image.nbytes, 'bytes, max instructions',
          max(max(c['instructions']) for c in checks), flush=True)


def forward_prefill(eng, ids, progress, compile_ahead=False):
    if compile_ahead:
        from concurrent.futures import ThreadPoolExecutor
        eng._drain()
        def compile_row(position):
            programs = eng.image.compile_rows([(0, position)],
                [0] if position == len(ids) - 1 else [], eng.block,
                **eng._tokens_kw([int(ids[position])]))
            prepare = getattr(eng.backend, 'prepare', None)
            if prepare is not None:
                prepare(programs)
            return programs
        with ThreadPoolExecutor(1, thread_name_prefix='zu3eg-prefill') as pool:
            pending = pool.submit(compile_row, 0)
            for position, token in enumerate(ids):
                begin = time.monotonic()
                programs = pending.result()
                def started():
                    nonlocal pending
                    if position + 1 < len(ids):
                        pending = pool.submit(compile_row, position + 1)
                    else:
                        eng._prefetch(len(ids))
                asynchronous = hasattr(eng.backend, 'start')
                if asynchronous:
                    eng.backend.on_started = started
                else:
                    started()
                logits = eng._run_rows([(0, position)], [int(token)],
                    [0] if position == len(ids) - 1 else [], programs)
                if asynchronous:
                    eng.backend.on_started = None
                eng.pos = position + 1
                progress(position, time.monotonic() - begin)
        if not np.isfinite(logits).all():
            raise RuntimeError('Nonfinite prefill logits')
        return logits[0]
    for position, token in enumerate(ids):
        begin = time.monotonic()
        logits = eng.run_rows([(0, position)], [int(token)], [0] if position == len(ids) - 1 else [])
        eng.pos = position + 1
        progress(position, time.monotonic() - begin)
    if not np.isfinite(logits).all():
        raise RuntimeError('Nonfinite prefill logits')
    return logits[0]


def reference_run(options, ids, request_id, tokenizer):
    eng = engine(options.model, options.out, 'isa', options.target, options.capacity)
    begin = time.monotonic()
    try:
        def progress(position, elapsed):
            if position % 8 == 7 or position == len(ids) - 1:
                save(options.out / 'reference_progress.json', {'prefill_tokens': position + 1,
                                                              'seconds': time.monotonic() - begin})
                print(f'ISA reference prefill {position+1}/{len(ids)}', flush=True)
        logits = forward_prefill(eng, ids, progress)
        logits.astype('<f4').tofile(options.out / 'reference_prefill_logits.bin')
        seed = token = int(np.argmax(logits))
        generated = []
        for index in range(options.decode):
            logits = eng.step(token)
            if not np.isfinite(logits).all():
                raise RuntimeError('Nonfinite ISA logits')
            token = int(np.argmax(logits))
            generated.append(token)
            print(f'ISA reference decode {index+1}/{options.decode}: {tokenizer.decode([seed]+generated)!r}', flush=True)
        logits.astype('<f4').tofile(options.out / 'reference_final_logits.bin')
        save(options.out / 'reference_result.json', {'request_id': request_id,
             'seed_token': seed, 'generated_tokens': generated,
             'generated_text': tokenizer.decode([seed]+generated),
             'seconds': time.monotonic() - begin, 'complete': True})
    finally:
        eng._drain()


def verify_reference(out, result):
    reference = json.loads((out / 'reference_result.json').read_text())
    if reference.get('request_id') != result['request_id'] or not reference['complete']:
        raise RuntimeError('The ISA reference belongs to a different request or model configuration')
    checks = {'prefill_logits_bit_exact': bool(np.array_equal(
        np.fromfile(out / 'prefill_logits.bin', dtype='<u4'),
        np.fromfile(out / 'reference_prefill_logits.bin', dtype='<u4'))),
        'generated_tokens_equal': result['seed_token'] == reference['seed_token'] and
                                  result['generated_tokens'] == reference['generated_tokens'],
        'final_logits_bit_exact': bool(np.array_equal(
        np.fromfile(out / 'final_logits.bin', dtype='<u4'),
        np.fromfile(out / 'reference_final_logits.bin', dtype='<u4')))}
    checks['passed'] = all(checks.values())
    save(out / 'isa_verification.json', checks)
    if not checks['passed']:
        raise RuntimeError(f'Board/ISA verification failed: {checks}')
    print('BOARD_REQUEST_ISA_BIT_EXACT', flush=True)


def memory_statistics(link, cycles):
    if cycles >= 1 << 32:
        raise RuntimeError('This run exceeds the 32-bit memory counter measurement range')
    words = link.read(CONTROL_BASE + 0x300, 16).view('<u4').tolist()
    if words[0] != 0x4d535431:
        raise RuntimeError('Program a MEMORY_STATS bitstream before measuring memory stalls')
    pressure, starve, union = words[1:]
    if not max(pressure, starve) <= union <= min(cycles, pressure + starve):
        raise RuntimeError(f'Invalid memory statistics: {words}, RUN cycles={cycles}')
    return {'request_backpressure_cycles': pressure, 'weight_starvation_cycles': starve,
            'either_condition_cycles': union, 'overlap_cycles': pressure + starve - union}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEPLOY / 'run')
    parser.add_argument('--model', type=Path, default=MODEL)
    parser.add_argument('--target', default=DEFAULT_TARGET)
    parser.add_argument('--hardware', type=Path, default=DEPLOY / 'hardware')
    parser.add_argument('--prefill', type=int, default=PREFILL)
    parser.add_argument('--decode', type=int, default=DECODE, help='additional forward/argmax steps after the prefill seed')
    parser.add_argument('--capacity', type=int, default=CAPACITY)
    parser.add_argument('--prompt-file', type=Path, default=ROOT / 'tools/data/austen_pp_ch1.txt')
    parser.add_argument('--data-transport', choices=('jtag', 'ethernet'), default='jtag',
                        help='transport for input/program uploads and logit reads')
    parser.add_argument('--ethernet-address', default='192.168.200.2')
    parser.add_argument('--run-control', choices=('jtag', 'ethernet'), default='jtag',
                        help='run/start/wait control path; Ethernet requires --data-transport ethernet')
    parser.add_argument('--compile-ahead', action='store_true',
                        help='compile the next prefill/decode program while the FPGA executes')
    parser.add_argument('--profile', action='store_true',
                        help='record per-step host, transfer, control and FPGA timings')
    parser.add_argument('--memory-stats', action='store_true',
                        help='read per-run counters from a MEMORY_STATS instrumented bitstream')
    stages = parser.add_mutually_exclusive_group()
    stages.add_argument('--prepare', action='store_true')
    stages.add_argument('--preflight', action='store_true')
    stages.add_argument('--reference-only', action='store_true')
    stages.add_argument('--kernel-only', action='store_true')
    parser.add_argument('--preloaded', action='store_true')
    parser.add_argument('--verify-isa', action='store_true')
    options = parser.parse_args()
    if options.run_control == 'ethernet' and options.data_transport != 'ethernet':
        parser.error('--run-control ethernet requires --data-transport ethernet')
    if min(options.prefill, options.decode) < 1 or options.prefill + options.decode > options.capacity:
        parser.error('positive counts with prefill + decode <= capacity are required')
    options.out = options.out.resolve()
    options.out.mkdir(parents=True, exist_ok=True)
    for name in ('OTPU_IMG_CACHE', 'OTPU_IMAGE_CACHE', 'OTPU_PROG_CACHE'):
        os.environ[name] = '0'
    os.environ.pop('OTPU_FORMATS', None)
    os.environ.setdefault('OTPU_BUILD_JOBS', '1')
    cfg = T.config(options.target)
    if options.preflight:
        preflight(options.model, options.target, options.capacity, options.prefill, options.decode, options.out)
        return
    if options.prepare:
        class Prepared:
            def __init__(self, cfg, images):
                self.images = images
        eng = engine(options.model, options.out, Prepared, options.target, options.capacity)
        print('MODEL_IMAGE_PREPARED', eng.image.nbytes, flush=True)
        eng._drain()
        return
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(options.model, local_files_only=True)
    ids = tokenizer(options.prompt_file.read_text(), add_special_tokens=False).input_ids[:options.prefill]
    if len(ids) != options.prefill:
        raise ValueError('Prompt file contains fewer tokens than --prefill')
    request_id = digest({'layout': model_layout(options.model, options.target, options.capacity),
                         'prompt_ids': ids, 'decode_steps': options.decode})
    if options.reference_only:
        reference_run(options, ids, request_id, tokenizer)
        return
    hardware = json.loads((options.hardware / 'configuration.json').read_text())
    if hardware.get('target', DEFAULT_TARGET) != options.target or hardware['arch'] != asdict(cfg):
        raise ValueError('Hardware and software target configurations differ')
    hardware_id = file_digest(options.hardware / 'opentpu_zu3eg.bit')
    link, eng = Jtag(options.out), None
    if options.data_transport == 'ethernet':
        from .ethernet import Ethernet, EthernetDataLink
        try:
            data = Ethernet(options.ethernet_address)
        except Exception:
            link.close()
            raise
        link = EthernetDataLink(link, data, run_control=options.run_control)
    if options.profile:
        from .profiling import ProfiledLink
        link = ProfiledLink(link)
    try:
        version = (cfg.D << 16) | (cfg.MCOLS << 8) | cfg.LANES
        if link.words(CONTROL_BASE, 2) != [0x4f545055, version]:
            raise RuntimeError('Program the matching bitstream first')
        khz = link.words(CONTROL_BASE + 0x44)[0]
        if khz != round(hardware['core_mhz'] * 1000):
            raise RuntimeError('Hardware/software core clock metadata differ')
        if 'rtl_parameters' in hardware and link.words(CONTROL_BASE + 0x9c)[0] != T.arch_hash(cfg):
            raise RuntimeError('Hardware configuration hash differs')
        if options.kernel_only and (options.out / 'model_loaded.json').exists():
            loaded_before = json.loads((options.out / 'model_loaded.json').read_text())
            loaded_before['pristine'] = False
            save(options.out / 'model_loaded.json', loaded_before)
        if options.preloaded:
            loaded = json.loads((options.out / 'model_loaded.json').read_text())
            manifest = json.loads((options.out / 'model_image.json').read_text())
            if (not loaded.get('pristine', False) or not loaded['sample_readbacks_passed'] or
                not loaded['accelerator_restored'] or loaded.get('hardware_id') != hardware_id or
                loaded.get('sha256') != manifest['sha256']):
                raise RuntimeError('Reload and validate the initial model for this hardware')
            for fmt in ('int8', 'int4'):
                check = json.loads((options.out / f'kernel_{fmt}_result.json').read_text())
                if not check['passed'] or check.get('hardware_id') != hardware_id:
                    raise RuntimeError('Matching physical kernel validation required before bulk loading')
        else:
            kernel_check(link, options.out, options.target, hardware_id)
            kernel_check(link, options.out, options.target, hardware_id, 'int4')
        if options.kernel_only:
            return
        request = {'target': options.target, 'config': asdict(cfg), 'hardware_id': hardware_id,
                   'core_khz': khz, 'mem_mhz': hardware.get('mem_mhz', hardware['core_mhz']),
                   'prefill_tokens': ids, 'decode_steps': options.decode, 'request_id': request_id,
                   'context_capacity': options.capacity, 'weight_format': 'int4', 'head_format': 'int8',
                   'transport': (f'Ethernet DDR data (inputs, programs, logits); {options.run_control.upper()} run control'
                                 if options.data_transport == 'ethernet' else
                                 'physical JTAG to PS DDR and AXI-Lite controls'),
                   'data_transport': options.data_transport,
                   'run_control': options.run_control, 'compile_ahead': options.compile_ahead,
                   'memory_stats_version': 1 if options.memory_stats else None,
                   'prefill_method': 'one row per run; LM head on final row only',
                   'decode_method': 'README perf.py: seed from prefill, then N forward/argmax steps'}
        save(options.out / 'request.json', request)
        setup = time.monotonic()
        backend = JtagBackend
        if options.run_control == 'ethernet':
            from .ethernet import EthernetBackend
            backend = EthernetBackend
        eng = engine(options.model, options.out,
                     lambda c, images: backend(c, images, link, verify=False, upload=not options.preloaded),
                     options.target, options.capacity, pipeline='thread' if options.compile_ahead else False)
        request.update(setup_wall_seconds=time.monotonic() - setup, image_bytes=eng.image.nbytes)
        if options.preloaded:
            loaded['pristine'] = False
            save(options.out / 'model_loaded.json', loaded)
        if options.profile:
            link.reset()
        steps, generated = [], []
        begin = time.monotonic()
        def progress(position, elapsed):
            row = {'phase': 'prefill', 'position': position, 'cycles': eng.stats[-1]['cycles'], 'wall_seconds': elapsed}
            if options.profile:
                row['profile'] = link.take(elapsed, row['cycles'], khz * 1000)
            if options.memory_stats:
                row['memory_stats'] = memory_statistics(link, row['cycles'])
            steps.append(row)
            save(options.out / 'progress.json', request | {'complete': False, 'steps': steps})
            print(f'BOARD prefill {position+1}/{len(ids)}: {row["cycles"]} cycles, {elapsed:.3f} wall s', flush=True)
        logits = forward_prefill(eng, ids, progress, options.compile_ahead)
        prefill_wall = time.monotonic() - begin
        logits.astype('<f4').tofile(options.out / 'prefill_logits.bin')
        seed = token = int(np.argmax(logits))
        decode_begin = time.monotonic()
        for index in range(options.decode):
            step_begin = time.monotonic()
            logits = eng.step(token)
            if not np.isfinite(logits).all():
                raise RuntimeError('Nonfinite decode logits')
            token = int(np.argmax(logits))
            generated.append(token)
            row = {'phase': 'decode', 'position': len(ids) + index,
                   'cycles': eng.stats[-1]['cycles'], 'wall_seconds': time.monotonic() - step_begin, 'token': token}
            if options.profile:
                row['profile'] = link.take(row['wall_seconds'], row['cycles'], khz * 1000)
            if options.memory_stats:
                row['memory_stats'] = memory_statistics(link, row['cycles'])
            steps.append(row)
            save(options.out / 'progress.json', request | {'complete': False, 'steps': steps, 'generated_tokens': generated})
            print(f'BOARD decode {index+1}/{options.decode}: {eng.stats[-1]["cycles"]} cycles, {tokenizer.decode([seed]+generated)!r}', flush=True)
        decode_wall, request_wall = time.monotonic() - decode_begin, time.monotonic() - begin
        logits.astype('<f4').tofile(options.out / 'final_logits.bin')
        pc = sum(s['cycles'] for s in steps if s['phase'] == 'prefill')
        dc = sum(s['cycles'] for s in steps if s['phase'] == 'decode')
        frequency, n = khz * 1000, options.decode
        result = request | {'complete': True, 'steps': steps, 'seed_token': seed,
                  'generated_tokens': generated, 'generated_text': tokenizer.decode([seed]+generated),
                  'prefill_cycles': pc, 'decode_cycles': dc, 'prefill_wall_seconds': prefill_wall,
                  'decode_wall_seconds': decode_wall, 'request_wall_seconds': request_wall,
                  'prefill_device_tokens_per_second': len(ids)*frequency/pc,
                  'decode_device_tokens_per_second': n*frequency/dc,
                  'prefill_wall_tokens_per_second': len(ids)/prefill_wall,
                  'decode_wall_tokens_per_second': n/decode_wall,
                  'request_device_decode_steps_per_second': n*frequency/(pc+dc),
                  'request_wall_decode_steps_per_second': n/request_wall,
                  'total_output_tokens_including_prefill_seed': n+1,
                  'request_device_output_tokens_per_second': (n+1)*frequency/(pc+dc),
                  'request_wall_output_tokens_per_second': (n+1)/request_wall}
        save(options.out / 'result.json', result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('steps','prefill_tokens','config')}, indent=2), flush=True)
    finally:
        if eng is not None:
            eng._drain()
        link.close()
    reference_path = options.out / 'reference_result.json'
    if options.verify_isa and not reference_path.exists():
        del eng
        reference_run(options, ids, request_id, tokenizer)
    if reference_path.exists():
        verify_reference(options.out, result)


if __name__ == '__main__':
    main()
