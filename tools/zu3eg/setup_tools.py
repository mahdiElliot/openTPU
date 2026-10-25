"""Configure the optional project-local Verilator wheel for this laptop's GCC toolchain."""
from __future__ import annotations

import shlex
import sys
from pathlib import Path


def main():
    import verilator

    root = Path(verilator.__file__).resolve().parent
    makefile = root / 'include/verilated.mk'
    text = makefile.read_text()
    empty = 'CFG_CXXFLAGS_PCH_I = \n'
    if empty in text:
        makefile.write_text(text.replace(empty, 'CFG_CXXFLAGS_PCH_I = -include\n'))
    wrapper = Path(sys.executable).parent / 'verilator'
    wrapper.write_text('#!/bin/sh\nexport VERILATOR_ROOT=' + shlex.quote(str(root)) + '\n'
                       + 'export CXXFLAGS="${CXXFLAGS:---std=c++20 -DVL_TIME_CONTEXT}"\n'
                       + 'exec ' + shlex.quote(str(root / 'bin/verilator')) + ' "$@"\n')
    wrapper.chmod(0o755)
    print('Project-local Verilator configured:', wrapper)


if __name__ == '__main__':
    main()
