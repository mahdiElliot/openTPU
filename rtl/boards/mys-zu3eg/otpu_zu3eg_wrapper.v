// Verilog module-reference boundary for Vivado IP Integrator.
module otpu_zu3eg_wrapper #(
  parameter integer D = 128,
  parameter integer HP_PORTS = 2,
  parameter integer AXI_DATA_WIDTH = 256,
  parameter integer MEMORY_STATS = 0,
  parameter integer SEQ_FP_PIPE = 0,
  parameter integer SEQ_CMD_BRAM = 0,
  parameter integer SEQ_NARROW_PC = 0,
  parameter integer BRIDGE_READ_DEPTH = 8,
  parameter integer BRIDGE_B_DEPTH = 8,
  parameter integer BRIDGE_READ_CHUNKS = 1,
  parameter integer BRIDGE_A_CACHE_LINES = 1,
  parameter integer BRIDGE_WRITE_DEPTH = 1,
  parameter integer MCOLS = 1,
  parameter integer ACT_BLOCKS = 48,
  parameter integer ACT_ROWS = 1,
  parameter integer TMEM_WORDS = 65536,
  parameter integer IMEM_WORDS = 32768,
  parameter integer FIFO_DEPTH = 64,
  parameter integer LANES = 4,
  parameter integer WIN = 8,
  parameter integer MXU_CL = 16,
  parameter integer MXU_E = 1,
  parameter integer VPU_CL = 1,
  parameter integer ULANES = 4,
  parameter integer VPU_COMPACT_COMP = 0,
  parameter integer VPU_COMPACT_STAGES = 3,
  parameter integer VPU_COL_BRAM = 0,
  parameter integer SEQ_WC = 0,
  parameter integer RPB = 32,
  parameter integer WPB = 1,
  parameter integer TMEM_NSB = 2,
  parameter integer TMEM_SBP = 3,
  parameter integer TMEM_SBH = 0,
  parameter integer TMEM_RPL = 1,
  parameter integer TMEM_TDP = 1,
  parameter integer TMEM_GST = 1,
  parameter integer TMEM_PMP = 0,
  parameter integer TMEM_PRK = 0,
  parameter integer TMEM_SDM = -1,
  parameter integer CORE_KHZ = 112500,
  parameter integer ARCH_HASH = 0
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK",
     X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF M_AXI:M_META_AXI:S_AXI, ASSOCIATED_RESET aresetn" *)
  input wire clk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST",
     X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
  input wire aresetn,
  // Upper bounds cover all bridge configurations exposed by build_board.
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWADDR",
     X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXI, NUM_READ_OUTSTANDING 32, NUM_WRITE_OUTSTANDING 8, MAX_BURST_LENGTH 256" *)
  output wire [39:0] m_axi_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWLEN" *)
  output wire [7:0] m_axi_awlen,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWSIZE" *)
  output wire [2:0] m_axi_awsize,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWBURST" *)
  output wire [1:0] m_axi_awburst,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWID" *)
  output wire [5:0] m_axi_awid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWLOCK" *)
  output wire m_axi_awlock,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWCACHE" *)
  output wire [3:0] m_axi_awcache,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWPROT" *)
  output wire [2:0] m_axi_awprot,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWQOS" *)
  output wire [3:0] m_axi_awqos,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWVALID" *)
  output wire m_axi_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI AWREADY" *)
  input wire m_axi_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WDATA" *)
  output wire [AXI_DATA_WIDTH-1:0] m_axi_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WSTRB" *)
  output wire [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WLAST" *)
  output wire m_axi_wlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WVALID" *)
  output wire m_axi_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI WREADY" *)
  input wire m_axi_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BID" *)
  input wire [5:0] m_axi_bid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BRESP" *)
  input wire [1:0] m_axi_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BVALID" *)
  input wire m_axi_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI BREADY" *)
  output wire m_axi_bready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARADDR" *)
  output wire [39:0] m_axi_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARLEN" *)
  output wire [7:0] m_axi_arlen,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARSIZE" *)
  output wire [2:0] m_axi_arsize,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARBURST" *)
  output wire [1:0] m_axi_arburst,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARID" *)
  output wire [5:0] m_axi_arid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARLOCK" *)
  output wire m_axi_arlock,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARCACHE" *)
  output wire [3:0] m_axi_arcache,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARPROT" *)
  output wire [2:0] m_axi_arprot,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARQOS" *)
  output wire [3:0] m_axi_arqos,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARVALID" *)
  output wire m_axi_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI ARREADY" *)
  input wire m_axi_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RDATA" *)
  input wire [AXI_DATA_WIDTH-1:0] m_axi_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RID" *)
  input wire [5:0] m_axi_rid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RRESP" *)
  input wire [1:0] m_axi_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RLAST" *)
  input wire m_axi_rlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RVALID" *)
  input wire m_axi_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_AXI RREADY" *)
  output wire m_axi_rready,
  // Upper bounds cover all bridge configurations exposed by build_board.
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWADDR",
     X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_META_AXI, NUM_READ_OUTSTANDING 32, NUM_WRITE_OUTSTANDING 8, MAX_BURST_LENGTH 256" *)
  output wire [39:0] m_meta_axi_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWLEN" *)
  output wire [7:0] m_meta_axi_awlen,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWSIZE" *)
  output wire [2:0] m_meta_axi_awsize,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWBURST" *)
  output wire [1:0] m_meta_axi_awburst,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWID" *)
  output wire [5:0] m_meta_axi_awid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWLOCK" *)
  output wire m_meta_axi_awlock,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWCACHE" *)
  output wire [3:0] m_meta_axi_awcache,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWPROT" *)
  output wire [2:0] m_meta_axi_awprot,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWQOS" *)
  output wire [3:0] m_meta_axi_awqos,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWVALID" *)
  output wire m_meta_axi_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI AWREADY" *)
  input wire m_meta_axi_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI WDATA" *)
  output wire [AXI_DATA_WIDTH-1:0] m_meta_axi_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI WSTRB" *)
  output wire [AXI_DATA_WIDTH/8-1:0] m_meta_axi_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI WLAST" *)
  output wire m_meta_axi_wlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI WVALID" *)
  output wire m_meta_axi_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI WREADY" *)
  input wire m_meta_axi_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI BID" *)
  input wire [5:0] m_meta_axi_bid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI BRESP" *)
  input wire [1:0] m_meta_axi_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI BVALID" *)
  input wire m_meta_axi_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI BREADY" *)
  output wire m_meta_axi_bready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARADDR" *)
  output wire [39:0] m_meta_axi_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARLEN" *)
  output wire [7:0] m_meta_axi_arlen,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARSIZE" *)
  output wire [2:0] m_meta_axi_arsize,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARBURST" *)
  output wire [1:0] m_meta_axi_arburst,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARID" *)
  output wire [5:0] m_meta_axi_arid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARLOCK" *)
  output wire m_meta_axi_arlock,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARCACHE" *)
  output wire [3:0] m_meta_axi_arcache,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARPROT" *)
  output wire [2:0] m_meta_axi_arprot,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARQOS" *)
  output wire [3:0] m_meta_axi_arqos,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARVALID" *)
  output wire m_meta_axi_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI ARREADY" *)
  input wire m_meta_axi_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI RDATA" *)
  input wire [AXI_DATA_WIDTH-1:0] m_meta_axi_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI RID" *)
  input wire [5:0] m_meta_axi_rid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI RRESP" *)
  input wire [1:0] m_meta_axi_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI RLAST" *)
  input wire m_meta_axi_rlast,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI RVALID" *)
  input wire m_meta_axi_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 M_META_AXI RREADY" *)
  output wire m_meta_axi_rready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
  input wire [11:0] s_axi_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
  input wire s_axi_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
  output wire s_axi_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
  input wire [31:0] s_axi_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
  input wire [3:0] s_axi_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
  input wire s_axi_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
  output wire s_axi_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
  output wire [1:0] s_axi_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
  output wire s_axi_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
  input wire s_axi_bready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
  input wire [11:0] s_axi_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
  input wire s_axi_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
  output wire s_axi_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
  output wire [31:0] s_axi_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
  output wire [1:0] s_axi_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
  output wire s_axi_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
  input wire s_axi_rready
);
  wire [HP_PORTS*(40)-1:0] bus_m_axi_awaddr;
  assign {m_meta_axi_awaddr, m_axi_awaddr} = {{((2-HP_PORTS)*(40)){1'b0}}, bus_m_axi_awaddr};
  wire [HP_PORTS*(8)-1:0] bus_m_axi_awlen;
  assign {m_meta_axi_awlen, m_axi_awlen} = {{((2-HP_PORTS)*(8)){1'b0}}, bus_m_axi_awlen};
  wire [HP_PORTS*(3)-1:0] bus_m_axi_awsize;
  assign {m_meta_axi_awsize, m_axi_awsize} = {{((2-HP_PORTS)*(3)){1'b0}}, bus_m_axi_awsize};
  wire [HP_PORTS*(2)-1:0] bus_m_axi_awburst;
  assign {m_meta_axi_awburst, m_axi_awburst} = {{((2-HP_PORTS)*(2)){1'b0}}, bus_m_axi_awburst};
  wire [HP_PORTS*(6)-1:0] bus_m_axi_awid;
  assign {m_meta_axi_awid, m_axi_awid} = {{((2-HP_PORTS)*(6)){1'b0}}, bus_m_axi_awid};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_awlock;
  assign {m_meta_axi_awlock, m_axi_awlock} = {{((2-HP_PORTS)*(1)){1'b0}}, bus_m_axi_awlock};
  wire [HP_PORTS*(4)-1:0] bus_m_axi_awcache;
  assign {m_meta_axi_awcache, m_axi_awcache} = {{((2-HP_PORTS)*(4)){1'b0}}, bus_m_axi_awcache};
  wire [HP_PORTS*(3)-1:0] bus_m_axi_awprot;
  assign {m_meta_axi_awprot, m_axi_awprot} = {{((2-HP_PORTS)*(3)){1'b0}}, bus_m_axi_awprot};
  wire [HP_PORTS*(4)-1:0] bus_m_axi_awqos;
  assign {m_meta_axi_awqos, m_axi_awqos} = {{((2-HP_PORTS)*(4)){1'b0}}, bus_m_axi_awqos};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_awvalid;
  assign {m_meta_axi_awvalid, m_axi_awvalid} = {{((2-HP_PORTS)*(1)){1'b0}}, bus_m_axi_awvalid};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_awready;
  wire [2*(1)-1:0] all_m_axi_awready = {m_meta_axi_awready, m_axi_awready};
  assign bus_m_axi_awready = all_m_axi_awready[HP_PORTS*(1)-1:0];
  wire [HP_PORTS*(AXI_DATA_WIDTH)-1:0] bus_m_axi_wdata;
  assign {m_meta_axi_wdata, m_axi_wdata} = {{((2-HP_PORTS)*(AXI_DATA_WIDTH)){1'b0}}, bus_m_axi_wdata};
  wire [HP_PORTS*(AXI_DATA_WIDTH/8)-1:0] bus_m_axi_wstrb;
  assign {m_meta_axi_wstrb, m_axi_wstrb} = {{((2-HP_PORTS)*(AXI_DATA_WIDTH/8)){1'b0}}, bus_m_axi_wstrb};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_wlast;
  assign {m_meta_axi_wlast, m_axi_wlast} = {{((2-HP_PORTS)*(1)){1'b0}}, bus_m_axi_wlast};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_wvalid;
  assign {m_meta_axi_wvalid, m_axi_wvalid} = {{((2-HP_PORTS)*(1)){1'b0}}, bus_m_axi_wvalid};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_wready;
  wire [2*(1)-1:0] all_m_axi_wready = {m_meta_axi_wready, m_axi_wready};
  assign bus_m_axi_wready = all_m_axi_wready[HP_PORTS*(1)-1:0];
  wire [HP_PORTS*(6)-1:0] bus_m_axi_bid;
  wire [2*(6)-1:0] all_m_axi_bid = {m_meta_axi_bid, m_axi_bid};
  assign bus_m_axi_bid = all_m_axi_bid[HP_PORTS*(6)-1:0];
  wire [HP_PORTS*(2)-1:0] bus_m_axi_bresp;
  wire [2*(2)-1:0] all_m_axi_bresp = {m_meta_axi_bresp, m_axi_bresp};
  assign bus_m_axi_bresp = all_m_axi_bresp[HP_PORTS*(2)-1:0];
  wire [HP_PORTS*(1)-1:0] bus_m_axi_bvalid;
  wire [2*(1)-1:0] all_m_axi_bvalid = {m_meta_axi_bvalid, m_axi_bvalid};
  assign bus_m_axi_bvalid = all_m_axi_bvalid[HP_PORTS*(1)-1:0];
  wire [HP_PORTS*(1)-1:0] bus_m_axi_bready;
  assign {m_meta_axi_bready, m_axi_bready} = {{((2-HP_PORTS)*(1)){1'b0}}, bus_m_axi_bready};
  wire [HP_PORTS*(40)-1:0] bus_m_axi_araddr;
  assign {m_meta_axi_araddr, m_axi_araddr} = {{((2-HP_PORTS)*(40)){1'b0}}, bus_m_axi_araddr};
  wire [HP_PORTS*(8)-1:0] bus_m_axi_arlen;
  assign {m_meta_axi_arlen, m_axi_arlen} = {{((2-HP_PORTS)*(8)){1'b0}}, bus_m_axi_arlen};
  wire [HP_PORTS*(3)-1:0] bus_m_axi_arsize;
  assign {m_meta_axi_arsize, m_axi_arsize} = {{((2-HP_PORTS)*(3)){1'b0}}, bus_m_axi_arsize};
  wire [HP_PORTS*(2)-1:0] bus_m_axi_arburst;
  assign {m_meta_axi_arburst, m_axi_arburst} = {{((2-HP_PORTS)*(2)){1'b0}}, bus_m_axi_arburst};
  wire [HP_PORTS*(6)-1:0] bus_m_axi_arid;
  assign {m_meta_axi_arid, m_axi_arid} = {{((2-HP_PORTS)*(6)){1'b0}}, bus_m_axi_arid};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_arlock;
  assign {m_meta_axi_arlock, m_axi_arlock} = {{((2-HP_PORTS)*(1)){1'b0}}, bus_m_axi_arlock};
  wire [HP_PORTS*(4)-1:0] bus_m_axi_arcache;
  assign {m_meta_axi_arcache, m_axi_arcache} = {{((2-HP_PORTS)*(4)){1'b0}}, bus_m_axi_arcache};
  wire [HP_PORTS*(3)-1:0] bus_m_axi_arprot;
  assign {m_meta_axi_arprot, m_axi_arprot} = {{((2-HP_PORTS)*(3)){1'b0}}, bus_m_axi_arprot};
  wire [HP_PORTS*(4)-1:0] bus_m_axi_arqos;
  assign {m_meta_axi_arqos, m_axi_arqos} = {{((2-HP_PORTS)*(4)){1'b0}}, bus_m_axi_arqos};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_arvalid;
  assign {m_meta_axi_arvalid, m_axi_arvalid} = {{((2-HP_PORTS)*(1)){1'b0}}, bus_m_axi_arvalid};
  wire [HP_PORTS*(1)-1:0] bus_m_axi_arready;
  wire [2*(1)-1:0] all_m_axi_arready = {m_meta_axi_arready, m_axi_arready};
  assign bus_m_axi_arready = all_m_axi_arready[HP_PORTS*(1)-1:0];
  wire [HP_PORTS*(AXI_DATA_WIDTH)-1:0] bus_m_axi_rdata;
  wire [2*(AXI_DATA_WIDTH)-1:0] all_m_axi_rdata = {m_meta_axi_rdata, m_axi_rdata};
  assign bus_m_axi_rdata = all_m_axi_rdata[HP_PORTS*(AXI_DATA_WIDTH)-1:0];
  wire [HP_PORTS*(6)-1:0] bus_m_axi_rid;
  wire [2*(6)-1:0] all_m_axi_rid = {m_meta_axi_rid, m_axi_rid};
  assign bus_m_axi_rid = all_m_axi_rid[HP_PORTS*(6)-1:0];
  wire [HP_PORTS*(2)-1:0] bus_m_axi_rresp;
  wire [2*(2)-1:0] all_m_axi_rresp = {m_meta_axi_rresp, m_axi_rresp};
  assign bus_m_axi_rresp = all_m_axi_rresp[HP_PORTS*(2)-1:0];
  wire [HP_PORTS*(1)-1:0] bus_m_axi_rlast;
  wire [2*(1)-1:0] all_m_axi_rlast = {m_meta_axi_rlast, m_axi_rlast};
  assign bus_m_axi_rlast = all_m_axi_rlast[HP_PORTS*(1)-1:0];
  wire [HP_PORTS*(1)-1:0] bus_m_axi_rvalid;
  wire [2*(1)-1:0] all_m_axi_rvalid = {m_meta_axi_rvalid, m_axi_rvalid};
  assign bus_m_axi_rvalid = all_m_axi_rvalid[HP_PORTS*(1)-1:0];
  wire [HP_PORTS*(1)-1:0] bus_m_axi_rready;
  assign {m_meta_axi_rready, m_axi_rready} = {{((2-HP_PORTS)*(1)){1'b0}}, bus_m_axi_rready};

  otpu_zu3eg_accel #(
    .HP_PORTS(HP_PORTS),
    .AXI_DATA_WIDTH(AXI_DATA_WIDTH), .MEMORY_STATS(MEMORY_STATS),
    .SEQ_FP_PIPE(SEQ_FP_PIPE),
    .SEQ_CMD_BRAM(SEQ_CMD_BRAM),
    .SEQ_NARROW_PC(SEQ_NARROW_PC),
    .BRIDGE_READ_DEPTH(BRIDGE_READ_DEPTH), .BRIDGE_B_DEPTH(BRIDGE_B_DEPTH),
    .BRIDGE_READ_CHUNKS(BRIDGE_READ_CHUNKS), .BRIDGE_A_CACHE_LINES(BRIDGE_A_CACHE_LINES),
    .BRIDGE_WRITE_DEPTH(BRIDGE_WRITE_DEPTH),
    .D(D),
    .MCOLS(MCOLS),
    .ACT_BLOCKS(ACT_BLOCKS),
    .ACT_ROWS(ACT_ROWS),
    .TMEM_WORDS(TMEM_WORDS),
    .IMEM_WORDS(IMEM_WORDS),
    .FIFO_DEPTH(FIFO_DEPTH),
    .LANES(LANES),
    .WIN(WIN),
    .MXU_CL(MXU_CL),
    .MXU_E(MXU_E),
    .VPU_CL(VPU_CL),
    .ULANES(ULANES),
    .VPU_COMPACT_COMP(VPU_COMPACT_COMP),
    .VPU_COMPACT_STAGES(VPU_COMPACT_STAGES),
    .VPU_COL_BRAM(VPU_COL_BRAM),
    .SEQ_WC(SEQ_WC),
    .RPB(RPB),
    .WPB(WPB),
    .TMEM_NSB(TMEM_NSB),
    .TMEM_SBP(TMEM_SBP),
    .TMEM_SBH(TMEM_SBH),
    .TMEM_RPL(TMEM_RPL),
    .TMEM_TDP(TMEM_TDP),
    .TMEM_GST(TMEM_GST),
    .TMEM_PMP(TMEM_PMP),
    .TMEM_PRK(TMEM_PRK),
    .TMEM_SDM(TMEM_SDM),
    .CORE_KHZ(CORE_KHZ),
    .ARCH_HASH(ARCH_HASH)
  ) u_accel (
    .clk(clk),
    .aresetn(aresetn),
    .m_axi_awaddr(bus_m_axi_awaddr),
    .m_axi_awlen(bus_m_axi_awlen),
    .m_axi_awsize(bus_m_axi_awsize),
    .m_axi_awburst(bus_m_axi_awburst),
    .m_axi_awid(bus_m_axi_awid),
    .m_axi_awlock(bus_m_axi_awlock),
    .m_axi_awcache(bus_m_axi_awcache),
    .m_axi_awprot(bus_m_axi_awprot),
    .m_axi_awqos(bus_m_axi_awqos),
    .m_axi_awvalid(bus_m_axi_awvalid),
    .m_axi_awready(bus_m_axi_awready),
    .m_axi_wdata(bus_m_axi_wdata),
    .m_axi_wstrb(bus_m_axi_wstrb),
    .m_axi_wlast(bus_m_axi_wlast),
    .m_axi_wvalid(bus_m_axi_wvalid),
    .m_axi_wready(bus_m_axi_wready),
    .m_axi_bid(bus_m_axi_bid),
    .m_axi_bresp(bus_m_axi_bresp),
    .m_axi_bvalid(bus_m_axi_bvalid),
    .m_axi_bready(bus_m_axi_bready),
    .m_axi_araddr(bus_m_axi_araddr),
    .m_axi_arlen(bus_m_axi_arlen),
    .m_axi_arsize(bus_m_axi_arsize),
    .m_axi_arburst(bus_m_axi_arburst),
    .m_axi_arid(bus_m_axi_arid),
    .m_axi_arlock(bus_m_axi_arlock),
    .m_axi_arcache(bus_m_axi_arcache),
    .m_axi_arprot(bus_m_axi_arprot),
    .m_axi_arqos(bus_m_axi_arqos),
    .m_axi_arvalid(bus_m_axi_arvalid),
    .m_axi_arready(bus_m_axi_arready),
    .m_axi_rdata(bus_m_axi_rdata),
    .m_axi_rid(bus_m_axi_rid),
    .m_axi_rresp(bus_m_axi_rresp),
    .m_axi_rlast(bus_m_axi_rlast),
    .m_axi_rvalid(bus_m_axi_rvalid),
    .m_axi_rready(bus_m_axi_rready),
    .s_axi_awaddr(s_axi_awaddr),
    .s_axi_awvalid(s_axi_awvalid),
    .s_axi_awready(s_axi_awready),
    .s_axi_wdata(s_axi_wdata),
    .s_axi_wstrb(s_axi_wstrb),
    .s_axi_wvalid(s_axi_wvalid),
    .s_axi_wready(s_axi_wready),
    .s_axi_bresp(s_axi_bresp),
    .s_axi_bvalid(s_axi_bvalid),
    .s_axi_bready(s_axi_bready),
    .s_axi_araddr(s_axi_araddr),
    .s_axi_arvalid(s_axi_arvalid),
    .s_axi_arready(s_axi_arready),
    .s_axi_rdata(s_axi_rdata),
    .s_axi_rresp(s_axi_rresp),
    .s_axi_rvalid(s_axi_rvalid),
    .s_axi_rready(s_axi_rready)
  );
endmodule
