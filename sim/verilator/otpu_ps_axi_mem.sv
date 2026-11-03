`timescale 1ns/1ps
// Small PS-like AXI slave for bridge tests: queued reads with fixed first-beat
// latency, random channel stalls, and an independent writer with delayed BRESP.
module otpu_ps_axi_mem #(
  parameter int WORDS = 2048, READ_DEPTH = 16, READ_LATENCY = 24, DATA_WIDTH = 128, R_STALL = 1,
  parameter int WRITE_DEPTH = 1,
  parameter logic [39:0] BASE = 0
) (
  input logic clk, rst, wr_idle,
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
  output int read_bursts = 0, max_read_pending = 0, cycles = 0
);
  localparam int BEAT_BYTES = DATA_WIDTH / 8;
  localparam int BEAT_WORDS = DATA_WIDTH / 32;
  localparam int SIZE = $clog2(BEAT_BYTES);
  logic [31:0] memory [WORDS];
  logic [31:0] random_state = 32'h5e7311;
  int read_at [READ_DEPTH], read_last [READ_DEPTH], due [READ_DEPTH];
  int rh = 0, rt = 0, rn = 0, rbeat = 0;
  int write_at = 0, writes_left = 0, b_delay = 0;
  bit writing = 0;
  int write_due [WRITE_DEPTH];
  int wh = 0, wt = 0, wn = 0, max_write_pending = 0;
  wire ar_fire = m_axi_arvalid && m_axi_arready;
  wire r_fire = m_axi_rvalid && m_axi_rready;
  wire r_done = r_fire && m_axi_rlast;
  assign m_axi_awready = !rst && !writing && random_state[0] &&
                        (WRITE_DEPTH > 1 ? wn < WRITE_DEPTH : (!m_axi_bvalid && b_delay == 0));
  assign m_axi_wready = !rst && writing && random_state[2];
  assign m_axi_arready = !rst && rn < READ_DEPTH && random_state[3];

  // An AXI master must not change or withdraw any channel under backpressure.
  bit ar_stalled = 0, aw_stalled = 0, w_stalled = 0;
  logic [47:0] held_ar, held_aw;
  logic [DATA_WIDTH+BEAT_BYTES:0] held_w;
  always @(posedge clk) begin
    if (!rst) begin
      if (ar_stalled && (!m_axi_arvalid || {m_axi_araddr, m_axi_arlen} !== held_ar))
        $fatal(1, "AR changed while stalled");
      if (aw_stalled && (!m_axi_awvalid || {m_axi_awaddr, m_axi_awlen} !== held_aw))
        $fatal(1, "AW changed while stalled");
      if (w_stalled && (!m_axi_wvalid || {m_axi_wdata, m_axi_wstrb, m_axi_wlast} !== held_w))
        $fatal(1, "W changed while stalled");
    end
    ar_stalled <= !rst && m_axi_arvalid && !m_axi_arready;
    aw_stalled <= !rst && m_axi_awvalid && !m_axi_awready;
    w_stalled <= !rst && m_axi_wvalid && !m_axi_wready;
    held_ar <= {m_axi_araddr, m_axi_arlen};
    held_aw <= {m_axi_awaddr, m_axi_awlen};
    held_w <= {m_axi_wdata, m_axi_wstrb, m_axi_wlast};
    cycles <= cycles + 1;
    random_state <= {random_state[30:0], random_state[31] ^ random_state[21] ^ random_state[1] ^ random_state[0]};
    if (rst) begin
      rh <= 0; rt <= 0; rn <= 0; rbeat <= 0;
      writing <= 0; b_delay <= 0;
      m_axi_bvalid <= 0; m_axi_rvalid <= 0;
      m_axi_bresp <= 0; m_axi_rresp <= 0;
      m_axi_bid <= 0; m_axi_rid <= 0; m_axi_rlast <= 0; m_axi_rdata <= '0;
      max_read_pending <= 0;
      wh <= 0; wt <= 0; wn <= 0; max_write_pending <= 0;
    end else begin
      rn <= rn + int'(ar_fire) - int'(r_done);
      if (rn > max_read_pending) max_read_pending <= rn;
      if (ar_fire) begin
        if ((m_axi_araddr & (BEAT_BYTES-1)) != 0 || m_axi_arsize != 3'(SIZE) || m_axi_arburst != 1 || m_axi_arid != 0)
          $fatal(1, "bad AR");
        if (m_axi_araddr[11:0] + BEAT_BYTES * (int'(m_axi_arlen) + 1) > 4096)
          $fatal(1, "4K read crossing");
        if (m_axi_araddr < BASE || (m_axi_araddr - BASE) / 4 + BEAT_WORDS*(int'(m_axi_arlen)+1) > WORDS)
          $fatal(1, "read outside memory");
        read_at[rt] <= int'((m_axi_araddr - BASE) / 4);
        read_last[rt] <= int'(m_axi_arlen);
        due[rt] <= cycles + READ_LATENCY;
        rt <= (rt + 1) % READ_DEPTH;
        read_bursts <= read_bursts + 1;
      end
      if (r_fire) begin
        if (r_done) begin rh <= (rh + 1) % READ_DEPTH; rbeat <= 0; end
        else rbeat <= rbeat + 1;
      end
      if (!m_axi_rvalid || m_axi_rready) begin
        m_axi_rvalid <= 0;
        if (!r_done && rn != 0 && cycles >= due[rh] && !hold_reads && (!R_STALL || random_state[4])) begin
          int beat;
          beat = rbeat + int'(r_fire);
          m_axi_rvalid <= 1;
          m_axi_rresp <= inject_r_error ? 2 : 0;
          m_axi_rlast <= beat == read_last[rh];
          for (int w = 0; w < BEAT_WORDS; w++) m_axi_rdata[w*32 +: 32] <= memory[read_at[rh] + BEAT_WORDS*beat + w];
        end
      end
      if (m_axi_awvalid && m_axi_awready) begin
        if ((m_axi_awaddr & (BEAT_BYTES-1)) != 0 || m_axi_awsize != 3'(SIZE) || m_axi_awburst != 1 || m_axi_awid != 0)
          $fatal(1, "bad AW");
        if (m_axi_awaddr[11:0] + BEAT_BYTES * (int'(m_axi_awlen) + 1) > 4096)
          $fatal(1, "4K write crossing");
        write_at <= int'((m_axi_awaddr - BASE) / 4);
        writes_left <= int'(m_axi_awlen) + 1;
        writing <= 1;
      end
      if (m_axi_wvalid && m_axi_wready) begin
        for (int w = 0; w < BEAT_WORDS; w++)
          for (int b = 0; b < 4; b++)
            if (m_axi_wstrb[w*4+b]) memory[write_at+w][8*b +: 8] <= m_axi_wdata[w*32+8*b +: 8];
        if (m_axi_wlast != (writes_left == 1)) $fatal(1, "bad WLAST");
        write_at <= write_at + BEAT_WORDS;
        writes_left <= writes_left - 1;
          if (writes_left == 1) begin
            writing <= 0;
            if (WRITE_DEPTH == 1) b_delay <= 11;
          end
      end
      if (WRITE_DEPTH > 1) begin
        wn <= wn + int'(m_axi_wvalid && m_axi_wready && m_axi_wlast) -
                   int'(m_axi_bvalid && m_axi_bready);
        if (m_axi_wvalid && m_axi_wready && m_axi_wlast) begin
          write_due[wt] <= cycles+11;
          wt <= (wt+1)%WRITE_DEPTH;
        end
        if (wn > max_write_pending) max_write_pending <= wn;
        if (wn != 0 && wr_idle) $fatal(1, "write idle before queued B responses");
        if (m_axi_bvalid && m_axi_bready) begin
          wh <= (wh+1)%WRITE_DEPTH;
          m_axi_bvalid <= 0;
        end else if (!m_axi_bvalid && wn != 0 && cycles >= write_due[wh] && !hold_b) begin
          m_axi_bvalid <= 1;
          m_axi_bresp <= inject_b_error ? 2 : 0;
        end
      end else begin
        if (b_delay > 0) begin
          if (wr_idle) $fatal(1, "write idle before B response");
          if (b_delay > 1) b_delay <= b_delay - 1;
          else if (!hold_b) begin
            b_delay <= 0;
            m_axi_bvalid <= 1;
            m_axi_bresp <= inject_b_error ? 2 : 0;
          end
        end
        if (m_axi_bvalid && m_axi_bready) m_axi_bvalid <= 0;
      end
    end
  end
endmodule
