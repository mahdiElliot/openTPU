"""The board's single-run decode must match independent host-stepped decoding."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from opentpu import target as T
from opentpu.llm.qwen3 import Engine
from tools.zu3eg.run_board import device_decode


def test_device_decode_matches_host_steps(monkeypatch):
    from test_qwen35 import _tiny_model
    monkeypatch.setenv('OTPU_IMG_CACHE', '0')
    monkeypatch.setenv('OTPU_PROG_CACHE', '0')
    _, weights, spec = _tiny_model(4, 4)
    spec = replace(spec, kinds=('linear',))
    cfg = replace(T.config('mys-zu3eg:d64-m2-r4-cl2'), DRAM_BYTES=1 << 25)
    engines = [Engine(spec, weights, cfg=cfg, cap=256, rows=4, backend='isa',
                      resident=True, pipeline=False, snapshots=0, prog_cache=False,
                      wformat='int4', head_format='int8') for _ in range(2)]
    try:
        for eng in engines:
            for token in (13, 17, 19, 23):
                logits = eng.step(token)
        seed = int(np.argmax(logits))
        token, wanted = seed, []
        for _ in range(3):
            expected = engines[0].step(token)
            token = int(np.argmax(expected))
            wanted.append(token)
        actual, generated, _ = device_decode(engines[1], seed, 3)
        assert generated == wanted
        assert np.array_equal(actual.view('<u4'), expected.view('<u4'))
        assert engines[1].pos == 7
        with pytest.raises(ValueError, match='one attention bucket'):
            device_decode(engines[1], token, 250)
    finally:
        for eng in engines:
            eng._drain()


def test_reference_replays_the_requested_prefill_groups(tmp_path, monkeypatch):
    import tools.zu3eg.run_board as B
    from test_qwen35 import _tiny_model
    monkeypatch.setenv('OTPU_IMG_CACHE', '0')
    monkeypatch.setenv('OTPU_PROG_CACHE', '0')
    _, weights, spec = _tiny_model(4, 4)
    spec = replace(spec, kinds=('linear',))
    cfg = replace(T.config('mys-zu3eg:d64-m2-r6-cl2'), DRAM_BYTES=1 << 25)
    engines = [Engine(spec, weights, cfg=cfg, cap=256, rows=6, backend='isa',
                      resident=True, pipeline=False, snapshots=0, prog_cache=False,
                      wformat='int4', head_format='int8') for _ in range(2)]
    tokens = [13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59]
    monkeypatch.setattr(B, 'engine', lambda *args, **kw: engines[0])
    options = SimpleNamespace(model=None, out=tmp_path, target='mys-zu3eg:d64-m2-r6-cl2',
                              capacity=256, prefill_rows=6, image_rows=6,
                              device_decode=True, decode=2)
    tokenizer = SimpleNamespace(decode=lambda ids: str(ids))
    try:
        B.reference_run(options, tokens, 'grouped-reference', tokenizer)
        # A paired single-row reference is a different arithmetic schedule.
        assert len(engines[0].stats) == 4
        assert [s['rows'] for s in engines[0].stats[:2]] == [6, 6]
        for at in (0, 6):
            expected = engines[1].run_rows([(0, j) for j in range(at, at+6)],
                                           tokens[at:at+6], [5] if at == 6 else [])
        assert np.array_equal(np.fromfile(tmp_path/'reference_prefill_logits.bin', dtype='<u4'),
                              expected[0].view('<u4'))
    finally:
        for eng in engines:
            eng._drain()
