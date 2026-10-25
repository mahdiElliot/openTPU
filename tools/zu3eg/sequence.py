"""Measure a complete prompt and continuation on the winning ZU3EG RTL profile."""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import numpy as np

from opentpu import rtlsim, target as T
from opentpu.isasim import Machine
from opentpu.llm import load_spec
from opentpu.llm.qwen3 import Engine, load_weights
from opentpu.llm.rtl_backend import RtlBackend
from .evaluate import MODEL

TARGET = 'mys-zu3eg:d128-m1'


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def metrics(steps, mhz, generated):
    prefill = sum(s['cycles'] for s in steps if s['phase'] == 'prefill')
    decode = sum(s['cycles'] for s in steps if s['phase'] == 'decode')
    frequency = mhz * 1e6
    seconds = (prefill + decode) / frequency
    return {'prefill_cycles': prefill, 'decode_cycles': decode,
            'total_cycles': prefill + decode,
            'simulated_time_to_first_token_seconds': prefill / frequency,
            'simulated_request_seconds': seconds,
            'simulated_request_output_tokens_per_second': generated / seconds if seconds else None,
            'simulated_decode_tokens_per_second':
                sum(s['phase'] == 'decode' for s in steps) * frequency / decode if decode else None}


class CheckedRtlBackend(RtlBackend):
    """Carry RTL DRAM between tokens; check every complete step against the ISA."""

    def __init__(self, cfg, images, uarch):
        super().__init__(cfg, images, uarch=uarch, axi=False, boot=False)
        self.steps = []
        self.phase = 'prefill'

    def run(self, programs, args=None):
        # Like RtlBackend, each invocation starts with zeroed scratchpad. All persistent
        # model state is in DRAM, including Qwen3.5's DeltaNet and convolution state.
        begin = time.monotonic()
        reference = Machine(self.cfg, programs, self.drams, args).run(max_steps=1 << 40)
        active_bytes = 1 << (max(map(len, self.drams)) - 1).bit_length()
        build_top = rtlsim.build_top

        def bounded(cfg, dram_lat=8, uarch=None, axi=False, dram_bytes=None, ldc=None):
            return build_top(cfg, dram_lat, uarch, False, active_bytes, None)

        with patch.object(rtlsim, 'build_top', bounded):
            drams, tmems, stats = rtlsim.run(
                self.cfg, programs, self.drams, dram_lat=8, max_cycles=200_000_000,
                uarch=self.uarch, axi=False, boot=False, args=args)
        for s, (actual, tmem, part) in enumerate(zip(drams, tmems, reference.slices)):
            nbytes = len(self.drams[s])
            for at in range(0, nbytes, 1 << 20):
                end = min(nbytes, at + (1 << 20))
                if not np.array_equal(actual[at:end], part.dram[at:end]):
                    raise RuntimeError(f'RTL/ISA DRAM mismatch: slice {s}, offset {at}')
            if not np.array_equal(tmem, part.tmem):
                raise RuntimeError(f'RTL/ISA TMEM mismatch: slice {s}')
        self.drams = [d[:len(old)].copy() for d, old in zip(drams, self.drams)]
        self.steps.append({'phase': self.phase, 'cycles': stats['cycles'],
                           'rtl_and_isa_seconds': time.monotonic() - begin,
                           'dram_and_tmem_bit_exact': True})
        return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=MODEL)
    parser.add_argument('--prompt-file', type=Path, help='raw text, exactly --input-tokens tokens')
    parser.add_argument('--input-tokens', type=int, default=64)
    parser.add_argument('--output-tokens', type=int, default=64)
    parser.add_argument('--ignore-eos', action='store_true', help='force the fixed output length')
    parser.add_argument('--mhz', type=float, default=112.5)
    parser.add_argument('--out', type=Path, required=True, help='new output directory')
    options = parser.parse_args()
    if min(options.input_tokens, options.output_tokens) < 1 or not 0 < options.mhz < float('inf'):
        parser.error('token counts and frequency must be positive and finite')
    if options.input_tokens + options.output_tokens > 256:
        parser.error('the configured context capacity is 256 tokens')
    for variable in ('OTPU_IMAGE_CACHE', 'OTPU_IMG_CACHE', 'OTPU_PROG_CACHE'):
        os.environ[variable] = '0'
    os.environ.pop('OTPU_FORMATS', None)
    os.environ.setdefault('OTPU_BUILD_JOBS', '1')
    rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j') + 1] = '2'
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(options.model, local_files_only=True)
    if options.prompt_file:
        prompt = options.prompt_file.read_text()
    else:
        text = ('Explain how a small FPGA can run a language model using quantized weights. '
                'Discuss memory bandwidth, parallel processing, and the difference between '
                'processing a prompt and generating the next token. ')
        ids = tokenizer(text * options.input_tokens, add_special_tokens=False).input_ids
        prompt = tokenizer.decode(ids[:options.input_tokens], clean_up_tokenization_spaces=False)
    ids = tokenizer(prompt, add_special_tokens=False).input_ids
    if len(ids) != options.input_tokens:
        parser.error(f'prompt has {len(ids)} tokens; expected {options.input_tokens} (raw text, no chat template)')
    options.out.mkdir(parents=True, exist_ok=False)
    os.environ.setdefault('OTPU_SIM_TMP', str(options.out.resolve() / 'tmp'))
    request = {'target': TARGET, 'clock_mhz': options.mhz, 'prompt': prompt,
               'prompt_tokens': ids, 'requested_output_tokens': options.output_tokens,
               'ignore_eos': options.ignore_eos, 'weight_format': 'int4', 'head_format': 'int8',
               'context_capacity': 256, 'memory': 'behavioral, eight-cycle reads, no bandwidth calibration',
               'board_end_to_end_tokens_per_second': None}
    save(options.out / 'request.json', request)
    cfg = T.config(TARGET)
    target = T.load(TARGET)
    uarch = T.sim_uarch(target) | {
        k: target.uarch[k] for k in ('MXU_IMPL', 'MXU_CL', 'MXU_E', 'VPU_CL', 'ULANES')}
    uarch['COLL_GATHER'] = 0
    spec = load_spec(options.model)
    print(f'Building model; {len(ids)} input tokens, up to {options.output_tokens} output tokens.', flush=True)
    build_begin = time.monotonic()
    engine = Engine(spec, load_weights(options.model), cfg=cfg, cap=256, rows=1,
                    backend=lambda c, images: CheckedRtlBackend(c, images, uarch),
                    pipeline=False, resident=False, snapshots=0,
                    wformat='int4', head_format='int8', prog_cache=False)
    build_seconds = time.monotonic() - build_begin
    generated = []
    request_begin = time.monotonic()

    def progress(complete=False):
        elapsed = time.monotonic() - request_begin
        result = request | {'complete': complete, 'config': asdict(cfg), 'uarch': uarch,
                            'generated_tokens': generated, 'generated_text': tokenizer.decode(generated),
                            'model_build_wall_seconds': build_seconds,
                            'request_wall_seconds': elapsed,
                            'laptop_wall_output_tokens_per_second': len(generated) / elapsed,
                            'all_steps_bit_exact': all(s['dram_and_tmem_bit_exact'] for s in engine.backend.steps),
                            'steps': engine.backend.steps} | metrics(engine.backend.steps, options.mhz, len(generated))
        save(options.out / 'result.json', result)
        return result

    try:
        for index, token in enumerate(ids):
            logits = engine.step(int(token))
            if not np.isfinite(logits).all():
                raise RuntimeError('non-finite logits')
            progress()
            print(f'RTL prefill {index + 1}/{len(ids)}: {engine.stats[-1]["cycles"]} cycles, bit exact', flush=True)
        for index in range(options.output_tokens):
            if index:
                engine.backend.phase = 'decode'
                logits = engine.step(generated[-1])
                if not np.isfinite(logits).all():
                    raise RuntimeError('non-finite logits')
            token = int(np.argmax(logits))
            generated.append(token)
            progress()
            print(f'RTL output {index + 1}/{options.output_tokens}: {tokenizer.decode(generated)!r}', flush=True)
            if token in spec.eos and not options.ignore_eos:
                break
        result = progress(complete=True)
        print(json.dumps({k: v for k, v in result.items() if k not in ('steps', 'prompt_tokens', 'generated_tokens', 'config', 'uarch')}, indent=2), flush=True)
        print(f'Result: {options.out / "result.json"}', flush=True)
    finally:
        engine._drain()


if __name__ == '__main__':
    main()
