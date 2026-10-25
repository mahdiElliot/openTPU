# Recheck fixed routed core timing; does not change or overwrite the validated checkpoint.
# vivado -mode batch -source clock_check.tcl -tclargs CORE_ROUTE.dcp OUT_DIR
set checkpoint [file normalize [lindex $argv 0]]
set out [file normalize [lindex $argv 1]]
file mkdir $out
set_param general.maxThreads 4
open_checkpoint $checkpoint
set summary [open [file join $out clock_check.csv] w]
puts $summary "mhz,period_ns,setup_slack_ns,hold_slack_ns"
foreach mhz {50 75 100 112.5 125} {
  set period [expr {1000.0 / $mhz}]
  create_clock -name core_clk -period $period -waveform [list 0 [expr {$period / 2.0}]] [get_ports clk]
  set label [string map {. p} $mhz]
  report_timing_summary -delay_type min_max -report_unconstrained \
    -file [file join $out timing_${label}mhz.rpt]
  set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
  set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
  puts $summary "$mhz,$period,$setup,$hold"
  flush $summary
  puts "CLOCK_CHECK mhz=$mhz setup=$setup hold=$hold"
}
close $summary
exit
