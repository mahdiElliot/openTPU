# Temporary PL-only bulk loader. DDR contents survive switching back to openTPU.
set root [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
set_param general.maxThreads 4
create_project -force loader [file join $out project] -part xczu3eg-sfvc784-1-i
create_bd_design loader
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.4 ps]
set_property -dict [list CONFIG.PSU__PSS_REF_CLK__FREQMHZ {33.333333} \
  CONFIG.PSU__USE__M_AXI_GP0 {0} CONFIG.PSU__USE__M_AXI_GP1 {0} CONFIG.PSU__USE__M_AXI_GP2 {0} \
  CONFIG.PSU__USE__S_AXI_GP2 {1} CONFIG.PSU__SAXIGP2__DATA_WIDTH {64} \
  CONFIG.PSU__FPGA_PL0_ENABLE {1} CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100}] $ps
set clk [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clocks]
set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} \
  CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {112.500} CONFIG.USE_RESET {false}] $clk
set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 resets]
set invert [create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 invert_reset]
set_property -dict [list CONFIG.C_OPERATION {not} CONFIG.C_SIZE {1}] $invert
set jtag [create_bd_cell -type ip -vlnv xilinx.com:ip:jtag_axi:1.2 loader_axi]
set_property -dict [list CONFIG.M_AXI_DATA_WIDTH {64} CONFIG.M_AXI_ADDR_WIDTH {32} \
  CONFIG.PROTOCOL {0} CONFIG.WR_TXN_QUEUE_LENGTH {16} CONFIG.RD_TXN_QUEUE_LENGTH {16}] $jtag
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins clocks/clk_in1]
connect_bd_net [get_bd_pins clocks/clk_out1] [get_bd_pins resets/slowest_sync_clk] \
  [get_bd_pins ps/saxihp0_fpd_aclk] [get_bd_pins loader_axi/aclk]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins invert_reset/Op1]
connect_bd_net [get_bd_pins invert_reset/Res] [get_bd_pins resets/ext_reset_in]
connect_bd_net [get_bd_pins clocks/locked] [get_bd_pins resets/dcm_locked]
connect_bd_net [get_bd_pins resets/peripheral_aresetn] [get_bd_pins loader_axi/aresetn]
connect_bd_intf_net [get_bd_intf_pins loader_axi/M_AXI] [get_bd_intf_pins ps/S_AXI_HP0_FPD]
assign_bd_address
validate_bd_design
save_bd_design
generate_target all [get_files loader.bd]
make_wrapper -files [get_files loader.bd] -top
add_files [file join $out project/loader.gen/sources_1/bd/loader/hdl/loader_wrapper.v]
set_property top loader_wrapper [current_fileset]
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {error "Loader synthesis failed"}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {error "Loader implementation failed"}
file copy -force [get_property DIRECTORY [get_runs impl_1]]/loader_wrapper.bit [file join $out loader.bit]
puts "ZU3EG_LOADER_BITSTREAM_COMPLETE"
