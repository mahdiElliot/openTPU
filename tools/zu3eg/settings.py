"""Selected MYIR queued-bridge deployment profile; clocks qualified by Vivado."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / 'build/zu3eg/wide_128_16'
TARGET = 'mys-zu3eg:d128-m1'
CORE_MHZ = 177.5
MEM_MHZ = 332.8125
AXI_DATA_WIDTH = 256
PREFILL = 128
DECODE = 16
CAPACITY = 256
