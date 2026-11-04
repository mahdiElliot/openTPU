"""A damaged table must be recomputed; a valid one must avoid fresh analysis."""
from types import SimpleNamespace

import numpy as np

from opentpu import isa as I
from tools.zu3eg import wait_cache as C
from tools.zu3eg.jtag import program_words


def test_verified_wait_cache_avoids_recomputation_and_repairs_damage(tmp_path, monkeypatch):
    monkeypatch.setenv('OTPU_ZU3EG_WAIT_CACHE', str(tmp_path))
    cfg = SimpleNamespace(D=64, S=1, SEQ_WC=True, IMEM_WORDS=128)
    p = [I.ld(0, 0, 8), I.st(256, 0, 8), I.halt()]
    expected = program_words(cfg, p)
    original = C.W.entries
    calls = []

    def compute(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(C.W, 'entries', compute)
    assert np.array_equal(program_words(cfg, p), expected)
    assert calls == []
    path = next(tmp_path.glob('*.npz'))
    np.savez_compressed(path, entries=np.zeros(3, np.uint64), key='damaged', sha256='bad')
    assert np.array_equal(program_words(cfg, p), expected)
    assert calls == [1]
    path.write_bytes(b'')
    assert np.array_equal(program_words(cfg, p), expected)
    assert calls == [1, 1]
