# Static clock-constraint sweep of the fixed physical-board route. Does not
# program hardware, change the saved checkpoint, or select new MMCM settings.
set checkpoint [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
file mkdir $out
set_param general.maxThreads 4
open_checkpoint $checkpoint
set clock_name clk_out1_system_clocks_0
set output [get_pins system_i/clocks/inst/mmcme4_adv_inst/CLKOUT0]
set source [get_pins system_i/clocks/inst/mmcme4_adv_inst/CLKIN1]
if {[llength $output] != 1 || [llength $source] != 1} {error "Expected board MMCM missing"}
set csv [open [file join $out board_clock_check.csv] w]
puts $csv "mhz,period_ns,setup_slack_ns,hold_slack_ns"
foreach mhz {112.5 125 130 133.333 134 134.25 134.3 134.4 135 140} {
  # PS input remains 100 MHz; replace only this MMCM output's generated-clock
  # constraint. Existing routes, source clock and timing exceptions are retained.
  set multiply [expr {round($mhz * 1000)}]
  create_generated_clock -name $clock_name -source $source \
    -multiply_by $multiply -divide_by 100000 $output
  set clock [get_clocks $clock_name]
  set setup [get_property SLACK [get_timing_paths -from $clock -to $clock -delay_type max -max_paths 1]]
  set hold [get_property SLACK [get_timing_paths -from $clock -to $clock -delay_type min -max_paths 1]]
  set period [get_property PERIOD $clock]
  puts $csv "$mhz,$period,$setup,$hold"
  flush $csv
  puts "BOARD_CLOCK_CHECK mhz=$mhz setup=$setup hold=$hold"
  if {$mhz in {112.5 125 133.333 134.3 134.4 140}} {
    set label [string map {. p} $mhz]
    report_timing_summary -delay_type min_max -file [file join $out timing_${label}mhz.rpt]
  }
}
close $csv
puts "BOARD_CLOCK_CHECK_COMPLETE"
exit
