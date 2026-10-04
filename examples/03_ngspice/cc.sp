* loadrail, CC netlist: the same four devices with every net ideal (what a
* C-only extraction or the schematic gives; this one has no parasitic caps).
.SUBCKT loadrail VDD VSS G0 G1 G2 G3
XMN0 VDD G0 VSS VSS nsink W=80u L=1u
XMN1 VDD G1 VSS VSS nsink W=100u L=1u
XMN2 VDD G2 VSS VSS nsink W=120u L=1u
XMN3 VDD G3 VSS VSS nsink W=100u L=1u
.ENDS loadrail
