# Local stdin/stdout command protocol; only the Python caller supplies commands.
fconfigure stdout -buffering line
connect -url tcp:127.0.0.1:3121
jtag targets -set -filter {level == 0}
jtag frequency 12000000
targets -set -filter {name =~ "PSU"}
# The fresh PL control window is not in the debugger's built-in PS-only map.
# The caller limits accesses to our explicit CSR and DDR addresses.
configparams force-mem-accesses 1
proc otpu_wait {mask value timeout_ms} {
  set deadline [expr {[clock milliseconds] + $timeout_ms}]
  while {1} {
    set status [lindex [mrd -value 0x8000000c] 0]
    if {$status & 0x112} {error [format "Accelerator error: STATUS=0x%08x" $status]}
    if {($status & $mask) == $value} {return $status}
    if {[clock milliseconds] > $deadline} {error [format "Accelerator timeout: STATUS=0x%08x" $status]}
    after 1
  }
}
proc otpu_run {program_addr instructions arguments} {
  mwr 0x80000008 4
  otpu_wait 8 8 30000
  mwr 0x80000010 $program_addr
  mwr 0x80000014 $instructions
  mwr 0x80000008 2
  otpu_wait 4 0 30000
  for {set k 0} {$k < [llength $arguments]} {incr k} {
    mwr [expr {0x80000060 + 4 * $k}] [lindex $arguments $k]
  }
  mwr 0x80000008 5
  set status [otpu_wait 1 1 180000]
  otpu_wait 8 8 30000
  set counters [mrd -value 0x80000018 3]
  set cycles [expr {([lindex $counters 1] << 32) | [lindex $counters 0]}]
  mwr 0x80000008 0
  return [list $cycles [lindex $counters 2] $status]
}
puts "OTPU_READY"
while {[gets stdin line] >= 0} {
  set split [string first " " $line]
  set id [string range $line 0 [expr {$split-1}]]
  set code [encoding convertfrom utf-8 [binary format H* [string range $line [expr {$split+1}] end]]]
  set status [catch {uplevel #0 $code} result]
  binary scan [encoding convertto utf-8 $result] H* encoded
  puts "OTPU_RESPONSE $id $status $encoded"
}
disconnect
