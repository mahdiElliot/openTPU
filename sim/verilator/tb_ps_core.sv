// Complete board-control, loader, core and PS-memory-path ISA comparison.
module tb_ps_core #(
  parameter int D = 128, MCOLS = 1, ACT_BLOCKS = 48, ACT_ROWS = 1, MXU_E = 1
);
  localparam int WORDS = 1 << 18;
  logic clk = 0, aresetn = 0, dump = 0;
  always #5 clk = !clk;
  logic [39:0] m_axi_awaddr;
  logic [7:0] m_axi_awlen;
  logic [2:0] m_axi_awsize;
  logic [1:0] m_axi_awburst;
  logic [5:0] m_axi_awid;
  logic m_axi_awlock;
  logic [3:0] m_axi_awcache;
  logic [2:0] m_axi_awprot;
  logic [3:0] m_axi_awqos;
  logic m_axi_awvalid;
  logic m_axi_awready = '0;
  logic [127:0] m_axi_wdata;
  logic [15:0] m_axi_wstrb;
  logic m_axi_wlast;
  logic m_axi_wvalid;
  logic m_axi_wready = '0;
  logic [5:0] m_axi_bid = '0;
  logic [1:0] m_axi_bresp = '0;
  logic m_axi_bvalid = '0;
  logic m_axi_bready;
  logic [39:0] m_axi_araddr;
  logic [7:0] m_axi_arlen;
  logic [2:0] m_axi_arsize;
  logic [1:0] m_axi_arburst;
  logic [5:0] m_axi_arid;
  logic m_axi_arlock;
  logic [3:0] m_axi_arcache;
  logic [2:0] m_axi_arprot;
  logic [3:0] m_axi_arqos;
  logic m_axi_arvalid;
  logic m_axi_arready = '0;
  logic [127:0] m_axi_rdata = '0;
  logic [5:0] m_axi_rid = '0;
  logic [1:0] m_axi_rresp = '0;
  logic m_axi_rlast = '0;
  logic m_axi_rvalid = '0;
  logic m_axi_rready;
  logic [11:0] s_axi_awaddr = '0;
  logic s_axi_awvalid = '0;
  logic s_axi_awready;
  logic [31:0] s_axi_wdata = '0;
  logic [3:0] s_axi_wstrb = '0;
  logic s_axi_wvalid = '0;
  logic s_axi_wready;
  logic [1:0] s_axi_bresp;
  logic s_axi_bvalid;
  logic s_axi_bready = '0;
  logic [11:0] s_axi_araddr = '0;
  logic s_axi_arvalid = '0;
  logic s_axi_arready;
  logic [31:0] s_axi_rdata;
  logic [1:0] s_axi_rresp;
  logic s_axi_rvalid;
  logic s_axi_rready = '0;
  otpu_zu3eg_accel #(.D(D), .MCOLS(MCOLS), .ACT_BLOCKS(ACT_BLOCKS),
                      .ACT_ROWS(ACT_ROWS), .MXU_E(MXU_E)) dut (.*);
  wire wr_idle = dut.wr_idle;
  logic [31:0] memory [WORDS];
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
      write_at <= (int'(m_axi_awaddr) - 32'h10000000) / 4;
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
      read_at <= (int'(m_axi_araddr) - 32'h10000000) / 4;
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


  task automatic write_reg(input logic [11:0] addr, input logic [31:0] data);
    @(negedge clk); s_axi_awaddr = addr; s_axi_wdata = data;
    s_axi_wstrb = 15; s_axi_awvalid = 1; s_axi_wvalid = 1;
    do @(posedge clk); while (!(s_axi_awready && s_axi_wready));
    @(negedge clk); s_axi_awvalid = 0; s_axi_wvalid = 0;
    wait (s_axi_bvalid); @(negedge clk);
  endtask
  task automatic read_reg(input logic [11:0] addr, output logic [31:0] data);
    @(negedge clk); s_axi_araddr = addr; s_axi_arvalid = 1;
    do @(posedge clk); while (!s_axi_arready);
    @(negedge clk); s_axi_arvalid = 0;
    while (!s_axi_rvalid) @(negedge clk);
    data = s_axi_rdata;
    @(negedge clk);
  endtask
  initial begin
    string dir;
    int fd, bytes_read, program_addr, program_n, cycles;
    logic [31:0] status;
    void'($value$plusargs("dir=%s", dir));
    void'($value$plusargs("program_addr=%d", program_addr));
    void'($value$plusargs("program_n=%d", program_n));
    for (int i = 0; i < WORDS; i++) memory[i] = 0;
    fd = $fopen({dir, "/input.bin"}, "rb");
    bytes_read = $fread(memory, fd); $fclose(fd);
    s_axi_bready = 1; s_axi_rready = 1;
    repeat (8) @(negedge clk); aresetn = 1;
    write_reg(12'h010, program_addr);
    write_reg(12'h014, program_n);
    write_reg(12'h008, 2);
    do begin read_reg(12'h00c, status); end while (status & 4);
    if (status & 18) $fatal(1, "loader failed %x", status);
    write_reg(12'h008, 5);
    do begin read_reg(12'h00c, status); end while (!(status & 1) && !(status & 18));
    if (status & 18) $fatal(1, "core failed %x", status);
    do begin read_reg(12'h00c, status); end while (!(status & 8));
    read_reg(12'h018, status); cycles = status;
    repeat (8) @(negedge clk);
    fd = $fopen({dir, "/dram_out.bin"}, "wb");
    for (int i = 0; i < WORDS; i++) begin
      $fwrite(fd, "%c%c%c%c", memory[i][7:0], memory[i][15:8], memory[i][23:16], memory[i][31:24]);
    end
    $fclose(fd);
    force dut.u_core.u_slice.u_tmem.dump = 1'b1;
    @(negedge clk);
    release dut.u_core.u_slice.u_tmem.dump;
    repeat (4) @(negedge clk);
    $display("PS_CORE_PASS cycles=%0d", cycles); $finish;
  end
  initial begin #100000000; $fatal(1, "timeout"); end
endmodule
