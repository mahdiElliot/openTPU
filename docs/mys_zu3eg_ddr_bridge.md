# Wide queued PS DDR bridge for MYS-ZU3EG

`rtl/boards/mys-zu3eg/otpu_ps_dram.sv` has a **256-bit AXI interface at
177.5 MHz**, eight outstanding reads and an independent write engine. It
preserves the core's word addresses, numerical format and model memory layout.
The PS HP port remains 128 bits at 332.8125 MHz.

## Clock and width conversion

```text
256-bit bridge @ 177.5 MHz
  -> 256-bit asynchronous AXI Clock Converter
  -> 256-to-128 AXI downsizer @ 332.8125 MHz
  -> PS S_AXI_HP0_FPD
```

Downsizing follows the clock crossing, so it does not leave a 128-bit data
path at the core clock. The core-side interface supplies an ideal 5.680 GB/s;
the HP interface caps the read-stream ceiling at 5.325 GB/s before overhead.
`--axi-data-width 128` is available for comparison builds in separate output
directories; the deployment default is 256.

A D128 chunk uses four 256-bit beats and eight 128-bit HP beats. At D32/D64,
the wide interface uses one/two beats. Port A's 32-byte scale line fits one
wide beat. Scalar stores align to the bus beat and place their bytes in the
correct lanes; the downsizer preserves byte strobes and burst responses.

## Queues and ordering

- A, B and scalar stores have 4/8/4 input slots. Ready depends on registered
  occupancy, not the current request signals.
- Eight metadata slots identify outstanding AXI reads. One AXI ID preserves
  issue order; B's original core tag returns with its data.
- A and B misses arbitrate round-robin. A allows one scale-line miss in flight
  so subsequent requests can reuse the line. B uses the remaining credits.
- The write engine arbitrates stores round-robin and handles one AW/W/B burst
  at a time. Independent reads can continue while BRESP is delayed.
- Queue payload arrays have no reset, and B payload memory updates only for
  accepted stores. `AXI_DATA_WIDTH`, `READ_DEPTH`, `A_DEPTH`, `B_DEPTH` and
  `SW_DEPTH` are bridge parameters. Input depths are powers of two of at least
  two; `READ_DEPTH` is a power of two of at least one.

A write waits for older reads from its own port. Its queue advances past the
write only after BRESP, preserving per-port read/write order. Across ports,
the slice uses its existing write-drain barriers for dependent accesses.
`wr_idle` includes queued stores and pending responses; `rd_idle` includes
queued reads, issued reads and the response output cycle.

Accepted writes and `a_flush` invalidate the scale cache and poison pending
fills. A late response cannot restore an invalidated cache entry. Response
errors, unexpected IDs and incorrect RLAST remain sticky errors. AW, W and AR
stay stable under backpressure.

## Validation

Run from the repository root with the project environment activated:

```bash
python -m pytest -q -s tests/test_zu3eg_ps.py tests/test_zu3eg_build.py
python -m tools.zu3eg.check_memory_path --out build/zu3eg/wide_validation/vendor_wide
```

The protocol tests cover D32/D64/D128, 128/256-bit interfaces, multiple reads,
queue wraparound, delayed responses, byte masks, tags, read/write order,
4-KiB boundary accesses, flushes during misses and AXI errors. Small int8/int4
MLPs compare every byte of 1 MiB DRAM and every scratchpad word with the ISA.
Build-check regressions reject a narrow clock crossing or a downsizer placed
in the slow domain.

`check_memory_path` generates and simulates the actual Xilinx clock converter
and downsizer, using the deployment clocks and a fast 128-bit HP memory model.
It verifies protocol/data behavior, including eight HP reads in flight. The
FIFO behavioral models do not reproduce physical synchronization delays;
routed timing and CDC reports qualify those crossings.

The controlled vendor-IP comparison streams 24 D128 chunks (3,072 bytes):

| Core-side width | HP width/clock | Stream HP cycles |
|---|---|---:|
| 128 bits | 128 bits / 332.8125 MHz | 424 |
| 256 bits | 128 bits / 332.8125 MHz | 279 |

That is **1.52x throughput** on this small stream, including the converter
path and queue startup. It is not a full-model tokens/s measurement. The
comparison can be repeated with `check_memory_path --axi-data-width 128` in
another output directory.

Small direct-bridge MLP results, with all DRAM and scratchpad words bit-exact:

| Target | Weights | Kernel RUN cycles |
|---|---|---:|
| D128 x 1 | int8 | 7,663 |
| D128 x 1 | int4 | 10,008 |
| D64 x 2 | int4 | 13,612 |

The combined focused regression suite passed **50 tests**. Simulation logs
and the consolidated report are under `build/zu3eg/wide_validation/`.

## Full-system implementation

Vivado 2022.2 routes the complete system on `xczu3eg-sfvc784-1-i` at
**177.5-MHz core / 332.8125-MHz HP AXI**, with:

- Setup +0.011 ns, hold +0.012 ns and pulse-width +0.002 ns; zero failing endpoints.
- CDC: 20 safe endpoints, zero unsafe and zero unknown.
- 55,616 LUTs, 49,758 registers, 148.5 BRAM tiles and 229 DSPs.
- 8,779/8,820 occupied CLB sites (99.54%).

The margins are small but positive. Build this profile and verify the actual
clock/width topology with:

```bash
python -u -m tools.zu3eg.build_board --target mys-zu3eg:d128-m1 \
  --core-mhz 177.5 --mem-mhz 332.8125 --axi-data-width 256 \
  --out build/zu3eg/wide_128_16/hardware
python -m tools.zu3eg.check_build build/zu3eg/wide_128_16/hardware
```

The bitstream and XSA are ready in that hardware directory. The board was not
programmed during validation. Follow the
[deployment guide](mys_zu3eg_jtag_128_16.md) for physical kernel checks, model
loading and the complete 128/16 request; full-model board throughput remains
unmeasured for this implementation.
