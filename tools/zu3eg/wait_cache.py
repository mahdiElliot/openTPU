"""Verified dependency-table cache, independent of the model/program caches."""
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile

import numpy as np

from opentpu import waits as W

ROOT = Path(__file__).resolve().parents[2]


def entries(words, cfg, program):
    location = os.environ.get('OTPU_ZU3EG_WAIT_CACHE', str(ROOT/'build/zu3eg/wait_entries'))
    if location == '0':
        return W.entries(words, cfg.D, cfg.S, prog=program)
    directory = Path(location)
    key = W.entries_key(words, cfg.D, cfg.S, getattr(program, 'run_args', None))
    path = directory/f'{key}.npz'
    try:
        with np.load(path, allow_pickle=False) as data:
            result = data['entries']
            if (result.dtype != np.uint64 or result.shape != (len(words)//8,) or
                str(data['key']) != key or
                str(data['sha256']) != hashlib.sha256(result.tobytes()).hexdigest()):
                raise ValueError('Invalid cached dependency table')
            return result.copy()
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        pass
    result = W.entries(words, cfg.D, cfg.S, prog=program)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix='waits-', suffix='.npz',
                                         delete=False) as stream:
            temporary = Path(stream.name)
            np.savez_compressed(stream, entries=result, key=key,
                                sha256=hashlib.sha256(result.tobytes()).hexdigest())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return result
