`timescale 1ns/1ps
// Address issuance must progress independently under asymmetric port stalls.
module tb_mxu_issue;
  import otpu_pkg::*;
  localparam int D=64, MCOLS=2, LANES=4, DEPTH=16;
  logic clk=0, rst=1, start=0, go=0;
  always #5 clk=!clk;
  cmd_t cmd='0;
  logic rdy,done,computing,pf_starve,pf_block,pf_u;
  logic [$clog2(DEPTH):0] pf_level;
  logic [3:0][31:0] pf_uv;
  logic [15:0] act_blk,act_blk2;
  logic [MCOLS-1:0] act_hi,act_ren;
  logic [7:0] act_grp;
  logic [MCOLS*D*8-1:0] act_data='0;
  logic [MCOLS*64-1:0] act_scale='0;
  logic a_req,a_gnt=0,a_rvalid=0,b_req,b_gnt=0,b_rvalid=0;
  logic [31:0] a_addr,b_addr;
  logic [255:0] a_rdata='0;
  logic [D*8-1:0] b_rdata='0;
  logic [LANES-1:0] t_ren,t_wen,t_rben;
  logic [LANES-1:0][31:0] t_raddr,t_rdata='0,t_waddr,t_wdata,t_rbad;
  logic t_gnt=1,t_lost=0;
  otpu_mxu #(.D(D),.MCOLS(MCOLS),.ROWS(2),.DEPTH(DEPTH),.LANES(LANES),.E(1)) dut(.*);
  int na=0,nb=0;
  always @(posedge clk) if (!rst) begin
    if(a_req)na++;
    if(b_req)begin
      if(b_addr!==32'h4000+32'(nb*D/4))$fatal(1,"weight address duplicated or reordered");
      nb++;
    end
  end
  task automatic begin_case(input bit allow_a,input bit allow_b);
    @(negedge clk);rst=1;start=0;
    repeat(4)@(negedge clk);
    na=0;nb=0;a_gnt=allow_a;b_gnt=allow_b;rst=0;
    @(negedge clk);
    // Eight rows, eight K blocks, INT4 PAIR: 32 data chunks. Consumer stays
    // unreleased; each stream must stop after reserving DEPTH response slots.
    cmd='0;cmd.op=OP_MM;cmd.flags=8'h50;
    cmd.w1=32'h10000;cmd.w2=32'h40000;
    cmd.w3=0;cmd.w4=32'h00080008;cmd.w5=256;
    cmd.w6=32'h00010008;cmd.w7=40;
    start=1;@(negedge clk);start=0;
    repeat(80)@(negedge clk);
  endtask
  initial begin
    begin_case(0,1);
    if(nb!=DEPTH||na!=0)$fatal(1,"A stall blocked B: A=%0d B=%0d",na,nb);
    if(dut.occ!=DEPTH||rdy)$fatal(1,"weight reservations did not stop at capacity");
    begin_case(1,0);
    if(na<DEPTH||nb!=0)$fatal(1,"B stall blocked A: A=%0d B=%0d",na,nb);
    if(dut.occ!=DEPTH||dut.bq_n!=DEPTH)$fatal(1,"queued weights lost their reservations");
    $display("MXU_INDEPENDENT_ISSUE_PASS");$finish;
  end
  initial begin #100000;$fatal(1,"timeout");end
endmodule
