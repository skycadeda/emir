* Invented device models for the open-simulator examples: round numbers, not
* any foundry's. A level-1 NMOS with lambda = 0 and no capacitances, so in
* saturation its drain current is exactly (kp/2)(W/L)(Vgs - vto)^2, whatever
* the rail droop: 25 uA * W/L at Vgs = 1 V.
.model nlv NMOS level=1 vto=0.5 kp=200u lambda=0

* the device as an open PDK ships one: a MOSFET wrapped in a subcircuit, whose
* pin currents no simulator prints directly (hence `emir probe`)
.subckt nsink d g s b params: W=1u L=1u
M1 d g s b nlv W={W} L={L}
.ends nsink
