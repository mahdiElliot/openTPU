# Qwen3.5-0.8B on MYS-ZU3EG K2: 64 × 2 PEs at 200 MHz

This guide reproduces the best measured **64 × 2 PE** deployment on the
**MYIR MYS-ZU3EG K2, 2 GiB PS DDR4**, part **`xczu3eg-sfvc784-1-i`**.
The benchmark uses **128 prompt tokens, 16 decode steps, int4 decoder weights,
and an int8 LM head**. JTAG programs PL and boots the bare-metal Arm Ethernet
monitor; inputs, programs, model loading, outputs and run control use Ethernet.
Linux is not required.

Follow sections 3–14 in order for a fresh deployment. Section 7 also explains
how to reuse the retained, qualified bitstream. Section 15 covers later runs;
section 16 provides a separate interactive-chat image.

## 1. Winning configuration and expected result

| Setting | Selected value |
|---|---|
| Target | `mys-zu3eg:d64-m2-r6-cl2` |
| Array | `S=1`, `D=64`, `MCOLS=2`: **128 PEs** |
| Core / HP AXI clocks | **200 / 332.8125 MHz**, independent MMCMs |
| PS PL0 input / input-jitter budget | **100 MHz / 0.100 ns** |
| Activation capacity | `ACT_BLOCKS=96`, `ACT_ROWS=6` |
| Benchmark grouping / image allocation | **`--prefill-rows 4 --image-rows 6`** |
| MXU | Systolic, `MXU_CL=16`, `MXU_E=1`, `PAIR=true` |
| VPU | `LANES=4`, `ULANES=4`, `VPU_CL=2` |
| Weight FIFO / scheduler | `FIFO_DEPTH=128` (8 KiB of weight payload), `WIN=8`, footprint pipeline enabled |
| TMEM | 65,536 words (256 KiB), implementation 3, four hashed sub-banks, replay and true dual-port |
| Instruction memory | 32,768 words: 4,096 instructions |
| AXI path | 256-bit bridge and clock crossing; 256-to-128 downsizing at the HP clock |
| Active read credits / combined read burst | **16 / eight 64-byte chunks = 512 bytes** |
| Scale cache / active write credits | **Eight 32-byte lines = 256 bytes / four ordered responses** |
| Disabled | `DSTEP`, `STREAM`, `SEQ_WC`, memory pumping (`TMEM_PMP=0`) |
| Decode | **`--device-decode`**, one existing hardware greedy loop |
| Context capacity | 256 positions |
| Prepared resident image | **1,596,454,400 bytes: 1.60 GB / 1.49 GiB** |

`r6` names the hardware's **capacity**, not the winning prefill group size.
Four-row prefill was faster than six rows on the final eight-line-cache build.
Changing `--image-rows 6` to four makes a different model layout; retain both
explicit row arguments for this reproduction.

The existing resident/lookup path stores FP32 embeddings for device-side token
lookup. That table contributes approximately 1.02 GB; decoder weights remain
int4 and the head remains int8. It preserves the original host-fed embedding
precision. Initial image preparation, upload, and the independent ISA reference
are outside the request timing below.

Measured on the connected board on 2026-11-03, with the commands in this guide:

| Metric | Measured result |
|---|---:|
| Prefill wall-clock rate | **21.92 tokens/s** |
| Decode wall-clock rate | **6.39 tokens/s** |
| Prefill FPGA execution-only rate | **23.15 tokens/s** |
| Decode FPGA execution-only rate | **6.73 tokens/s** |
| Prefill / decode wall time | **5.838 / 2.505 s** |
| Complete request wall time | **8.344 s** |
| 16 decode steps / complete request time | **1.918 tokens/s** |
| 17 outputs including prefill seed / request time | **2.037 tokens/s** |

These are reproduction targets, not bit-for-bit timing promises. Host load,
Ethernet latency, DDR scheduling and a newly routed implementation can change
rates. The prompt, token counts, formats, row settings and runtime flags must
match; logits and token IDs must pass the exact ISA comparison. Prefer the
retained bitstream in section 7B for the closest comparison to this measurement.

Retained measurement: `build/zu3eg/opt64_20261103/cache8_group4/run/result.json`.
The qualified build is `build/zu3eg/opt64_20261103/hardware_cache8`:

| Routed check | Result |
|---|---:|
| Setup / hold / pulse-width slack | **+0.102 / +0.010 / +0.002 ns** |
| Failing timing endpoints | **0** |
| CDC endpoints | **20 safe, 0 unsafe, 0 unknown** |
| Methodology critical rules | **0** |
| LUTs | **59,353 / 70,560 (84.12%)** |
| Registers | **54,414 / 141,120 (38.56%)** |
| Occupied CLBs | **8,786 / 8,820 (99.61%)** |
| BRAM / DSP48E2 | **145.5 / 216; 256 / 360** |

Occupied CLB sites are nearly full even though some LUT/FF slots remain unused.
Every PE uses one DSP; the 128 PE DSPs and 128 auxiliary DSPs total 256.

## 2. Clocks, DDR ownership and boot prerequisite

```text
Board boot firmware initializes the PS DDR controller and 2 GiB DDR.
PS PL0: 100 MHz (existing RPLL 800 MHz / 8)
  ├── Clock Wizard / MMCM → core 200 MHz → compute, control, DDR bridge
  └── Clock Wizard / MMCM → HP AXI 332.8125 MHz

256-bit bridge → 256-bit async AXI crossing → 256-to-128 downsizer → HP0 → PS DDR
    core clock          three sync stages           HP clock
```

`--mem-mhz` sets the **HP AXI interface clock**, not the physical DDR or Arm CPU
clock. The two PL domains have synchronized resets. Both MMCMs use the existing
100-MHz PS source. `No_buffer` preserves its primary-clock definition, and the
flow applies **100 ps input jitter** to `clk_pl_0`. Keep that budget when checking
timing. Rounded periods in `clocks.csv` may display approximately 332.779 MHz;
the generated-clock timing table rounds the requested HP frequency to 332.813 MHz.

This is an **exclusive bare-metal deployment**. Let normal board firmware
initialize DDR first. Reserve physical `0x10000000` through the model/program
window below `0x80000000`; the monitor uses low DDR outside that window. Do not
run an OS or another application using this memory. The helpers preserve the
working DDR/PLL initialization and program volatile PL. They do not install
firmware on SD, eMMC or QSPI. A power-cycle loses the model and PL configuration.
**Do not run generated `psu_init()` wholesale**: its DDR defaults are not a verified
MYIR cold-boot preset.

## 3. Install and activate the host environment

Run from the repository root; vendor paths below match this laptop.

```bash
cd /home/mahdi/Documents/brochrue/project/openTPU
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential usbutils libusb-1.0-0
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e . -r tools/zu3eg/requirements.txt
python -m tools.zu3eg.setup_tools
python -m pip check
verilator --version

export VIVADO=/home/mahdi/vivado/Vivado/2022.2/bin/vivado
export HW_SERVER=/home/mahdi/vivado/Vivado/2022.2/bin/hw_server
export VITIS_XSDB=/home/mahdi/vivado/Vitis/2022.2/bin/xsdb
test -x "$VIVADO"
test -x "$HW_SERVER"
test -x "$VITIS_XSDB"
"$VIVADO" -version
```

With an existing environment, activate it instead of recreating it. The measured
host used Python 3.12, CPU PyTorch, Transformers and Verilator 5.34. Vivado/Vitis
**2022.2** supplies hardware tools, XSDB, XSCT, the Arm compiler and lwIP BSP.
QuestaSim is not required. Leave several GiB of free RAM, swap and approximately
30 GiB of disk for model images, simulation caches and Vivado projects.

## 4. Download and verify the checkpoint

```bash
export HF_HOME="$PWD/build/zu3eg/hf-cache"
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
mkdir -p "$HF_HOME"
unset HF_HUB_OFFLINE
python -u -m tools.zu3eg.download
sha256sum models/Qwen3.5-0.8B/model.safetensors-00001-of-00001.safetensors
export HF_HUB_OFFLINE=1
```

Expected checkpoint SHA-256:

```text
04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696
```

The downloader pins revision `2fc06364715b967f1860aea9cf38778875588b17` and resumes
interrupted downloads. The single shard has the full filename above; it is not
named `model.safetensors`. This flow runs the 24-layer text decoder and head,
without the vision tower or MTP drafter. `fp4` is a different weight format.

## 5. Define one deployment directory and the exact workload

Use a new directory for a fresh build/run; this keeps prior validated files intact.

```bash
export TARGET='mys-zu3eg:d64-m2-r6-cl2'
export DEPLOY="$PWD/build/zu3eg/64x2_200_128_16"
export HW="$DEPLOY/hardware"
export RUN="$DEPLOY/run"
export MODEL="$PWD/models/Qwen3.5-0.8B"
export PROMPT="$PWD/tools/data/austen_pp_ch1.txt"
export CORE_MHZ=200
export MEM_MHZ=332.8125
export AXI_DATA_WIDTH=256
export ETH_WORK="$DEPLOY/ethernet/workspace"
export BOARD_IP=192.168.200.2
export NET_IFACE=enp45s0
mkdir -p "$DEPLOY" "$RUN"

RUN_ARGS=(--target "$TARGET" --model "$MODEL" --out "$RUN"
  --prompt-file "$PROMPT" --prefill 128 --decode 16 --capacity 256
  --prefill-rows 4 --image-rows 6 --device-decode --compute-overlap)
ETH_ARGS=(--data-transport ethernet --ethernet-address "$BOARD_IP"
  --run-control ethernet --compile-ahead --memory-stats)
python -m opentpu.target show "$TARGET"
```

Re-establish the environment, variables and Bash arrays in a new terminal.
The target inherits a legacy `platform.core_mhz=177.5`; the **explicit 200-MHz
build argument overrides it**. Do not rely on defaults in `settings.py`.
Keep `HW` consistent for programming, kernel checks, loading and inference.

The measured prompt is the first **128 tokenizer tokens** of the bundled file,
with `add_special_tokens=False` and no chat template. Keep that file for the
performance comparison. For a different prompt, set `PROMPT` before defining
`RUN_ARGS` and regenerate the reference. It must contain at least 128 tokens.

## 6. Run host/RTL checks and model preflight

```bash
python -m pytest -q tests/test_target.py tests/test_zu3eg_build.py \
  tests/test_zu3eg_device_decode.py tests/test_zu3eg_restore.py \
  tests/test_zu3eg_deployment_report.py \
  tests/test_zu3eg_chat.py tests/test_zu3eg_sequence.py tests/test_zu3eg_ethernet.py
python -m pytest -q tests/test_zu3eg_ps.py -k bridge
python -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --preflight
```

The host command passes **78 checks**, including four report-collector
regressions added with this guide update; the RTL command passes **23 bridge checks**. The
bridge tests cover burst grouping, cache fills/invalidation, held responses,
masked writes, errors and read-after-write fences with multiple pending writes.
Preflight checks representative step/group programs and the DDR window. The
selected device decode program also fits the 4,096-instruction memory; the
measured resident generate program contains 2,611 instructions.

## 7. Build or reuse the qualified hardware

### 7A. Build from scratch

All these arguments are required to reproduce the selected profile:

```bash
python -u -m tools.zu3eg.build_board \
  --target "$TARGET" --core-mhz "$CORE_MHZ" --mem-mhz "$MEM_MHZ" \
  --axi-data-width "$AXI_DATA_WIDTH" --hp-ports 1 --read-depth 16 --read-chunks 8 \
  --scale-cache-lines 8 --write-depth 4 --memory-stats \
  --scheduler-pipeline --separate-mmcm \
  --strategy Performance_ExplorePostRoutePhysOpt \
  --vivado "$VIVADO" --out "$HW" > "$DEPLOY/build.log" 2>&1
python -m tools.zu3eg.check_build "$HW"
```

**Wait for each long process to exit before reading its logs.** Once the build
finishes, read `$DEPLOY/build.log` and `$HW/console.log` if needed. Do not use
`tail -f` during builds or weight transfers.

The helper creates the complete PS/PL project on the exact part. The read path
crosses domains at 256 bits and downsizes at the HP clock. Interface attributes
advertise bounds of 32 reads/eight writes; the bridge actively uses **16/four**.
The writer frees payload slots after WLAST but holds same-port read fences and
write-idle status until the ordered BRESPs finish.

`build_check.json` must show `passed: true`, all timing endpoint counts must be
zero, and setup/hold/pulse-width slacks must be nonnegative. Verify input jitter
is **0.100 ns**, the memory path passes, methodology critical rules are empty,
and `cdc.rpt` has no unsafe/unknown crossings. Bitstream creation alone is not
qualification. A rebuild can have different routing, slack, resource packing,
bitstream SHA and small performance differences.

Key evidence under `HW`: `configuration.json`, `build_params.tcl`, `timing.rpt`,
`utilization.rpt`, `methodology.rpt`, `clock_root.rpt`, `clocks.csv`, `cdc.rpt`,
`drc.rpt`, `build_check.json`, `opentpu_zu3eg.bit` and `opentpu_zu3eg.xsa`.

### 7B. Reuse the exact measured bitstream on this PC

Instead of 7A, override **only `HW`**; keep the new deployment/run directories:

```bash
export HW="$PWD/build/zu3eg/opt64_20261103/hardware_cache8"
python -m tools.zu3eg.check_build "$HW"
sha256sum "$HW/opentpu_zu3eg.bit"
```

Expected retained bitstream SHA-256:

```text
e54276132981e40699d99749fb625f58720b6ee2c9fdd0b81b08b3415c038514
```

This is the bitstream used for the 21.92/6.39 measurement. It does not need a
new Vivado build. Continue with section 8; run new physical kernel checks and
load a pristine image for your new run directory.

## 8. Connect JTAG and check the initialized PS

Power the board and let normal boot firmware finish. Connect the JTAG cable and
direct Ethernet cable. The USB cable normally appears as `03fd:0008`:

```bash
lsusb
```

If needed, install vendor cable drivers once, then reconnect the cable:

```bash
sudo /home/mahdi/vivado/Vivado/2022.2/data/xicom/cable_drivers/lin64/install_script/install_drivers/install_drivers
```

XSDB expects a local hardware server at TCP port 3121. Reuse an existing server;
otherwise start it in a second terminal and leave it running:

```bash
/home/mahdi/vivado/Vivado/2022.2/bin/hw_server -s tcp::3121
```

Inspect DDR and establish the verified **100-MHz PL0 input**. Some factory boots
leave the same RPLL with PL0 divided by four (200 MHz). The guarded conversion
below changes only that known divider, after halting the A53 cores. It does not
initialize DDR or change either PLL. Use it only for this exclusive bare-metal
session, with no other debugger or board application active:

```bash
python - <<'PY'
import os
from pathlib import Path
from tools.zu3eg.jtag import Jtag
link = Jtag(Path(os.environ['DEPLOY']) / 'inspect')
try:
    print(link.command('targets'))
    assert link.words(0xFD070004)[0] & 7 == 1, 'Boot firmware must initialize DDR'
    assert link.words(0xFF5E0030)[0] == 0x13000, 'Unexpected RPLL; stop and inspect'
    assert link.words(0xFF5E0020)[0] == 0x15A00, 'Unexpected IOPLL for Ethernet'
    pl0 = link.words(0xFF5E00C0)[0]
    assert pl0 in (0x01010402, 0x01010802), f'Unrecognized PL0 setting: {pl0:#x}'
    if pl0 == 0x01010402:
        for core in range(4):
            link.command('targets -set -filter {name == "Cortex-A53 #%d"}; '
                         'if {[catch {stop} message] && $message ne "Already stopped"} {error $message}' % core)
        link.command('targets -set -filter {name == "PSU"}')
        link.write_word(0xFF5E00C0, 0x01010802)
    assert link.words(0xFF5E00C0)[0] == 0x01010802
    print('DDR initialized; PL0 input = 100 MHz; PLLs preserved')
finally:
    link.close()
PY
```

If a guard fails, resolve the board boot/configuration state instead of guessing
register values. Never invoke the generated full `psu_init()` as a substitute.

## 9. Program PL and check its clock

```bash
python -u -m tools.zu3eg.program_board --hardware "$HW" --out "$DEPLOY/bringup" \
  > "$DEPLOY/program.log" 2>&1
cat "$DEPLOY/program.log"
```

This checks the exact geometry/hash, `CORE_KHZ=200000`, CSR readback and a
rough two-second RUN-counter measurement against the host timer. The measured
final board check was approximately 199.87 MHz; host/debugger timing introduces
error. Vivado's generated clocks establish the exact nominal frequencies.

**Perform this before model loading.** The clock test writes a loop at physical
`0x4f000000`, inside the new 1.60-GB image's memory window. Programming/checking
after loading requires model restoration again.

## 10. Build and boot Ethernet firmware

For a new workspace:

```bash
python -u -m tools.zu3eg.build_ethernet --create --workspace "$ETH_WORK" \
  > "$DEPLOY/ethernet_build.log" 2>&1
```

For an existing workspace with its BSP already generated, rebuild current
sources without `--create`:

```bash
python -u -m tools.zu3eg.build_ethernet --workspace "$ETH_WORK" \
  > "$DEPLOY/ethernet_rebuild.log" 2>&1
```

Choose one command, then wait for completion. The standalone firmware lives at
`$ETH_WORK/monitor/Debug/monitor.elf`. Its hardware export is for the Arm/lwIP
platform; **do not program that export's PL image over the accelerator**.

Configure the direct link on the PC:

```bash
ip -brief link
cat "/sys/class/net/$NET_IFACE/carrier"
cat "/sys/class/net/$NET_IFACE/speed"
if ! nmcli connection show opentpu-direct >/dev/null 2>&1; then
  nmcli connection add save no type ethernet ifname "$NET_IFACE" \
    con-name opentpu-direct connection.autoconnect no \
    ipv4.method manual ipv4.addresses 192.168.200.1/24 \
    ipv4.never-default yes ipv6.method disabled
fi
nmcli connection up opentpu-direct
ip route get "$BOARD_IP"
python -u -m tools.zu3eg.boot_ethernet --workspace "$ETH_WORK" \
  --out "$DEPLOY/ethernet/boot" --reset-apu > "$DEPLOY/ethernet_boot.log" 2>&1
cat "$DEPLOY/ethernet_boot.log"
ping -I "$NET_IFACE" -c 3 "$BOARD_IP"
```

Carrier must be `1` and speed should be `1000` Mb/s. Firmware uses
`192.168.200.2/24`, TCP 8020; the PC uses `.1/24` on `enp45s0` here. The temporary
NetworkManager profile does not replace the default route. After a power-cycle,
reactivate it; it is not configured to autoconnect.

`ETHERNET_BOOT_STATE 6` means ready. `--reset-apu` stops old GEM DMA and resets
the A53 group/debug state while preserving DDR and PL. Keep the monitor running
for all Ethernet operations. No competing JTAG clients should be active.

## 11. Validate physical int8 and int4 kernels

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" "${ETH_ARGS[@]}" \
  --hardware "$HW" --kernel-only > "$DEPLOY/kernel_checks.log" 2>&1
cat "$DEPLOY/kernel_checks.log"
```

Require both `BOARD_KERNEL_BIT_EXACT int8` and `BOARD_KERNEL_BIT_EXACT int4`.
Their records bind validation to the programmed bitstream SHA. These kernels
use Ethernet data/run control; an inherited backend log may still say `JTAG model
loaded`. **Run kernels before loading the model**, because they overwrite its
first MiB. Rerun validation after programming a different bitstream.

## 12. Prepare the resident model and matching ISA reference

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --prepare \
  > "$DEPLOY/prepare.log" 2>&1
cat "$DEPLOY/prepare.log"
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --reference-only \
  > "$DEPLOY/reference.log" 2>&1
cat "$DEPLOY/reference.log"
```

Wait until each command exits before inspecting its log. Neither accesses the
board. The image manifest covers architecture, six allocated image rows,
capacity, checkpoint, formats and relevant source. The reference uses the same
four-row prefill grouping and 16 decode steps. Its request ID distinguishes
paired/replay grouping; do not reuse another grouping's reference blindly.

For the measured revision, `$RUN/model_image.bin` has **1,596,454,400 bytes**
and SHA-256:

```text
2fc2e178555274e1a4aa396ad5e28c5bf512a0856fc140ae91c98ebfab763d14
```

Use a fresh `RUN` after a layout/source mismatch. Do not edit a manifest to bypass
validation. Image/reference creation must retain `--device-decode`,
`--image-rows 6` and `--prefill-rows 4` for this result.

## 13. Load over Ethernet and run the benchmark

Load the complete image and leave the monitor alive:

```bash
python -u -m tools.zu3eg.load_ethernet --hardware "$HW" \
  --image "$RUN/model_image.bin" --address "$BOARD_IP" --keep-running \
  > "$DEPLOY/load_ethernet.log" 2>&1
tail -n 30 "$DEPLOY/load_ethernet.log"
```

Require `ETHERNET_FULL_DDR_SHA256_PASS`. The loader CRC-checks upload chunks and
reads back the entire image for SHA-256, including the FP32 embedding table.
The initial measured upload/CRC took about 58 s and full readback about 25 s;
these are excluded from request tokens/s. The image is marked pristine only
when validation succeeds. Do not run kernels or the clock test afterward.

Run exactly this request; do not add `--profile` to the performance comparison:

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" "${ETH_ARGS[@]}" \
  --hardware "$HW" --preloaded --verify-isa > "$DEPLOY/board_run.log" 2>&1
tail -n 80 "$DEPLOY/board_run.log"
```

Wait for exit code zero and `BOARD_REQUEST_ISA_BIT_EXACT`. The FPGA runs all
prefill groups, with the head on the last row only. It then runs all 16 decode
steps in **one greedy hardware loop**, using existing RLD/LOOP/ARGMAX instructions.
The PC uploads the loop once and reads generated IDs and final logits rather
than uploading a program and fetching a full vocabulary every token.

`--compute-overlap` enables the four-row projection/VPU/DMA schedule.
`--compile-ahead` compiles and assembles the next prefill program while PL runs.
Both **inference data and run control** must use Ethernet; Ethernet model loading
alone is insufficient. JTAG remains for startup/configuration checks.

EOS does not stop this fixed-length benchmark. `--decode 16` means 16 forward
steps **after the seed from prefill**, hence **17 selected output tokens**.
The expected measured text is:

```text
 the last time I have heard of it. I have been told that it is a
```

Chat templates, another prompt, fewer image rows, host-per-token decoding,
profiling, or a different prefill grouping are different workloads and need
separate performance results.

## 14. Verify the result and compare rates

```bash
python -m tools.zu3eg.deployment_report --directory "$DEPLOY" --hardware "$HW" \
  > "$DEPLOY/deployment_report.log" 2>&1

python - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ['RUN'])
r = json.loads((p / 'result.json').read_text())
v = json.loads((p / 'isa_verification.json').read_text())
assert r['complete'] and v['passed']
assert r['core_khz'] == 200000 and r['mem_mhz'] == 332.8125
assert r['data_transport'] == r['run_control'] == 'ethernet'
assert r['prefill_rows'] == 4 and r['image_rows'] == 6 and r['device_decode']
assert len(r['prefill_tokens']) == 128 and r['decode_steps'] == 16
assert sum(s.get('tokens', 1) for s in r['steps'] if s['phase'] == 'decode') == 16
assert r['compile_ahead'] and r['compute_overlap']
for phase in ('prefill', 'decode'):
    print(phase, 'wall tokens/s:', r[phase + '_wall_tokens_per_second'],
          'FPGA-only tokens/s:', r[phase + '_device_tokens_per_second'])
print('Request wall seconds:', r['request_wall_seconds'])
print('16 decode steps / whole request:', r['request_wall_decode_steps_per_second'])
print('17 outputs / whole request:', r['request_wall_output_tokens_per_second'])
print('Text:', r['generated_text'])
print('ISA checks:', v)
PY
```

Expected approximately **21.92 / 6.39 wall-clock tokens/s** and **23.15 / 6.73
FPGA-only tokens/s**, as recorded in section 1. FPGA rates divide tokens by RUN
cycles at 200 MHz. Wall rates include timed host compilation, Ethernet transfers,
control and waits. End-to-end request timing includes prefill and decode, with
initial quantization, image upload/verification, engine setup and ISA reference
excluded. The single aggregated decode record contains `tokens=16`; it is not
one generated token.

`deployment_report` writes `$DEPLOY/report.json` from the standard `$DEPLOY/run`
and `$DEPLOY/bringup` layout. Individual files remain the primary evidence:
`result.json`, `isa_verification.json`, both kernel records, the model-loaded
record, and hardware build/programming checks. Activity counters are not PE
utilization measurements.

## 15. Repeat, change the prompt or recover after reboot

After every request, restore pristine DDR before executing another. For the
same prepared image, use the faster changed-block restoration:

```bash
python -u -m tools.zu3eg.load_ethernet --hardware "$HW" \
  --image "$RUN/model_image.bin" --address "$BOARD_IP" --keep-running \
  --restore-pristine > "$DEPLOY/restore_ethernet.log" 2>&1
tail -n 25 "$DEPLOY/restore_ethernet.log"
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" "${ETH_ARGS[@]}" \
  --hardware "$HW" --preloaded --verify-isa > "$DEPLOY/board_run.log" 2>&1
```

Restoration scans the whole image, repairs only changed 64-KiB blocks, rereads
repaired blocks, and checks full-image SHA-256. It took about 29 s while repairing
27 MB after a benchmark, instead of rewriting 1.60 GB. It preserves the same
verification requirement. Use the full initial upload in section 13 after
power loss or when installing this image for the first time.

For a new prompt, change `PROMPT`, rebuild `RUN_ARGS`, regenerate the ISA
reference with those arguments, restore the image, and run again. Keep a new
`RUN` for a new layout, capacity, image-row count or source version. Changing only
the prompt does not require resynthesis or weight preparation. Rates for another
prompt/output count need their own measurement.

If only the monitor stopped, boot it using section 10, then restore the image.
After power-cycling, start at section 8: verify PS state, program PL, boot Ethernet,
run physical kernels and **fully reload** the model before inference. Reuse the
qualified bitstream, environment and prepared/reference files if unchanged.

## 16. Interactive chat on the same 64 × 2 hardware

`tools.zu3eg.chat_board` supports this board; generic `otpu-chat --backend board`
is for PCIe/XDMA hardware. The chat frontend currently uses host-per-token
sampling and a **separate non-resident image**. Do not pass the benchmark's
resident six-row image to it. Chat has templates, EOS stopping and variable
lengths, so its rates do not reproduce the fixed 128/16 benchmark above.

Keep `HW` and `TARGET` from this guide. Prepare a separate four-row chat image
without `--device-decode`, and copy the matching physical kernel records:

```bash
export CHAT_RUN="$DEPLOY/chat_run"
export OTPU_Q35_ROWS_OVERLAP=1
mkdir -p "$CHAT_RUN"
python -u -m tools.zu3eg.run_board --target "$TARGET" --model "$MODEL" \
  --out "$CHAT_RUN" --prefill 128 --decode 16 --capacity 256 \
  --prefill-rows 4 --image-rows 4 --compute-overlap --prepare \
  > "$DEPLOY/chat_prepare.log" 2>&1
cp "$RUN/kernel_int8_result.json" "$RUN/kernel_int4_result.json" "$CHAT_RUN/"
python -u -m tools.zu3eg.load_ethernet --hardware "$HW" \
  --image "$CHAT_RUN/model_image.bin" --address "$BOARD_IP" --keep-running \
  > "$DEPLOY/chat_load.log" 2>&1
python -u -m tools.zu3eg.chat_board --hardware "$HW" --run "$CHAT_RUN" \
  --model "$MODEL" --address "$BOARD_IP" --max-new 64
```

Wait for preparation/loading success before starting the terminal chat. Type a
question at `You>`; `/reset` clears history and `/quit` exits. Chat replays the
conversation from cleared recurrent state each turn. The 256-position capacity
includes formatting, history and reply reserve: a 64-token reply needs a prompt
of at most 192 templated tokens. For one question, add `--prompt "Your question"`.
This chat frontend has host tests; the measured physical validation here is the
fixed benchmark, not an interactive-chat performance claim. Fully reload the
chat image before launching another chat process, and restore the **benchmark**
image before returning to section 13 or 15.

## 17. Optional JTAG-only data path

This is a fallback and will **not match the Ethernet wall-clock performance**.
The winning workflow above does not need a temporary bulk loader. If Ethernet
is unavailable, build and load through the temporary JTAG/AXI image:

```bash
export LOADER="$DEPLOY/loader"
python -u -m tools.zu3eg.build_board --loader --vivado "$VIVADO" --out "$LOADER" \
  > "$DEPLOY/loader_build.log" 2>&1
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --hardware "$HW" \
  --data-transport jtag --run-control jtag --kernel-only \
  > "$DEPLOY/jtag_kernel_checks.log" 2>&1
python -u -m tools.zu3eg.load_model --loader "$LOADER" --hardware "$HW" \
  --image "$RUN/model_image.bin" > "$DEPLOY/load_model.log" 2>&1
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --hardware "$HW" \
  --data-transport jtag --run-control jtag --compile-ahead --memory-stats \
  --preloaded --verify-isa > "$DEPLOY/jtag_board_run.log" 2>&1
```

Run the physical kernel checks first with JTAG transport if Ethernet was never
available. The temporary loader halts the Arm monitor, transfers the model and
restores the accelerator while preserving DDR. Read its primary log and
`$LOADER/upload.log` only after the process exits. Inference and final-logit
readbacks over JTAG can be slow; its upload validation is sampled, unlike the
full Ethernet SHA-256 path. Restart Ethernet firmware and fully validate the
pristine image before switching back to the measured workflow.

## 18. Scale the design or change timing

Use a new target variant and deployment directory; rerun preflight, RTL/ISA
checks, implementation qualification, physical kernels, image preparation and
reference whenever affected. The goal's measured settings are specific to this
part, clocks, prompt, formats and grouping.

| Parameter | What must be reconsidered |
|---|---|
| `D`, `MCOLS` | PEs = `S*D*MCOLS`; D changes weight/activation quantization blocks, scale traffic, bursts and image layout. Rebuild image and reference. |
| `ACT_ROWS`, `ACT_BLOCKS` | Physical capacity; runtime grouping may be smaller. Eight-row full-model prefill exhausted this target's 64K-word TMEM. |
| `--prefill-rows`, `--image-rows` | Grouping versus allocated image I/O. Four/six is the winner; both arguments are intentional. Regenerate matching references and any changed image layout. |
| `FIFO_DEPTH`, `WIN` | Buffering/window size versus packing and timing. 128/8 was selected; larger windows brought little proxy gain. |
| `VPU_CL`, `MXU_E`, lane counts | Compute versus area/routing. Two composite lanes and one epilogue lane were selected from checked proxies. |
| `--read-depth`, `--read-chunks` | Active credits and coalescing. Keep byte sizes in mind: eight D64 chunks are 512 B, not 1,024 B. |
| `--scale-cache-lines` | 1/2/4/8 lines; eight requires 256-bit AXI. A fill is at most eight AXI beats, with per-beat A capture. |
| `--write-depth` | 1/2/4/8 pending responses. Payload slots retire after WLAST; read fences and idle status wait through BRESP. |
| `--axi-data-width` | 128 or 256; HP0 remains 128. Keep downsizing after the wide crossing. |
| Clocks | Preserve 100-MHz PS input and 100-ps input jitter; regenerate actual MMCM clocks and qualify setup, hold, pulse width and CDC at the requested pair. |
| `--capacity`, formats, resident decoding | Affect DDR reservations, programs, model image and reference. The device loop must fit one attention bucket after convolution warmup. |

## 19. Troubleshooting

- **Prepared file size differs:** check target, allocated image rows, context, formats, source and `--device-decode`. A smaller legacy image cannot reproduce this run.
- **Manifest mismatch:** use a fresh run directory; never relabel an old image to bypass checks.
- **No JTAG target:** check power, USB cable/drivers, hardware server and competing debuggers.
- **PS clock guard:** section 8 handles only the two verified PL0 divider values; do not guess PLL/DDR register settings.
- **Timing/jitter/metadata check fails:** do not program that build. Keep the failed evidence; change implementation strategy or fix the design at the same requested clocks.
- **Kernel or full-model mismatch:** stop and inspect the finished log/status. Both kernel records must match the current hardware hash, and the ISA reference must match grouping/request.
- **No Ethernet carrier:** check both physical plugs and link LEDs before changing IP settings.
- **State 6 but ping fails:** reactivate `opentpu-direct` and check the PC route uses `NET_IFACE` and source `192.168.200.1`.
- **Monitor startup/EDITR timeout:** retry `boot_ethernet --reset-apu`; if recovery fails, power-cycle, let normal firmware initialize DDR, then repeat section 8 onward. Ping cannot work before state 6.
- **All upload CRCs passed but readback timed out:** with no intervening request/kernel/clock test, rerun `load_ethernet --keep-running --verify-only`. It marks pristine only after full SHA success.
- **Repeated request rejected:** restore the initial image first; a completed benchmark deliberately marks it non-pristine.
- **Rates below the table:** verify 200/332.8125 MHz, the eight-line cache, four write credits, exact row arguments, resident decode, overlap/compile-ahead flags, Gigabit link and idle host. Compare FPGA-only versus wall rates before attributing the difference to RTL.
- **Cold startup is much longer than 8.344 s:** that figure is the loaded-model request. Initial preparation/upload/verification and engine setup are separate.

When finished with the temporary direct-link network profile:

```bash
nmcli connection down opentpu-direct
nmcli connection delete opentpu-direct
```
