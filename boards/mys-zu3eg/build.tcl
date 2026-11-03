# First deployment uses the board's already initialized PS DDR. This project
# programs PL only; do NOT run its default DDR/PLL initialization on the board.
set root [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
source [file join $out sources.tcl]
source [file join $out build_params.tcl]
if {![info exists separate_mmcm]} {set separate_mmcm 0}
if {![info exists implementation_strategy]} {set implementation_strategy default}
if {![info exists hp_ports]} {set hp_ports 1}
set_param general.maxThreads 4
create_project -force opentpu_zu3eg [file join $out project] -part xczu3eg-sfvc784-1-i
set_property verilog_define {SYNTHESIS} [current_fileset]
foreach f $rtl_sources {add_files [file join $root rtl $f]}
update_compile_order -fileset sources_1
create_bd_design system
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.4 ps]
set_property -dict [list \
  CONFIG.PSU__PSS_REF_CLK__FREQMHZ {33.333333} \
  CONFIG.PSU__USE__M_AXI_GP0 {0} CONFIG.PSU__USE__M_AXI_GP1 {0} \
  CONFIG.PSU__USE__M_AXI_GP2 {1} CONFIG.PSU__MAXIGP2__DATA_WIDTH {32} \
  CONFIG.PSU__USE__S_AXI_GP2 {1} CONFIG.PSU__SAXIGP2__DATA_WIDTH {128} \
  CONFIG.PSU__FPGA_PL0_ENABLE {1} \
  CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100}] $ps
if {$hp_ports >= 2} {
  set_property -dict [list CONFIG.PSU__USE__S_AXI_GP3 {1} CONFIG.PSU__SAXIGP3__DATA_WIDTH {128}] $ps
}
# HP2 and HP3 stay disabled in every supported configuration.
set_property CONFIG.PSU__USE__S_AXI_GP4 {0} $ps
set_property CONFIG.PSU__USE__S_AXI_GP5 {0} $ps
set clk [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 clocks]
set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} \
  CONFIG.PRIM_SOURCE {No_buffer} \
  CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $core_mhz \
  CONFIG.USE_RESET {false}] $clk
set all_locked [get_bd_pins clocks/locked]
if {$mem_mhz != $core_mhz && !$separate_mmcm} {
  set_property -dict [list CONFIG.CLKOUT2_USED {true} CONFIG.CLKOUT2_REQUESTED_OUT_FREQ $mem_mhz] $clk
}
if {$mem_mhz != $core_mhz && $separate_mmcm} {
  set mem_clk [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:6.0 memory_clocks]
  set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ $mem_mhz CONFIG.USE_RESET {false}] $mem_clk
  connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins memory_clocks/clk_in1]
  set locks [create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 clock_locks]
  set_property -dict [list CONFIG.C_OPERATION {and} CONFIG.C_SIZE {1}] $locks
  connect_bd_net [get_bd_pins clocks/locked] [get_bd_pins clock_locks/Op1]
  connect_bd_net [get_bd_pins memory_clocks/locked] [get_bd_pins clock_locks/Op2]
  set all_locked [get_bd_pins clock_locks/Res]
}
set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 resets]
set invert [create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 invert_reset]
set_property -dict [list CONFIG.C_OPERATION {not} CONFIG.C_SIZE {1}] $invert
set core [create_bd_cell -type module -reference otpu_zu3eg_wrapper accelerator]
set_property -dict $accelerator_parameters $core
# Module-reference AXI capabilities come from the wrapper's HDL attributes.
# BD considers those properties read-only; check_build verifies the generated BD.
set control [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 control]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $control
connect_bd_net [get_bd_pins ps/pl_clk0] [get_bd_pins clocks/clk_in1]
connect_bd_net [get_bd_pins clocks/clk_out1] \
  [get_bd_pins resets/slowest_sync_clk] [get_bd_pins accelerator/clk] \
  [get_bd_pins ps/maxihpm0_lpd_aclk] \
  [get_bd_pins control/ACLK] [get_bd_pins control/S00_ACLK] [get_bd_pins control/M00_ACLK]
connect_bd_net [get_bd_pins ps/pl_resetn0] [get_bd_pins invert_reset/Op1]
connect_bd_net [get_bd_pins invert_reset/Res] [get_bd_pins resets/ext_reset_in]
connect_bd_net $all_locked [get_bd_pins resets/dcm_locked]
connect_bd_net [get_bd_pins resets/peripheral_aresetn] [get_bd_pins accelerator/aresetn] \
  [get_bd_pins control/ARESETN] [get_bd_pins control/S00_ARESETN] [get_bd_pins control/M00_ARESETN]
connect_bd_intf_net [get_bd_intf_pins ps/M_AXI_HPM0_LPD] [get_bd_intf_pins control/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins control/M00_AXI] [get_bd_intf_pins accelerator/S_AXI]
# Keep the wide stream across the crossing. Downsizing at the core clock
# would leave a 128-bit/core-clock bandwidth bottleneck in front of HP0.
set hp_clock [get_bd_pins clocks/clk_out1]
set hp_reset [get_bd_pins resets/peripheral_aresetn]
if {$mem_mhz != $core_mhz} {
  set mem_reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 memory_resets]
  set hp_clock [get_bd_pins [expr {$separate_mmcm ? "memory_clocks/clk_out1" : "clocks/clk_out2"}]]
  set hp_reset [get_bd_pins memory_resets/peripheral_aresetn]
  connect_bd_net $hp_clock [get_bd_pins memory_resets/slowest_sync_clk]
  connect_bd_net [get_bd_pins invert_reset/Res] [get_bd_pins memory_resets/ext_reset_in]
  connect_bd_net $all_locked [get_bd_pins memory_resets/dcm_locked]
}
foreach hp [lrange {0 1} 0 [expr {$hp_ports-1}]] bus [lrange {M_AXI M_META_AXI} 0 [expr {$hp_ports-1}]] {
  set suffix [expr {$hp == 0 ? "" : "_hp$hp"}]
  set memory_axi [get_bd_intf_pins accelerator/$bus]
  if {$mem_mhz != $core_mhz} {
    set name memory_clock_converter$suffix
    set crossing [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_clock_converter:2.1 $name]
    set_property -dict [list CONFIG.ACLK_ASYNC {1} CONFIG.SYNCHRONIZATION_STAGES {3} \
      CONFIG.DATA_WIDTH $axi_data_width] $crossing
    connect_bd_net [get_bd_pins clocks/clk_out1] [get_bd_pins $name/s_axi_aclk]
    connect_bd_net $hp_clock [get_bd_pins $name/m_axi_aclk]
    connect_bd_net [get_bd_pins resets/peripheral_aresetn] [get_bd_pins $name/s_axi_aresetn]
    connect_bd_net $hp_reset [get_bd_pins $name/m_axi_aresetn]
    connect_bd_intf_net $memory_axi [get_bd_intf_pins $name/S_AXI]
    set memory_axi [get_bd_intf_pins $name/M_AXI]
  }
  connect_bd_net $hp_clock [get_bd_pins ps/saxihp${hp}_fpd_aclk]
  if {$axi_data_width != 128} {
    set name memory_width_converter$suffix
    set downsize [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_dwidth_converter:2.1 $name]
    set_property -dict [list CONFIG.SI_DATA_WIDTH $axi_data_width CONFIG.MI_DATA_WIDTH {128} \
      CONFIG.ADDR_WIDTH {40} CONFIG.SI_ID_WIDTH {6} CONFIG.FIFO_MODE {0}] $downsize
    connect_bd_net $hp_clock [get_bd_pins $name/s_axi_aclk]
    connect_bd_net $hp_reset [get_bd_pins $name/s_axi_aresetn]
    connect_bd_intf_net $memory_axi [get_bd_intf_pins $name/S_AXI]
    set memory_axi [get_bd_intf_pins $name/M_AXI]
  }
  connect_bd_intf_net $memory_axi [get_bd_intf_pins ps/S_AXI_HP${hp}_FPD]
}
puts "MEMORY_PATH bridge_bits=$axi_data_width crossing_bits=$axi_data_width hp_bits=128 downsizer_mhz=$mem_mhz"
assign_bd_address
set ctlseg [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps/Data] -filter {NAME =~ *accelerator*}]
if {[llength $ctlseg] != 1} {error "Cannot identify accelerator control segment: $ctlseg"}
set_property offset 0x80000000 $ctlseg
set_property range 4K $ctlseg
validate_bd_design
save_bd_design
generate_target all [get_files system.bd]
make_wrapper -files [get_files system.bd] -top
add_files [file join $out project/opentpu_zu3eg.gen/sources_1/bd/system/hdl/system_wrapper.v]
set_property top system_wrapper [current_fileset]
update_compile_order -fileset sources_1
# The PS clock is already buffered. Keep its primary clock and preserve the
# previous input-jitter budget instead of redefining clocks at MMCM inputs.
set clock_constraints [file join $out board_clock.xdc]
set cf [open $clock_constraints w]
puts $cf {set_input_jitter [get_clocks clk_pl_0] 0.100}
close $cf
add_files -fileset constrs_1 $clock_constraints
set_property PROCESSING_ORDER LATE [get_files $clock_constraints]
write_bd_tcl -force [file join $out system_bd.tcl]
launch_runs synth_1 -jobs 2
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {error "Synthesis failed"}
if {$implementation_strategy ne "default"} {set_property strategy $implementation_strategy [get_runs impl_1]}
launch_runs impl_1 -to_step write_bitstream -jobs 2
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {error "Implementation failed"}
open_run impl_1
report_timing_summary -delay_type min_max -report_unconstrained -file [file join $out timing.rpt]
report_utilization -file [file join $out utilization.rpt]
report_drc -file [file join $out drc.rpt]
report_cdc -file [file join $out cdc.rpt]
report_methodology -file [file join $out methodology.rpt]
report_property -file [file join $out clock_root.rpt] [get_clocks clk_pl_0]
set clock_report [open [file join $out clocks.csv] w]
puts $clock_report "name,period_ns,mhz"
foreach clock [get_clocks] {
  set period [get_property PERIOD $clock]
  puts $clock_report "$clock,$period,[expr {1000.0 / $period}]"
}
close $clock_report
set margins [open [file join $out timing_status.tcl] w]
puts $margins "set setup_slack_ns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]"
puts $margins "set hold_slack_ns [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]"
close $margins
write_hw_platform -fixed -include_bit -force -file [file join $out opentpu_zu3eg.xsa]
file copy -force [get_property DIRECTORY [get_runs impl_1]]/system_wrapper.bit [file join $out opentpu_zu3eg.bit]
puts "ZU3EG_BOARD_BITSTREAM_COMPLETE"
