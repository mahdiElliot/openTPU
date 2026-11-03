// Complete board-control, loader, core and PS-memory-path ISA comparison.
module tb_ps_core #(
  parameter int D = 128, MCOLS = 1, ACT_BLOCKS = 48, ACT_ROWS = 1, MXU_E = 1,
                AXI_DATA_WIDTH = 256, READ_DEPTH = 8, READ_CHUNKS = 1, WORDS = 1 << 18,
                TMEM_NSB = 2, TMEM_SBH = 0, VPU_CL = 1, WIN = 8
);
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
  logic m_axi_awready;
  logic [AXI_DATA_WIDTH-1:0] m_axi_wdata;
  logic [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb;
  logic m_axi_wlast;
  logic m_axi_wvalid;
  logic m_axi_wready;
  logic [5:0] m_axi_bid;
  logic [1:0] m_axi_bresp;
  logic m_axi_bvalid;
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
  logic m_axi_arready;
  logic [AXI_DATA_WIDTH-1:0] m_axi_rdata;
  logic [5:0] m_axi_rid;
  logic [1:0] m_axi_rresp;
  logic m_axi_rlast;
  logic m_axi_rvalid;
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
                      .ACT_ROWS(ACT_ROWS), .MXU_E(MXU_E), .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
                      .MEMORY_STATS(1), .BRIDGE_READ_DEPTH(READ_DEPTH),
                      .TMEM_NSB(TMEM_NSB), .TMEM_SBH(TMEM_SBH),
                      .VPU_CL(VPU_CL), .WIN(WIN),
                      .BRIDGE_B_DEPTH(READ_DEPTH > 8 ? READ_DEPTH : 8),
                      .BRIDGE_READ_CHUNKS(READ_CHUNKS)) dut (.*);
  int expected_pressure = 0, expected_starve = 0, expected_either = 0;
  int profile_mxu = 0, profile_vpu = 0, profile_dma = 0, profile_quant = 0;
  int profile_mxu_vpu = 0, profile_dma_vpu = 0;
  int profile_vpu_deny = 0, profile_vpu_replay = 0, profile_quant_deny = 0;
  always @(posedge clk) if (dut.run && !dut.halted) begin
    bit mb, vb, db;
    mb = dut.u_core.u_slice.u_seq.uq_h[otpu_pkg::U_MXU] != dut.u_core.u_slice.u_seq.uq_t[otpu_pkg::U_MXU];
    vb = dut.u_core.u_slice.u_seq.uq_h[otpu_pkg::U_VPU] != dut.u_core.u_slice.u_seq.uq_t[otpu_pkg::U_VPU];
    db = dut.u_core.u_slice.u_seq.uq_h[otpu_pkg::U_DMA] != dut.u_core.u_slice.u_seq.uq_t[otpu_pkg::U_DMA];
    profile_mxu += int'(mb); profile_vpu += int'(vb); profile_dma += int'(db);
    profile_quant += int'(dut.u_core.u_slice.u_seq.uq_h[otpu_pkg::U_Q] != dut.u_core.u_slice.u_seq.uq_t[otpu_pkg::U_Q]);
    profile_mxu_vpu += int'(mb && vb); profile_dma_vpu += int'(db && vb);
    profile_vpu_deny += int'(dut.u_core.u_slice.lose_vpu);
    profile_vpu_replay += int'(dut.u_core.u_slice.tm_lost[dut.u_core.u_slice.P_VA] ||
                              dut.u_core.u_slice.tm_lost[dut.u_core.u_slice.P_VB]);
    profile_quant_deny += int'(dut.u_core.u_slice.lose_q);
  end
  logic previous_active = 0, previous_pressure = 0, previous_starve = 0;
  always @(posedge clk) begin
    if (previous_active) begin
      expected_pressure += int'(previous_pressure);
      expected_starve += int'(previous_starve);
      expected_either += int'(previous_pressure || previous_starve);
    end
    previous_active = dut.run && !dut.halted;
    // Check the counters against source events, before perf's pipeline stage.
    previous_pressure = (dut.u_core.u_slice.b_req_c && !dut.u_core.u_slice.b_rdy_i) ||
      (dut.u_core.u_slice.mxu_areq && !dut.u_core.u_slice.a_rdy_i) ||
      (dut.u_core.u_slice.q_awant && !dut.u_core.u_slice.sw_rdy_i);
    previous_starve = dut.u_core.u_slice.mxu_starve;
  end
  wire wr_idle = dut.wr_idle;
  otpu_ps_axi_mem #(.WORDS(WORDS), .DATA_WIDTH(AXI_DATA_WIDTH), .BASE(40'h10000000)) memory_model (
    .clk, .rst(!aresetn), .wr_idle,
    .hold_reads(1'b0), .hold_b(1'b0), .inject_b_error(1'b0), .inject_r_error(1'b0),
    .read_bursts(), .max_read_pending(), .cycles(), .*
  );

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
    for (int i = 0; i < WORDS; i++) memory_model.memory[i] = 0;
    fd = $fopen({dir, "/input.bin"}, "rb");
    bytes_read = $fread(memory_model.memory, fd); $fclose(fd);
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
    read_reg(12'h300, status);
    if (status != 32'h4d535431) $fatal(1, "memory stats signature %x", status);
    read_reg(12'h304, status);
    if (status != expected_pressure) $fatal(1, "pressure counter %d expected %d", status, expected_pressure);
    read_reg(12'h308, status);
    if (status != expected_starve) $fatal(1, "starvation counter %d expected %d", status, expected_starve);
    read_reg(12'h30c, status);
    if (status != expected_either) $fatal(1, "union counter %d expected %d", status, expected_either);
    write_reg(12'h008, 0);
    repeat (8) @(negedge clk);
    read_reg(12'h30c, status);
    if (status != expected_either) $fatal(1, "STOP changed memory statistics");
    $display("MEMORY_STATS_PASS pressure=%0d starve=%0d either=%0d", expected_pressure, expected_starve, expected_either);
    fd = $fopen({dir, "/dram_out.bin"}, "wb");
    for (int i = 0; i < WORDS; i++) begin
      $fwrite(fd, "%c%c%c%c", memory_model.memory[i][7:0], memory_model.memory[i][15:8], memory_model.memory[i][23:16], memory_model.memory[i][31:24]);
    end
    $fclose(fd);
    force dut.u_core.u_slice.u_tmem.dump = 1'b1;
    @(negedge clk);
    release dut.u_core.u_slice.u_tmem.dump;
    repeat (4) @(negedge clk);
    $display("PS_CORE_PASS cycles=%0d max_reads=%0d", cycles, memory_model.max_read_pending);
    $display("COMPUTE_PROFILE mxu=%0d vpu=%0d dma=%0d quant=%0d mxu_vpu=%0d dma_vpu=%0d vpu_deny=%0d vpu_replay=%0d quant_deny=%0d",
      profile_mxu, profile_vpu, profile_dma, profile_quant, profile_mxu_vpu, profile_dma_vpu,
      profile_vpu_deny, profile_vpu_replay, profile_quant_deny);
    $finish;
  end
  initial begin #100000000; $fatal(1, "timeout"); end
endmodule
