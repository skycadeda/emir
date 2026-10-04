* Invented loads for example 12: a load whose current follows its supply. A
* 600 ohm resistor draws exactly V / 600 ohm (2 mA at 1.2 V), so the closed-loop
* answer is the resistive network itself (README.md works it out by hand).
.subckt rload p n
R1 p n 600
.ends rload
