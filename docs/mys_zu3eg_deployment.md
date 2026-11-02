# MYS-ZU3EG wide-bridge deployment

Follow the [complete deployment guide](mys_zu3eg_jtag_128_16.md) for
Qwen3.5-0.8B int4 weights with an int8 head on the MYIR MYS-ZU3EG K2,
2 GiB DDR4. It covers host setup, building, JTAG/Ethernet loading and
128-token prefill plus 16 decode steps.

The `mys-zu3eg:d128-m1` profile uses **128 x 1 PEs**, a **177.5-MHz core**
and **332.8125-MHz HP AXI clock**. The bridge has a **256-bit AXI interface**,
eight outstanding reads and an independent write engine. A 256-bit clock
crossing precedes the 256-to-128 downsizer in the HP domain.
[Bridge notes](mys_zu3eg_ddr_bridge.md) explain the ordering and validation.
PS DDR and CPU clocks retain the board's working boot settings. Linux is
not required; initialized board-specific PS DDR is required.

The full system routes on `xczu3eg-sfvc784-1-i`: setup +0.011 ns, hold
+0.012 ns and pulse-width +0.002 ns, with zero failing endpoints. CDC has
20 safe endpoints and none unsafe or unknown. It uses 55,616 LUTs,
49,758 registers, 148.5 BRAM tiles and 229 DSPs, occupying 8,779/8,820 CLB
sites (99.54%). The margins are small but positive.

The bitstream and XSA are in `build/zu3eg/wide_128_16/hardware/`; validation
and implementation evidence is in `build/zu3eg/wide_validation/report.json`.
Fifty regression tests passed, along with functional simulations of the
actual Xilinx converters. Small kernels match ISA DRAM and scratchpad
contents exactly. The vendor-IP stream test improved from 424 to 279 HP
cycles (1.52x); this is not full-model tokens/s. The board has not been
programmed or measured with this implementation.
