"""Screen 64x2 configurations with real-shape Qwen layers and ISA-checked PS RTL.

The reduced vocabulary avoids timing the whole LM head for every candidate.
These simulations rank candidates; physical-board runs determine tokens/s.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import time

import numpy as np

from opentpu import isa as I, rtlsim, target as T
from opentpu.isasim import Machine
from opentpu.llm import load_spec
from opentpu.llm.qwen3 import load_weights, rope_tables

from .evaluate import MODEL
from .sequence import save


def prefix(kind, weights, full):
    index = full.kinds.index(kind)
    old = f'model.layers.{index}.'
    kept = {k.replace(old, 'model.layers.0.', 1): v for k, v in weights.items()
            if k.startswith(old) or not k.startswith('model.layers.')}
    for key in ('model.embed_tokens.weight', 'lm_head.weight'):
        if key in kept:
            kept[key] = kept[key][:1024]
    return replace(full, kinds=(kind,), vocab=1024), kept


def measure(out, cfg, parameters, spec, weights, rows, head, bridge_source=None):
    image = spec.image(cfg, 256, rows=rows, wformat='int4', head_format='int8', lookup=False)
    need = image.nbytes + cfg.IMEM_WORDS * 4 + 4096
    cfg = replace(cfg, DRAM_BYTES=1 << max(20, (need - 1).bit_length()))
    image = spec.image(cfg, 256, rows=rows, wformat='int4', head_format='int8', lookup=False)
    payload = image.build(weights)[0]
    positions = list(range(32, 32 + rows))
    embeddings = np.stack([weights['model.embed_tokens.weight'][791+j] for j in range(rows)])
    tables = [rope_tables(spec, p) for p in positions]
    for key, value in [('x', embeddings), ('cos', np.stack([v[0] for v in tables])),
                       ('sin', np.stack([v[1] for v in tables]))]:
        raw = np.ascontiguousarray(value, dtype=np.float32).view(np.uint8).ravel()
        payload[image.io[key]:image.io[key]+raw.size] = raw
    programs = (image.compile_step(32) if rows == 1 else
                image.compile_rows([(0, p) for p in positions], [rows-1] if head else []))
    at = (len(payload) + 4095) & ~4095
    words = np.asarray(I.assemble(programs[0]), dtype='<u4').view(np.uint8)
    assert words.size <= cfg.IMEM_WORDS * 4
    dram = np.zeros(cfg.DRAM_BYTES, dtype=np.uint8)
    dram[:payload.size] = payload
    dram[at:at+words.size] = words
    reference = Machine(cfg, programs, [dram]).run(max_steps=1 << 34)
    out.mkdir(parents=True, exist_ok=False)
    dram.view('<u4').astype('>u4').tofile(out/'input.bin')
    sources = [rtlsim.RTL/f for f in T.CORE + T.TECH['generic'] + [
        'asic/otpu_asic_top.sv', 'shell/otpu_ctrl.sv',
        'boards/mys-zu3eg/otpu_ps_dram.sv', 'boards/mys-zu3eg/otpu_ps_dram_multi.sv',
        'boards/mys-zu3eg/otpu_zu3eg_accel.sv']]
    if bridge_source is not None:
        sources = [bridge_source.resolve() if p.name == 'otpu_ps_dram.sv' else p for p in sources]
    params = {k: getattr(cfg, k) for k in ('D', 'MCOLS', 'ACT_BLOCKS', 'ACT_ROWS')}
    params.update(parameters, WORDS=cfg.DRAM_BYTES//4)
    exe = rtlsim.build('tb_ps_core', sources + [
        rtlsim.TB/'otpu_ps_multi_mem.sv', rtlsim.TB/'tb_ps_core.sv'], params)
    begin = time.monotonic()
    result = rtlsim.run_sim([str(exe), f'+dir={out.resolve()}',
                            f'+program_addr={at}', f'+program_n={len(programs[0])}'], timeout=300)
    (out/'simulation.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert np.array_equal(np.fromfile(out/'dram_out.bin', dtype=np.uint8), reference.slices[0].dram)
    assert np.array_equal(rtlsim._read_hex(out/'tmem_0.hex', cfg.TMEM_WORDS), reference.slices[0].tmem)
    matches = re.search(r'PS_CORE_PASS cycles=(\d+) max_reads=(\d+)', result.stdout)
    counters = dict((k, int(v)) for k, v in re.findall(r'(\w+)=(\d+)',
                    re.search(r'COMPUTE_PROFILE.*', result.stdout)[0]))
    stats = {'cycles': int(matches[1]), 'cycles_per_token': int(matches[1])/rows,
             'max_reads': int(matches[2]), 'counters': counters,
             'simulation_seconds': time.monotonic()-begin, 'rows': rows,
             'instructions': len(programs[0]), 'isa_bit_exact': True, 'parameters': params,
             'bridge_source': str(bridge_source) if bridge_source else 'repository'}
    save(out/'result.json', stats)
    # Large, reproducible transport dumps are no longer needed after comparison.
    (out/'input.bin').unlink()
    (out/'dram_out.bin').unlink()
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--fifo', type=int, choices=(64, 128, 256), default=128)
    parser.add_argument('--rows', type=int, choices=(2, 4, 6), default=4)
    parser.add_argument('--capacity-rows', type=int, choices=(2, 4, 6),
                        help='physical activation rows; defaults to the tested group size')
    parser.add_argument('--epilogue', type=int, choices=(1, 2), default=2)
    parser.add_argument('--chunks', type=int, choices=(2, 4, 8), default=8)
    parser.add_argument('--hp-ports', type=int, choices=(1, 2), default=2)
    parser.add_argument('--reads', type=int, choices=(8, 16, 32), default=16)
    parser.add_argument('--latency', type=int, default=64)
    parser.add_argument('--cache-lines', type=int, choices=(1, 2, 4, 8), default=1)
    parser.add_argument('--bridge-source', type=Path, help='isolated bridge prototype for screening')
    parser.add_argument('--writes', type=int, choices=(1, 2, 4, 8), default=1)
    parser.add_argument('--window', type=int, choices=(8, 16), default=8)
    parser.add_argument('--vpu-cl', type=int, choices=(1, 2, 4), default=2)
    parser.add_argument('--kinds', default='linear,attn')
    parser.add_argument('--phases', default='prefill,decode')
    options = parser.parse_args()
    for key in ('OTPU_IMG_CACHE', 'OTPU_IMAGE_CACHE', 'OTPU_PROG_CACHE'):
        os.environ[key] = '0'
    os.environ['OTPU_Q35_ROWS_OVERLAP'] = '1'
    rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j')+1] = '2'
    cfg = replace(T.config('mys-zu3eg:d64-m2-r4-cl2'), ACT_ROWS=options.capacity_rows or options.rows)
    parameters = dict(HP_PORTS=options.hp_ports, MXU_E=options.epilogue, FIFO_DEPTH=options.fifo,
                      TMEM_NSB=4, TMEM_SBH=1, VPU_CL=options.vpu_cl, WIN=options.window, SEQ_FP_PIPE=1,
                      READ_DEPTH=options.reads, READ_CHUNKS=options.chunks,
                      A_CACHE_LINES=options.cache_lines,
                      WRITE_DEPTH=options.writes,
                      READ_LATENCY=options.latency)
    weights, full = load_weights(MODEL), load_spec(MODEL)
    results = {}
    for kind in options.kinds.split(','):
        spec, subset = prefix(kind, weights, full)
        for phase, rows, head in [('prefill', options.rows, False), ('decode', 1, True)]:
            if phase not in options.phases.split(','):
                continue
            key = kind + '_' + phase
            results[key] = measure(options.out/key, cfg, parameters, spec, subset, rows, head,
                                   options.bridge_source)
            print(key, json.dumps(results[key]), flush=True)
    save(options.out/'results.json', results)


if __name__ == '__main__':
    main()
