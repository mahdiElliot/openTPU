# Qwen3.5-0.8B on MYS-ZU3EG K2: 128 × 1 PEs, dual clocks, 128/16 workload

This guide builds a complete FPGA system from this repository, prepares Qwen3.5-0.8B with **int4 layer weights and an int8 LM head**, loads it through JTAG or Ethernet, and measures inference on the MYIR **MYS-ZU3EG K2, XCZU3EG-1SFVC784I, 2 GiB PS DDR4**.

**Ethernet alternative:** section 14 also provides a verified Ethernet model-loading path. JTAG still programs the FPGA, starts the Arm loader and runs inference; Ethernet replaces the slow bulk model transfer. A previous D64 × 2, 543.8-MiB image took 19.8 seconds to upload with CRC checks plus 63.7 seconds for a complete SHA-256 readback, compared with about 30 minutes through JTAG. Prepare a fresh image for the D128 × 1 configuration below.

The selected array has **one column of depth 128**: `MCOLS=1`, `D=128`, `S=1`, or **128 PEs total**. The workload is a **128-token prompt followed by 16 measured decode steps**. The software, hardware and model-image configuration must match.

The workload executes the text decoder's 24 layers and LM head. The checkpoint's vision tower and MTP drafter are not part of this text-only deployment.

## 1. Understand what has been validated

The earlier physical-board run used D128 × 1, a single 112.5-MHz core/AXI clock, and 512/16 tokens. Its tokens and logits matched the ISA reference, with 1.160 prefill and 0.684 decode device tokens/s. The subsequent D64 × 2 build at 150/300 MHz measured about 0.619 prefill and 0.354 decode device tokens/s over its completed 128 prefill tokens and first ten decode steps. The workloads differ, but D128 × 1 is the faster demonstrated geometry. Both arrays contain 128 PEs.

The D128 × 1 build at 150-MHz core / 325-MHz HP AXI subsequently completed the 128/16 physical-board request. Prefill and final logits and all generated token IDs matched the ISA reference bit for bit. Its measured rates are:

| Phase | FPGA execution tokens/s | Wall-clock tokens/s, including JTAG and host |
|---|---:|---:|
| Prefill, 128 tokens | 1.1005 | 0.3724 |
| Decode, 16 steps | 0.6445 | 0.02875 |

The request took 900.27 seconds (15.00 minutes), excluding preparation, model upload and the separate ISA reference. Counting the prefill seed plus 16 decode outputs gives 17 output tokens and 0.01888 end-to-end output tokens/s. These results are saved locally under `build/zu3eg/d128m1_128_16/run/`; generated artifacts are excluded from version control. Repeat physical kernel and full-request checks for an independently rebuilt bitstream.

This guide's code supports D32, D64 and D128 memory chunks, target-selected geometry and independent core/AXI clocks. The clock and validation results for the selected build are recorded in section 9. A build that passes Vivado timing still needs the physical kernel and model checks in this guide before its board performance is established.

**Boot prerequisite:** this JTAG flow preserves the working PS DDR initialization supplied by the board's existing bootloader. Linux is not required. We do not yet provide a verified cold-boot MYIR DDR preset or a replacement FSBL. Starting with an uninitialized chip in JTAG boot mode requires the exact board DDR configuration first. Selecting a ZU3EG part alone cannot infer DDR timing, wiring or training settings.

Programming changes volatile PL configuration and model DDR contents. It halts Arm core 0 to give the accelerator exclusive use of its memory region. It does not write SD, eMMC or QSPI flash. Rebooting restores the board's original boot sequence and loses the model loaded in DDR.

## 2. Configuration and clock domains

| Setting | Value |
|---|---:|
| Target | `mys-zu3eg:d128-m1` |
| Requested core / HP AXI clocks | 150 / 325 MHz; validate the complete route in sections 7 and 9 |
| PEs | `1 × 128 × 1 = 128` |
| `D / MCOLS` | `128 / 1` |
| `ACT_BLOCKS / ACT_ROWS` | `48 / 1` |
| `PAIR` | Disabled |
| `LANES / ULANES` | `4 / 4` |
| `MXU_E / VPU_CL` | `1 / 1` |
| `TMEM_WORDS` | 65,536 words = 256 KiB logical scratchpad |
| `IMEM_WORDS` | 32,768 words = 4,096 instructions |
| `WIN / FIFO_DEPTH` | `8 / 64` |
| TMEM implementation | 3, single copy, two sub-banks, TDP and replay |
| `TMEM_PMP / DSTEP / STREAM / SEQ_WC` | `0 / false / false / false` |
| Context capacity | 256 tokens |
| Prefill / decode | 128 prompt tokens / 16 additional forward passes |
| Model image | 552,667,648 bytes, approximately 527.1 MiB |

There are several different clocks; changing one does not change all of them:

```text
Existing board PS reference and PLLs
    ├── PS DDR controller and physical DDR: existing boot settings
    └── PS PL0 reference output: 100 MHz
          └── Clock Wizard
                ├── core clock: accelerator, control registers, memory bridge
                └── AXI clock: PS-facing HP0 memory interface

accelerator + bridge ── AXI Clock Converter ── S_AXI_HP0_FPD ── PS DDR
        core clock                crossing          AXI clock
```

Here, **double clocking means two PL clock domains**. `--mem-mhz` names the **AXI HP interface clock**, not the DDR chip clock or Arm CPU frequency. The DDR controller performs its own internal crossings. The core and AXI clocks come from the same Clock Wizard, but the supplied crossing uses asynchronous mode with three synchronization stages, allowing different frequency ratios safely. Each side has its own synchronized reset, driven by a common reset source and Clock Wizard lock signal.

The code does not double-pump the scratchpad: keep `TMEM_PMP=0`. The `clk_f` scratchpad-pumping option is a different architecture and is not enabled by this guide.

AMD documents asynchronous AXI conversion in [PG059](https://docs.amd.com/r/en-US/pg059-axi-interconnect/Clock-Conversion) and the PS AXI performance limit in [DS925](https://docs.amd.com/r/en-US/ds925-zynq-ultrascale-plus/Processor-System-PS-Performance-Characteristics). This build flow rejects requested AXI frequencies above 333 MHz; a particular routed design can have a lower limit.

## 3. Install host prerequisites

Use the repository root for every command. Clone [mahdiElliot/openTPU](https://github.com/mahdiElliot/openTPU) with `git clone https://github.com/mahdiElliot/openTPU.git` if starting with a new checkout. These examples use Bash and the paths on this PC; change the root or tool installation path if necessary.

```bash
cd /home/mahdi/Documents/brochrue/project/openTPU

# Ubuntu host prerequisites, if missing:
sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential usbutils libusb-1.0-0

python3 --version
# The pinned environment used here is Python 3.12.
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e . -r tools/zu3eg/requirements.txt
python -m tools.zu3eg.setup_tools
python -m pip check
verilator --version
```

The requirements include NumPy, CPU PyTorch, Transformers, safetensors, Hugging Face Hub, pytest, Textual and the project-local Verilator wheel. `setup_tools` applies the GCC/PCH settings needed by that wheel and creates `.venv/bin/verilator`. Activate this environment before running RTL checks.

Vivado **2022.2** and XSDB are used for implementation and JTAG. The JTAG-only path needs no Arm compiler, QuestaSim, Linux on the board or Ethernet. The optional Ethernet path additionally uses the installed **Vitis 2022.2**, its standalone/lwIP libraries, Arm compiler and XSCT. It boots a bare-metal service, without Linux.

```bash
export VIVADO=/home/mahdi/vivado/Vivado/2022.2/bin/vivado
export VITIS_XSDB=/home/mahdi/vivado/Vitis/2022.2/bin/xsdb
test -x "$VIVADO"
test -x "$VITIS_XSDB"
"$VIVADO" -version
```

The Python transport currently defaults to the XSDB path above. If installed elsewhere, change the default in `tools/zu3eg/jtag.py`. `build_board --vivado` selects a different Vivado executable. The bulk-loader helper uses the same 2022.2 Vivado path in `tools/zu3eg/load_model.py`.

Allow approximately 30 GiB free disk for tools' build files, model, reference outputs and simulation caches. A 16-GiB laptop with swap was sufficient for the earlier implementation; synthesis and routing can use several GiB per Vivado process. The scripts limit build parallelism to avoid excessive memory usage.

## 4. Download and verify the checkpoint

```bash
mkdir -p build/zu3eg/hf-cache
export HF_HOME="$PWD/build/zu3eg/hf-cache"
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2
unset HF_HUB_OFFLINE
python -u -m tools.zu3eg.download

sha256sum models/Qwen3.5-0.8B/model.safetensors-00001-of-00001.safetensors
```

Expected checkpoint SHA-256:

```text
04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696
```

The downloader pins revision `2fc06364715b967f1860aea9cf38778875588b17`. After the complete checkpoint and tokenizer have been downloaded, enable offline use:

```bash
export HF_HUB_OFFLINE=1
```

The repository's `int4` format is its integer MX format. The README also discusses other 4-bit formats, including `fp4`; they are not interchangeable. Keep `wformat='int4'` and `head_format='int8'` in this deployment unless deliberately creating and validating another format.

## 5. Define this experiment once

Use a new directory for a different target, capacity or weight format. Never reuse an old quantized image merely because its file size looks similar.

```bash
export TARGET='mys-zu3eg:d128-m1'
export DEPLOY="$PWD/build/zu3eg/d128m1_128_16"
export HW="$DEPLOY/hardware"
export LOADER="$DEPLOY/loader"
export RUN="$DEPLOY/run"
export CORE_MHZ=150
export MEM_MHZ=325

mkdir -p "$DEPLOY" "$RUN"
RUN_ARGS=(--target "$TARGET" --prefill 128 --decode 16 --capacity 256 --out "$RUN")

python -m opentpu.target show "$TARGET"
python -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --preflight
```

The preflight compiles representative prompt and decode programs, with and without the LM head, checks the 4,096-instruction limit, and checks the physical DDR window. For this profile and capacity, the largest sampled program has 2,276 instructions. Results are in `$RUN/preflight.json`.

These shell variables must be defined again in a new terminal. The target TOML's inherited `platform.core_mhz=50` describes an earlier experiment; **the explicit build arguments and generated hardware metadata select this deployment's clocks**.

The default prompt is the first 128 tokenizer tokens of `tools/data/austen_pp_ch1.txt`, matching the benchmark text used in the repository's qualification tool. No chat template is applied. To use another file, append `--prompt-file /absolute/path/prompt.txt` to `RUN_ARGS`; the runner takes its first 128 tokens and rejects a shorter file.

## 6. Check RTL before implementation

```bash
python -m pytest -q tests/test_zu3eg_ps.py tests/test_zu3eg_sequence.py
```

The PS bridge tests exercise 32-, 64- and 128-byte chunks, stalled AXI channels, byte/word masks, read tags, delayed write responses, error reporting and scale-cache invalidation. Complete kernel tests compare all DRAM and scratchpad contents against the ISA, including D64 × 2 int4. These tests do not simulate the vendor AXI Clock Converter; its generated constraints and CDC report are checked in the Vivado build.

## 7. Build the accelerator and both clock domains

```bash
python -u -m tools.zu3eg.build_board \
  --target "$TARGET" --core-mhz "$CORE_MHZ" --mem-mhz "$MEM_MHZ" \
  --vivado "$VIVADO" --out "$HW"

python -m tools.zu3eg.check_build "$HW"
```

`build_board` creates the PS block design, Clock Wizard, AXI-Lite control interconnect, target-parameterized accelerator, AXI Clock Converter and reset controller for each clock domain. It runs synthesis, placement, routing and bitstream generation on **the exact ZU3EG part**, then exports the XSA.

If `--mem-mhz` equals `--core-mhz`, the direct single-clock connection is used. With different clocks, the AXI crossing is inserted automatically. Do not connect the core's raw memory handshake signals directly to another clock domain.

Useful output files:

| File under `$HW` | Meaning |
|---|---|
| `configuration.json` | Target, architecture, core and AXI clocks, RTL parameters |
| `build_params.tcl` | Parameters passed to the wrapper and Clock Wizard |
| `system_bd.tcl` | Reproducible block design |
| `clocks.csv` | Actual generated clock constraints |
| `timing.rpt` | Setup, hold and pulse-width slack for the complete system |
| `cdc.rpt` | Clock-domain-crossing diagnostics |
| `utilization.rpt` | Full-system LUT, FF, BRAM, DSP and occupied CLB usage |
| `drc.rpt` | Design-rule diagnostics |
| `opentpu_zu3eg.bit` | Volatile FPGA image |
| `opentpu_zu3eg.xsa` | Hardware handoff; not a validated MYIR cold-boot preset |
| `build_check.json` | Parsed timing and clock checks |

All three worst slacks must be nonnegative; failing endpoint counts must be zero. Read any critical CDC diagnostic and resolve its named path. Do not hide unknown crossings with a broad false-path constraint. The converter supplies its own CDC constraints; the common reset source is synchronized separately for each clock.

The programming helper independently rejects negative setup, hold or pulse-width slack. A generated `.bit` alone does not prove timing closure.

## 8. Build the temporary bulk loader

**Skip this section if using Ethernet in section 14B.** The temporary PL loader is needed only for the JTAG bulk-transfer method.

```bash
python -u -m tools.zu3eg.build_board --loader \
  --vivado "$VIVADO" --out "$LOADER"
```

This small, separate bitstream contains AMD's queued 64-bit JTAG-to-AXI master. Its only purpose is to load DDR efficiently. It contains no model accelerator, uses its own 112.5-MHz interface clock, and is replaced by the accelerator after loading. Its clock need not equal the accelerator's clocks.

This method avoids adding the loader's logic permanently to an accelerator that already occupies most of the FPGA's CLB sites. DDR contents survive a PL-only reconfiguration when PS DDR is left running.

## 9. Select clocks using full-system timing

The selected pair is **150-MHz core / 325-MHz HP AXI** on the exact `xczu3eg-sfvc784-1-i`. This is the fastest complete D128 × 1 dual-clock implementation checked during this work. Its implementation and reports are retained under `build/zu3eg/d128m1_dual150_325/hardware/`.

Vivado 2022.2 full-system results, 2026-10-24:

| Check | Routed result |
|---|---:|
| Worst setup slack | +0.425 ns |
| Worst hold slack | +0.010 ns |
| Worst pulse-width slack | +0.038 ns |
| Setup / hold / pulse-width failing endpoints | 0 / 0 / 0 |
| CDC endpoints | 20 safe, 0 unsafe, 0 unknown |
| CLB LUTs | 52,828 / 70,560 (74.87%) |
| Registers | 43,506 / 141,120 (30.83%) |
| Occupied CLB sites | 8,555 / 8,820 (97.00%) |
| BRAM tiles | 142 / 216 (65.74%) |
| DSPs | 229 / 360 (63.61%) |

The converter, both reset domains, PS interfaces and accelerator are included. This bitstream also passed physical int8/int4 kernel checks and the complete 128/16 model request; its logits and token IDs matched the ISA reference. Section 1 records the measured performance. Use sections 11–16 to validate a new deployment.

The Clock Wizard selects a 100-MHz input, multiplier 9.75, input divider 1, and a 975-MHz VCO. Output dividers 6.5 and 3 generate exactly 150 and 325 MHz. `clocks.csv` reports frequencies derived from periods rounded to three decimal places, so small differences from nominal values are expected.

The old D128 × 1 single-clock route's estimated 134.3-MHz limit applied to that fixed placement and routing. It is not a global limit for the core. Adding the converter, changing placement and routing again can change the critical paths substantially. Likewise, the D64 × 2 route's 150-MHz timing result cannot qualify this D128 × 1 build.

The build and report commands in this guide select this 150/325-MHz implementation. If your independent rebuild fails timing, stop before programming; a generated bitstream alone does not qualify it. Keep the reports for the exact bitstream you eventually use.

On this PC, you can reuse the retained implementation instead of rebuilding section 7. After defining the variables in section 5, override `HW` and check it:

```bash
export HW="$PWD/build/zu3eg/d128m1_dual150_325/hardware"
python -m tools.zu3eg.check_build "$HW"
```

Keep this `HW` value for programming, kernel checks, loading, inference and the final report. Use the fresh D128 `$RUN` directory from section 5; a retained bitstream does not supply a prepared model or physical kernel validation for this experiment.

The clock pair passes implementation and physical-board checks; it is not proof of an absolute maximum or a throughput optimum. The 325-MHz AXI clock is below this flow's 333-MHz limit; the PS DDR and Arm CPU clocks retain their existing boot settings. Higher AXI frequency cannot remove the bridge's one-outstanding-burst restriction or the synchronization latency at the crossing. Repeat physical kernel and full-model checks to establish correctness and tokens/s for a new build.

## 10. Connect power, JTAG and check the current PS state

Power the board with its normal supply, connect the Platform Cable USB II to the board's JTAG header, and connect the cable to the PC. Keep the cooling fan operating. UART is optional. For Ethernet loading, also connect the board's RJ45 socket directly to the PC's Ethernet port; JTAG remains connected.

```bash
lsusb
# Expect Xilinx Platform Cable USB II, commonly 03fd:0008.
```

If the cable is visible but Vivado cannot open it, install the vendor cable drivers once:

```bash
sudo /home/mahdi/vivado/Vivado/2022.2/data/xicom/cable_drivers/lin64/install_script/install_drivers/install_drivers
```

Reconnect the cable after installing udev rules. Close other tools that hold the JTAG target. Vivado/XSDB can start the local hardware server automatically; normally do not run the entire deployment as root.

Read the connected target and boot state without programming it:

```bash
python - <<'PY'
from tools.zu3eg.jtag import Jtag
link = Jtag('build/zu3eg/inspect')
try:
    print(link.command('targets'))
    print('DDRC status:', link.words(0xFD070004))
    print('RPLL control:', [hex(v) for v in link.words(0xFF5E0030)])
    print('PL0 reference:', [hex(v) for v in link.words(0xFF5E00C0)])
finally:
    link.close()
PY
```

The previously inspected board reports DDRC normal mode (`status & 7 == 1`), RPLL control `0x00013000`, and PL0 control `0x01010802`, corresponding to the observed 100-MHz reference. The programming helper checks these values and refuses an unrecognized clock setup. A different bootloader or board revision needs a verified reference-clock configuration rather than removing that guard.

**Do not call the generated `psu_init()` wholesale.** The current project does not know the MYIR DDR preset. The helper applies only PS-PL interface widths, isolation removal and PL reset configuration while preserving working DDR and PLL settings.

## 11. Program the accelerator and check its physical clock

```bash
python -u -m tools.zu3eg.program_board --hardware "$HW" --out "$DEPLOY/bringup"
```

Expected checks include:

- FPGA ID/version corresponding to D128, one column and four vector lanes.
- Architecture hash matching the selected target.
- Core frequency metadata matching the build.
- AXI-Lite scratch-register readback.
- A two-second cycle-counter sanity check against the PC timer.

The timer check includes JTAG measurement error; Vivado's Clock Wizard and routed timing establish the intended clock configuration. Its result is not a precision frequency-counter measurement. The AXI-domain frequency is established by the generated clock report, not by the core RUN counter.

## 12. Run physical kernels before loading the model

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" \
  --hardware "$HW" --kernel-only
```

Both int8 and int4 kernels must pass byte-for-byte comparison with the ISA. Their JSON results include the exact bitstream identity, preventing reuse of an earlier board test for a different build.

Run this step **before** bulk loading: the kernels overwrite the beginning of the accelerator's DDR region. Do not run them between loading Qwen and starting the preloaded request.

## 13. Prepare the quantized image and reference on the PC

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --prepare
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" --reference-only \
  > "$DEPLOY/reference.log" 2>&1
```

The first command writes `$RUN/model_image.bin` and a checksum/layout manifest. The second executes the same 128/16 request on the ISA simulator and saves expected prefill logits, output token IDs and final logits. It does not access the board.

The cache manifest covers geometry, context capacity, formats, checkpoint identity and relevant Python source. A mismatch is an error; use a new output directory or deliberately regenerate that experiment's image. Changing D also changes quantization blocks, so never reuse the previous D64 image, reference or kernel results for this D128 build. The older D128 capacity-1024 image also has a different layout from this capacity-256 image. Changing only clock frequencies preserves the model layout, but physical kernel records must match the newly programmed bitstream.

The reference can run while Vivado works because it uses only the PC. Do not run two physical JTAG clients concurrently while either is programming or transferring model data.

## 14. Load the full image: choose JTAG or Ethernet

Use **one** of the following transfer methods. Both produce the same `$RUN/model_loaded.json` manifest for section 15. The physical kernels in section 12 must pass before either method; they overwrite the beginning of the model region.

### 14A. JTAG bulk transfer

Optional scratch transfer check:

```bash
python -u -m tools.zu3eg.load_model --loader "$LOADER" --hardware "$HW" \
  --image "$RUN/model_image.bin" --limit 65536
```

Then load the model:

```bash
python -u -m tools.zu3eg.load_model --loader "$LOADER" --hardware "$HW" \
  --image "$RUN/model_image.bin" > "$DEPLOY/load_model.log" 2>&1
```

The helper checks the image checksum, halts Arm core 0, loads the temporary PL loader, transfers the image into DDR starting at physical `0x10000000`, reads back selected beginning/middle/end bursts, restores the accelerator bitstream and writes `$RUN/model_loaded.json`.

During the transfer, `load_model.log` can remain at `Programming temporary AXI loader; preserving initialized PS DDR...` because Vivado's detailed progress is written to a separate file. From another terminal with `LOADER` defined, monitor it with:

```bash
tail -f "$LOADER/upload.log"
```

Lines such as `AXI_LOADED 42991616 552667648 127025` mean **bytes loaded**, **total bytes**, and **elapsed milliseconds**. Increasing byte counts confirm that the transfer is progressing. Press Ctrl+C in the monitoring terminal to stop `tail` only; allow the original upload command to finish. For the optional scratch test, the corresponding file is `$LOADER/upload_test.log`.

The earlier image took approximately 30 minutes at 300–350 KiB/s through this cable. Direct XSDB memory writes were much slower, approximately 30 KiB/s. Neither upload time is part of the inference request's reported timing.

The selected model image occupies approximately 527 MiB. Additional program and input/output areas are in DDR; programs are staged just after the image and copied into instruction memory by the loader. The software checks that the physical region stays below the board's 2-GiB boundary. The old 1-GiB behavioral simulation window is not a physical-board DDR allocation rule.

### 14B. Ethernet bulk transfer, with JTAG programming and inference

This path was verified on the connected board at 1 Gb/s. It uses the PS GEM3 MAC and the board's AR8035 PHY at MDIO address 4. The working firmware preserves the board's original high-impedance Ethernet reset setting and supplies the AR8035 RGMII delays and clock divisors. Its program/stack/heap live in low DDR, outside the accelerator model starting at `0x10000000`.

**Keep the accelerator bitstream from section 11 programmed.** Ethernet loading does not use the temporary PL bulk-loader bitstream. If an interrupted JTAG upload left the temporary loader programmed, restore the accelerator first:

```bash
python -u -m tools.zu3eg.program_board --hardware "$HW" --out "$DEPLOY/bringup"
```

Do not run that programming command during an active transfer. The interrupted image must be uploaded again from its beginning; a partial JTAG image is not usable.

#### Build the bare-metal Ethernet firmware once

```bash
export ETH_WORK="$DEPLOY/ethernet/workspace"

python -u -m tools.zu3eg.build_ethernet --create --workspace "$ETH_WORK" \
  > "$DEPLOY/ethernet_build.log" 2>&1
```

`--create` generates a software-only Vivado handoff with the MYIR Ethernet/UART MIO assignments, creates a Vitis standalone platform with lwIP, then builds the supplied monitor. It does not program PL or run `psu_init()`. The helper removes a conflicting `LD_PRELOAD` from compiler subprocesses and uses XSCT's `-nodisp` mode, avoiding the old GLIBC mismatch and Xvfb requirement encountered during bring-up.

Once the platform exists, rebuild edited monitor sources without regenerating it:

```bash
python -u -m tools.zu3eg.build_ethernet --workspace "$ETH_WORK"
```

The output is `$ETH_WORK/monitor/Debug/monitor.elf`. Hardware/software generation logs are under `$DEPLOY/ethernet/hardware_export/`. The default tool paths use `/home/mahdi/vivado/Vitis/2022.2` and `/home/mahdi/vivado/Vivado/2022.2`; adjust the helpers for another installation.

#### Establish the direct network link

First check the cable and link LEDs. The PC must report carrier `1` before an IP address or software server can be useful:

```bash
ip -brief link
cat /sys/class/net/enp45s0/carrier
cat /sys/class/net/enp45s0/speed
```

On this PC the interface is `enp45s0`; change that name if yours differs. The verified link reported speed `1000` (Mb/s). If carrier is `0`, reseat both plugs, check board power and try a known-good cable. Do not repeatedly reflash the accelerator to solve an absent physical carrier.

Create a temporary direct-link profile once:

```bash
nmcli connection add save no type ethernet ifname enp45s0 \
  con-name opentpu-direct connection.autoconnect no \
  ipv4.method manual ipv4.addresses 192.168.200.1/24 \
  ipv4.never-default yes ipv6.method disabled
nmcli connection up opentpu-direct
```

If `opentpu-direct` already exists, run only the `connection up` command. This profile gives the PC `192.168.200.1/24`; the board firmware uses `192.168.200.2/24`. Wi-Fi remains the default route. The profile is unsaved and does not require a DHCP server or router. NetworkManager may request host-administrator authorization.

#### Start and check the service through JTAG

```bash
python -u -m tools.zu3eg.boot_ethernet --workspace "$ETH_WORK" \
  --out "$DEPLOY/ethernet/boot"

ping -I enp45s0 -c 3 192.168.200.2
```

`ETHERNET_BOOT_STATE 6` means the monitor reached its network loop. The helper resets only Cortex-A53 core 0, loads the ELF into low DDR and starts it; it preserves the programmed accelerator and PS DDR. It checks the observed 1.5-GHz IOPLL configuration used by its UART/GEM clock divisors. The service listens on TCP port **8020**. JTAG boot is required because the service is not installed in flash.

Do not run another JTAG programming command while the Arm service is transferring data. A minimal reachability check for the transfer service is:

```bash
python - <<'PY'
from tools.zu3eg.ethernet import Ethernet
link = Ethernet('192.168.200.2')
try:
    print('Accelerator ID/version:', link.read(0x80000000, 8).view('<u4').tolist())
finally:
    link.close()
PY
```

#### Upload, verify all DDR bytes, and return to JTAG inference

```bash
python -u -m tools.zu3eg.load_ethernet --address 192.168.200.2 \
  --hardware "$HW" --image "$RUN/model_image.bin" \
  > "$DEPLOY/load_ethernet.log" 2>&1
```

This verifies the local image SHA-256 and selected hardware geometry, sends 8-MiB chunks to the model's physical DDR address, verifies each chunk's CRC32, and then **reads back the entire model over Ethernet and checks its SHA-256**. Cache flush/invalidation operations keep the CPU and noncoherent PL memory path consistent. Progress appears as `ETHERNET_LOADED`, `ETHERNET_READBACK` and `ETHERNET_FULL_DDR_SHA256_PASS`.

After successful verification, the helper halts the Arm monitor through JTAG and writes both `$RUN/ethernet_load_result.json` and `$RUN/model_loaded.json` with `pristine: true`. The monitor is intentionally stopped so the existing JTAG inference runner owns DDR exclusively; the TCP service will no longer respond until restarted. You can then proceed directly to section 15 using `--preloaded`.

Measured for the D64 × 2, capacity-256 image on 2026-10-24:

| Check | Result |
|---|---:|
| Ethernet link | 1 Gb/s |
| Scratch write/read test | 1 MiB, all bytes equal, CRC equal |
| Prepared model bytes | 570,194,432 |
| Full upload plus per-chunk CRC | 19.808 seconds |
| Complete DDR readback plus SHA-256 | 63.671 seconds |
| Local and physical-DDR SHA-256 | `2541753679c296755f9cad8b256418454edfb91ab45e2bfbf685c4df11dce197` |

These are historical transfer/verification measurements, not tokens/s or the expected checksum for this guide's D128 image. The loader obtains the correct size and SHA-256 from the freshly generated `$RUN/model_image.json`; do not copy the D64 checksum into it.

For a later request, restart the service with `boot_ethernet` and reload the initial image using `load_ethernet`. Do not rerun the physical kernels after loading. If finished with the temporary host network configuration, remove it with:

```bash
nmcli connection down opentpu-direct
nmcli connection delete opentpu-direct
```

## 15. Run the complete 128/16 request

```bash
python -u -m tools.zu3eg.run_board "${RUN_ARGS[@]}" \
  --hardware "$HW" --preloaded --verify-isa \
  > "$DEPLOY/board_run.log" 2>&1
```

Each prompt row executes on the FPGA. The LM head executes only on the last prompt row, producing the initial output token. Then the runner executes 16 further greedy forward/argmax steps, matching `tools/qual/perf.py --prompt 128 --tokens 16`. EOS does not terminate this fixed benchmark early.

**Token-count convention:** 16 decode steps after the prefill seed means 17 selected output tokens in total. If you want exactly 16 outputs including that seed, use `--decode 15` everywhere, including the reference. The report provides both decode-step rates and total-output request rates.

The selected one-column profile uses one prefill row per run, following the already validated board bring-up path. Changing prefill row scheduling is a separate optimization that requires its own compiler and numerical checks.

For quiet operation, wait for the command to return before reading the log. For progress, inspect `$RUN/progress.json` or `tail "$DEPLOY/board_run.log"`. Avoid another hardware-manager session during the request.

The runner marks the preloaded image as used before execution. To run another request, repeat your chosen JTAG or Ethernet load step to restore zero initial KV/recurrent state. Reprogramming PL alone does not reset model state in DDR.

## 16. Verify results and report performance

```bash
python -m tools.zu3eg.deployment_report --directory "$DEPLOY" --hardware "$HW"

python - <<'PY'
import json, os
from pathlib import Path
run = Path(os.environ['RUN'])
result = json.loads((run / 'result.json').read_text())
verification = json.loads((run / 'isa_verification.json').read_text())
assert result['complete'] and verification['passed']
for key in ('prefill_device_tokens_per_second', 'decode_device_tokens_per_second',
            'prefill_wall_tokens_per_second', 'decode_wall_tokens_per_second',
            'request_wall_output_tokens_per_second', 'request_wall_seconds'):
    print(key, result[key])
print('Generated text:', result['generated_text'])
print('Verification:', verification)
PY
```

If using `$HW` outside `$DEPLOY/hardware`, pass that directory using the report tool's `--hardware` option. The final report must refer to the same programmed bitstream and its timing/utilization results.

Interpret the rates as follows:

```text
frequency_hz = core_khz × 1000
prefill_device_tokens/s = 128 × frequency_hz / sum(prefill RUN cycles)
decode_device_tokens/s = 16 × frequency_hz / sum(decode RUN cycles)
decode_wall_tokens/s = 16 / decode wall seconds
request_output_tokens/s = 17 / (prefill wall seconds + decode wall seconds)
```

The full request includes prefill and generation. FPGA RUN time excludes host program loading, PC compilation and JTAG logit transfers. Wall time includes those operations but excludes initial quantization, the large model upload and the independent ISA reference run. Reading every vocabulary logit over JTAG can dominate decode wall time; do not present a RUN-cycle number as complete application throughput.

The bridge still has **one burst outstanding**. A faster HP clock and correct crossings do not hide DDR latency or guarantee a speedup. Multiple outstanding reads, longer buffered weight streams, and local token selection are future performance work. The old 9.64 ideal-memory core figure is not a prediction for this physical system.

For a controlled throughput comparison, run the same 128/16 workload, target and capacity with the earlier 112.5/112.5-MHz single-clock setting in another hardware directory. Revalidate kernels and reload the initial image before each request. Two clock domains are supported, but their additional handshake latency can outweigh the higher AXI frequency; a timing pass alone cannot establish which setting is faster.

## 17. Scale the design down or change its geometry

### Select an existing target

| Target | Depth × columns | PEs | Main tradeoff |
|---|---:|---:|---|
| `mys-zu3eg:d128-m1` | 128 × 1 | 128 | This guide; faster demonstrated board geometry; PAIR disabled |
| `mys-zu3eg:d64-m2` | 64 × 2 | 128 | Smaller quantization block; PAIR enabled; slower measured board run |
| `mys-zu3eg:d32-m4` | 32 × 4 | 128 | More columns, smaller block, reduced instruction memory |
| `mys-zu3eg` | 32 × 2 | 64 | Existing smaller baseline |

For another layout, change `TARGET`, use a new `DEPLOY`, rebuild the FPGA, prepare a new image/reference, rerun physical kernels, reload DDR and repeat inference. A core frequency selected for one geometry is not established for another.

### Add a custom variant

For example, add this section to `targets/mys-zu3eg.toml` for a 64-PE D64 × 1 candidate:

```toml
[variants.d64-m1-small]
D = 64
MCOLS = 1
ACT_BLOCKS = 96
ACT_ROWS = 1
PAIR = false
MXU_E = 1
```

Then use `--target mys-zu3eg:d64-m1-small` in all commands. This is a candidate configuration; run preflight and implementation before claiming it fits.

### Understand each knob before reducing it

| Parameter | Effect and required checks |
|---|---|
| `S` | Slice count; this PS shell supports only `S=1`. More slices require another shell/driver design. |
| `D` | Quantization depth and core chunk width; supported values here are 32, 64, 128. Rebuild weights, all programs and references. Verify model dimension divisibility. |
| `MCOLS` | Matrix columns; PE count is `S × D × MCOLS`. Reducing it can reduce compute parallelism and ACT rows. |
| `PAIR` | Compiler selection for eligible int4 multiplies. Disable for one column; it does not accelerate the int8 head in the same way. |
| `ACT_BLOCKS` | Per-row activation capacity in chunks. Reducing it can cause `ACT RAM full`, even if dimensions fit, because several operands are live together. |
| `ACT_ROWS` | Activation rows. Keep sufficient rows for compiled kernels; lowering it restricts batching. |
| `TMEM_WORDS` | Logical scratchpad words. Reduction saves BRAM but can cause compiler allocation failures. Validate the complete model, not only a small MLP. |
| `IMEM_WORDS` | Eight 32-bit words per instruction. Check actual program counts at prompt/decode positions. Smaller instruction memory can fail at longer contexts. |
| `LANES / ULANES / VPU_CL` | Vector/quantization parallelism and expensive composite-function lanes. Reductions affect runtime and routing. Keep `ULANES <= LANES`; run RTL comparisons. |
| `MXU_E` | Matrix epilogue lanes; choose a value compatible with the column count. |
| `WIN / FIFO_DEPTH` | Scheduling window and weight FIFO. Smaller values can reduce area but increase stalls. |
| TMEM implementation knobs | This shell supports `TMEM_IMPL=3`, no pumping. Sub-bank/replay/TDP choices affect BRAM, arbitration and timing; revalidate them. |
| `--capacity` | DDR KV/state reservation and compiler position range. It does not reduce PE count. It must cover prefill plus decode steps. Rebuild the image and reference after changing it. |
| `--core-mhz / --mem-mhz` | Implementation clock constraints and generated clocks. Both belong in build metadata; do not edit only the printed rate. |

Changing the target file is sufficient to propagate supported parameters through `build_board`, the wrapper, control registers, bridge and runner. Unsupported changes such as `DSTEP`, `STREAM`, `SEQ_WC`, multiple slices or scratchpad pumping are rejected; supporting them requires additional RTL and runtime work.

PE count is not the FPGA DSP count: vector floating-point arithmetic, quantization and matrix epilogues also consume DSPs. Neither LUT/CLB usage nor token throughput scales linearly with PE count. Context layouts can also round memory allocations to cache tiles, so reducing `--capacity` may save less DDR than expected; use the tested capacity 256 for this workload unless preflight and reference checks justify another value.

## 18. Troubleshooting and recovery

- **No JTAG target:** check board power, header orientation, cable drivers and another process holding the cable. A USB cable appearing in `lsusb` does not prove the FPGA is reachable.
- **DDRC not normal / unexpected PS clock registers:** boot the existing board firmware normally, then recheck. Obtain or reconstruct a verified exact-board PS preset before cold initialization. Do not use a ZCU102 preset on the MYIR board.
- **`PL AXI slave ports access is not allowed`:** the provided JTAG server enables `force-mem-accesses` for its explicit DDR/CSR addresses because XSDB's default PS-only map omits the new control window.
- **ID/version/hash mismatch:** program the bitstream built for the selected target and use its `configuration.json`. Do not bypass these checks.
- **Model manifest mismatch:** target, capacity, source or checkpoint changed. Use a fresh experiment directory and prepare the correct image.
- **Negative timing slack:** choose another clock and implement it again; do not program a failing build.
- **Kernel mismatch or AXI error:** stop before the model run. Read the saved status, check bridge masks/tags and the generated CDC report, and reproduce in RTL simulation.
- **Interrupted model upload:** rerun `load_model` from the initial image; do not assume partially written DDR is usable. End stalled debugger clients before opening another client. The board's DDR can remain initialized without a full power cycle.
- **Second request differs:** reload the initial image. Qwen3.5 has persistent recurrent/convolution state as well as attention caches.
- **Slow JTAG decode:** the current flow reads full logits. The device can spend much less time computing than the PC spends transferring them; report device and wall measurements separately.

After an error, stop the core through the supplied register interface if the target is responsive. A normal board reboot is the recovery path for unknown PS/DDR state. Rebooting requires reprogramming PL and reloading the initial model before inference.

## 19. Code changes supplied with this guide

| File | Change |
|---|---|
| `rtl/boards/mys-zu3eg/otpu_ps_dram.sv` | Supports D32/D64/D128 chunks and the corresponding burst lengths and masks; external AXI remains 128 bits. |
| `rtl/boards/mys-zu3eg/otpu_zu3eg_accel.sv`, `otpu_zu3eg_wrapper.v` | Expose geometry, activation, scratchpad, instruction-memory, lane and scheduling parameters; report the configured clock and architecture hash. |
| `boards/mys-zu3eg/build.tcl` | Generate separate core/AXI clocks, instantiate AXI Clock Converter and one reset controller per domain; export timing, clock and CDC reports. |
| `tools/zu3eg/build_board.py` | Accept `--target`, `--core-mhz`, `--mem-mhz` and propagate supported target parameters into RTL. |
| `tools/zu3eg/program_board.py` | Check the selected geometry/hash and timing, apply only PS-PL initialization, and scale the clock sanity check to the built frequency. |
| `tools/zu3eg/run_board.py` | Accept workload/context/target options, prepare and validate image manifests, run a matching ISA reference and compute device/wall/request rates. |
| `tools/zu3eg/load_model.py` | Check the prepared image and record which bitstream and model were restored. |
| `tools/zu3eg/check_build.py` | Parse final setup/hold/pulse margins and compare actual generated frequencies with the requested pair. |
| `tools/zu3eg/deployment_report.py` | Report the selected geometry, clocks and workload rather than fixed 512/16 settings. |
| `boards/mys-zu3eg/ethernet/` | Source and handoff scripts for the verified bare-metal AR8035/GEM3 transfer service. |
| `tools/zu3eg/build_ethernet.py`, `boot_ethernet.py` | Generate the standalone lwIP platform, compile the monitor, and start it without resetting DDR or PL. |
| `tools/zu3eg/ethernet.py`, `load_ethernet.py` | Transfer the prepared model over TCP, verify every chunk and a full SHA-256 readback, then hand control back to JTAG. |
| `tests/test_zu3eg_ps.py`, simulation testbenches | Cover all supported chunk depths and both D128 and D64 core paths. |

The numeric format and model kernels have not been changed. A second clock is implemented at the AXI boundary; the compute core remains synchronous to one core clock. The previous 112.5-MHz single-clock path remains available by supplying equal core and AXI frequencies.
