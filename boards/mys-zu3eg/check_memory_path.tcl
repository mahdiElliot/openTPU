# Generate scripts for the converter-only functional simulation. No PS or board access.
set root [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
set width [lindex $argv 2]
create_project -force memory_path [file join $out project] -part xczu3eg-sfvc784-1-i
create_ip -name axi_clock_converter -vendor xilinx.com -library ip -version 2.1 -module_name otpu_test_clock_converter
set_property -dict [list CONFIG.ADDR_WIDTH 40 CONFIG.DATA_WIDTH $width CONFIG.ID_WIDTH 6 \
  CONFIG.ACLK_ASYNC 1 CONFIG.SYNCHRONIZATION_STAGES 3] [get_ips otpu_test_clock_converter]
if {$width == 256} {
create_ip -name axi_dwidth_converter -vendor xilinx.com -library ip -version 2.1 -module_name otpu_test_width_converter
set_property -dict [list CONFIG.ADDR_WIDTH 40 CONFIG.SI_DATA_WIDTH 256 CONFIG.MI_DATA_WIDTH 128 \
  CONFIG.SI_ID_WIDTH 6 CONFIG.FIFO_MODE 0] [get_ips otpu_test_width_converter]
}
generate_target simulation [get_ips]
add_files -fileset sim_1 [list \
  [file join $root rtl/boards/mys-zu3eg/otpu_ps_dram.sv] \
  [file join $root sim/verilator/otpu_ps_axi_mem.sv] \
  [file join $root sim/verilator/otpu_ps_test_mem.sv] \
  [file join $root sim/verilator/tb_ps_dram.sv]]
set_property top tb_ps_dram [get_filesets sim_1]
set_property generic "D=128 AXI_DATA_WIDTH=$width VENDOR_PATH=1" [get_filesets sim_1]
update_compile_order -fileset sim_1
launch_simulation -scripts_only
puts VENDOR_MEMORY_PATH_SCRIPTS_READY
