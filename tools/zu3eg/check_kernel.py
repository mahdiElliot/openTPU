"""Check the selected target's RTL memory and arithmetic against the ISA on a small MLP."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from opentpu import rtlsim, target as T
from opentpu.kernels import mlp
from opentpu.runtime import compile_kernel, run

ROOT = Path(__file__).resolve().parents[2]


def main():
    sys.path.insert(0, str(ROOT / "tests"))
    from test_kernels import mlp_args

    cfg = replace(T.config("mys-zu3eg"), DRAM_BYTES=1 << 20)
    uarch = T.sim_uarch("mys-zu3eg") | {
        "MXU_IMPL": 2, "MXU_CL": 16, "MXU_E": 1, "VPU_CL": 1,
        "ULANES": 4, "COLL_GATHER": 0,
    }
    args, _ = mlp_args(np.random.default_rng(11), M=2, H=64, Fd=128)
    comp, imgs = compile_kernel(mlp, cfg, **args)
    isa = run(comp, [im.copy() for im in imgs], "isa")
    rtlsim.VFLAGS[rtlsim.VFLAGS.index('-j') + 1] = '2'
    drams, tmems, stats = rtlsim.run(
        cfg, comp.programs, imgs, uarch=uarch, axi=False,
        keep=ROOT / "build/zu3eg/verilator_smoke", max_cycles=20000, trace=True)
    for a, b in zip(isa.drams, drams):
        assert np.array_equal(a, b), "DRAM differs"
    for a, b in zip(isa.tmems, tmems):
        assert np.array_equal(a, b), "TMEM differs"
    (ROOT / "build/zu3eg/verilator_smoke.json").write_text(json.dumps(stats, indent=2) + "\n")
    print("VERILATOR_ISA_BIT_EXACT", stats["cycles"], flush=True)


if __name__ == "__main__":
    main()
