#!/bin/sh
# The open-source DSPF flow with ngspice: see emir_open_flow.sh for the options.
#   run_ngspice.sh --mode rcc|cc-grid [--net N] [--probe] [--period S] [--out DIR] \
#       TESTBENCH DSPF PROCESS
exec "$(dirname "$0")/emir_open_flow.sh" ngspice "$@"
