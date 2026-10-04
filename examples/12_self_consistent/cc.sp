* biasrail, CC netlist: the same four loads with every net ideal (what a C-only
* extraction or the schematic gives; this one has no parasitic caps).
.SUBCKT biasrail VDD VSS
XL0 VDD VSS rload
XL1 VDD VSS rload
XL2 VDD VSS rload
XL3 VDD VSS rload
.ENDS biasrail
