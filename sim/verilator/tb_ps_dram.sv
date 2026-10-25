// PS bridge protocol regression: independent AW/W/R stalls, delayed B responses,
// masked chunk stores, byte stores, tagged reads, and host cache invalidation.
module tb_ps_dram #(parameter int D = 128);
  localparam int CW = D/4;
  logic clk = 0;
  always #5 clk = !clk;
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
  logic [5:0] m_axi_awid, m_axi_arid, m_axi_bid = 0, m_axi_rid = 0;
  logic m_axi_awlock, m_axi_arlock;
  logic [3:0] m_axi_awcache, m_axi_arcache, m_axi_awqos, m_axi_arqos;
  logic [2:0] m_axi_awprot, m_axi_arprot;
  logic m_axi_awvalid, m_axi_arvalid, m_axi_awready, m_axi_arready;
  logic [127:0] m_axi_wdata, m_axi_rdata = 0;
  logic [15:0] m_axi_wstrb;
  logic m_axi_wlast, m_axi_wvalid, m_axi_wready;
  logic [1:0] m_axi_bresp = 0, m_axi_rresp = 0;
  logic m_axi_bvalid = 0, m_axi_rvalid = 0, m_axi_rlast = 0;
  logic m_axi_bready, m_axi_rready;
  otpu_ps_dram #(.BASE(0), .D(D)) dut (.*);

  logic [31:0] memory [2048], expected [2048];
  logic [31:0] random_state = 32'h5e7311;
  int write_at, read_at, writes_left, reads_left, b_delay = 0;
  int read_bursts = 0;
  bit writing = 0, reading = 0, inject_b_error = 0;
  assign m_axi_awready = !writing && !m_axi_bvalid && random_state[0];
  assign m_axi_wready = writing && random_state[2];
  assign m_axi_arready = !reading && !m_axi_rvalid && random_state[3];
  always @(posedge clk) begin
    random_state <= {random_state[30:0], random_state[31] ^ random_state[21] ^ random_state[1] ^ random_state[0]};
    if (m_axi_awvalid && m_axi_awready) begin
      if (m_axi_awaddr[3:0] || m_axi_awsize != 4 || m_axi_awburst != 1) $fatal(1, "bad AW");
      if (m_axi_awaddr[11:0] + 16 * (int'(m_axi_awlen) + 1) > 4096) $fatal(1, "4K write crossing");
      write_at <= int'(m_axi_awaddr) / 4;
      writes_left <= int'(m_axi_awlen) + 1;
      writing <= 1;
    end
    if (m_axi_wvalid && m_axi_wready) begin
      for (int w = 0; w < 4; w++)
        for (int b = 0; b < 4; b++)
          if (m_axi_wstrb[w*4+b]) memory[write_at+w][8*b +: 8] <= m_axi_wdata[w*32+8*b +: 8];
      if (m_axi_wlast != (writes_left == 1)) $fatal(1, "bad WLAST");
      write_at <= write_at + 4;
      writes_left <= writes_left - 1;
      if (writes_left == 1) begin writing <= 0; b_delay <= 11; end
    end
    if (b_delay > 0) begin
      if (wr_idle) $fatal(1, "write idle before B response");
      b_delay <= b_delay - 1;
      if (b_delay == 1) begin
        m_axi_bvalid <= 1;
        m_axi_bresp <= inject_b_error ? 2 : 0;
      end
    end
    if (m_axi_bvalid && m_axi_bready) m_axi_bvalid <= 0;
    if (m_axi_arvalid && m_axi_arready) begin
      if (m_axi_araddr[3:0] || m_axi_arsize != 4 || m_axi_arburst != 1) $fatal(1, "bad AR");
      if (m_axi_araddr[11:0] + 16 * (int'(m_axi_arlen) + 1) > 4096) $fatal(1, "4K read crossing");
      read_at <= int'(m_axi_araddr) / 4;
      reads_left <= int'(m_axi_arlen) + 1;
      reading <= 1;
      read_bursts <= read_bursts + 1;
    end
    if (m_axi_rvalid && m_axi_rready) begin
      m_axi_rvalid <= 0;
      read_at <= read_at + 4;
      reads_left <= reads_left - 1;
      if (reads_left == 1) reading <= 0;
    end else if (!m_axi_rvalid && reading && random_state[4]) begin
      m_axi_rvalid <= 1;
      m_axi_rlast <= reads_left == 1;
      for (int w = 0; w < 4; w++) m_axi_rdata[w*32 +: 32] <= memory[read_at+w];
    end
  end

  task automatic scalar_write(input int at, input logic [31:0] value, input logic [3:0] enables);
    @(negedge clk); sw_req = 1; sw_addr = at; sw_wdata = value; sw_be = enables;
    do @(posedge clk); while (!sw_rdy);
    @(negedge clk); sw_req = 0;
    for (int b = 0; b < 4; b++) if (enables[b]) expected[at][8*b +: 8] = value[8*b +: 8];
    wait (wr_idle); @(negedge clk);
  endtask
  task automatic read_a(input int at);
    @(negedge clk); a_req = 1; a_addr = at;
    do @(posedge clk); while (!a_rdy);
    @(negedge clk); a_req = 0;
    wait (a_rvalid);
    for (int w = 0; w < 8; w++)
      if (a_rdata[w*32 +: 32] !== expected[(at & ~7)+w]) $fatal(1, "A data mismatch %0d", at);
    @(negedge clk);
  endtask
  task automatic read_b(input int at, input bit requested_tag);
    @(negedge clk); b_req = 1; b_we = 0; b_addr = at; b_tag = requested_tag;
    do @(posedge clk); while (!b_rdy);
    @(negedge clk); b_req = 0;
    wait (b_rvalid);
    if (b_rtag !== requested_tag) $fatal(1, "B tag mismatch");
    for (int w = 0; w < CW; w++)
      if (b_rdata[w*32 +: 32] !== expected[at+w]) $fatal(1, "B data mismatch %0d", at+w);
    @(negedge clk);
  endtask
  task automatic write_b(input int at, input logic [31:0] enables);
    @(negedge clk); b_req = 1; b_we = 1; b_addr = at; b_wmask = enables;
    for (int w = 0; w < CW; w++) b_wdata[w*32 +: 32] = 32'hba100000 + at + w;
    do @(posedge clk); while (!b_rdy);
    for (int w = 0; w < CW; w++) if (enables[w]) expected[at+w] = b_wdata[w*32 +: 32];
    @(negedge clk); b_req = 0;
    wait (wr_idle); @(negedge clk);
  endtask
  initial begin
    for (int i = 0; i < 2048; i++) begin memory[i] = 32'h56000000 + i; expected[i] = memory[i]; end
    repeat (4) @(negedge clk);
    rst = 0;
    write_b(32, 32'hffffffff);
    write_b(32, 32'h55555555);
    for (int i = 0; i < 16; i++) scalar_write(32+i, 32'hd1020304+i, 4'(i));
    read_b(32, 0);
    read_b(32, 1);
    read_a(33);
    begin
      int previous_reads;
      previous_reads = read_bursts;
      read_a(38);
      if (read_bursts != previous_reads) $fatal(1, "scale cache missed");
      memory[33] = 32'h12345678; expected[33] = 32'h12345678;
      @(negedge clk); a_flush = 1;
      @(negedge clk); a_flush = 0;
      read_a(33);
      if (read_bursts != previous_reads + 1) $fatal(1, "scale cache not invalidated");
    end
    write_b(992, 32'h81818181);
    read_b(992, 1);
    for (int i = 0; i < 2048; i++) if (memory[i] !== expected[i]) $fatal(1, "memory differs %0d", i);
    if (error) $fatal(1, "unexpected AXI error");
    inject_b_error = 1;
    scalar_write(1, 42, 15);
    if (!error) $fatal(1, "AXI error not recorded");
    $display("PS_DRAM_PROTOCOL_PASS");
    $finish;
  end
  initial begin #1000000; $fatal(1, "timeout"); end
endmodule
