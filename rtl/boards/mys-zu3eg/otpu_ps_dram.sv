// Core memory ports to one Zynq PS 128-bit AXI HP port. One outstanding burst;
// write responses drain before subsequent reads. Addresses at the core are WORDS.
module otpu_ps_dram #(
  parameter int D = 128,
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
  output logic [39:0] m_axi_awaddr, m_axi_araddr,
  output logic [7:0] m_axi_awlen, m_axi_arlen,
  output logic [2:0] m_axi_awsize, m_axi_arsize,
  output logic [1:0] m_axi_awburst, m_axi_arburst,
  output logic [5:0] m_axi_awid, m_axi_arid,
  output logic m_axi_awlock, m_axi_arlock,
  output logic [3:0] m_axi_awcache, m_axi_arcache,
  output logic [2:0] m_axi_awprot, m_axi_arprot,
  output logic [3:0] m_axi_awqos, m_axi_arqos,
  output logic m_axi_awvalid, m_axi_arvalid,
  input logic m_axi_awready, m_axi_arready,
  output logic [127:0] m_axi_wdata,
  output logic [15:0] m_axi_wstrb,
  output logic m_axi_wlast, m_axi_wvalid,
  input logic m_axi_wready,
  input logic [5:0] m_axi_bid, m_axi_rid,
  input logic [1:0] m_axi_bresp, m_axi_rresp,
  input logic m_axi_bvalid, m_axi_rvalid, m_axi_rlast,
  output logic m_axi_bready, m_axi_rready,
  input logic [127:0] m_axi_rdata
);
  initial if (!(D inside {32, 64, 128})) $fatal(1, "ZU3EG PS bridge requires D=32,64,128");
  typedef enum logic [2:0] {IDLE, RA, RD, REPLY, WA, WD, WB} state_t;
  state_t state;
  logic [39:0] address;
  logic [2:0] beat, last;
  logic port_b, tag;
  logic [D*8-1:0] payload;
  logic [D-1:0] mask;
  logic cache_valid;
  logic [28:0] cache_line;
  logic [255:0] cache_data;

  assign sw_rdy = state == IDLE;
  assign a_rdy = state == IDLE && !sw_req;
  assign b_rdy = state == IDLE && !sw_req && !a_req;
  assign wr_idle = !(state inside {WA, WD, WB});
  assign rd_idle = !(state inside {RA, RD, REPLY});
  assign a_rvalid = state == REPLY && !port_b;
  assign b_rvalid = state == REPLY && port_b;
  assign a_rdata = payload[255:0];
  assign b_rdata = payload;
  assign b_rtag = tag;
  assign m_axi_awaddr = address;
  assign m_axi_araddr = address;
  assign m_axi_awlen = {5'd0, last};
  assign m_axi_arlen = {5'd0, last};
  assign m_axi_awsize = 3'd4;
  assign m_axi_arsize = 3'd4;
  assign m_axi_awburst = 2'b01;
  assign m_axi_arburst = 2'b01;
  assign m_axi_awid = '0;
  assign m_axi_arid = '0;
  assign m_axi_awlock = 1'b0;
  assign m_axi_arlock = 1'b0;
  assign m_axi_awcache = 4'b0011;
  assign m_axi_arcache = 4'b0011;
  assign m_axi_awprot = '0;
  assign m_axi_arprot = '0;
  assign m_axi_awqos = '0;
  assign m_axi_arqos = '0;
  assign m_axi_awvalid = state == WA;
  assign m_axi_arvalid = state == RA;
  assign m_axi_wvalid = state == WD;
  assign m_axi_wdata = payload[128*beat +: 128];
  assign m_axi_wstrb = mask[16*beat +: 16];
  assign m_axi_wlast = beat == last;
  assign m_axi_bready = state == WB;
  assign m_axi_rready = state == RD;

  always_ff @(posedge clk) begin
    if (rst) begin
      state <= IDLE;
      address <= '0;
      beat <= '0;
      last <= '0;
      port_b <= 1'b0;
      tag <= 1'b0;
      payload <= '0;
      mask <= '0;
      error <= 1'b0;
      cache_valid <= 1'b0;
      cache_line <= '0;
      cache_data <= '0;
    end else begin
      case (state)
        IDLE: begin
          beat <= '0;
          if (sw_req || (a_req && a_we)) begin
            address <= BASE + {6'd0, (sw_req ? sw_addr[31:2] : a_addr[31:2]), 4'd0};
            payload <= (D*8)'(sw_req ? sw_wdata : a_wdata) <<
                       (32 * (sw_req ? sw_addr[1:0] : a_addr[1:0]));
            mask <= D'(sw_req ? sw_be : a_be) <<
                    (4 * (sw_req ? sw_addr[1:0] : a_addr[1:0]));
            last <= '0;
            cache_valid <= 1'b0;
            state <= WA;
          end else if (a_req) begin
            port_b <= 1'b0;
            cache_line <= a_addr[31:3];
            if (cache_valid && !a_flush && cache_line == a_addr[31:3]) begin
              payload <= (D*8)'(cache_data);
              state <= REPLY;
            end else begin
              address <= BASE + {6'd0, a_addr[31:3], 5'd0};
              last <= 3'd1;
              state <= RA;
            end
          end else if (b_req) begin
            port_b <= 1'b1;
            tag <= b_tag;
            address <= BASE + {6'd0, b_addr, 2'd0};
            last <= 3'(D / 16 - 1);
            if (b_we) begin
              payload <= b_wdata;
              for (int w = 0; w < D/4; w++) mask[4*w +: 4] <= {4{b_wmask[w]}};
              cache_valid <= 1'b0;
              state <= WA;
            end else state <= RA;
          end
        end
        RA: if (m_axi_arready) state <= RD;
        RD: if (m_axi_rvalid) begin
          payload[128*beat +: 128] <= m_axi_rdata;
          if (m_axi_rresp != 0 || m_axi_rid != 0 || m_axi_rlast != (beat == last)) error <= 1'b1;
          if (beat == last || m_axi_rlast) state <= REPLY;
          else beat <= beat + 1'b1;
        end
        REPLY: begin
          if (!port_b && !a_flush) begin
            cache_valid <= 1'b1;
            cache_data <= payload[255:0];
          end
          state <= IDLE;
        end
        WA: if (m_axi_awready) state <= WD;
        WD: if (m_axi_wready) begin
          if (beat == last) state <= WB;
          else beat <= beat + 1'b1;
        end
        WB: if (m_axi_bvalid) begin
          if (m_axi_bresp != 0 || m_axi_bid != 0) error <= 1'b1;
          state <= IDLE;
        end
        default: state <= IDLE;
      endcase
      if (a_flush) cache_valid <= 1'b0;
    end
  end
endmodule
