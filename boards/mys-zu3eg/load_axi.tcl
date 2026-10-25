# Bulk DDR upload through a temporary JTAG-to-AXI image. The accelerator is
# restored separately without resetting PS DDR.
set image [file normalize [lindex $argv 0]]
set base [expr {[lindex $argv 1]}]
set limit [expr {[lindex $argv 2]}]
open_hw_manager
connect_hw_server -url localhost:3121
set target [lindex [get_hw_targets] 0]
current_hw_target $target
set_property PARAM.FREQUENCY 12000000 $target
open_hw_target
puts "AXI_DEVICES [get_hw_devices]"
current_hw_device [lindex [get_hw_devices xczu3_0] 0]
refresh_hw_device [current_hw_device]
set master [lindex [get_hw_axis] 0]
puts "AXI_MASTER $master"
if {$master eq ""} {error "The temporary JTAG-to-AXI loader must be programmed first"}
reset_hw_axi $master
puts "AXI_MASTER_RESET"
set bytes_per_burst 2048
set transactions {}
for {set k 0} {$k < 16} {incr k} {
  create_hw_axi_txn loader$k $master -type WRITE \
    -address [format %08x $base] -len 256 -data 0000000000000000
  lappend transactions [get_hw_axi_txns loader$k]
}
puts "AXI_TRANSACTIONS [llength $transactions]"
set input [open $image rb]
fconfigure $input -translation binary -encoding binary
set total [file size $image]
if {$limit > 0 && $limit < $total} {set total $limit}
set offset 0
set start [clock milliseconds]
while {$offset < $total} {
  set batch {}
  for {set k 0} {$k < 16} {incr k} {
    if {$offset >= $total} break
    set n [expr {min($bytes_per_burst, $total - $offset)}]
    set bytes [read $input $n]
    if {[string length $bytes] != $n} {error "Short input file"}
    if {$n % 8} {error "Input must contain whole 64-bit AXI beats"}
    binary scan [string reverse $bytes] H* data
    create_hw_axi_txn -force loader$k $master -type WRITE \
      -address [format %08x [expr {$base + $offset}]] -len [expr {$n/8}] -data $data
    set transaction [get_hw_axi_txns loader$k]
    lappend batch $transaction
    incr offset $n
  }
  run_hw_axi -quiet -queue {*}$batch
  if {$offset % 1048576 == 0 || $offset == $total} {
    puts "AXI_LOADED $offset $total [expr {[clock milliseconds] - $start}]"
    flush stdout
  }
}
close $input
foreach transaction $transactions {delete_hw_axi_txn $transaction}
# Independently read selected 2-KiB bursts back through AXI and compare bytes.
set input [open $image rb]
fconfigure $input -translation binary -encoding binary
set positions [lsort -integer -unique [list 0 [expr {($total/2)/2048*2048}] [expr {max(0,$total-2048)}]]]
foreach at $positions {
  set n [expr {min(2048, $total - $at)}]
  seek $input $at
  set expected [read $input $n]
  create_hw_axi_txn check $master -type READ \
    -address [format %08x [expr {$base + $at}]] -len [expr {$n/8}]
  set txn [get_hw_axi_txns check]
  run_hw_axi -quiet $txn
  set actual [string map {_ ""} [get_property DATA $txn]]
  binary scan [string reverse $expected] H* wanted
  if {[string tolower $actual] ne [string tolower $wanted]} {
    error "AXI readback mismatch at $at: [string range $actual end-31 end] versus [string range $wanted end-31 end]"
  }
  delete_hw_axi_txn $txn
  puts "AXI_READBACK_PASS $at $n"
}
close $input
puts "AXI_UPLOAD_COMPLETE $total [expr {[clock milliseconds] - $start}]"
close_hw_target
disconnect_hw_server
close_hw_manager
