# Qwen3.5-0.8B on a MYIR MYS-ZU3EG K2: core evaluation

This workflow evaluates the **text decoder's 24 layers and LM head** with openTPU's
`int4` layer weights and `int8` head. It targets the MYS-ZU3EG-8E2D-EDGE-K2's
XCZU3EG-1SFVC784I and 2 GiB PS DDR4. The board identification is in
[MYIR's product manual](https://www.myir.cn/public/static/files/MYIR_product_manual.pdf).

The result is an accelerator-core evaluation, not a bootable board bitstream. The
Zynq PS/AXI DDR bridge, Arm-side transport, board clock integration and pin constraints
are outside this implementation. Resource figures below are measured on the actual
ZU3EG, without substituting or scaling another FPGA's results.

## Configuration

`targets/mys-zu3eg.toml` defines the architecture and microarchitecture:

| Setting | Value |
|---|---:|
| Slices / matrix depth / columns | 1 / 32 / 2 |
| Vector and quantizer lanes | 4 |
| Composite vector / matrix epilogue lanes | 1 / 1 |
| Activation memory | 2 rows, 192 blocks per row |
| Scratchpad | 65,536 32-bit words, 256 KiB logical |
| Scratchpad implementation | Single copy, true dual port, replay, no double pumping |
| Instruction memory | 32,768 words, 4,096 instructions |
| Scheduler window / matrix FIFO | 8 / 64 |
| Trace / DSTEP / STREAM / PAIR / WAITC | Disabled |
| Core clock / context capacity tested | 50 MHz / 256 tokens |

The initial 128 activation blocks passed the model's static dimension check but failed
compilation because several quantized operands are live together. With 192 blocks,
the complete model compiles at positions 0, 3 and 255 into 1,790, 1,952 and 1,944
instructions. The layout occupies **604,150,272 bytes (576.16 MiB)**. The model's
recurrent states remain fp32; `int4` is the repository's integer MX weight format,
not its separate `fp4` format. The vision tower and MTP draft layer are excluded.

## Measured physical implementation

Vivado 2022.2 synthesized and fully routed `otpu_asic_top` out of context on
**xczu3eg-sfvc784-1-i**. No fallback part was needed. The 50 MHz constraint also
applies 2 ns input and output delays at the core boundary.

| Resource | Used | ZU3EG available | Utilization |
|---|---:|---:|---:|
| LUTs, including LUT memory | 44,542 | 70,560 | 63.13% |
| Flip-flops | 34,482 | 141,120 | 24.43% |
| BRAM36-equivalent tiles | 121 | 216 | 56.02% |
| DSP48E2 | 174 | 360 | 48.33% |
| Occupied CLB sites | 8,299 | 8,820 | 94.09% |

Post-route setup slack is **+11.540 ns**, hold slack **+0.019 ns**, and total setup
and hold violations are zero. No latches or unresolved black boxes are reported.
The DRC report contains DSP pipeline warnings/advisories and an out-of-context
boundary-net warning, with no error-severity findings.

**Physical CLB packing is tight even though LUT utilization is 63%.** Adding the
PS/AXI interface requires another implementation run; the current figures do not
establish whole-board fit or board-level timing. The clock source is a core boundary
port, so these are out-of-context timing results, not a measured board clock limit.

Detailed results are retained under `build/zu3eg/vivado_win8/`:
`utilization_route.rpt`, `timing_route.rpt`, `drc_route.rpt`, and `core_route.dcp`.
The earlier 16-entry-window experiment is under `build/zu3eg/vivado/`; it occupied
96.42% of CLB sites and was superseded by the 8-entry configuration.

## Higher-performance candidates

The subsequent **128-PE, 112.5 MHz** comparison is documented in
[mys_zu3eg_128pe.md](mys_zu3eg_128pe.md). Its fastest tested configuration is D128-by-1,
at 9.637 simulated core tokens/s. The candidate status described below
records the earlier clock-only investigation.

The existing routed checkpoint was rechecked with different clock constraints;
the placement, routing, core RTL and validated 50 MHz target file were preserved.
This is static timing analysis of the out-of-context core, not a board clock test.

| Clock | Setup slack | Hold slack | Existing route |
|---|---:|---:|---|
| 50 MHz | +11.540 ns | +0.019 ns | Pass |
| 75 MHz | +4.873 ns | +0.019 ns | Pass |
| 100 MHz | +1.540 ns | +0.019 ns | Pass |
| 112.5 MHz | +0.429 ns | +0.019 ns | Pass, less margin |
| 125 MHz | -0.460 ns | +0.019 ns | Fails setup |

Reports are in `build/zu3eg/clock_checks/`. Reproduce this check with
`tools/zu3eg/clock_check.tcl`, passing the routed checkpoint and an output directory
as its two Tcl arguments. The worst reported setup path is in the instruction
memory/sequencer logic. A fresh implementation with tighter constraints may change
the maximum frequency; the failing 125 MHz check is not a device-wide frequency limit.
The missing board clock-source context and PS/AXI integration still require validation.

For single-sequence int4 decode, the first performance candidate is the existing
32-by-2 array at 100 MHz, with `PAIR` enabled after RTL validation. PAIR lets eligible
one-row int4 multiplies use both existing columns for two weight blocks. Its hardware
is already present; the compiler selects it through instruction flags. The complete
model compiles with `PAIR=True`, but this new mode has not had the full-token RTL
check used for the baseline. The int8 head does not use int4 PAIR.

Two 128-PE candidates also compile: D=64, MCOLS=2, ACT_BLOCKS=96; and D=32,
MCOLS=4, ACT_ROWS=4, ACT_BLOCKS=192 (both with PAIR). Neither candidate has been
synthesized or routed on this device. D=64 widens the core's memory data interface;
more columns mainly help prefill/batched rows. Their benefit depends on memory
bandwidth and physical packing. Compilation success is not proof of device fit.
Candidate compile results are in `build/zu3eg/performance_candidates.json`.

An optimal board configuration has not been established. Prioritize measured PS-DDR
bandwidth, PAIR validation, and a higher clock before spending the remaining CLB
capacity on a larger array. End-to-end token throughput still requires board measurements.

## Simulation method

The real checkpoint's SHA-256 was verified as
`04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696`.
On the ISA simulator, the raw prompt `The capital of France is` generated token IDs
`[11751, 13]`, decoded as ` Paris.`. The six executed token steps took approximately
5.6–6.2 seconds each on this laptop; image construction took 107.6 seconds. These
are laptop simulation times.

**The full-token RTL comparison passed.** At prompt position 4, Verilator executed
all 24 layers and the int8 LM head in **25,346,648 cycles**, retiring 13,694
instructions. All **604,150,272 compared bytes matched**, with zero differences.
The complete RTL run and comparison took 334.7 seconds (about 5.6 minutes) on this
laptop. The input state, program, expected state, actual state and JSON results are
retained under `build/zu3eg/qwen35_int4_h8/`.

The repository's native Verilator path is used for RTL validation. A small MLP on
the final microarchitecture matches the ISA simulator in every DRAM byte and TMEM
word and halts in 2,143 cycles. The target, ISA and compiler tests pass (70 tests),
and the Qwen architecture-matrix test passes for this target.

The supplied QuestaSim 2021.2_1 installation passes a standalone tool smoke test,
but its openTPU kernel test does not halt correctly. Declaration-order and known
initial-state compatibility attempts did not resolve it. Questa is consequently
not a validated simulator for this result. Diagnostic logs are retained under
`build/zu3eg/questa/` and `build/zu3eg/questa_check.log`.

The full-model workflow first runs a short prompt on the ISA simulator. Immediately
before its last prompt token, it saves the complete active DRAM image and compiled
program. Verilator then executes that **whole token through all 24 layers and the
LM head**, and its entire active DRAM image is compared byte for byte with the ISA's
result, including logits, KV cache, and DeltaNet/convolution state. Prompt-prefix
execution is on the ISA simulator; it is not presented as an RTL-prefill test.

The logical configuration has 2 GiB DRAM. The RTL behavioral model stores the used
lower 1-GiB window to reduce laptop memory and disk usage; the complete 576-MiB
active image is compared. This model is not a PS DDR4 controller/bandwidth model.
Simulation wall time or ideal-memory cycle counts do not establish board tokens/s.

`build/zu3eg/report.json` collects the current pass/fail status, model revision,
checkpoint checksum, target configuration, physical resources and timing.

## Reproduce from the repository root

The recorded environment uses Python 3.12.3, CPU PyTorch 2.14.1, Transformers
5.20.1 and NumPy 2.5.4. Use Python 3.12 for the pinned environment below.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e . -r tools/zu3eg/requirements.txt
python -m tools.zu3eg.setup_tools

export HF_HOME="$PWD/build/zu3eg/hf-cache"
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
python -m tools.zu3eg.download
python -m tools.zu3eg.evaluate preflight
python -m tools.zu3eg.check_kernel
python -m tools.zu3eg.evaluate isa --tokens 2
python -m tools.zu3eg.evaluate rtl
python -m tools.zu3eg.synthesize \
  --vivado /home/mahdi/vivado/Vivado/2022.2/bin/vivado
python -m tools.zu3eg.report
```

The downloader pins the official checkpoint revision and checks the full safetensors
SHA-256 after resumable HTTP-range transfers. Python dependencies use CPU-only
PyTorch. `setup_tools` configures the local Verilator 5.34.0 Python wheel for GCC
and installs its command wrapper inside `.venv`; a working C++ compiler and Make
are also required. The MXU's local running-max variable was renamed from `mk` to
`mx_key` to avoid a name collision with the imported FP package on this Verilator
version; its logic is unchanged.
