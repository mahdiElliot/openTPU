// Independent PS memory paths: lane 0 -> HP0 (bulk), lane 1 -> HP1
// (A scales/SW). HP2 and HP3 are disabled.
// The original bridge retains per-path read/write fences. Global completion
// and cache invalidation cover every path, including late AXI BRESPs.
module otpu_ps_dram_multi #(
  parameter int D = 64, AXI_DATA_WIDTH = 256, HP_PORTS = 2,
                READ_DEPTH = 16, READ_CHUNKS = 8, A_CACHE_LINES = 8,
                WRITE_DEPTH = 4, A_DEPTH = 4, B_DEPTH = 16, SW_DEPTH = 4,
  parameter logic [39:0] BASE = 40'h0010000000
) (
  input logic clk, rst, a_flush,
  input logic a_req, a_we,
  input logic [31:0] a_addr, a_wdata,
  input logic [3:0] a_be,
  output logic a_rdy, a_rvalid,
  output logic [255:0] a_rdata,
  input logic sw_req,
  input logic [31:0] sw_addr, sw_wdata,
  input logic [3:0] sw_be,
  output logic sw_rdy,
  input logic b_req, b_we, b_tag,
  input logic [31:0] b_addr,
  input logic [D*8-1:0] b_wdata,
  input logic [D/4-1:0] b_wmask,
  output logic b_rdy, b_rvalid, b_rtag,
  output logic [D*8-1:0] b_rdata,
  output logic wr_idle, rd_idle, error,
  output logic [HP_PORTS-1:0][39:0] m_axi_awaddr, m_axi_araddr,
  output logic [HP_PORTS-1:0][7:0] m_axi_awlen, m_axi_arlen,
  output logic [HP_PORTS-1:0][2:0] m_axi_awsize, m_axi_arsize,
  output logic [HP_PORTS-1:0][1:0] m_axi_awburst, m_axi_arburst,
  output logic [HP_PORTS-1:0][5:0] m_axi_awid, m_axi_arid,
  output logic [HP_PORTS-1:0] m_axi_awlock, m_axi_arlock,
  output logic [HP_PORTS-1:0][3:0] m_axi_awcache, m_axi_arcache,
  output logic [HP_PORTS-1:0][2:0] m_axi_awprot, m_axi_arprot,
  output logic [HP_PORTS-1:0][3:0] m_axi_awqos, m_axi_arqos,
  output logic [HP_PORTS-1:0] m_axi_awvalid, m_axi_arvalid,
  input logic [HP_PORTS-1:0] m_axi_awready, m_axi_arready,
  output logic [HP_PORTS-1:0][AXI_DATA_WIDTH-1:0] m_axi_wdata,
  output logic [HP_PORTS-1:0][AXI_DATA_WIDTH/8-1:0] m_axi_wstrb,
  output logic [HP_PORTS-1:0] m_axi_wlast, m_axi_wvalid,
  input logic [HP_PORTS-1:0] m_axi_wready,
  input logic [HP_PORTS-1:0][5:0] m_axi_bid, m_axi_rid,
  input logic [HP_PORTS-1:0][1:0] m_axi_bresp, m_axi_rresp,
  input logic [HP_PORTS-1:0] m_axi_bvalid, m_axi_rvalid, m_axi_rlast,
  output logic [HP_PORTS-1:0] m_axi_bready, m_axi_rready,
  input logic [HP_PORTS-1:0][AXI_DATA_WIDTH-1:0] m_axi_rdata
);

  initial if (!(HP_PORTS inside {1, 2}))
    $fatal(1, "Use legacy HP0 or independent HP0/HP1; HP2 and HP3 are reserved");
  logic [HP_PORTS-1:0] lane_a_rdy, lane_a_valid, lane_sw_rdy;
  logic [HP_PORTS-1:0][255:0] lane_a_data;
  logic [HP_PORTS-1:0] lane_b_req, lane_b_rdy, lane_b_valid, lane_b_tag;
  logic [HP_PORTS-1:0][D*8-1:0] lane_b_data;
  logic [HP_PORTS-1:0] lane_wr_idle, lane_rd_idle, lane_error;
  localparam int META = HP_PORTS == 1 ? 0 : 1;
  wire bulk_wr_idle = lane_wr_idle[0];
  wire bulk_store = b_req && b_rdy && b_we;
  assign a_rdy = lane_a_rdy[META];
  assign a_rvalid = lane_a_valid[META];
  assign a_rdata = lane_a_data[META];
  assign sw_rdy = lane_sw_rdy[META];
  assign wr_idle = &lane_wr_idle;
  assign error = |lane_error;

  if (1) begin : g_direct
    assign lane_b_req[0] = b_req;
    if (HP_PORTS == 2) assign lane_b_req[1] = 1'b0;
    assign b_rdy = lane_b_rdy[0];
    assign b_rvalid = lane_b_valid[0];
    assign b_rtag = lane_b_tag[0];
    assign b_rdata = lane_b_data[0];
    assign rd_idle = &lane_rd_idle;
  end

  for (genvar p = 0; p < HP_PORTS; p++) begin : g_path
    localparam bit IS_META = p == META;
    localparam bit IS_BULK = p == 0;
    otpu_ps_dram #(.D(D), .AXI_DATA_WIDTH(AXI_DATA_WIDTH), .BASE(BASE),
      .READ_DEPTH(IS_BULK ? READ_DEPTH : 2),
      .READ_CHUNKS(IS_BULK ? READ_CHUNKS : 1),
      .A_CACHE_LINES(IS_META ? A_CACHE_LINES : 1),
      .WRITE_DEPTH(WRITE_DEPTH), .A_DEPTH(A_DEPTH), .B_DEPTH(B_DEPTH),
      .SW_DEPTH(SW_DEPTH)) u_bridge (
      .clk, .rst,
      .a_flush(a_flush || (HP_PORTS > 1 && (!bulk_wr_idle || bulk_store))),
      .a_req(IS_META && a_req), .a_we, .a_addr, .a_wdata, .a_be,
      .a_rdy(lane_a_rdy[p]), .a_rvalid(lane_a_valid[p]), .a_rdata(lane_a_data[p]),
      .sw_req(IS_META && sw_req), .sw_addr, .sw_wdata, .sw_be, .sw_rdy(lane_sw_rdy[p]),
      .b_req(lane_b_req[p]), .b_we, .b_tag, .b_addr, .b_wdata, .b_wmask,
      .b_rdy(lane_b_rdy[p]), .b_rvalid(lane_b_valid[p]),
      .b_rtag(lane_b_tag[p]), .b_rdata(lane_b_data[p]),
      .wr_idle(lane_wr_idle[p]), .rd_idle(lane_rd_idle[p]), .error(lane_error[p]),
      .m_axi_awaddr(m_axi_awaddr[p]),
      .m_axi_araddr(m_axi_araddr[p]),
      .m_axi_awlen(m_axi_awlen[p]),
      .m_axi_arlen(m_axi_arlen[p]),
      .m_axi_awsize(m_axi_awsize[p]),
      .m_axi_arsize(m_axi_arsize[p]),
      .m_axi_awburst(m_axi_awburst[p]),
      .m_axi_arburst(m_axi_arburst[p]),
      .m_axi_awid(m_axi_awid[p]),
      .m_axi_arid(m_axi_arid[p]),
      .m_axi_awlock(m_axi_awlock[p]),
      .m_axi_arlock(m_axi_arlock[p]),
      .m_axi_awcache(m_axi_awcache[p]),
      .m_axi_arcache(m_axi_arcache[p]),
      .m_axi_awprot(m_axi_awprot[p]),
      .m_axi_arprot(m_axi_arprot[p]),
      .m_axi_awqos(m_axi_awqos[p]),
      .m_axi_arqos(m_axi_arqos[p]),
      .m_axi_awvalid(m_axi_awvalid[p]),
      .m_axi_arvalid(m_axi_arvalid[p]),
      .m_axi_awready(m_axi_awready[p]),
      .m_axi_arready(m_axi_arready[p]),
      .m_axi_wdata(m_axi_wdata[p]),
      .m_axi_wstrb(m_axi_wstrb[p]),
      .m_axi_wlast(m_axi_wlast[p]),
      .m_axi_wvalid(m_axi_wvalid[p]),
      .m_axi_wready(m_axi_wready[p]),
      .m_axi_bid(m_axi_bid[p]),
      .m_axi_rid(m_axi_rid[p]),
      .m_axi_bresp(m_axi_bresp[p]),
      .m_axi_rresp(m_axi_rresp[p]),
      .m_axi_bvalid(m_axi_bvalid[p]),
      .m_axi_rvalid(m_axi_rvalid[p]),
      .m_axi_rlast(m_axi_rlast[p]),
      .m_axi_bready(m_axi_bready[p]),
      .m_axi_rready(m_axi_rready[p]),
      .m_axi_rdata(m_axi_rdata[p])
    );
  end
endmodule
