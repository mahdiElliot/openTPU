`timescale 1ns/1fs
// PS bridge protocol regression: independent AW/W/R stalls, delayed B responses,
// masked chunk stores, byte stores, tagged reads, and host cache invalidation.
module tb_ps_dram #(parameter int D = 128, READ_DEPTH = 8, READ_CHUNKS = 1,
                    AXI_DATA_WIDTH = 256, VENDOR_PATH = 0, A_CACHE_LINES = 1, WRITE_DEPTH = 1);
  localparam int CW = D/4;
  localparam int STREAM_N = (READ_DEPTH*READ_CHUNKS+8 > 24) ? READ_DEPTH*READ_CHUNKS+8 : 24;
  logic clk = 0, mem_clk = 0;
  // The vendor path uses the deployment clocks; the direct model uses 100 MHz.
  always #(500.0/332.8125) mem_clk = !mem_clk;
  always #(VENDOR_PATH ? 500.0/177.5 : 5.0) clk = !clk;
  logic rst = 1, a_flush = 0;
  logic a_req = 0, a_we = 0, sw_req = 0, b_req = 0, b_we = 0, b_tag = 0;
  logic [31:0] a_addr = 0, a_wdata = 0, sw_addr = 0, sw_wdata = 0, b_addr = 0;
  logic [3:0] a_be = 0, sw_be = 0;
  logic [CW-1:0] b_wmask = 0;
  logic [D*8-1:0] b_wdata = 0, b_rdata;
  logic [255:0] a_rdata;
  logic a_rdy, a_rvalid, sw_rdy, b_rdy, b_rvalid, b_rtag, wr_idle, rd_idle, error;
  logic [39:0] m_axi_awaddr, m_axi_araddr;
  logic [7:0] m_axi_awlen, m_axi_arlen;
  logic [2:0] m_axi_awsize, m_axi_arsize;
  logic [1:0] m_axi_awburst, m_axi_arburst;
  logic [5:0] m_axi_awid, m_axi_arid, m_axi_bid, m_axi_rid;
  logic m_axi_awlock, m_axi_arlock;
  logic [3:0] m_axi_awcache, m_axi_arcache, m_axi_awqos, m_axi_arqos;
  logic [2:0] m_axi_awprot, m_axi_arprot;
  logic m_axi_awvalid, m_axi_arvalid, m_axi_awready, m_axi_arready;
  logic [AXI_DATA_WIDTH-1:0] m_axi_wdata, m_axi_rdata;
  logic [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb;
  logic m_axi_wlast, m_axi_wvalid, m_axi_wready;
  logic [1:0] m_axi_bresp, m_axi_rresp;
  logic m_axi_bvalid, m_axi_rvalid, m_axi_rlast;
  logic m_axi_bready, m_axi_rready;
  otpu_ps_dram #(.BASE(0), .D(D), .READ_DEPTH(READ_DEPTH), .READ_CHUNKS(READ_CHUNKS),
    .B_DEPTH(READ_DEPTH > 8 ? READ_DEPTH : 8), .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
    .A_CACHE_LINES(A_CACHE_LINES), .WRITE_DEPTH(WRITE_DEPTH)) dut (.*);

  logic hold_reads = 0, hold_b = 0, inject_b_error = 0, inject_r_error = 0;
  int read_bursts, max_read_pending, cycles;
  otpu_ps_test_mem #(.DATA_WIDTH(AXI_DATA_WIDTH), .READ_DEPTH(READ_DEPTH),
    .WRITE_DEPTH(WRITE_DEPTH), .VENDOR_PATH(VENDOR_PATH)) memory_model (.*);
  logic [31:0] expected [2048];
  logic [D*8-1:0] want_b [512];
  logic [255:0] want_a [512];
  bit want_tag [512];
  int sent_a = 0, sent_b = 0, got_a = 0, got_b = 0;
  // Capture expected data at request acceptance: a later queued write may
  // change memory before an older read returns. Tags and responses stay ordered.
  always @(negedge clk) if (!rst) begin
    if (a_rvalid) begin
      if (got_a >= sent_a || a_rdata !== want_a[got_a]) $fatal(1, "A response %0d differs", got_a);
      got_a++;
    end
    if (b_rvalid) begin
      if (got_b >= sent_b || b_rdata !== want_b[got_b] || b_rtag !== want_tag[got_b])
        $fatal(1, "B response %0d differs (tag %0d)", got_b, b_rtag);
      got_b++;
    end
  end
  task automatic send_a(input int at);
    @(negedge clk); a_req = 1; a_we = 0; a_addr = at;
    do @(posedge clk); while (!a_rdy);
    for (int w = 0; w < 8; w++) want_a[sent_a][32*w +: 32] = expected[(at & ~7)+w];
    sent_a++;
    @(negedge clk); a_req = 0;
  endtask
  task automatic send_b(input int at, input bit tag);
    @(negedge clk); b_req = 1; b_we = 0; b_addr = at; b_tag = tag;
    do @(posedge clk); while (!b_rdy);
    for (int w = 0; w < CW; w++) want_b[sent_b][32*w +: 32] = expected[at+w];
    want_tag[sent_b] = tag; sent_b++;
    @(negedge clk); b_req = 0;
  endtask
  task automatic send_sw(input int at, input logic [31:0] value, input logic [3:0] enables);
    @(negedge clk); sw_req = 1; sw_addr = at; sw_wdata = value; sw_be = enables;
    do @(posedge clk); while (!sw_rdy);
    for (int b = 0; b < 4; b++) if (enables[b]) expected[at][8*b +: 8] = value[8*b +: 8];
    @(negedge clk); sw_req = 0;
  endtask
  task automatic send_write_b(input int at, input logic [CW-1:0] enables);
    @(negedge clk); b_req = 1; b_we = 1; b_addr = at; b_wmask = enables;
    for (int w = 0; w < CW; w++) b_wdata[32*w +: 32] = 32'hba100000 + at + w;
    do @(posedge clk); while (!b_rdy);
    for (int w = 0; w < CW; w++) if (enables[w]) expected[at+w] = b_wdata[32*w +: 32];
    @(negedge clk); b_req = 0;
  endtask
  task automatic send_write_a(input int at, input logic [31:0] value, input logic [3:0] enables);
    @(negedge clk); a_req = 1; a_we = 1; a_addr = at; a_wdata = value; a_be = enables;
    do @(posedge clk); while (!a_rdy);
    for (int b = 0; b < 4; b++) if (enables[b]) expected[at][8*b +: 8] = value[8*b +: 8];
    @(negedge clk); a_req = 0; a_we = 0;
  endtask
  task automatic drain;
    wait (wr_idle && rd_idle && got_a == sent_a && got_b == sent_b);
    @(negedge clk);
  endtask
  task automatic read_a(input int at);
    send_a(at); wait (got_a == sent_a); @(negedge clk);
  endtask
  task automatic read_b(input int at, input bit tag);
    send_b(at, tag); wait (got_b == sent_b); @(negedge clk);
  endtask
  task automatic scalar_write(input int at, input logic [31:0] value, input logic [3:0] enables);
    send_sw(at, value, enables); drain();
  endtask
  task automatic write_b(input int at, input logic [CW-1:0] enables);
    send_write_b(at, enables); drain();
  endtask
  initial begin
    int start_cycles, stream_cycles, stream_max, stream_bursts, old_reads;
    for (int i = 0; i < 2048; i++) begin
      memory_model.slave.memory[i] = 32'h56000000 + i;
      expected[i] = memory_model.slave.memory[i];
    end
    repeat (32) @(negedge clk); rst = 0;
    write_b(32, '1);
    write_b(32, CW'(32'h55555555));
    for (int i = 0; i < 16; i++) scalar_write(32+i, 32'hd1020304+i, 4'(i));
    read_b(32, 0); read_b(32, 1); read_a(33);
    old_reads = read_bursts;
    read_a(38);
    if (read_bursts != old_reads) $fatal(1, "scale cache missed");
    memory_model.slave.memory[33] = 32'h12345678; expected[33] = 32'h12345678;
    @(negedge clk); a_flush = 1;
    @(negedge clk); a_flush = 0;
    read_a(33);
    if (read_bursts != old_reads + 1) $fatal(1, "scale cache not invalidated");
    write_b(1024-CW, CW'(32'h81818181)); read_b(1024-CW, 1);
    scalar_write(1023, 32'h76543210, 4'b1010);
    read_a(1023);

    // Both halves of an aligned scale fill must return their own words, and
    // stores into either half invalidate the complete cached region.
    read_a(1440);
    old_reads = read_bursts;
    read_a(1448);
    if (read_bursts != old_reads + (A_CACHE_LINES == 1 ? 1 : 0))
      $fatal(1, "adjacent scale-line fill not reused");
    scalar_write(1449, 32'h8badf00d, 15);
    read_a(1448); read_a(1440);
    if (WRITE_DEPTH > 1) begin
      hold_b = 1;
      for (int i = 0; i < WRITE_DEPTH; i++) send_sw(1504+i, 32'h24680000+i, 15);
      wait (memory_model.slave.wn == WRITE_DEPTH);
      if (wr_idle) $fatal(1, "pipelined writes lost their outstanding responses");
      // Independent reads must continue while all BRESP credits are occupied.
      read_b(128, 0);
      hold_b = 0;
      drain();
      // A same-port read must wait for the older write's BRESP.
      hold_b = 1;
      send_write_b(1536, '1);
      wait (dut.b_wflight != 0);
      old_reads = read_bursts;
      send_b(1536, 1);
      repeat (20) @(negedge clk);
      if (read_bursts != old_reads) $fatal(1, "B read passed its port's pending write");
      hold_b = 0;
      drain();
    end

    // Queue more than a ring's worth of tagged reads. Hold responses initially:
    // an implementation which waits for each RLAST cannot fill the read credits.
    start_cycles = cycles;
    hold_reads = 1;
    old_reads = read_bursts;
    fork
      begin
        for (int i = 0; i < STREAM_N; i++) send_b((i % 16) * CW, 1'(i));
      end
      begin
        wait (dut.rn == READ_DEPTH);
        repeat (8) @(negedge clk);
        if (rd_idle) $fatal(1, "read idle with queued responses");
        hold_reads = 0;
      end
    join
    drain();
    stream_cycles = cycles - start_cycles;
    stream_bursts = read_bursts - old_reads;
    if (READ_CHUNKS > 1 && stream_bursts >= STREAM_N)
      $fatal(1, "contiguous reads were not combined");
    stream_max = max_read_pending;
    if ((!VENDOR_PATH && max_read_pending != READ_DEPTH) || max_read_pending < 1)
      $fatal(1, "read credits not filled: %0d", max_read_pending);

    // A and B requests and data can progress together, preserving each port's
    // ordering even when local A cache hits bypass outstanding B bursts.
    fork
      begin for (int i = 0; i < 12; i++) send_b((16+i) * CW, 1'(i)); end
      begin for (int i = 0; i < 12; i++) send_a(768 + 8*(i/3)); end
    join
    drain();

    // Consecutive chunks on opposite sides of a 4-KiB boundary cannot merge.
    send_b(1024-CW, 0); send_b(1024, 1); drain();

    // A stopped/flush-invalidated miss must not refill a stale scale cache later.
    hold_reads = 1;
    old_reads = read_bursts;
    send_a(832);
    wait (read_bursts == old_reads + 1);
    @(negedge clk); a_flush = 1;
    @(negedge clk); a_flush = 0; hold_reads = 0;
    drain();
    old_reads = read_bursts;
    read_a(832);
    if (read_bursts != old_reads + 1) $fatal(1, "late A response undid flush");

    // A pending scalar BRESP must not stall independent B reads. A dependent
    // cross-port read is sent only after drain(), as the slice's contract requires.
    hold_b = 1;
    send_sw(1, 32'hddccbbaa, 15);
    wait (m_axi_bready);
    for (int i = 0; i < 4; i++) send_b(128 + i*CW, 1'(i));
    wait (got_b == sent_b);
    if (wr_idle) $fatal(1, "pending write lost from wr_idle");
    hold_b = 0; drain();
    read_a(1);

    // Read -> write -> read to the same chunk, all queued without host fences.
    // The first read sees old data, the second new data, even with a late response.
    hold_reads = 1;
    send_b(512, 0);
    send_write_b(512, '1);
    send_b(512, 1);
    repeat (12) @(negedge clk);
    hold_reads = 0; drain();

    // The optional A scalar-write interface has the same ordering guarantee.
    hold_reads = 1;
    old_reads = read_bursts;
    send_a(896);
    wait (read_bursts == old_reads + 1);
    send_write_a(896, 32'h11223344, 4'b0101);
    send_a(896);
    repeat (12) @(negedge clk);
    hold_reads = 0; drain();
    // Queued scalar writes wrap their ring and remain busy until every BRESP.
    for (int i = 0; i < 20; i++) send_sw(1024+i, 32'hace00000+i, 4'(i));
    drain();
    for (int i = 0; i < 2048; i++)
      if (memory_model.slave.memory[i] !== expected[i]) $fatal(1, "memory differs %0d", i);
    if (error) $fatal(1, "unexpected AXI error");
    inject_r_error = 1; read_b(32, 1); drain();
    if (!error) $fatal(1, "AXI read error not recorded");
    @(negedge clk); rst = 1;
    repeat (32) @(negedge clk); rst = 0; inject_r_error = 0;
    if (error) $fatal(1, "error did not reset");
    inject_b_error = 1; scalar_write(1, 42, 15);
    if (!error) $fatal(1, "AXI write error not recorded");
    $display("PS_DRAM_PIPELINE depth=%0d cycles=%0d max_reads=%0d width=%0d chunks=%0d bursts=%0d", READ_DEPTH, stream_cycles, stream_max, AXI_DATA_WIDTH, STREAM_N, stream_bursts);
    $display("PS_DRAM_PROTOCOL_PASS");
    $finish;
  end
  initial begin #1000000; $fatal(1, "timeout"); end
endmodule
