// MYS-ZU3EG core plus AXI HP memory bridge and CPU/JTAG-accessible controls.
// All ports here use core_clk; IP Integrator converts M_AXI into the HP clock domain.
module otpu_zu3eg_accel #(
  parameter int D = 128,
  parameter int AXI_DATA_WIDTH = 256,
  parameter bit MEMORY_STATS = 1'b0,
  parameter int BRIDGE_READ_DEPTH = 8,
  parameter int BRIDGE_B_DEPTH = 8,
  parameter int BRIDGE_READ_CHUNKS = 1,
  parameter int MCOLS = 1,
  parameter int ACT_BLOCKS = 48,
  parameter int ACT_ROWS = 1,
  parameter int TMEM_WORDS = 65536,
  parameter int IMEM_WORDS = 32768,
  parameter int FIFO_DEPTH = 64,
  parameter int LANES = 4,
  parameter int WIN = 8,
  parameter int MXU_CL = 16,
  parameter int MXU_E = 1,
  parameter int VPU_CL = 1,
  parameter int ULANES = 4,
  parameter int RPB = 32,
  parameter int WPB = 1,
  parameter int TMEM_NSB = 2,
  parameter int TMEM_SBP = 3,
  parameter int TMEM_SBH = 0,
  parameter int TMEM_RPL = 1,
  parameter int TMEM_TDP = 1,
  parameter int TMEM_GST = 1,
  parameter int TMEM_PMP = 0,
  parameter int TMEM_PRK = 0,
  parameter int TMEM_SDM = -1,
  parameter int CORE_KHZ = 112500,
  parameter int ARCH_HASH = 0
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK",
     X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF M_AXI:S_AXI, ASSOCIATED_RESET aresetn" *)
  input logic clk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST",
     X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
  input logic aresetn,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWADDR" *)
  output logic [39:0] m_axi_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWLEN" *)
  output logic [7:0] m_axi_awlen,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWSIZE" *)
  output logic [2:0] m_axi_awsize,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWBURST" *)
  output logic [1:0] m_axi_awburst,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWID" *)
  output logic [5:0] m_axi_awid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWLOCK" *)
  output logic m_axi_awlock,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWCACHE" *)
  output logic [3:0] m_axi_awcache,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWPROT" *)
  output logic [2:0] m_axi_awprot,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWQOS" *)
  output logic [3:0] m_axi_awqos,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWVALID" *)
  output logic m_axi_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWREADY" *)
  input logic m_axi_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WDATA" *)
  output logic [AXI_DATA_WIDTH-1:0] m_axi_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WSTRB" *)
  output logic [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WLAST" *)
  output logic m_axi_wlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WVALID" *)
  output logic m_axi_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WREADY" *)
  input logic m_axi_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BID" *)
  input logic [5:0] m_axi_bid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BRESP" *)
  input logic [1:0] m_axi_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BVALID" *)
  input logic m_axi_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BREADY" *)
  output logic m_axi_bready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARADDR" *)
  output logic [39:0] m_axi_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARLEN" *)
  output logic [7:0] m_axi_arlen,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARSIZE" *)
  output logic [2:0] m_axi_arsize,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARBURST" *)
  output logic [1:0] m_axi_arburst,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARID" *)
  output logic [5:0] m_axi_arid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARLOCK" *)
  output logic m_axi_arlock,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARCACHE" *)
  output logic [3:0] m_axi_arcache,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARPROT" *)
  output logic [2:0] m_axi_arprot,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARQOS" *)
  output logic [3:0] m_axi_arqos,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARVALID" *)
  output logic m_axi_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARREADY" *)
  input logic m_axi_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RDATA" *)
  input logic [AXI_DATA_WIDTH-1:0] m_axi_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RID" *)
  input logic [5:0] m_axi_rid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RRESP" *)
  input logic [1:0] m_axi_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RLAST" *)
  input logic m_axi_rlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RVALID" *)
  input logic m_axi_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RREADY" *)
  output logic m_axi_rready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
  input logic [11:0] s_axi_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
  input logic s_axi_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
  output logic s_axi_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
  input logic [31:0] s_axi_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
  input logic [3:0] s_axi_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
  input logic s_axi_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
  output logic s_axi_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
  output logic [1:0] s_axi_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
  output logic s_axi_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
  input logic s_axi_bready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
  input logic [11:0] s_axi_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
  input logic s_axi_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
  output logic s_axi_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
  output logic [31:0] s_axi_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
  output logic [1:0] s_axi_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
  output logic s_axi_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
  input logic s_axi_rready
);
  logic run, ld_start, ld_busy, halted, core_error, wait_to, mem_error;
  logic wr_idle, rd_idle, a_inval;
  logic [31:0] ld_addr, ld_n, icount;
  logic [31:0] args [8];
  logic [7:0][31:0] rinit;
  always_comb for (int i = 0; i < 8; i++) rinit[i] = args[i];
  logic a_req, a_we, a_rdy, a_rvalid, sw_req, sw_rdy;
  logic b_req, b_we, b_tag, b_par, b_rdy, b_rvalid, b_rtag;
  logic [31:0] a_addr, a_wdata, sw_addr, sw_wdata, b_addr;
  logic [D/4-1:0] b_wmask;
  logic [3:0] a_be, sw_be;
  logic [255:0] a_rdata;
  logic [D*8-1:0] b_wdata, b_rdata;
  logic [1:0] memory_stall;
  logic [31:0] ctrl_rdata;
  if (MEMORY_STATS) begin : g_memory_stats
    // Per-run counters, frozen after HALT/STOP. These are overlapping event
    // conditions, not a claim that all these cycles prevented other useful work.
    logic run_q, active_q, selected;
    logic [31:0] pressure, starvation, either_stall, read_value;
    always_ff @(posedge clk) begin
      if (!aresetn) begin
        run_q <= 1'b0; active_q <= 1'b0;
        pressure <= '0; starvation <= '0; either_stall <= '0;
        selected <= 1'b0; read_value <= '0;
      end else begin
        run_q <= run;
        // core_perf is one cycle late: qualify it with the previous RUN/HALT
        // state, including the final event but excluding program-loader traffic.
        active_q <= run && !halted;
        if (run && !run_q) begin
          pressure <= '0; starvation <= '0; either_stall <= '0;
        end else if (active_q) begin
          pressure <= pressure + 32'(memory_stall[0]);
          starvation <= starvation + 32'(memory_stall[1]);
          either_stall <= either_stall + 32'(|memory_stall);
        end
        if (s_axi_arvalid && s_axi_arready) begin
          selected <= s_axi_araddr >= 12'h300 && s_axi_araddr <= 12'h30c;
          case (s_axi_araddr[11:2])
            10'h0c0: read_value <= 32'h4d535431; // MST1: counter definition version
            10'h0c1: read_value <= pressure;
            10'h0c2: read_value <= starvation;
            10'h0c3: read_value <= either_stall;
            default: read_value <= '0;
          endcase
        end
      end
    end
    assign s_axi_rdata = selected ? read_value : ctrl_rdata;
  end else begin : g_no_memory_stats
    assign s_axi_rdata = ctrl_rdata;
  end

  otpu_asic_top #(
    .D(D), .MCOLS(MCOLS), .ACT_BLOCKS(ACT_BLOCKS), .ACT_ROWS(ACT_ROWS),
    .TMEM_WORDS(TMEM_WORDS), .IMEM_WORDS(IMEM_WORDS), .FIFO_DEPTH(FIFO_DEPTH),
    .LANES(LANES), .WIN(WIN), .MXU_IMPL(2), .MXU_CL(MXU_CL), .MXU_E(MXU_E),
    .VPU_CL(VPU_CL), .ULANES(ULANES), .HAS_DSTEP(0), .TMEM_IMPL(3),
    .RPB(RPB), .WPB(WPB), .TMEM_NSB(TMEM_NSB), .TMEM_SBP(TMEM_SBP), .TMEM_SBH(TMEM_SBH),
    .TMEM_RPL(TMEM_RPL), .TMEM_TDP(TMEM_TDP), .TMEM_GST(TMEM_GST), .TMEM_PMP(TMEM_PMP), .TMEM_PRK(TMEM_PRK), .TMEM_SDM(TMEM_SDM),
    .SEQ_WC(0), .COLL_GATHER(0), .MEMORY_STATS(MEMORY_STATS)
  ) u_core (
    .clk, .sys_rst(!aresetn), .rst(!aresetn || !run), .rinit,
    .ld_start, .ld_addr, .ld_n, .ld_busy,
    .a_req, .a_we, .a_addr, .a_wdata, .a_be, .a_rdy, .a_rvalid, .a_rdata,
    .sw_req, .sw_addr, .sw_wdata, .sw_be, .sw_rdy,
    .b_req, .b_we, .b_tag, .b_addr, .b_wdata, .b_wmask, .b_par,
    .b_rdy, .b_rvalid, .b_rtag, .b_rdata, .wr_idle, .rd_idle,
    .halted, .error(core_error), .wait_to, .a_inval, .icount, .memory_stall
  );
  otpu_ps_dram #(.D(D), .AXI_DATA_WIDTH(AXI_DATA_WIDTH), .READ_DEPTH(BRIDGE_READ_DEPTH),
    .B_DEPTH(BRIDGE_B_DEPTH), .READ_CHUNKS(BRIDGE_READ_CHUNKS)) u_ddr (
    .clk, .rst(!aresetn), .a_flush(a_inval || !run),
    .a_req, .a_we, .a_addr, .a_wdata, .a_be, .a_rdy, .a_rvalid, .a_rdata,
    .sw_req, .sw_addr, .sw_wdata, .sw_be, .sw_rdy,
    .b_req, .b_we, .b_tag, .b_addr, .b_wdata, .b_wmask,
    .b_rdy, .b_rvalid, .b_rtag, .b_rdata, .wr_idle, .rd_idle,
    .error(mem_error),
    .m_axi_awaddr,
    .m_axi_awlen,
    .m_axi_awsize,
    .m_axi_awburst,
    .m_axi_awid,
    .m_axi_awlock,
    .m_axi_awcache,
    .m_axi_awprot,
    .m_axi_awqos,
    .m_axi_awvalid,
    .m_axi_awready,
    .m_axi_wdata,
    .m_axi_wstrb,
    .m_axi_wlast,
    .m_axi_wvalid,
    .m_axi_wready,
    .m_axi_bid,
    .m_axi_bresp,
    .m_axi_bvalid,
    .m_axi_bready,
    .m_axi_araddr,
    .m_axi_arlen,
    .m_axi_arsize,
    .m_axi_arburst,
    .m_axi_arid,
    .m_axi_arlock,
    .m_axi_arcache,
    .m_axi_arprot,
    .m_axi_arqos,
    .m_axi_arvalid,
    .m_axi_arready,
    .m_axi_rdata,
    .m_axi_rid,
    .m_axi_rresp,
    .m_axi_rlast,
    .m_axi_rvalid,
    .m_axi_rready
  );
  otpu_ctrl #(
    .D(D), .MCOLS(MCOLS), .LANES(LANES), .ACT_ROWS(ACT_ROWS), .CORE_KHZ(CORE_KHZ),
    .BUILD_ID(32'h20261024), .TRACE_DEPTH(0), .HAS_TEMP(0), .HAS_I2C(0),
    .CHASH(0), .DSTEP(0), .HOSTCAL(0), .COLL_GATHER(0),
    .S(1), .TMEM_WORDS(TMEM_WORDS), .IMEM_WORDS(IMEM_WORDS), .ACT_BLOCKS(ACT_BLOCKS),
    .DRAM_LOG2(31), .NCH(1), .CH_LOG2(31), .MXU_IMPL(2), .TMEM_IMPL(3),
    .VPU_CL(VPU_CL), .ULANES(ULANES), .SEQ_WC(0), .PLATFORM(3), .ARCH_HASH(ARCH_HASH)
  ) u_ctrl (
    .clk, .rst(!aresetn),
    .s_awaddr(s_axi_awaddr),
    .s_awvalid(s_axi_awvalid),
    .s_awready(s_axi_awready),
    .s_wdata(s_axi_wdata),
    .s_wstrb(s_axi_wstrb),
    .s_wvalid(s_axi_wvalid),
    .s_wready(s_axi_wready),
    .s_bresp(s_axi_bresp),
    .s_bvalid(s_axi_bvalid),
    .s_bready(s_axi_bready),
    .s_araddr(s_axi_araddr),
    .s_arvalid(s_axi_arvalid),
    .s_arready(s_axi_arready),
    .s_rdata(ctrl_rdata),
    .s_rresp(s_axi_rresp),
    .s_rvalid(s_axi_rvalid),
    .s_rready(s_axi_rready),
    .run, .ld_start, .arg(args), .ld_addr, .ld_n, .ld_busy, .halted,
    .error(core_error || mem_error), .wait_to, .icount(icount),
    .wr_idle, .axi_err(mem_error), .calib(2'b11), .ecc_ded(1'b0),
    .b_rd(b_req && b_rdy && !b_we), .b_wr(b_req && b_rdy && b_we),
    .a_rd(a_req && a_rdy && !a_we), .a_wr(sw_req && sw_rdy),
    .b_wait(b_req && !b_rdy), .temp_v(1'b0), .temp(12'd0),
    .mxu_busy(1'b0), .mxu_mac(1'b0), .vpu_busy(1'b0), .qnt_busy(1'b0),
    .dma_busy(1'b0), .tmem_deny(1'b0), .mxu_starve(1'b0),
    .dram_rd(1'b0), .dram_wr(1'b0), .dram_wait(1'b0), .instr(2'd0),
    .tr_en(), .tr_stop(), .tr_clear(), .tr_addr(), .tr_count(32'd0),
    .tr_drop(32'd0), .tr_busy(1'b0), .tr_rdata(64'd0), .i2c_lo(), .i2c_in(5'd0)
  );
endmodule
