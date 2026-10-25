# Software handoff for the board's MIO Ethernet and UART. Do not apply its
# default DDR/PLL initialization: the loader preserves the existing PS boot.
set out [file normalize [lindex $argv 0]]
create_project -force ethernet [file join $out project] -part xczu3eg-sfvc784-1-i
create_bd_design ethernet
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.4 ps]
set_property -dict [list CONFIG.PSU__PSS_REF_CLK__FREQMHZ {33.333333} \
  CONFIG.PSU__USE__M_AXI_GP0 {0} CONFIG.PSU__USE__M_AXI_GP1 {0} CONFIG.PSU__USE__M_AXI_GP2 {0} \
  CONFIG.PSU__ENET3__PERIPHERAL__ENABLE {1} CONFIG.PSU__ENET3__PERIPHERAL__IO {MIO 64 .. 75} \
  CONFIG.PSU__ENET3__GRP_MDIO__ENABLE {1} CONFIG.PSU__ENET3__GRP_MDIO__IO {MIO 76 .. 77} \
  CONFIG.PSU__UART0__PERIPHERAL__ENABLE {1} CONFIG.PSU__UART0__PERIPHERAL__IO {MIO 34 .. 35}] $ps
validate_bd_design
save_bd_design
generate_target all [get_files ethernet.bd]
make_wrapper -files [get_files ethernet.bd] -top
add_files [file join $out project/ethernet.gen/sources_1/bd/ethernet/hdl/ethernet_wrapper.v]
set_property top ethernet_wrapper [current_fileset]
write_hw_platform -fixed -force -file [file join $out ethernet.xsa]
