# Run from build/zu3eg/vivado with ROOT and the generated params.tcl in the environment.
set root $::env(OTPU_ROOT)
source $::env(OTPU_SYNTH_PARAMS)
set_param general.maxThreads 4
set part $target_part
if {[llength [get_parts -quiet $part]] == 0} {
  error "Requested part $part is unavailable; select a documented same-family fallback explicitly"
}
create_project -in_memory -part $part
set_property verilog_define {SYNTHESIS} [current_fileset]
foreach f $rtl_sources {read_verilog -sv [file join $root rtl $f]}
synth_design -top otpu_asic_top -part $part -mode out_of_context \
  -flatten_hierarchy rebuilt -generic $core_generics
write_checkpoint -force core_synth.dcp
if {![info exists core_period_ns]} {set core_period_ns 20.000}
create_clock -name core_clk -period $core_period_ns [get_ports clk]
set data_inputs [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_input_delay 2.000 -clock core_clk $data_inputs
set_output_delay 2.000 -clock core_clk [all_outputs]
report_utilization -file utilization_synth.rpt
report_utilization -hierarchical -file utilization_hierarchy.rpt
report_timing_summary -file timing_synth.rpt
write_checkpoint -force core_synth.dcp
set bad [get_cells -hier -filter {IS_BLACKBOX == 1}]
puts "BLACKBOX_COUNT [llength $bad]"
if {[llength $bad] > 0} {error "Unresolved cells: $bad"}
opt_design
place_design
phys_opt_design
route_design
report_utilization -file utilization_route.rpt
report_timing_summary -delay_type min_max -report_unconstrained -file timing_route.rpt
report_drc -file drc_route.rpt
write_checkpoint -force core_route.dcp
puts "ZU3EG_CORE_ROUTE_COMPLETE"
exit
