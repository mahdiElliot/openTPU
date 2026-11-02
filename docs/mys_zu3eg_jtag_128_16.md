# Qwen3.5-0.8B on MYS-ZU3EG K2: 256-bit queued DDR, 128 PEs and 128/16 inference

This guide builds and deploys the current queued-bridge design on the **MYIR
MYS-ZU3EG K2, 2 GiB PS DDR4**, using the exact `xczu3eg-sfvc784-1-i` part.
Qwen3.5-0.8B uses **int4 decoder weights and an int8 LM head**, with **128 input
tokens and 16 decode steps**. JTAG programs and controls the accelerator. Ethernet can transfer the model,
inference inputs, programs and output logits; a JTAG data path is also available.

For terminal chat using the newer four-row weight-reuse build on this PC, see
[section 15C](#15c-interactive-chat-over-ethernet). That section supplies its
own hardware and model paths; sections 1–14 retain the original one-row profile.

## 1. Selected configuration and validation

| Setting | Value |
|---|---|
| Target | `mys-zu3eg:d128-m1` |
| Array | `S=1`, `D=128`, `MCOLS=1`: 128 PEs |
| `--axi-data-width` | 256 is the selected bridge width; 128 is available for comparisons. The PS port stays 128 bits. Keep any downsizing after the clock crossing. |
| Core / HP AXI clocks | **177.5 / 332.8125 MHz** |
| Activation memory | `ACT_BLOCKS=48`, `ACT_ROWS=1` |
| Vector / quantization lanes | `LANES=4`, `ULANES=4`, `VPU_CL=1` |
| MXU | Systolic, `MXU_CL=16`, `MXU_E=1`, `PAIR=false` |
| Scratchpad | `TMEM_WORDS=65536`: 256 KiB logical storage |
| Instruction memory | `IMEM_WORDS=32768`: 4,096 instructions |
| Scheduling / weight FIFO | `WIN=8`, `FIFO_DEPTH=64` |
| TMEM | Implementation 3, two sub-banks, replay, true dual-port, no pumping |
| Disabled features | `DSTEP`, `STREAM`, `SEQ_WC`; `TMEM_PMP=0` |
| DDR bridge | 8 outstanding reads; A/B/SW input queues of 4/8/4 requests |
| Core-side AXI / D128 burst | 256 bits / 4 beats per chunk |
| HP AXI / D128 burst | 128 bits / 8 beats per chunk |
| Context capacity | 256 tokens |
| Prefill / decode | 128 input tokens / 16 additional forward passes |
| Prepared image | 552,667,648 bytes, approximately 527.1 MiB |

The complete system was synthesized, placed and routed in Vivado 2022.2 on
2026-10-25. The bitstream and XSA are ready; no board programming was performed.

| Routed check | Result |
|---|---:|
| Setup slack | +0.011 ns |
| Hold slack | +0.012 ns |
| Pulse-width slack | +0.002 ns |
| Setup / hold / pulse-width failing endpoints | 0 / 0 / 0 |
| CDC endpoints | 20 safe, 0 unsafe, 0 unknown |
| CLB LUTs | 55,616 / 70,560 (78.82%) |
| Registers | 49,758 / 141,120 (35.26%) |
| Occupied CLB sites | 8,779 / 8,820 (99.54%) |
| BRAM tiles | 148.5 / 216 (68.75%) |
| DSPs | 229 / 360 (63.61%) |

The MMCM uses a 100-MHz input, input divider 2 and feedback multiplier 26.625:
a 1,331.25-MHz VCO divided by 7.5 for the core and by 4 for HP AXI. Reported
clock frequencies can differ slightly because `clocks.csv` uses rounded
periods. Setup and HP AXI pulse-width margins are small but positive; retain all
timing checks when rebuilding.

The small RTL tests compare int8/int4 kernels against the ISA reference and
exercise read queues, tags, masked writes, backpressure, ordering and cache
invalidation. The full Qwen model must also pass the physical checks in
sections 11–16 before reporting board tokens/s for this implementation.

## 2. Clock and memory organization

```text
PS DDR controller and physical DDR: existing board boot configuration
PS PL0 reference: 100 MHz
  └── Clock Wizard
        ├── core clock: compute, control registers and queued DDR bridge
        └── HP AXI clock: PS-facing memory interface

compute + 256-bit bridge ── 256-bit AXI Clock Converter ── 256-to-128 downsizer ── HP0 ── PS DDR
       core clock                  clock crossing              HP AXI clock
```

`--mem-mhz` sets the **HP AXI interface clock**. It does not change the Arm CPU
or physical DDR clock. The two PL domains use an AXI Clock Converter with
three synchronization stages and a synchronized reset for each domain.

The wide stream crosses clocks before downsizing; the downsizer is clocked at
332.8125 MHz. This avoids a 128-bit bottleneck at the 177.5-MHz core clock.
The ideal interface ceiling is 5.325 GB/s, before DDR and protocol overhead.

The bridge accepts requests into separate input queues, issues up to eight
reads before their data returns, and overlaps independent reads with writes.
A write completes only after BRESP. The scale cache holds one 32-byte line;
flushes also invalidate pending fills. See [bridge details](mys_zu3eg_ddr_bridge.md).

**Boot prerequisite:** the board must already have working PS DDR initialization
from its normal boot firmware. Linux is not required. This flow preserves that
initialization; the repository does not supply a verified MYIR cold-boot DDR
preset or replacement FSBL. Model memory starts at physical `0x10000000` and
must be reserved for this deployment. JTAG-only data transfers halt Cortex-A53
core 0; Ethernet data transfers keep its low-DDR monitor running. The helpers
preserve DDR during PL reconfiguration. Rebooting loses the
loaded model. The commands do not install firmware in SD, eMMC or QSPI.

## 3. Install and activate the host environment

Run every command from the repository root. The paths below match this PC;
adjust the root and vendor-tool locations for another installation.

```bash
cd /home/mahdi/Documents/brochrue/project/openTPU

# Install missing Ubuntu prerequisites once.
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential usbutils libusb-1.0-0

# Python 3.12 was used for this environment.
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e . -r tools/zu3eg/requirements.txt
python -m tools.zu3eg.setup_tools
python -m pip check
verilator --version

export VIVADO=/home/mahdi/vivado/Vivado/2022.2/bin/vivado
export VITIS_XSDB=/home/mahdi/vivado/Vitis/2022.2/bin/xsdb
test -x "$VIVADO"
test -x "$VITIS_XSDB"
"$VIVADO" -version
```

With an existing environment, activate it and check the tools instead of
creating it again. The pinned dependencies include CPU PyTorch, Transformers,
NumPy, safetensors, Hugging Face Hub, pytest, Textual and Verilator.
`setup_tools` configures the Verilator wrapper for the installed GCC.

Vivado and XSDB **2022.2** are required for the FPGA/JTAG flow. Ethernet loading
also uses **Vitis 2022.2**, its Arm compiler, XSCT and standalone lwIP libraries.
QuestaSim is not needed for these tests. Vendor paths are configured in the
helpers; `build_board --vivado` overrides the Vivado build executable.
Keep several GiB of RAM plus swap and approximately 30 GiB of disk available.

## 4. Download and verify the checkpoint

```bash
export HF_HOME="$PWD/build/zu3eg/hf-cache"
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
mkdir -p "$HF_HOME"
unset HF_HUB_OFFLINE
python -u -m tools.zu3eg.download
sha256sum models/Qwen3.5-0.8B/model.safetensors-00001-of-00001.safetensors
```

Expected SHA-256:

```text
04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696
```

The downloader pins checkpoint revision
`2fc06364715b967f1860aea9cf38778875588b17` and resumes interrupted transfers.
Once the checkpoint and tokenizer are complete:

```bash
export HF_HUB_OFFLINE=1
```

This deployment runs the 24-layer text decoder and its LM head. The checkpoint's
vision tower and MTP drafter are not executed. The repository's integer `int4`
format is selected; `fp4` denotes a different format.

## 5. Define the deployment directories and workload

```bash
export TARGET='mys-zu3eg:d128-m1'
export DEPLOY="$PWD/build/zu3eg/wide_128_16"
export HW="$DEPLOY/hardware"
export LOADER="$DEPLOY/loader"
export RUN="$DEPLOY/run"
export CORE_MHZ=177.5
export MEM_MHZ=332.8125
export AXI_DATA_WIDTH=256
export ETH_WORK="$DEPLOY/ethernet/workspace"

mkdir -p "$DEPLOY" "$RUN"
RUN_ARGS=(--target "$TARGET" --prefill 128 --decode 16 --capacity 256 --out "$RUN")
python -m opentpu.target show "$TARGET"
```

Define these variables and activate the environment again in a new terminal.
Keep `HW` consistent for programming, physical kernels, loading, inference and
reporting. Use a fresh `DEPLOY` for another geometry or software configuration.
The explicit build arguments and generated hardware metadata select the clocks.
The helpers share the selected defaults in `tools/zu3eg/settings.py`.

The default prompt is the first 128 tokenizer tokens of
`tools/data/austen_pp_ch1.txt`, without a chat template. For a custom prompt,
append `--prompt-file /absolute/path/prompt.txt` when creating `RUN_ARGS`.
The file must contain at least 128 tokens; the runner uses its first 128.

## 6. Run small RTL checks and model preflight

```bash
python -m pytest -q tests/test_zu3eg_mxu.py tests/test_zu3eg_ps.py tests/test_zu3eg_sequence.py tests/test_zu3eg_build.py
python -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --preflight
```

The PS tests use a queued AXI model with response latency, random stalls and
held write responses. Small D128 int8/int4 and D64 int4 MLPs compare every DRAM
byte and scratchpad word against the ISA. The vendor clock converter is checked
through its generated constraints and Vivado CDC reports.

Preflight compiles representative prompt/decode positions with and without the
head, checks instruction-memory capacity and checks the physical DDR window.
This 128/16, capacity-256 workload uses at most 2,276 sampled instructions out
of 4,096. Its result is `$RUN/preflight.json`.

## 7. Build the accelerator with both clocks

```bash
python -u -m tools.zu3eg.build_board \
  --target "$TARGET" --core-mhz "$CORE_MHZ" --mem-mhz "$MEM_MHZ" \
  --axi-data-width "$AXI_DATA_WIDTH" --vivado "$VIVADO" --out "$HW"
python -m tools.zu3eg.check_build "$HW"
```

The helper creates the PS block design, parameterized core, queued bridge,
AXI-Lite interconnect, Clock Wizard, AXI Clock Converter and reset controllers.
A 256-to-128 AXI downsizer follows the wide crossing in the HP clock domain.
It synthesizes, places, routes and generates the bitstream for the exact ZU3EG
part. No board preset is needed to implement PL; PS DDR boot initialization is
preserved separately when programming.

The Python command writes Vivado output to `$HW/console.log`. From another
terminal, inspect it with `tail -f "$HW/console.log"`.

| File under `$HW` | Purpose |
|---|---|
| `configuration.json`, `build_params.tcl` | Target, parameters, clock requests and bridge description |
| `system_bd.tcl` | Generated block design |
| `timing.rpt`, `timing_status.tcl` | Routed setup, hold and pulse-width margins |
| `clocks.csv`, `cdc.rpt` | Generated frequencies and crossing diagnostics |
| `utilization.rpt`, `drc.rpt` | Resource use and design-rule checks |
| `opentpu_zu3eg.bit` | Volatile PL image |
| `opentpu_zu3eg.xsa` | Hardware handoff |
| `build_check.json` | Parsed timing and generated-clock checks |

All worst slacks must be nonnegative and failing endpoint counts zero. Review
CDC for unsafe or unknown crossings. A bitstream's existence alone is not a
timing pass. A different target or RTL revision requires its own qualification.

## 8. Build the temporary JTAG bulk loader, if needed

**Skip this step when using Ethernet in section 14B.**

```bash
python -u -m tools.zu3eg.build_board --loader --vivado "$VIVADO" --out "$LOADER"
```

This separate bitstream contains a queued 64-bit JTAG-to-AXI master for bulk
DDR writes. It uses its own 112.5-MHz clock and is replaced by the accelerator
after uploading. Model contents survive this PL-only reconfiguration.

## 9. Use the qualified implementation on this PC

The full-system implementation is available at the default `$HW` directory.
After step 5, verify it:

```bash
python -m tools.zu3eg.check_build "$HW"
```

If the check passes and you are using the retained build, skip step 7 and
continue with step 10. Keep the same `HW` for all remaining physical operations.
The report checks setup, hold and pulse width, generated clocks, and the actual
block-design topology: the clock crossing must remain 256 bits and downsizing
must run in the HP domain. The implementation and simulation evidence is in
`build/zu3eg/wide_validation/report.json`.

For a reproducible functional check using the actual Xilinx converter models:

```bash
python -m tools.zu3eg.check_memory_path --out "$DEPLOY/memory_path_check"
```

This builds a dedicated simulation, with different core and HP clocks, and
runs the protocol regression through the actual clock converter and downsizer.
It checks data, byte masks, ordering, tags, flushes and errors. It does not
access the board. Its functional FIFO models do not model physical
synchronization delays; routed timing and CDC checks qualify those crossings.
The helper handles an empty, unused `secureip` library found in this local
Vivado installation without modifying vendor files.

## 10. Connect and inspect the board

Boot the board normally, keep cooling active, and connect the Platform Cable
USB II to the JTAG header and PC. For Ethernet loading, also connect the board's
RJ45 port directly to the PC's Ethernet port.

```bash
lsusb
```

The Platform Cable USB II commonly appears as `03fd:0008`. If vendor cable
drivers are missing, install them once and reconnect the cable:

```bash
sudo /home/mahdi/vivado/Vivado/2022.2/data/xicom/cable_drivers/lin64/install_script/install_drivers/install_drivers
```

Inspect targets and PS state without programming:

```bash
python - <<'PY'
from tools.zu3eg.jtag import Jtag
link = Jtag('build/zu3eg/inspect')
try:
    print(link.command('targets'))
    print('DDRC status:', link.words(0xFD070004))
    print('RPLL:', [hex(v) for v in link.words(0xFF5E0030)])
    print('PL0 reference:', [hex(v) for v in link.words(0xFF5E00C0)])
finally:
    link.close()
PY
```

The supported boot state has DDRC normal mode (`status & 7 == 1`), RPLL control
`0x00013000`, and PL0 control `0x01010802`, providing the 100-MHz reference.
The helpers reject unrecognized settings. **Do not run the generated
`psu_init()` wholesale:** its default DDR/PLL settings are not a verified MYIR
preset. Only PS-PL interface widths, isolation and reset controls are applied.

## 11. Program the accelerator and check its clock

```bash
python -u -m tools.zu3eg.program_board --hardware "$HW" --out "$DEPLOY/bringup"
```

This programs volatile PL, preserves initialized DDR, and checks the D128 x 1
identity, architecture hash, core-frequency metadata and scratch register. A
two-second RUN-counter measurement provides a clock sanity check against the
PC timer. Vivado's generated-clock report establishes the HP AXI clock.

Do not run competing JTAG clients during programming, loading or inference.

## 12. Validate physical int8 and int4 kernels

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --hardware "$HW" --kernel-only
```

Both `BOARD_KERNEL_BIT_EXACT` checks must pass. Their records bind the test to
the bitstream checksum. **Run this before model loading:** the kernels overwrite
the beginning of the model's DDR region. Do not rerun them after loading.

## 13. Prepare the image and ISA reference

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --prepare
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --reference-only \
  > "$DEPLOY/reference.log" 2>&1
```

Preparation writes `$RUN/model_image.bin` and its checksum/layout manifest.
The reference runs the same prompt and decode count on the PC, saving expected
prefill logits, final logits and token IDs. These commands do not access the
board. The cache manifest covers geometry, capacity, formats, checkpoint and
relevant Python source; regenerate in a fresh directory after a mismatch.

## 14. Load the prepared model into DDR

Choose one method. Both generate `$RUN/model_loaded.json` for the preloaded
inference request. A partial upload must be restarted from the image's beginning.

### 14A. JTAG bulk loading

Complete step 8 first. Optionally test a small transfer at a scratch address:

```bash
python -u -m tools.zu3eg.load_model --loader "$LOADER" --hardware "$HW" \
  --image "$RUN/model_image.bin" --limit 65536
```

Load the complete model:

```bash
python -u -m tools.zu3eg.load_model --loader "$LOADER" --hardware "$HW" \
  --image "$RUN/model_image.bin" > "$DEPLOY/load_model.log" 2>&1
```

The helper loads the temporary PL loader, transfers the model to physical
`0x10000000`, checks sample readbacks and restores the selected accelerator.
Detailed progress is in a separate file:

```bash
tail -f "$LOADER/upload.log"
```

`AXI_LOADED` lines report loaded bytes, total bytes and elapsed milliseconds.
The primary log can stay at `Programming temporary AXI loader...` during this
work. Stop `tail` with Ctrl+C in the monitoring terminal; leave the upload
running. JTAG upload can take tens of minutes and is excluded from inference
throughput.

### 14B. Ethernet loading, with JTAG programming and control

Keep the accelerator programmed. If a temporary loader is active after an
interrupted JTAG upload, restore the accelerator using step 11 first.

Build the standalone Arm firmware:

```bash
python -u -m tools.zu3eg.build_ethernet --create --workspace "$ETH_WORK" \
  > "$DEPLOY/ethernet_build.log" 2>&1
```

This creates a software handoff and Vitis/lwIP platform, then builds
`$ETH_WORK/monitor/Debug/monitor.elf`. It does not program PL or initialize DDR.
For an existing workspace, rebuild the monitor before booting it:

```bash
python -u -m tools.zu3eg.build_ethernet --workspace "$ETH_WORK"
```

After changing the firmware ELF, use `--reset-apu` on the boot command below.
It stops GEM DMA and resets all four A53 cores, including shared processor/debug
state, while preserving initialized DDR and PL. This option is for this guide's
exclusive bare-metal deployment; no FPGA rebuild is needed for transport changes.

The current firmware invalidates CPU cache lines before partial input writes,
then flushes the written data, preserving neighboring bytes updated by PL.
Rebuild and boot this version before selecting Ethernet inference. No FPGA
rebuild is required for the new transport. The helper handles the compiler's
`LD_PRELOAD` environment and uses XSCT `-nodisp` mode.

Check the direct link. This PC uses `enp45s0`; select the correct interface on
another PC:

```bash
export NET_IFACE=enp45s0
ip -brief link
cat "/sys/class/net/$NET_IFACE/carrier"
cat "/sys/class/net/$NET_IFACE/speed"
```

Carrier must be `1`. The expected negotiated speed is `1000` Mb/s. If carrier
is `0`, check power, cable seating and link LEDs before changing IP settings.
Create or activate the temporary direct-link profile:

```bash
if ! nmcli connection show opentpu-direct >/dev/null 2>&1; then
  nmcli connection add save no type ethernet ifname "$NET_IFACE" \
    con-name opentpu-direct connection.autoconnect no \
    ipv4.method manual ipv4.addresses 192.168.200.1/24 \
    ipv4.never-default yes ipv6.method disabled
fi
nmcli connection up opentpu-direct
```

The PC uses `192.168.200.1/24`, and firmware uses `192.168.200.2/24`, TCP port
8020. This profile does not change the default route. NetworkManager may
request administrator authorization.

Start the monitor through JTAG and check reachability:

```bash
python -u -m tools.zu3eg.boot_ethernet --workspace "$ETH_WORK" \
  --out "$DEPLOY/ethernet/boot" --reset-apu
ping -I "$NET_IFACE" -c 3 192.168.200.2
```

`ETHERNET_BOOT_STATE 6` means the monitor reached its service loop. It runs
bare-metal on Cortex-A53 core 0, using low DDR outside the model region. Its
GEM3/AR8035 configuration uses the board's verified 1.5-GHz IOPLL setup and
preserves the Ethernet reset pin state. UART startup prints use 115200, 8N1.

The boot helper resets core 0 with `-clear-registers -isa A64` and waits up to
30 seconds, printing the startup stage. If an older helper stalls at state 2
and XSDB reports `Cannot halt processor`, `EDITR not ready`, or `EDITR timeout`,
retry with `--reset-apu`; an isolated core reset may not recover shared debug
state. If the group reset also fails, power-cycle the board and repeat
accelerator programming before booting the monitor. After a power-cycle,
reactivate `nmcli connection up opentpu-direct` and confirm the PC still has
`192.168.200.1/24`; this temporary profile does not reconnect automatically.
Rebooting also loses the model, so repeat model loading before inference.

Upload, verify, and leave the monitor running for Ethernet inference transfers:

```bash
python -u -m tools.zu3eg.load_ethernet --address 192.168.200.2 \
  --hardware "$HW" --image "$RUN/model_image.bin" --keep-running \
  > "$DEPLOY/load_ethernet.log" 2>&1
```

The helper checks each 8-MiB chunk's CRC32, reads back the entire image and
compares its SHA-256 to the prepared manifest. Cache maintenance keeps CPU and
PL memory accesses consistent. Success includes
`ETHERNET_FULL_DDR_SHA256_PASS`. With `--keep-running`, the helper marks the
image pristine and leaves the Arm service alive for step 15A. Without that flag,
it halts the monitor for the JTAG-only data path in step 15B.

If all `ETHERNET_LOADED` chunks passed CRC but a later readback timed out,
keep the board running and repeat only full-image verification:

```bash
python -u -m tools.zu3eg.load_ethernet --address 192.168.200.2 \
  --hardware "$HW" --image "$RUN/model_image.bin" --keep-running --verify-only \
  > "$DEPLOY/load_ethernet_verify.log" 2>&1
```

This performs no model writes and marks the image ready only after its complete
DDR SHA-256 matches. Timed-out or disconnected reads reconnect and retry at most
twice after a one-second read-idle timeout, discarding partial replies; writes
and run commands are not automatically replayed. A
checksum mismatch requires reloading the image. Do not reboot or run kernels
between uploading and verification, because they invalidate the loaded state.

## 15. Run the complete request

### 15A. Ethernet inputs, programs and output logits

After section 14B's upload with `--keep-running`, run:

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" \
  --hardware "$HW" --preloaded --verify-isa \
  --data-transport ethernet --ethernet-address 192.168.200.2 \
  --run-control ethernet --compile-ahead --profile \
  > "$DEPLOY/board_run.log" 2>&1
```

This sends each inference step's input data and assembled program through
Ethernet and reads its output logits through Ethernet. With `--run-control
ethernet`, the Arm monitor loads, starts and polls the accelerator through local
MMIO and returns hardware counters; JTAG checks identity/configuration before
the request. `--compile-ahead` compiles and assembles upcoming programs while
the FPGA runs. `--profile` adds per-step timings to `result.json`. The PC performs
argmax and tokenization. The Arm service stays alive so you can reload the
initial model for another request. Use `--run-control jtag` for the previous
JTAG start/wait path.

If the monitor was halted after loading, rebuild/boot it as described in 14B
and repeat `load_ethernet --keep-running` before this command. Do not rerun
physical kernels after loading the model.

### 15B. JTAG inputs and output logits

Use this after section 14A, or after Ethernet model loading without
`--keep-running`:

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" \
  --hardware "$HW" --preloaded --verify-isa --data-transport jtag \
  > "$DEPLOY/board_run.log" 2>&1
```

Every prompt row runs on the FPGA. The LM head runs after the last prompt row
and selects the initial output token; 16 more greedy forward/argmax steps
follow. This benchmark therefore selects **17 output tokens including the
prefill seed**. EOS does not terminate it early. For exactly 16 outputs
including the seed, consistently use `--decode 15` for reference and board runs.

The result records `data_transport` and a transport description. Selecting
Ethernet for model loading alone does not select Ethernet inference: use the
explicit `--data-transport ethernet` flag in 15A. Wait for the command to
finish before reading the log:

```bash
tail -n 80 "$DEPLOY/board_run.log"
```

Success prints `BOARD_REQUEST_ISA_BIT_EXACT`. The runner marks the loaded
image used; before another request, restore initial KV and recurrent state by
repeating the selected model-loading method. For Ethernet inference, reload
with `--keep-running`; restart the monitor first only if it has stopped.
Reprogramming PL alone does not reset model state.

### 15C. Interactive chat over Ethernet

Use `tools.zu3eg.chat_board` for this MYIR connection. The generic
`otpu-chat --backend board` command targets the PCIe/XDMA board instead.
Chat uses the checkpoint's chat template, greedy sampling, thinking disabled,
and EOS stopping. Input/program/logit transfers and run control use Ethernet;
JTAG remains connected for startup checks.

The commands below reuse the four-row image and qualified hardware already
built on this PC: **128 x 1 PEs, 177.5/332.8125 MHz, four activation rows,
16 outstanding reads and up to 512-byte read bursts**. They require the
existing prepared model, manifests and matching int8/int4 kernel-check records.
Activate the environment and override the older paths from section 5:

```bash
cd /home/mahdi/Documents/brochrue/project/openTPU
source .venv/bin/activate

export HF_HOME="$PWD/build/zu3eg/hf-cache"
export HF_HUB_OFFLINE=1
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
export TARGET='mys-zu3eg:d128-m1-r4'
export DEPLOY="$PWD/build/zu3eg/prefill_reuse_20261102"
export HW="$DEPLOY/hardware_checked"
export RUN="$DEPLOY/full/run"
export ETH_WORK="$PWD/build/zu3eg/wide_128_16/ethernet/workspace"
```

**Start at model loading (section 14B)** if the correct accelerator and Ethernet
monitor are still running after your successful benchmark. No rebuild or new
ISA reference is needed. If the board was rebooted, reprogrammed, or the monitor
is stopped, first complete sections 10–11 with the paths above, then boot it:

```bash
python -u -m tools.zu3eg.program_board \
  --hardware "$HW" --out "$DEPLOY/bringup"
nmcli connection up opentpu-direct
python -u -m tools.zu3eg.boot_ethernet --workspace "$ETH_WORK" \
  --out "$DEPLOY/boot" --reset-apu
ping -I enp45s0 -c 3 192.168.200.2
```

Reload the initial image **before every new chat-process launch**, including
after a benchmark or a previous chat session. Keep the monitor running:

```bash
python -u -m tools.zu3eg.load_ethernet \
  --hardware "$HW" --image "$RUN/model_image.bin" \
  --address 192.168.200.2 --keep-running \
  > "$DEPLOY/load_chat.log" 2>&1
```

Wait for this process to finish successfully, then read its log and confirm
`ETHERNET_FULL_DDR_SHA256_PASS`. Do not run kernels after loading. Now start
the interactive terminal directly, without redirecting its output:

```bash
python -u -m tools.zu3eg.chat_board \
  --hardware "$HW" --run "$RUN" \
  --address 192.168.200.2 --max-new 64
```

Type a question at `You>`. Replies stream to the terminal. `/reset` clears the
conversation; `/quit` or `/exit` ends the process. The session loads weights
once; between turns it clears recurrent state and replays the templated history
using four-row prefill, preserving Qwen3.5's state dependencies.

The **256-token capacity includes chat formatting, all conversation history,
and the reserved reply length**. With `--max-new 64`, the templated prompt must
fit within 192 tokens. If the context fills, use `/reset`, shorten the question,
or restart with a smaller `--max-new` after reloading. Chat does not pad or
truncate prompts to the benchmark's 128 tokens and does not force 16 outputs.

For a single question, reload the image first and use:

```bash
python -u -m tools.zu3eg.chat_board \
  --hardware "$HW" --run "$RUN" --max-new 64 \
  --prompt "Explain why the sky is blue in simple terms."
```

Each reply prints time to first token, decode steps/s and end-to-end output
tokens/s. The terminal chat entry point has focused host tests, but has not yet
been validated on the physical board; the four-row benchmark hardware and
sequential-reference comparisons were validated separately.

## 16. Read verification and performance results

```bash
python -m tools.zu3eg.deployment_report --directory "$DEPLOY" --hardware "$HW"

python - <<'PY'
import json, os
from pathlib import Path
run = Path(os.environ['RUN'])
r = json.loads((run / 'result.json').read_text())
v = json.loads((run / 'isa_verification.json').read_text())
assert r['complete'] and v['passed']
for k in ('prefill_device_tokens_per_second', 'decode_device_tokens_per_second',
          'prefill_wall_tokens_per_second', 'decode_wall_tokens_per_second',
          'request_wall_output_tokens_per_second', 'request_wall_seconds'):
    print(k, r[k])
print('Generated text:', r['generated_text'])
print('Verification:', v)
PY
```

The report tool expects the standard `$DEPLOY/run` layout. FPGA rates divide
token counts by measured RUN cycles at the recorded core clock. Wall-clock
rates include host processing, the selected data transfers and JTAG control. End-to-end output
rate is 17 outputs divided by the complete prefill-plus-decode elapsed time.
Quantization, initial model upload and the separate ISA reference are excluded.

The small read-stream comparison is not a full-model throughput result.
Measure the complete request before claiming a model speedup. Activity counters
are not connected in this wrapper, so RUN duty is not PE utilization.

## 17. Change scale or workload

The selected array has one column and depth 128. Supported chunk depths are
32, 64 and 128; the target must retain one slice and compatible model dimensions.
For another experiment, choose a target or add a variant, use a fresh deployment
directory, and repeat preflight, implementation, physical kernels and reference.

| Setting | Effect and checks |
|---|---|
| `D`, `MCOLS` | PE count is `S*D*MCOLS`. D also changes quantization blocks, bursts and image layout. Rebuild weights and references. |
| `ACT_BLOCKS`, `ACT_ROWS` | Capacity for live quantized activations and token rows. Reducing it may cause `ACT RAM full`. More rows need compiler/runtime scheduling support. |
| `TMEM_WORDS` | Scratchpad capacity. Smaller values save RAM but can prevent full-model allocation. |
| `IMEM_WORDS` | Eight 32-bit words per instruction. Check compiled programs at the requested context lengths. |
| `LANES`, `ULANES`, `MXU_E`, `VPU_CL` | Arithmetic parallelism, memory arbitration and routing. Keep compatible lane/column counts and rerun ISA comparisons. |
| `WIN`, `FIFO_DEPTH` | Scheduling and weight-prefetch capacity. Lower values can save area while increasing stalls. |
| TMEM sub-banks/replay | Affect conflicts and physical RAM layout; require correctness and timing checks. This shell keeps `TMEM_PMP=0`. |
| Bridge queue depths | `READ_DEPTH`, `A_DEPTH`, `B_DEPTH`, `SW_DEPTH` in `otpu_ps_dram.sv`; powers of two. Override through the wrapper if experimenting, and update metadata. |
| `--capacity` | Must cover prefill plus decode. Changes DDR reservation and compilation range; regenerate image/reference. |
| `--axi-data-width` | 256 is the selected bridge width; 128 is available for comparisons. The PS port stays 128 bits. Keep any downsizing after the clock crossing. |
| Core / HP AXI clocks | Change generated clocks, timing and metadata. Qualify the complete system at the requested pair. |

Example 64-PE variant in `targets/mys-zu3eg.toml`:

```toml
[variants.d64-m1-small]
D = 64
MCOLS = 1
ACT_BLOCKS = 96
ACT_ROWS = 1
PAIR = false
MXU_E = 1
```

Use `TARGET='mys-zu3eg:d64-m1-small'` throughout. It is a candidate until
preflight and implementation pass. Clock qualification is specific to geometry
and RTL; PE count is not DSP count, because vector and epilogue arithmetic also
consume resources.

## 18. Troubleshooting

- **Model file absent:** use the full checkpoint filename from step 4 and finish the downloader.
- **No JTAG target:** inspect power, cable orientation, vendor drivers and competing debugger sessions.
- **DDR or PS clock guard fails:** boot normal board firmware and inspect the state. Do not substitute another board's DDR preset or remove the guard.
- **Timing/CDC fails:** stop before programming. Keep the actual bitstream's reports; qualify another implementation if needed.
- **Kernel mismatch or AXI error:** stop before model loading and inspect saved status and RTL comparisons.
- **Manifest mismatch:** use a fresh deployment directory and regenerate the image and reference.
- **JTAG upload appears stuck:** monitor `$LOADER/upload.log`, not only the primary Python log.
- **No Ethernet carrier:** resolve the physical link before IP or firmware changes.
- **Monitor startup timeout:** read the printed stage and debugger log. State 2 precedes Ethernet setup; state 3 covers GEM/PHY initialization. Retry with `--reset-apu` after firmware updates. If group reset also fails, power-cycle, reprogram PL and restart the monitor as described in 14B.
- **State 6 but ping fails after reboot:** reactivate `opentpu-direct` and check `ip -brief address show "$NET_IFACE"`. The PC needs `192.168.200.1/24`; a Wi-Fi source address in ping output indicates the direct-link address is missing.
- **Ethernet stops after upload:** use `load_ethernet --keep-running` for Ethernet inference. Without that flag, halting the monitor is intentional.
- **Repeated request differs:** reload initial model state before executing again.
- **Slow elapsed decode:** use `--data-transport ethernet --run-control ethernet --compile-ahead`. Add `--profile` to separate transfers, control, host waits and FPGA cycles. Healthy logit reads still transfer the full vocabulary; retry messages identify stalled connections.

A normal board reboot restores a known boot state but requires reprogramming
PL and reloading DDR before inference. To remove the temporary host network
profile when finished:

```bash
nmcli connection down opentpu-direct
nmcli connection delete opentpu-direct
```
