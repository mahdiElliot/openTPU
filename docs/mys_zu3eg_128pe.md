# 128-PE Qwen3.5 decode at 112.5 MHz

The fastest of the three measured 128-PE geometries is **D=128, MCOLS=1**:
**9.637 simulated core tokens/s** for Qwen3.5-0.8B with integer int4 layer weights
and an int8 LM head. It completed a full-token RTL comparison and was synthesized,
placed and routed on the actual `xczu3eg-sfvc784-1-i` at 112.5 MHz.

This compares all three 128-PE geometries allowed by the current minimum D=32,
but is not an exhaustive microarchitecture search. It is not board end-to-end throughput. The FPGA core still needs its
Zynq PS/AXI memory bridge and host runtime.

The subsequent physical-board deployment is documented in
[mys_zu3eg_deployment.md](mys_zu3eg_deployment.md). Its new PS-DDR bridge runs
the complete 512-token prompt and 16 decode steps on the board, with bit-exact
ISA results. Measured device rates are 1.160 prefill tokens/s and 0.684 decode
tokens/s; the earlier 9.637 figure below remains an ideal-memory simulation.

## Decode benchmark

Each candidate processes the raw prompt `The capital of France is` on the ISA
simulator. At position 4, the exact program and complete active DRAM image are
saved. Verilator executes that token through all 24 text layers and the LM head,
and the entire resulting active image is compared with that candidate's ISA
reference. The vision encoder and MTP drafter are excluded.

| Candidate | PEs | Cycles per token | Core tokens/s at 112.5 MHz | Compared bytes | Differences |
|---|---:|---:|---:|---:|---:|
| D128, 1 column, no PAIR | 128 | 11,674,018 | **9.637** | 552,667,648 | 0 |
| D64, 2 columns, PAIR | 128 | 13,659,496 | **8.236** | 570,194,432 | 0 |
| D32, 4 columns, PAIR | 128 | 20,094,899 | **5.598** | 604,166,656 | 0 |

An independent ISA replay from each saved input also checks the entire 65,536-word
TMEM against the RTL dump, starting both with zero TMEM as in the standalone RTL
run. This checks scratchpad contents as well as the original engine's DRAM reference.

The D128 candidate is 17.0% faster than D64 and 72.1% faster than D32 on this
short-context, single-token decode workload. It uses all 128 PEs for the int8
head's single-row multiplies; the other layouts use 64 or 32 PEs for those
multiplies. PAIR uses the extra columns for eligible int4 operations but does not
apply to the int8 head. Four columns provide more parallel output rows, a different tradeoff
for prefill or batching. No prefill-throughput comparison was performed here.

Changing D changes quantization blocks and scale layout. Each candidate must
match its own ISA reference; their logits are not expected to be identical.
Greedy generation produced ` Paris,` for D64 and ` Paris.` for D32 and D128. These short
continuations are functional smoke checks, not model-quality evaluations.

The throughput calculation is `112,500,000 / measured_RTL_cycles`. The behavioral
memory has a fixed eight-cycle read latency, no PS-DDR arbitration, and the
candidate's own D-byte data interface. Thus the comparison also changes ideal
memory-interface width; ranking can change with a shared PS/AXI bandwidth limit.
It is not a bandwidth-calibrated model of the board. The simulation testbench's absolute time unit is irrelevant to this
cycle-based rate; timing closure is checked separately by Vivado. Host work,
program loading, compilation and token selection are excluded.

## Winning configuration

Use `mys-zu3eg:d128-m1` in `targets/mys-zu3eg.toml`:

| Parameter | Value |
|---|---:|
| S / D / MCOLS | 1 / 128 / 1 |
| PAIR | Disabled (requires at least two columns) |
| LANES / ULANES | 4 / 4 |
| MXU_E / VPU_CL | 1 / 1 |
| ACT_BLOCKS / ACT_ROWS | 48 / 1 |
| TMEM_WORDS | 65,536 (256 KiB logical) |
| IMEM_WORDS | 32,768 (4,096 instructions) |
| WIN / FIFO_DEPTH | 8 / 64 |
| TMEM | Single copy, two sub-banks, TDP, replay, no double pumping |
| DSTEP / STREAM / WAITC | Disabled |
| Logical DDR / context capacity | 2 GiB / 256 tokens |
| Implementation clock | **112.5 MHz** |

The baseline target's platform clock remains 50 MHz to preserve the earlier
validated experiment. The `--mhz 112.5` argument selects the implementation
constraint and rate calculation for this comparison.

The D64 candidate uses `mys-zu3eg:d64-m2`: two ACT rows, 96 ACT blocks,
32,768 instruction words and MXU_E=2, with PAIR enabled.
The D32 candidate uses `mys-zu3eg:d32-m4`: four ACT rows, 192 ACT blocks, and
16,384 instruction words. Its longest checked program is 1,910 instructions,
so it uses half the baseline instruction memory; it also enables PAIR and MXU_E=2.
All three compile at positions 0, 3 and 255 within their instruction capacities.

## Exact-part implementation

The winning D128 route uses 50,222 LUTs (71.18%), 39,475 registers (27.97%),
142 BRAM36-equivalent tiles (65.74%) and 229 DSP48E2s (63.61%). Setup slack is
**+2.010 ns**, hold slack **+0.020 ns**, and pulse-width slack **+3.872 ns**.
All specified timing constraints pass; the core boundary has 2 ns input/output
delay constraints. No substitute FPGA or resource scaling was used.

| Layout | Routed LUTs | BRAM tiles | DSPs | Occupied CLBs | Setup slack | Hold slack |
|---|---:|---:|---:|---:|---:|---:|
| D128 x 1 | 50,222 | 142 | 229 | 8,559 (97.04%) | +2.010 ns | +0.020 ns |
| D64 x 2 | 48,276 | 130 | 244 | 8,554 (96.98%) | +2.159 ns | +0.014 ns |
| D32 x 4 | 48,840 | 117 | 262 | 8,475 (96.09%) | +1.915 ns | +0.004 ns |

**The winner occupies 8,559 / 8,820 CLBs (97.04%).** This is tight physical packing,
despite the lower LUT percentage. A complete PS/AXI system still needs another
implementation run, and this core fit does not guarantee whole-board fit or
board-level timing. Existing out-of-context clock-source limitations also apply.

The machine-readable comparison, including both candidates' final physical
status, is `build/zu3eg/perf128/comparison.json`. Per-candidate artifacts are
under `build/zu3eg/perf128/d128_m1/`, `d64_m2/` and `d32_m4/`: `model/` contains ISA/RTL
results and reference snapshots; `vivado/` contains routed timing/utilization
reports and the implementation checkpoint.

The one-column configuration exposed a zero-width MXU column-index field. Its
minimum width was corrected in `rtl/mxu/otpu_mxu.sv`; configurations with two or
more columns retain the same widths. The new tests in `tests/test_zu3eg_mxu.py`
verify one- and two-column ACC, ASCALE and RMAX results in both DRAM and TMEM
against the ISA (two passed). The 70 target, compiler and ISA tests also pass.

## Reproduce

### Complete 64-input / 64-output request

The separate sequence runner executes every prompt and decode step in Verilator;
it does not warm the prefix with the ISA simulator. Every step's full active DRAM
and scratchpad are checked against an ISA reference. Run:

```sh
source .venv/bin/activate
export HF_HOME="$PWD/build/zu3eg/hf-cache"
export HF_HUB_OFFLINE=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
python -u -m tools.zu3eg.sequence --input-tokens 64 --output-tokens 64 \
  --ignore-eos --mhz 112.5 --out "build/zu3eg/sequence64_$(date +%Y%m%d_%H%M%S)"
```

This uses `mys-zu3eg:d128-m1`, int4 weights, int8 head, and an automatically
constructed raw prompt of exactly 64 tokenizer tokens (saved in `request.json`).
For your own prompt, add `--prompt-file prompt.txt`; it must tokenize to exactly
64 tokens without a chat template. `--ignore-eos` forces 64 outputs even if the
model emits EOS; omit it to allow early termination.

The output directory must be new. `result.json` is updated after each step and
marked `complete: true` at the end. Its
`simulated_request_output_tokens_per_second` is
`64 * 112,500,000 / (prefill_cycles + decode_cycles)` when all 64 outputs finish.
There are 64 prefill and 63 decode forward passes: prefill produces the first
output's logits. `simulated_time_to_first_token_seconds` and
`simulated_decode_tokens_per_second` report the two phases separately.
`laptop_wall_output_tokens_per_second` includes simulation, compilation, I/O,
verification and sampling after model setup; model build time is recorded
separately. Temporary RTL images are removed after each step.

Allow several hours for 127 full-model forward passes. This measures a complete
request under the existing idealized core memory model, **not board end-to-end
performance**. The cycle-based rate excludes host transfers, program loads and
sampling delays, and does not model Zynq DDR contention.

### Original single-token comparison

Activate the environment and ensure the checkpoint is downloaded as described
in `docs/mys_zu3eg.md`. Run from the repository root:

```sh
source .venv/bin/activate
export HF_HOME="$PWD/build/zu3eg/hf-cache"
export HF_HUB_OFFLINE=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2

python -m tools.zu3eg.evaluate isa --target mys-zu3eg:d128-m1 \
  --out build/zu3eg/perf128/d128_m1/model \
  --prompt "The capital of France is" --tokens 2
python -m tools.zu3eg.evaluate rtl --target mys-zu3eg:d128-m1 \
  --out build/zu3eg/perf128/d128_m1/model --mhz 112.5 --compact
python -m tools.zu3eg.verify_snapshot build/zu3eg/perf128/d128_m1/model
python -m tools.zu3eg.synthesize --target mys-zu3eg:d128-m1 \
  --mhz 112.5 --out build/zu3eg/perf128/d128_m1/vivado
```

For the other candidates, replace `d128-m1` with `d64-m2` or `d32-m4`, and use
the corresponding output directory `d64_m2` or `d32_m4`. Then run
`python -m tools.zu3eg.performance_report`.
The optional `--compact` removes reproducible RTL DRAM I/O files only after a
successful comparison, retaining reference snapshots and JSON results.
