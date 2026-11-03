// Core memory ports to a wide AXI interface; the board downsizes at the HP clock. Buffered A/B/SW inputs,
// up to READ_DEPTH AXI reads outstanding, and an independent write engine.
// AXI reads use one ID and return in issue order; B's tag follows its response.
// A and B preserve read/write order within their own queue. Across ports, the
// slice orders dependencies with wr_idle, as with otpu_native_dram. A write waits
// for its port's older reads; younger reads stay fenced through BRESP. With
// multiple write credits payload storage retires at WLAST while response tags
// remain queued. Other ports' independent reads may run beside writes.
// Addresses at the core are WORDS.
module otpu_ps_dram #(
  parameter int D = 128,
  parameter int AXI_DATA_WIDTH = 256,
  parameter int READ_DEPTH = 8,
  parameter int READ_CHUNKS = 1,
  parameter int A_CACHE_LINES = 1, // fetch 1/2/4/8 adjacent 32-byte scale lines
  parameter int WRITE_DEPTH = 1, // ordered BRESP credits; one active W payload
  parameter int A_DEPTH = 4,
  parameter int B_DEPTH = 8,
  parameter int SW_DEPTH = 4,
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
  output logic [AXI_DATA_WIDTH-1:0] m_axi_wdata,
  output logic [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb,
  output logic m_axi_wlast, m_axi_wvalid,
  input logic m_axi_wready,
  input logic [5:0] m_axi_bid, m_axi_rid,
  input logic [1:0] m_axi_bresp, m_axi_rresp,
  input logic m_axi_bvalid, m_axi_rvalid, m_axi_rlast,
  output logic m_axi_bready, m_axi_rready,
  input logic [AXI_DATA_WIDTH-1:0] m_axi_rdata
);
  localparam int AXI_BYTES = AXI_DATA_WIDTH / 8;
  localparam int AXI_SIZE = $clog2(AXI_BYTES);
  localparam int WORD_LG = AXI_SIZE - 2;
  localparam int GP = (READ_CHUNKS > 1) ? $clog2(READ_CHUNKS) : 1;
  localparam int GC = $clog2(READ_CHUNKS + 1);
  localparam int BF = $clog2(READ_DEPTH * READ_CHUNKS + 1);
  localparam int AC_LG = 3 + $clog2(A_CACHE_LINES);
  localparam int AIW = A_CACHE_LINES > 1 ? $clog2(A_CACHE_LINES) : 1;
  localparam bit WPIPE = WRITE_DEPTH > 1;
  localparam int WP = WPIPE ? $clog2(WRITE_DEPTH) : 1;
  localparam int WC = $clog2(WRITE_DEPTH+1);
  localparam int AWC = $clog2(A_DEPTH+WRITE_DEPTH+1);
  localparam int BWC = $clog2(B_DEPTH+WRITE_DEPTH+1);
  initial begin
    if (!(AXI_DATA_WIDTH inside {128, 256}) || D*8 < AXI_DATA_WIDTH)
      $fatal(1, "PS bridge requires AXI_DATA_WIDTH=128/256 and D >= one AXI beat");
    if (!(D inside {32, 64, 128})) $fatal(1, "ZU3EG PS bridge requires D=32,64,128");
    if (!(A_CACHE_LINES inside {1, 2, 4, 8}) || 32*A_CACHE_LINES/AXI_BYTES > 8)
      $fatal(1, "Scale cache requires 1/2/4/8 lines and at most eight AXI beats");
    if (WRITE_DEPTH < 1 || WRITE_DEPTH > 16 || (WRITE_DEPTH & (WRITE_DEPTH-1)) != 0)
      $fatal(1, "Write credits must be a power of two from 1 through 16");
    if (!(READ_CHUNKS inside {1, 2, 4, 8}) || READ_CHUNKS * D / AXI_BYTES > 256 ||
        (READ_CHUNKS > 1 && BASE[11:0] != 0))
      $fatal(1, "Read grouping requires 1/2/4/8 chunks and a 4-KiB-aligned BASE");
    if (READ_DEPTH < 1 || (READ_DEPTH & (READ_DEPTH-1)) != 0 ||
        A_DEPTH < 2 || (A_DEPTH & (A_DEPTH-1)) != 0 ||
        B_DEPTH < 2 || (B_DEPTH & (B_DEPTH-1)) != 0 ||
        SW_DEPTH < 2 || (SW_DEPTH & (SW_DEPTH-1)) != 0)
      $fatal(1, "PS bridge queue depths must be powers of two (input queues >=2)");
  end
  localparam int AP = $clog2(A_DEPTH), BP = $clog2(B_DEPTH), SP = $clog2(SW_DEPTH);
  localparam int RP = (READ_DEPTH > 1) ? $clog2(READ_DEPTH) : 1;
  localparam logic [1:0] W_SW = 0, W_A = 1, W_B = 2;

  typedef struct packed {
    logic we;
    logic [31:0] addr, data;
    logic [3:0] be;
  } aq_t;
  typedef struct packed {
    logic we, tag;
    logic [31:0] addr;
  } bq_t;
  typedef struct packed {
    logic [31:0] addr, data;
    logic [3:0] be;
  } swq_t;
  typedef struct packed {
    logic port_b;
    logic [READ_CHUNKS-1:0] tags;
    logic [GP-1:0] chunks_last;
    logic [2:0] last;
  } rt_t;
  // Payload arrays have no reset: only valid counts/pointers reset. In particular,
  // a queued B store must not turn into resettable flip-flops for every data bit.
  aq_t aq [A_DEPTH];
  bq_t bq [B_DEPTH];
  logic [D*8-1:0] bq_data [B_DEPTH];
  logic [D/4-1:0] bq_mask [B_DEPTH];
  swq_t sq [SW_DEPTH];
  rt_t rq [READ_DEPTH];
  logic [AP-1:0] ah, at;
  logic [BP-1:0] bh, bt;
  logic [SP-1:0] sh, st;
  logic [AP:0] an;
  logic [BP:0] bn;
  logic [SP:0] sn;
  wire a_push = a_req && a_rdy, b_push = b_req && b_rdy, sw_push = sw_req && sw_rdy;
  logic a_pop, b_pop, sw_pop;
  assign a_rdy = !rst && an < (AP+1)'(A_DEPTH);
  assign b_rdy = !rst && bn < (BP+1)'(B_DEPTH);
  assign sw_rdy = !rst && sn < (SP+1)'(SW_DEPTH);
  wire write_taken = sw_push || (a_push && a_we) || (b_push && b_we);

  // One scale-line miss in flight: repeated A reads can hit the returned line,
  // rather than sending duplicate AXI requests before it fills. This does not
  // prevent B from filling all READ_DEPTH read credits in the meantime.
  logic a_flight, a_cacheable;
  logic [31-AC_LG:0] a_line, cache_line;
  logic [AIW-1:0] a_select;
  logic cache_valid;
  logic [256*A_CACHE_LINES-1:0] cache_data;
  logic [BF-1:0] b_flight;
  logic [AP:0] a_pending;
  logic [BP+RP+1:0] b_pending;
  logic [WC-1:0] a_wflight, b_wflight;
  wire a_head_read = an != 0 && !aq[ah].we && (!WPIPE || a_wflight == 0);
  wire b_head_read = bn != 0 && !bq[bh].we && (!WPIPE || b_wflight == 0);
  wire a_hit = a_head_read && !a_flight && !(ar_valid && !ar_port_b) &&
               cache_valid && !a_flush && !write_taken && wr_idle &&
               cache_line == aq[ah].addr[31:AC_LG];

  // A registered AR remains unchanged under backpressure. A single ID lets the
  // PS keep many reads in flight without a data reorder buffer. Credits reserve
  // a metadata entry before ARVALID; the consumer has reserved its response room.
  logic ar_valid, ar_port_b, prefer_b;
  logic [READ_CHUNKS-1:0] ar_tags;
  logic [GP-1:0] ar_chunks_last, rchunk;
  logic [7:0] ar_burst_last;
  logic [39:0] ar_addr;
  logic [2:0] ar_last;
  logic [RP-1:0] rh, rt;
  logic [RP:0] rn;
  logic [2:0] rbeat;
  logic [D*8-1:0] rpayload, rcomplete;
  // Include the current beat in the assembled response before its register write.
  always_comb begin
    rcomplete = rpayload;
    if (rq[rh].port_b)
      rcomplete[AXI_DATA_WIDTH*rbeat +: AXI_DATA_WIDTH] = m_axi_rdata;
  end
  wire ar_fire = m_axi_arvalid && m_axi_arready;
  wire r_fire = m_axi_rvalid && m_axi_rready;
  wire chunk_done = r_fire && (rbeat == rq[rh].last || m_axi_rlast);
  wire r_done = r_fire && (m_axi_rlast ||
                (rbeat == rq[rh].last && rchunk == rq[rh].chunks_last));
  wire a_done = r_done && !rq[rh].port_b;
  wire b_done = chunk_done && rq[rh].port_b;
  assign m_axi_arvalid = ar_valid && !rst;
  assign m_axi_araddr = ar_addr;
  assign m_axi_arlen = ar_burst_last;
  assign m_axi_rready = !rst && rn != 0;
  assign rd_idle = a_pending == 0 && b_pending == 0 && !a_rvalid && !b_rvalid;

  // Collect only accepted, consecutive B reads. No speculative data is read.
  // A discontinuity, store, 4-KiB boundary, or bounded timeout ends the group.
  // Per-chunk tags remain in issue order and each core response is still D bytes.
  logic [GC-1:0] gather_count;
  logic [31:0] gather_word, gather_next;
  logic [READ_CHUNKS-1:0] gather_tags;
  logic [3:0] gather_age;
  wire gather_match = b_head_read && bq[bh].addr == gather_next &&
                       bq[bh].addr[31:10] == gather_word[31:10];
  wire gather_ready = gather_count != 0 &&
    (gather_count == GC'(READ_CHUNKS) || gather_age == 4'(2*READ_CHUNKS-1) ||
     (bn != 0 && !gather_match));
  wire gather_take = READ_CHUNKS > 1 && !ar_valid && rn < (RP+1)'(READ_DEPTH) &&
    !gather_ready && b_head_read && gather_count < GC'(READ_CHUNKS) &&
    (gather_count == 0 || gather_match);
  wire b_issue_ready = READ_CHUNKS == 1 ? b_head_read : gather_ready;

  // Only one AXI write burst needs an active payload register. Input queues
  // absorb more stores while AW/W/B stall; reads continue on their own engine.
  typedef enum logic [1:0] {W_IDLE, W_ADDR, W_DATA, W_RESP} wstate_t;
  wstate_t ws;
  logic [1:0] wport, wnext, pick_w;
  logic have_w;
  logic [39:0] waddr;
  logic [D*8-1:0] wpayload;
  logic [D-1:0] wmask;
  logic [2:0] wbeat, wlast;
  logic [AWC-1:0] a_writes;
  logic [BWC-1:0] b_writes;
  logic [1:0] wports [WRITE_DEPTH];
  logic [WP-1:0] wh, wt;
  logic [WC-1:0] wn;
  wire w_address = m_axi_awvalid && m_axi_awready;
  wire w_last_fire = ws == W_DATA && m_axi_wready && wbeat == wlast;
  wire w_done = m_axi_bvalid && m_axi_bready;
  wire [1:0] response_port = WPIPE ? wports[wh] : wport;
  wire w_retire = WPIPE ? w_last_fire : w_done;
  wire [2:0] w_candidates = {bn != 0 && bq[bh].we && b_flight == 0 &&
                             gather_count == 0 && !(ar_valid && ar_port_b),
                             an != 0 && aq[ah].we && !a_flight, sn != 0};
  always_comb begin
    have_w = 1'b0;
    pick_w = W_SW;
    for (int i = 0; i < 3; i++) begin
      int p;
      p = int'(wnext) + i;
      if (p >= 3) p = p - 3;
      if (!have_w && w_candidates[p]) begin have_w = 1'b1; pick_w = 2'(p); end
    end
  end
  assign wr_idle = a_writes == 0 && b_writes == 0 && sn == 0 && ws == W_IDLE && (!WPIPE || wn == 0);
  assign m_axi_awvalid = !rst && ws == W_ADDR;
  assign m_axi_awaddr = waddr;
  assign m_axi_awlen = {5'd0, wlast};
  assign m_axi_wvalid = !rst && ws == W_DATA;
  assign m_axi_wdata = wpayload[AXI_DATA_WIDTH*wbeat +: AXI_DATA_WIDTH];
  assign m_axi_wstrb = wmask[AXI_BYTES*wbeat +: AXI_BYTES];
  assign m_axi_wlast = wbeat == wlast;
  assign m_axi_bready = !rst && (WPIPE ? wn != 0 : ws == W_RESP);
  assign m_axi_awsize = 3'(AXI_SIZE);
  assign m_axi_arsize = 3'(AXI_SIZE);
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

  always_comb begin
    a_pop = a_hit || (ar_fire && !ar_port_b) || (w_retire && wport == W_A);
    b_pop = (READ_CHUNKS == 1 ? (ar_fire && ar_port_b) : gather_take) ||
            (w_retire && wport == W_B);
    sw_pop = w_retire && wport == W_SW;
  end
  always_ff @(posedge clk) begin
    if (a_push) aq[at] <= '{we:a_we, addr:a_addr, data:a_wdata, be:a_be};
    if (b_push) bq[bt] <= '{we:b_we, tag:b_tag, addr:b_addr};
    if (b_push && b_we) begin bq_data[bt] <= b_wdata; bq_mask[bt] <= b_wmask; end
    if (sw_push) sq[st] <= '{addr:sw_addr, data:sw_wdata, be:sw_be};
    if (ar_fire) rq[rt] <= '{port_b:ar_port_b, tags:ar_tags,
                             chunks_last:ar_chunks_last, last:ar_last};
    if (r_fire && rq[rh].port_b)
      rpayload[AXI_DATA_WIDTH*rbeat +: AXI_DATA_WIDTH] <= m_axi_rdata;
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      ah <= '0; at <= '0; an <= '0;
      bh <= '0; bt <= '0; bn <= '0;
      sh <= '0; st <= '0; sn <= '0;
      rh <= '0; rt <= '0; rn <= '0;
      ar_valid <= 1'b0; ar_port_b <= 1'b0; ar_tags <= '0;
      ar_chunks_last <= '0; ar_burst_last <= '0; rchunk <= '0;
      gather_count <= '0; gather_word <= '0; gather_next <= '0;
      gather_tags <= '0; gather_age <= '0;
      ar_addr <= '0; ar_last <= '0; prefer_b <= 1'b0;
      rbeat <= '0;
      a_pending <= '0; b_pending <= '0; b_flight <= '0;
      a_writes <= '0; b_writes <= '0;
      a_flight <= 1'b0; a_cacheable <= 1'b0;
      a_line <= '0; a_select <= '0; cache_valid <= 1'b0; cache_line <= '0;
      a_rvalid <= 1'b0; b_rvalid <= 1'b0; b_rtag <= 1'b0;
      ws <= W_IDLE; wport <= W_SW; wnext <= W_SW;
      wh <= '0; wt <= '0; wn <= '0; a_wflight <= '0; b_wflight <= '0;
      waddr <= '0; wbeat <= '0; wlast <= '0;
      error <= 1'b0;
    end else begin
      a_rvalid <= 1'b0;
      b_rvalid <= 1'b0;
      if (a_push) at <= at + 1'b1;
      if (a_pop) ah <= ah + 1'b1;
      if (b_push) bt <= bt + 1'b1;
      if (b_pop) bh <= bh + 1'b1;
      if (sw_push) st <= st + 1'b1;
      if (sw_pop) sh <= sh + 1'b1;
      an <= an + (AP+1)'(a_push) - (AP+1)'(a_pop);
      bn <= bn + (BP+1)'(b_push) - (BP+1)'(b_pop);
      sn <= sn + (SP+1)'(sw_push) - (SP+1)'(sw_pop);
      a_pending <= a_pending + (AP+1)'(a_push && !a_we) - (AP+1)'(a_done || a_hit);
      b_pending <= b_pending + (BP+RP+2)'(b_push && !b_we) - (BP+RP+2)'(b_done);
      a_writes <= a_writes + AWC'(a_push && a_we) - AWC'(w_done && response_port == W_A);
      b_writes <= b_writes + BWC'(b_push && b_we) - BWC'(w_done && response_port == W_B);
      if (WPIPE) begin
        // AW/W stay ordered on one ID. Retire payload storage after WLAST,
        // retaining each port's fence until its ordered BRESP has arrived.
        wn <= wn + WC'(w_address) - WC'(w_done);
        a_wflight <= a_wflight + WC'(w_address && wport == W_A) - WC'(w_done && response_port == W_A);
        b_wflight <= b_wflight + WC'(w_address && wport == W_B) - WC'(w_done && response_port == W_B);
        if (w_address) begin wports[wt] <= wport; wt <= wt + 1'b1; end
        if (w_done) wh <= wh + 1'b1;
      end
      if (w_done && (m_axi_bresp != 0 || m_axi_bid != 0)) error <= 1'b1;
      if (w_retire) wnext <= (wport == W_B) ? W_SW : wport + 1'b1;
      b_flight <= b_flight + (ar_fire && ar_port_b ? BF'(ar_chunks_last) + BF'(1) : BF'(0)) - BF'(b_done);
      rn <= rn + (RP+1)'(ar_fire) - (RP+1)'(r_done);
      if (ar_fire) rt <= (READ_DEPTH == 1) ? '0 : rt + 1'b1;
      if (r_done) rh <= (READ_DEPTH == 1) ? '0 : rh + 1'b1;

      if (gather_count != 0 && gather_age < 4'(2*READ_CHUNKS-1)) gather_age <= gather_age + 1'b1;
      if (gather_take) begin
        if (gather_count == 0) begin gather_word <= bq[bh].addr; gather_age <= '0; end
        gather_next <= bq[bh].addr + 32'(D/4);
        gather_tags[gather_count] <= bq[bh].tag;
        gather_count <= gather_count + 1'b1;
      end
      if (!ar_valid && rn < (RP+1)'(READ_DEPTH)) begin
        // Round-robin A/B misses. A hits return locally and do not use an AXI slot.
        if (b_issue_ready && (prefer_b || !a_head_read || a_flight || a_hit)) begin
          ar_valid <= 1'b1; ar_port_b <= 1'b1;
          ar_last <= 3'(D/AXI_BYTES-1);
          if (READ_CHUNKS == 1) begin
            ar_addr <= BASE + {6'd0, bq[bh].addr, 2'd0};
            ar_tags <= READ_CHUNKS'(bq[bh].tag); ar_chunks_last <= '0;
            ar_burst_last <= 8'(D/AXI_BYTES-1);
          end else begin
            ar_addr <= BASE + {6'd0, gather_word, 2'd0};
            ar_tags <= gather_tags; ar_chunks_last <= GP'(gather_count-1'b1);
            ar_burst_last <= 8'(int'(gather_count)*(D/AXI_BYTES)-1);
            gather_count <= '0; gather_age <= '0;
          end
        end else if (a_head_read && !a_flight && !a_hit) begin
          ar_valid <= 1'b1; ar_port_b <= 1'b0;
          // Align the wider fill to its whole region. It cannot
          // cross a 4-KiB boundary, including when the requested half is odd.
          ar_addr <= BASE + {6'd0, aq[ah].addr[31:AC_LG], {(AC_LG+2){1'b0}}};
          ar_last <= 3'(32*A_CACHE_LINES/AXI_BYTES-1); ar_tags <= '0; ar_chunks_last <= '0;
          ar_burst_last <= 8'(32*A_CACHE_LINES/AXI_BYTES-1);
        end
      end
      if (ar_fire) begin
        ar_valid <= 1'b0;
        prefer_b <= !ar_port_b;
        if (!ar_port_b) begin
          a_flight <= 1'b1;
          a_line <= aq[ah].addr[31:AC_LG];
          a_select <= A_CACHE_LINES == 1 ? '0 : aq[ah].addr[3 +: AIW];
          cache_valid <= 1'b0;
          a_cacheable <= !a_flush && wr_idle && !write_taken;
        end
      end
      if (a_hit) begin
        a_rvalid <= 1'b1;
        a_rdata <= cache_data[256*(A_CACHE_LINES == 1 ? 0 : int'(aq[ah].addr[3 +: AIW])) +: 256];
      end
      if (r_fire) begin
        // Capture A's selected line directly from the AXI beats. A wider
        // cache fill does not widen B's chunk buffer or add an assembly mux
        // to the scale response. Valid remains low until the fill completes.
        if (!rq[rh].port_b) begin
          cache_data[AXI_DATA_WIDTH*rbeat +: AXI_DATA_WIDTH] <= m_axi_rdata;
          if (int'(rbeat)/(32/AXI_BYTES) == int'(a_select))
            a_rdata[AXI_DATA_WIDTH*(int'(rbeat)%(32/AXI_BYTES)) +: AXI_DATA_WIDTH] <= m_axi_rdata;
        end
        if (m_axi_rresp != 0 || m_axi_rid != 0 ||
            m_axi_rlast != (rbeat == rq[rh].last && rchunk == rq[rh].chunks_last))
          error <= 1'b1;
        if (chunk_done) begin
          rbeat <= '0;
          rchunk <= r_done ? '0 : rchunk + 1'b1;
          if (rq[rh].port_b) begin
            b_rvalid <= 1'b1;
            b_rtag <= rq[rh].tags[rchunk];
            b_rdata <= rcomplete;
          end else begin
            a_flight <= 1'b0;
            a_rvalid <= 1'b1;
            if (a_cacheable && wr_idle && !write_taken && !a_flush) begin
              cache_valid <= 1'b1;
              cache_line <= a_line;
            end
          end
        end else rbeat <= rbeat + 1'b1;
      end
      // A flush must also poison an older A miss, so its late response cannot
      // repopulate a stale line after a host write or an accelerator store.
      if (a_flush || write_taken) begin cache_valid <= 1'b0; a_cacheable <= 1'b0; end

      case (ws)
        W_IDLE: if (have_w && (!WPIPE || wn < WC'(WRITE_DEPTH))) begin
          wport <= pick_w;
          wbeat <= '0;
          wlast <= '0;
          if (pick_w == W_B) begin
            waddr <= BASE + {6'd0, bq[bh].addr, 2'd0};
            wpayload <= bq_data[bh];
            for (int i = 0; i < D/4; i++) wmask[4*i +: 4] <= {4{bq_mask[bh][i]}};
            wlast <= 3'(D/AXI_BYTES-1);
          end else if (pick_w == W_A) begin
            waddr <= BASE + {6'd0, aq[ah].addr[31:WORD_LG], {AXI_SIZE{1'b0}}};
            wpayload <= (D*8)'(aq[ah].data) << (32 * aq[ah].addr[WORD_LG-1:0]);
            wmask <= D'(aq[ah].be) << (4 * aq[ah].addr[WORD_LG-1:0]);
          end else begin
            waddr <= BASE + {6'd0, sq[sh].addr[31:WORD_LG], {AXI_SIZE{1'b0}}};
            wpayload <= (D*8)'(sq[sh].data) << (32 * sq[sh].addr[WORD_LG-1:0]);
            wmask <= D'(sq[sh].be) << (4 * sq[sh].addr[WORD_LG-1:0]);
          end
          ws <= W_ADDR;
        end
        W_ADDR: if (m_axi_awready) ws <= W_DATA;
        W_DATA: if (m_axi_wready) begin
          if (wbeat == wlast) ws <= WPIPE ? W_IDLE : W_RESP;
          else wbeat <= wbeat + 1'b1;
        end
        W_RESP: if (m_axi_bvalid) begin
          ws <= W_IDLE;
        end
        default: ws <= W_IDLE;
      endcase
    end
  end
endmodule
