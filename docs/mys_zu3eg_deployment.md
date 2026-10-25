# MYS-ZU3EG physical-board deployment

For the configurable **D128 × 1**, **128/16** workload with independent core and
AXI clocks, follow [the complete JTAG guide](mys_zu3eg_jtag_128_16.md). The
measurements below remain the historical D128 × 1, 112.5-MHz run.

The updated guide selects **150-MHz core / 325-MHz HP AXI**, with PS CPU and
physical DDR clocks preserved. That D128 × 1 full-system route passes with
+0.425 ns setup, +0.010 ns hold and +0.038 ns pulse-width slack, and 20 safe
CDC endpoints with none unsafe or unknown. It occupies 8,555/8,820 CLB sites.
The 150/325-MHz bitstream subsequently passed physical int8/int4 kernel checks
and a complete 128-token prefill / 16-step decode run, with logits and token IDs
bit-identical to the ISA reference. Measured prefill was 1.1005 device / 0.3724
wall tokens/s; decode was 0.6445 device / 0.02875 wall tokens/s. The request took
900.27 seconds, excluding model upload and reference computation. Timing reports
are under `build/zu3eg/d128m1_dual150_325/hardware/`; run results are under
`build/zu3eg/d128m1_128_16/run/`. These generated files are ignored by Git.

The deployment under `boards/mys-zu3eg/` connects the validated
`mys-zu3eg:d128-m1` core to the Zynq PS: 128 PEs, 112.5 MHz, int4 weights,
int8 LM head, one slice, 256 KiB logical scratchpad and 4,096 instructions.
It is separate from the older out-of-context core measurements.

## Physical-board result, 2026-10-24

The complete request finished on the connected board. All 248,320 prefill
logits, the final logits, and all generated token IDs match the independently
executed ISA reference bit for bit. The physical int8 and int4 MLP checks also
passed, comparing every byte of their 1-MiB DRAM images.

| Phase | Tokens / steps | FPGA RUN cycles | Device tokens/s | Wall tokens/s over JTAG |
|---|---:|---:|---:|---:|
| Prefill | 512 | 49,650,186,506 | 1.1601 | 0.4173 |
| Decode | 16 | 2,632,028,936 | 0.6839 | 0.02861 |

Prefill took 441.335 seconds of accelerator RUN time and 1,226.888 seconds
including the host. Decode took 23.396 device seconds and 559.326 wall seconds.
The complete request took **1,786.214 seconds (29.77 minutes)** after setup.
Its rate is **0.008957 measured decode steps/s**, or **0.009517 output tokens/s**
when counting the initial token selected from prefill plus the 16 measured
decode steps. Model upload and quantization are excluded from those times.

This is substantially slower than the earlier 9.637-token/s ideal-memory
single-token simulation. The physical bridge serializes AXI bursts, and JTAG
program/input transfers and full-logit readbacks dominate wall time. These
results do not demonstrate the maximum throughput of an optimized PS-DDR bridge
or an Arm-local runtime.

The complete system routes on `xczu3eg-sfvc784-1-i` with setup slack +1.444 ns,
hold slack +0.010 ns and pulse-width slack +2.000 ns. It uses 52,384 LUTs,
42,198 registers, 142 BRAM tiles and 229 DSPs, occupying 8,600/8,820 CLB sites
(97.51%). A host-timer sanity check measured approximately 112.25 MHz, consistent
with the nominal 112.5-MHz clock within the measurement's JTAG overhead.

The consolidated result is `build/zu3eg/deployment512_16/report.json`, generated
by `python -m tools.zu3eg.deployment_report`. Per-run results and numerical
checks are under `run/`; final timing/utilization are under `hardware/`.

## Hardware and boot assumptions

This first bring-up programs **volatile PL configuration** over JTAG and uses
PS DDR already initialized by the board's existing bootloader. It does not
contain a verified MYIR DDR preset for cold boot. Do not run the generated
project's default `psu_init()` or use its default FSBL on this board.

The detected board has a 33.333333-MHz PS reference and an existing 100-MHz
PL0 clock from RPLL. Clock Wizard produces 112.5 MHz. The programming script
checks the observed clock registers and requires normal DDR operation. It
halts Cortex-A53 #0 before accelerator memory use; the benchmark owns DDR
starting at physical `0x10000000`. The model and programs overwrite this
volatile region. Reboot the board to return to its original boot software.

The accelerator control window is `0x80000000`. The reused `otpu_ctrl` provides
start/stop, loading, arguments, completion, errors and run-cycle counters.
Other activity/DRAM-bandwidth counters are not wired in this first wrapper;
do not interpret them as measured bandwidth or utilization.

## Memory bridge and verification

`rtl/boards/mys-zu3eg/otpu_ps_dram.sv` converts core word, scalar-store and
128-byte chunk requests to the PS `S_AXI_HP0_FPD` interface, 128 bits per beat.
It currently allows one burst outstanding, with eight beats per chunk and a
32-byte scale-read cache. Writes use byte strobes and drain their AXI responses
before completion; reads retain the core's response tags. This conservative
bridge establishes correctness and is not a bandwidth-optimized controller.

Run `python -m pytest -q tests/test_zu3eg_ps.py` with the project environment
activated. The tests cover AXI stalls, delayed responses, partial writes,
cache invalidation, error reporting, and a complete MLP through control,
instruction loading and the core. The full MLP DRAM and TMEM match the ISA.

## Build and run

From the repository root, with the board booted and JTAG connected:

```sh
source .venv/bin/activate
export HF_HOME="$PWD/build/zu3eg/hf-cache" HF_HUB_OFFLINE=1
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
python -m tools.zu3eg.build_board
python -m tools.zu3eg.build_board --loader \
  --out build/zu3eg/deployment512_16/loader
python -m tools.zu3eg.program_board
python -u -m tools.zu3eg.run_board --kernel-only
python -m tools.zu3eg.run_board --prepare
python -u -m tools.zu3eg.load_model
python -u -m tools.zu3eg.run_board --preloaded --verify-isa
```

The programming script requires passing setup and hold timing. A physical
clock sanity check compares the accelerator cycle counter with a host timer.
A small physical-board MLP must match the ISA before the model runs.

Direct XSDB memory writes measured about 30 KiB/s. The temporary
`build_loader.tcl` design uses AMD's queued 64-bit JTAG-to-AXI master to load
DDR at about 300-350 KiB/s. It checks sampled readbacks, then restores the
accelerator without resetting DDR. Loading the 532-MiB image took about
30 minutes. This remains a JTAG-only flow; Ethernet is not required.

The configurable runner now also stores a model-image layout/checksum manifest;
prepare a fresh output directory when switching from an older image without one.
The initial image must be loaded again before a new request, since inference
changes its KV and recurrent state. Do not run the kernel checks after bulk
loading: their scratch image uses the beginning of the model's DDR region.

The workload uses the first 512 tokenizer tokens of
`tools/data/austen_pp_ch1.txt`, followed by 16 greedy decode **steps**, matching
`tools/qual/perf.py` with `--prompt 512 --tokens 16`. The prompt's final logits
provide the seed; the 16 measured steps produce 16 further token decisions.
This convention differs from a request limited to 16 outputs, which needs
only 15 additional steps after prefill.

Context capacity is 1,024 tokens. Prefill uses one row per run, omitting the LM
head except on the last prompt row. The model image occupies 557,509,120 bytes.
Programs at positions 0, 3, 255, 256, 511, 512 and 527 fit the 4,096-instruction
limit. Model setup and initial JTAG upload are excluded from request timing.

Artifacts go under `build/zu3eg/deployment512_16/`. The run's `result.json`
separates device-cycle rates, prefill/decode wall rates and complete-request
rates. Wall rates include the PC compiler, JTAG transfers, polling and sampling;
they are not Arm-local runtime measurements. Optional ISA replay occurs after
the timed request. `--reference-only` can instead prepare the same reference
independently while hardware work continues.

The provided transport is specific to this bring-up register map; it does not
use the PCIe driver's two-channel interleaving or its capability/hash checks.

The Zynq system still needs a validated cold-boot DDR preset and an Arm-local
runtime for standalone deployment and lower host overhead.
