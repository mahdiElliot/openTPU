"""Compile and simulate real Qwen3.5-0.8B: int4 layers, int8 LM head, MYIR ZU3EG core."""
from __future__ import annotations

import argparse
import gc
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from opentpu import isa as I, rtlsim, target as T
from opentpu.llm import load_spec
from opentpu.llm.qwen3 import Engine, load_weights

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / 'models/Qwen3.5-0.8B'
OUT = ROOT / 'build/zu3eg/qwen35_int4_h8'
TARGET = 'mys-zu3eg'


def write_json(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def uarch():
    t = T.load(TARGET)
    return T.sim_uarch(t) | {k: t.uarch[k] for k in ('MXU_IMPL', 'MXU_CL', 'MXU_E', 'VPU_CL', 'ULANES')} | {'COLL_GATHER': 0}


def preflight():
    cfg = T.config(TARGET)
    spec = load_spec(MODEL)
    image = spec.image(cfg, 256, rows=1, wformat='int4', head_format='int8', lookup=False)
    programs = []
    for pos in (0, 3, 255):
        counts = [len(p) for p in image.compile_step(pos)]
        assert max(counts) <= cfg.IMEM_WORDS // 8
        programs.append({'position': pos, 'instructions': counts})
    result = {'target': TARGET, 'config': asdict(cfg), 'uarch': uarch(),
              'weight_format': 'int4', 'head_format': 'int8', 'context_capacity': 256,
              'image_bytes': image.nbytes, 'imem_capacity_instructions': cfg.IMEM_WORDS // 8,
              'compiled_programs': programs, 'passed': True}
    write_json('preflight.json', result)
    print('PREFLIGHT_OK', image.nbytes, programs, flush=True)
    return cfg, spec


def isa_run(prompt, tokens):
    from transformers import AutoTokenizer

    cfg, spec = preflight()
    tokenizer = AutoTokenizer.from_pretrained(MODEL, local_files_only=True)
    ids = tokenizer(prompt).input_ids
    assert ids and len(ids) + tokens <= 256
    start = time.monotonic()
    print('Building quantized model image...', flush=True)
    engine = Engine(spec, load_weights(MODEL), cfg=cfg, cap=256, rows=1,
                    backend='isa', pipeline=False, resident=False,
                    wformat='int4', head_format='int8', prog_cache=False)
    image_bytes = engine.image.nbytes
    assert engine.image.wformat == 'int4' and engine.image.head_format == 'int8'
    assert {fmt for group in engine.image.lf for fmt in group} == {'int4'}
    print('IMAGE_BUILT', image_bytes, 'bytes;', round(time.monotonic() - start, 2), 's', flush=True)
    original = engine.backend.run
    snapshot = {}

    def run(programs, args=None):
        capture = engine.pos == len(ids) - 1 and not snapshot
        if capture:
            for s, (part, program) in enumerate(zip(engine.backend.machine.slices, programs)):
                part.dram[:image_bytes].tofile(OUT / f'rtl_input_{s}.bin')
                np.asarray(I.assemble(program), dtype='<u4').tofile(OUT / f'rtl_program_{s}.bin')
            snapshot.update(position=engine.pos, args=args, image_bytes=image_bytes,
                            compiled_instructions=[len(p) for p in programs], config=asdict(cfg),
                            uarch=uarch(), input_token=int(ids[-1]))
            print('SAVED_RTL_INPUT', snapshot['position'], flush=True)
        stats = original(programs, args)
        if capture:
            for s, part in enumerate(engine.backend.machine.slices):
                part.dram[:image_bytes].tofile(OUT / f'rtl_expected_{s}.bin')
            snapshot['isa_stats'] = stats
            write_json('rtl_snapshot.json', snapshot)
        return stats

    engine.backend.run = run
    times = []
    logits = None
    for i, token in enumerate(ids):
        begin = time.monotonic()
        logits = engine.step(int(token))
        assert np.isfinite(logits).all()
        elapsed = time.monotonic() - begin
        times.append(elapsed)
        print(f'ISA prompt {i + 1}/{len(ids)}: {elapsed:.2f} s', flush=True)
    generated = []
    for i in range(tokens):
        token = int(np.argmax(logits))
        generated.append(token)
        print('ISA generated:', generated, repr(tokenizer.decode(generated)), flush=True)
        if token in spec.eos or i + 1 == tokens:
            break
        begin = time.monotonic()
        logits = engine.step(token)
        assert np.isfinite(logits).all()
        times.append(time.monotonic() - begin)
    logits.astype('<f4').tofile(OUT / 'isa_final_logits.bin')
    result = {'prompt': prompt, 'prompt_tokens': ids, 'generated_tokens': generated,
              'generated_text': tokenizer.decode(generated), 'weight_format': 'int4',
              'head_format': 'int8', 'config': asdict(cfg), 'uarch': uarch(),
              'image_bytes': image_bytes, 'context_capacity': 256,
              'seconds_per_step': times, 'total_seconds': time.monotonic() - start,
              'all_logits_finite': True, 'passed': True}
    write_json('isa_result.json', result)
    print('ISA_COMPLETE', result['generated_text'], flush=True)
    engine._drain()
    del engine
    gc.collect()


def rtl_run(simulator, compact=False, mhz=50.0):
    snapshot = json.loads((OUT / 'rtl_snapshot.json').read_text())
    cfg = T.config(TARGET)
    assert asdict(cfg) == snapshot['config'] and uarch() == snapshot['uarch'], 'Snapshot configuration changed'
    progs, imgs = [], []
    for s in range(cfg.S):
        words = np.fromfile(OUT / f'rtl_program_{s}.bin', dtype='<u4').reshape(-1, 8)
        progs.append([I.Instr.decode(w) for w in words])
        imgs.append(np.memmap(OUT / f'rtl_input_{s}.bin', mode='r', dtype=np.uint8))
    active_bytes = 1 << (snapshot['image_bytes'] - 1).bit_length()
    # The logical target still has 2 GiB; only the used, lower 1-GiB window needs storage
    # in the behavioral DRAM. The complete used image is compared after the full token.
    original = rtlsim.build_top
    backend = original
    rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j') + 1] = '2'

    def bounded(cfg, dram_lat=8, uarch=None, axi=False, dram_bytes=None, ldc=None):
        return backend(cfg, dram_lat, uarch, False, active_bytes, None)

    rtlsim.build_top = bounded
    begin = time.monotonic()
    try:
        drams, _, stats = rtlsim.run(
            cfg, progs, imgs, uarch=snapshot['uarch'], args=snapshot['args'], axi=False,
            max_cycles=200_000_000, keep=OUT / f'{simulator}_run')
    finally:
        rtlsim.build_top = original
    comparisons = []
    for s, actual in enumerate(drams):
        expected = np.memmap(OUT / f'rtl_expected_{s}.bin', mode='r', dtype=np.uint8)
        different = 0
        for at in range(0, len(expected), 1 << 20):
            end = min(len(expected), at + (1 << 20))
            different += int(np.count_nonzero(actual[at:end] != expected[at:end]))
        comparisons.append({'slice': s, 'compared_bytes': len(expected), 'different_bytes': different})
    result = {'simulator': simulator, 'config': asdict(cfg), 'uarch': uarch(),
              'snapshot_position': snapshot['position'], 'physical_model_bytes': active_bytes,
              'weight_format': 'int4', 'head_format': 'int8', 'all_24_layers_and_lm_head': True,
              'seconds': time.monotonic() - begin, 'stats': stats, 'comparisons': comparisons,
              'clock_mhz': mhz, 'simulated_core_tokens_per_second': mhz * 1e6 / stats['cycles'],
              'passed': all(c['different_bytes'] == 0 for c in comparisons)}
    write_json(f'{simulator}_result.json', result)
    assert result['passed'], comparisons
    print('RTL_FULL_TOKEN_BIT_EXACT', simulator, stats['cycles'], flush=True)
    if compact:
        # Only remove reproducible binary simulation I/O after the comparison passes.
        for path in (OUT / f'{simulator}_run').glob('dram*.bin'):
            path.unlink()


def main():
    global OUT, TARGET
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('preflight', 'isa', 'rtl'))
    parser.add_argument('--simulator', choices=('verilator',), default='verilator')
    parser.add_argument('--prompt', default='The capital of France is')
    parser.add_argument('--tokens', type=int, default=2)
    parser.add_argument('--target', default=TARGET)
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--mhz', type=float, default=50.0)
    parser.add_argument('--compact', action='store_true', help='remove large RTL I/O files after successful comparison')
    options = parser.parse_args()
    OUT, TARGET = options.out.resolve(), options.target
    if options.tokens < 1 or options.mhz <= 0:
        parser.error('--tokens and --mhz must be positive')
    OUT.mkdir(parents=True, exist_ok=True)
    for variable in ('OTPU_IMAGE_CACHE', 'OTPU_IMG_CACHE', 'OTPU_PROG_CACHE'):
        os.environ[variable] = '0'
    os.environ.pop('OTPU_FORMATS', None)
    os.environ.setdefault('OTPU_BUILD_JOBS', '1')
    if options.stage == 'preflight':
        preflight()
    elif options.stage == 'isa':
        isa_run(options.prompt, options.tokens)
    else:
        rtl_run(options.simulator, options.compact, options.mhz)


if __name__ == '__main__':
    main()
