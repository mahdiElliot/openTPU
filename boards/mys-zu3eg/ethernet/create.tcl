set workspace [file normalize [lindex $argv 0]]
set xsa [file normalize [lindex $argv 1]]
setws $workspace
if {[file exists [file join $workspace myir_runtime platform.spr]]} {
  platform active myir_runtime
} else {
  platform create -name myir_runtime -hw $xsa -proc psu_cortexa53_0 -os standalone
}
domain active standalone_domain
bsp setlib -name lwip211 -ver 1.8
bsp config lwip_dhcp false
platform generate
if {![file exists [file join $workspace monitor monitor.prj]]} {
  app create -name monitor -platform myir_runtime -domain standalone_domain -template {lwIP Echo Server}
}
puts "MYIR_ETHERNET_PLATFORM_CREATED"
