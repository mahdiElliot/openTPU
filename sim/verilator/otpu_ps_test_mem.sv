`timescale 1ns/1ps
// Test-only memory path. VENDOR_PATH instantiates the actual Xilinx clock
// converter and fast-domain downsizer, with independent core and memory clocks.
module otpu_ps_test_mem #(
  parameter int WORDS = 2048, READ_DEPTH = 16, READ_LATENCY = 24, DATA_WIDTH = 256, VENDOR_PATH = 0,
  parameter logic [39:0] BASE = 0
) (
  input logic clk, mem_clk, rst, wr_idle,
  input logic hold_reads = 0, hold_b = 0, inject_b_error = 0, inject_r_error = 0,
  input logic [39:0] m_axi_awaddr, m_axi_araddr,
  input logic [7:0] m_axi_awlen, m_axi_arlen,
  input logic [2:0] m_axi_awsize, m_axi_arsize,
  input logic [1:0] m_axi_awburst, m_axi_arburst,
  input logic [5:0] m_axi_awid, m_axi_arid,
  input logic m_axi_awvalid, m_axi_arvalid,
  output logic m_axi_awready, m_axi_arready,
  input logic [DATA_WIDTH-1:0] m_axi_wdata,
  input logic [DATA_WIDTH/8-1:0] m_axi_wstrb,
  input logic m_axi_wlast, m_axi_wvalid,
  output logic m_axi_wready,
  output logic [5:0] m_axi_bid, m_axi_rid,
  output logic [1:0] m_axi_bresp, m_axi_rresp,
  output logic m_axi_bvalid, m_axi_rvalid, m_axi_rlast,
  input logic m_axi_bready, m_axi_rready,
  output logic [DATA_WIDTH-1:0] m_axi_rdata,
  output int read_bursts, max_read_pending, cycles
);
  localparam int HP_WIDTH = VENDOR_PATH ? 128 : DATA_WIDTH;
  wire [6-1:0] wide_awid;
  wire [40-1:0] wide_awaddr;
  wire [8-1:0] wide_awlen;
  wire [3-1:0] wide_awsize;
  wire [2-1:0] wide_awburst;
  wire wide_awlock;
  wire [4-1:0] wide_awcache;
  wire [3-1:0] wide_awprot;
  wire [4-1:0] wide_awregion;
  wire [4-1:0] wide_awqos;
  wire wide_awvalid;
  wire wide_awready;
  wire [DATA_WIDTH-1:0] wide_wdata;
  wire [DATA_WIDTH/8-1:0] wide_wstrb;
  wire wide_wlast;
  wire wide_wvalid;
  wire wide_wready;
  wire [6-1:0] wide_bid;
  wire [2-1:0] wide_bresp;
  wire wide_bvalid;
  wire wide_bready;
  wire [6-1:0] wide_arid;
  wire [40-1:0] wide_araddr;
  wire [8-1:0] wide_arlen;
  wire [3-1:0] wide_arsize;
  wire [2-1:0] wide_arburst;
  wire wide_arlock;
  wire [4-1:0] wide_arcache;
  wire [3-1:0] wide_arprot;
  wire [4-1:0] wide_arregion;
  wire [4-1:0] wide_arqos;
  wire wide_arvalid;
  wire wide_arready;
  wire [6-1:0] wide_rid;
  wire [DATA_WIDTH-1:0] wide_rdata;
  wire [2-1:0] wide_rresp;
  wire wide_rlast;
  wire wide_rvalid;
  wire wide_rready;
  wire [6-1:0] hp_awid;
  wire [40-1:0] hp_awaddr;
  wire [8-1:0] hp_awlen;
  wire [3-1:0] hp_awsize;
  wire [2-1:0] hp_awburst;
  wire hp_awlock;
  wire [4-1:0] hp_awcache;
  wire [3-1:0] hp_awprot;
  wire [4-1:0] hp_awregion;
  wire [4-1:0] hp_awqos;
  wire hp_awvalid;
  wire hp_awready;
  wire [HP_WIDTH-1:0] hp_wdata;
  wire [HP_WIDTH/8-1:0] hp_wstrb;
  wire hp_wlast;
  wire hp_wvalid;
  wire hp_wready;
  wire [6-1:0] hp_bid;
  wire [2-1:0] hp_bresp;
  wire hp_bvalid;
  wire hp_bready;
  wire [6-1:0] hp_arid;
  wire [40-1:0] hp_araddr;
  wire [8-1:0] hp_arlen;
  wire [3-1:0] hp_arsize;
  wire [2-1:0] hp_arburst;
  wire hp_arlock;
  wire [4-1:0] hp_arcache;
  wire [3-1:0] hp_arprot;
  wire [4-1:0] hp_arregion;
  wire [4-1:0] hp_arqos;
  wire hp_arvalid;
  wire hp_arready;
  wire [6-1:0] hp_rid;
  wire [HP_WIDTH-1:0] hp_rdata;
  wire [2-1:0] hp_rresp;
  wire hp_rlast;
  wire hp_rvalid;
  wire hp_rready;
  logic [2:0] core_reset = '0, hp_reset = '0;
  always @(posedge clk or posedge rst)
    if (rst) core_reset <= '0; else core_reset <= {core_reset[1:0], 1'b1};
  always @(posedge mem_clk or posedge rst)
    if (rst) hp_reset <= '0; else hp_reset <= {hp_reset[1:0], 1'b1};
  if (VENDOR_PATH) begin : g_vendor
    assign hp_awid = 6'd0, hp_arid = 6'd0;
    otpu_test_clock_converter crossing (
      .s_axi_aclk(clk), .s_axi_aresetn(core_reset[2]),
      .m_axi_aclk(mem_clk), .m_axi_aresetn(hp_reset[2]),
      .s_axi_awid(m_axi_awid),
      .s_axi_awaddr(m_axi_awaddr),
      .s_axi_awlen(m_axi_awlen),
      .s_axi_awsize(m_axi_awsize),
      .s_axi_awburst(m_axi_awburst),
      .s_axi_awlock(1'b0),
      .s_axi_awcache(4'b0011),
      .s_axi_awprot(3'b0),
      .s_axi_awregion(4'b0),
      .s_axi_awqos(4'b0),
      .s_axi_awvalid(m_axi_awvalid),
      .s_axi_awready(m_axi_awready),
      .s_axi_wdata(m_axi_wdata),
      .s_axi_wstrb(m_axi_wstrb),
      .s_axi_wlast(m_axi_wlast),
      .s_axi_wvalid(m_axi_wvalid),
      .s_axi_wready(m_axi_wready),
      .s_axi_bid(m_axi_bid),
      .s_axi_bresp(m_axi_bresp),
      .s_axi_bvalid(m_axi_bvalid),
      .s_axi_bready(m_axi_bready),
      .s_axi_arid(m_axi_arid),
      .s_axi_araddr(m_axi_araddr),
      .s_axi_arlen(m_axi_arlen),
      .s_axi_arsize(m_axi_arsize),
      .s_axi_arburst(m_axi_arburst),
      .s_axi_arlock(1'b0),
      .s_axi_arcache(4'b0011),
      .s_axi_arprot(3'b0),
      .s_axi_arregion(4'b0),
      .s_axi_arqos(4'b0),
      .s_axi_arvalid(m_axi_arvalid),
      .s_axi_arready(m_axi_arready),
      .s_axi_rid(m_axi_rid),
      .s_axi_rdata(m_axi_rdata),
      .s_axi_rresp(m_axi_rresp),
      .s_axi_rlast(m_axi_rlast),
      .s_axi_rvalid(m_axi_rvalid),
      .s_axi_rready(m_axi_rready),
      .m_axi_awid(wide_awid),
      .m_axi_awaddr(wide_awaddr),
      .m_axi_awlen(wide_awlen),
      .m_axi_awsize(wide_awsize),
      .m_axi_awburst(wide_awburst),
      .m_axi_awlock(wide_awlock),
      .m_axi_awcache(wide_awcache),
      .m_axi_awprot(wide_awprot),
      .m_axi_awregion(wide_awregion),
      .m_axi_awqos(wide_awqos),
      .m_axi_awvalid(wide_awvalid),
      .m_axi_awready(wide_awready),
      .m_axi_wdata(wide_wdata),
      .m_axi_wstrb(wide_wstrb),
      .m_axi_wlast(wide_wlast),
      .m_axi_wvalid(wide_wvalid),
      .m_axi_wready(wide_wready),
      .m_axi_bid(wide_bid),
      .m_axi_bresp(wide_bresp),
      .m_axi_bvalid(wide_bvalid),
      .m_axi_bready(wide_bready),
      .m_axi_arid(wide_arid),
      .m_axi_araddr(wide_araddr),
      .m_axi_arlen(wide_arlen),
      .m_axi_arsize(wide_arsize),
      .m_axi_arburst(wide_arburst),
      .m_axi_arlock(wide_arlock),
      .m_axi_arcache(wide_arcache),
      .m_axi_arprot(wide_arprot),
      .m_axi_arregion(wide_arregion),
      .m_axi_arqos(wide_arqos),
      .m_axi_arvalid(wide_arvalid),
      .m_axi_arready(wide_arready),
      .m_axi_rid(wide_rid),
      .m_axi_rdata(wide_rdata),
      .m_axi_rresp(wide_rresp),
      .m_axi_rlast(wide_rlast),
      .m_axi_rvalid(wide_rvalid),
      .m_axi_rready(wide_rready)
    );
    if (DATA_WIDTH == 256) begin : g_wide
    otpu_test_width_converter downsizer (
      .s_axi_aclk(mem_clk), .s_axi_aresetn(hp_reset[2]),
      .s_axi_awid(wide_awid),
      .s_axi_awaddr(wide_awaddr),
      .s_axi_awlen(wide_awlen),
      .s_axi_awsize(wide_awsize),
      .s_axi_awburst(wide_awburst),
      .s_axi_awlock(wide_awlock),
      .s_axi_awcache(wide_awcache),
      .s_axi_awprot(wide_awprot),
      .s_axi_awregion(wide_awregion),
      .s_axi_awqos(wide_awqos),
      .s_axi_awvalid(wide_awvalid),
      .s_axi_awready(wide_awready),
      .s_axi_wdata(wide_wdata),
      .s_axi_wstrb(wide_wstrb),
      .s_axi_wlast(wide_wlast),
      .s_axi_wvalid(wide_wvalid),
      .s_axi_wready(wide_wready),
      .s_axi_bid(wide_bid),
      .s_axi_bresp(wide_bresp),
      .s_axi_bvalid(wide_bvalid),
      .s_axi_bready(wide_bready),
      .s_axi_arid(wide_arid),
      .s_axi_araddr(wide_araddr),
      .s_axi_arlen(wide_arlen),
      .s_axi_arsize(wide_arsize),
      .s_axi_arburst(wide_arburst),
      .s_axi_arlock(wide_arlock),
      .s_axi_arcache(wide_arcache),
      .s_axi_arprot(wide_arprot),
      .s_axi_arregion(wide_arregion),
      .s_axi_arqos(wide_arqos),
      .s_axi_arvalid(wide_arvalid),
      .s_axi_arready(wide_arready),
      .s_axi_rid(wide_rid),
      .s_axi_rdata(wide_rdata),
      .s_axi_rresp(wide_rresp),
      .s_axi_rlast(wide_rlast),
      .s_axi_rvalid(wide_rvalid),
      .s_axi_rready(wide_rready),
      .m_axi_awaddr(hp_awaddr),
      .m_axi_awlen(hp_awlen),
      .m_axi_awsize(hp_awsize),
      .m_axi_awburst(hp_awburst),
      .m_axi_awlock(hp_awlock),
      .m_axi_awcache(hp_awcache),
      .m_axi_awprot(hp_awprot),
      .m_axi_awregion(hp_awregion),
      .m_axi_awqos(hp_awqos),
      .m_axi_awvalid(hp_awvalid),
      .m_axi_awready(hp_awready),
      .m_axi_wdata(hp_wdata),
      .m_axi_wstrb(hp_wstrb),
      .m_axi_wlast(hp_wlast),
      .m_axi_wvalid(hp_wvalid),
      .m_axi_wready(hp_wready),
      .m_axi_bresp(hp_bresp),
      .m_axi_bvalid(hp_bvalid),
      .m_axi_bready(hp_bready),
      .m_axi_araddr(hp_araddr),
      .m_axi_arlen(hp_arlen),
      .m_axi_arsize(hp_arsize),
      .m_axi_arburst(hp_arburst),
      .m_axi_arlock(hp_arlock),
      .m_axi_arcache(hp_arcache),
      .m_axi_arprot(hp_arprot),
      .m_axi_arregion(hp_arregion),
      .m_axi_arqos(hp_arqos),
      .m_axi_arvalid(hp_arvalid),
      .m_axi_arready(hp_arready),
      .m_axi_rdata(hp_rdata),
      .m_axi_rresp(hp_rresp),
      .m_axi_rlast(hp_rlast),
      .m_axi_rvalid(hp_rvalid),
      .m_axi_rready(hp_rready)
    );
    end else begin : g_narrow
      assign hp_awaddr = wide_awaddr;
      assign hp_awlen = wide_awlen;
      assign hp_awsize = wide_awsize;
      assign hp_awburst = wide_awburst;
      assign hp_awlock = wide_awlock;
      assign hp_awcache = wide_awcache;
      assign hp_awprot = wide_awprot;
      assign hp_awregion = wide_awregion;
      assign hp_awqos = wide_awqos;
      assign hp_awvalid = wide_awvalid;
      assign wide_awready = hp_awready;
      assign hp_wdata = wide_wdata;
      assign hp_wstrb = wide_wstrb;
      assign hp_wlast = wide_wlast;
      assign hp_wvalid = wide_wvalid;
      assign wide_wready = hp_wready;
      assign wide_bid = hp_bid;
      assign wide_bresp = hp_bresp;
      assign wide_bvalid = hp_bvalid;
      assign hp_bready = wide_bready;
      assign hp_araddr = wide_araddr;
      assign hp_arlen = wide_arlen;
      assign hp_arsize = wide_arsize;
      assign hp_arburst = wide_arburst;
      assign hp_arlock = wide_arlock;
      assign hp_arcache = wide_arcache;
      assign hp_arprot = wide_arprot;
      assign hp_arregion = wide_arregion;
      assign hp_arqos = wide_arqos;
      assign hp_arvalid = wide_arvalid;
      assign wide_arready = hp_arready;
      assign wide_rid = hp_rid;
      assign wide_rdata = hp_rdata;
      assign wide_rresp = hp_rresp;
      assign wide_rlast = hp_rlast;
      assign wide_rvalid = hp_rvalid;
      assign hp_rready = wide_rready;
    end
  end else begin : g_direct
    assign hp_awid = m_axi_awid;
    assign hp_awaddr = m_axi_awaddr;
    assign hp_awlen = m_axi_awlen;
    assign hp_awsize = m_axi_awsize;
    assign hp_awburst = m_axi_awburst;
    assign hp_awlock = 1'b0;
    assign hp_awcache = 4'b0011;
    assign hp_awprot = 3'b0;
    assign hp_awregion = 4'b0;
    assign hp_awqos = 4'b0;
    assign hp_awvalid = m_axi_awvalid;
    assign m_axi_awready = hp_awready;
    assign hp_wdata = m_axi_wdata;
    assign hp_wstrb = m_axi_wstrb;
    assign hp_wlast = m_axi_wlast;
    assign hp_wvalid = m_axi_wvalid;
    assign m_axi_wready = hp_wready;
    assign m_axi_bid = hp_bid;
    assign m_axi_bresp = hp_bresp;
    assign m_axi_bvalid = hp_bvalid;
    assign hp_bready = m_axi_bready;
    assign hp_arid = m_axi_arid;
    assign hp_araddr = m_axi_araddr;
    assign hp_arlen = m_axi_arlen;
    assign hp_arsize = m_axi_arsize;
    assign hp_arburst = m_axi_arburst;
    assign hp_arlock = 1'b0;
    assign hp_arcache = 4'b0011;
    assign hp_arprot = 3'b0;
    assign hp_arregion = 4'b0;
    assign hp_arqos = 4'b0;
    assign hp_arvalid = m_axi_arvalid;
    assign m_axi_arready = hp_arready;
    assign m_axi_rid = hp_rid;
    assign m_axi_rdata = hp_rdata;
    assign m_axi_rresp = hp_rresp;
    assign m_axi_rlast = hp_rlast;
    assign m_axi_rvalid = hp_rvalid;
    assign hp_rready = m_axi_rready;
  end
  otpu_ps_axi_mem #(.WORDS(WORDS), .READ_DEPTH(READ_DEPTH),
                     .READ_LATENCY(READ_LATENCY), .DATA_WIDTH(HP_WIDTH), .R_STALL(!VENDOR_PATH), .BASE(BASE)) slave (
    .clk(VENDOR_PATH ? mem_clk : clk), .rst(VENDOR_PATH ? !hp_reset[2] : rst), .wr_idle,
    .hold_reads, .hold_b, .inject_b_error, .inject_r_error,
    .read_bursts, .max_read_pending, .cycles,
    .m_axi_awid(hp_awid),
    .m_axi_awaddr(hp_awaddr),
    .m_axi_awlen(hp_awlen),
    .m_axi_awsize(hp_awsize),
    .m_axi_awburst(hp_awburst),
    .m_axi_awvalid(hp_awvalid),
    .m_axi_awready(hp_awready),
    .m_axi_wdata(hp_wdata),
    .m_axi_wstrb(hp_wstrb),
    .m_axi_wlast(hp_wlast),
    .m_axi_wvalid(hp_wvalid),
    .m_axi_wready(hp_wready),
    .m_axi_bid(hp_bid),
    .m_axi_bresp(hp_bresp),
    .m_axi_bvalid(hp_bvalid),
    .m_axi_bready(hp_bready),
    .m_axi_arid(hp_arid),
    .m_axi_araddr(hp_araddr),
    .m_axi_arlen(hp_arlen),
    .m_axi_arsize(hp_arsize),
    .m_axi_arburst(hp_arburst),
    .m_axi_arvalid(hp_arvalid),
    .m_axi_arready(hp_arready),
    .m_axi_rid(hp_rid),
    .m_axi_rdata(hp_rdata),
    .m_axi_rresp(hp_rresp),
    .m_axi_rlast(hp_rlast),
    .m_axi_rvalid(hp_rvalid),
    .m_axi_rready(hp_rready)
  );
endmodule
