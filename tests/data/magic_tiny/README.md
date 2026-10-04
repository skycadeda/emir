# magic_tiny: the magic route's test cell

`tiny.gds` is drawn by `make_tiny.py` (synthetic, SKY130's public GDS layer numbers): one
NMOS, its source rail `vss` labelled at both ends. The other files are what `emir extract
--tool magic --gds tiny.gds --cell tiny --process pdks/sky130.yaml` had magic write for it
(magic 8.3.515 with the `geoout` patch, the sky130A technology file of open_pdks 1.0.510):

| file | what |
|---|---|
| `tiny.ext` | magic's extraction (`extract all`) |
| `tiny.res.ext`, `tiny.res.geo` | its resistance network, and each resistor's geometry (`extresist geoout on`, `simplify off`) |
| `tiny_pex.spice`, `tiny_lumped.spice` | the split and the lumped netlists |

`tests/test_magic.py` writes the DSPF and the CC netlist from them without magic, and,
marked `magic`, extracts `tiny.gds` again with a magic that writes geometry when there is
one and compares.

`pair.gds` is drawn by `make_pair.py`: two NMOS in a chain, the net between them with
no label (magic names it `a_188_0#` in its lumped netlist and `a_188_0.tK` in its split
one). `pair.ext`, `pair.res.ext`, `pair.res.geo`, `pair_pex.spice` and
`pair_lumped.spice` are what `emir extract --tool magic` had magic write for it, with
the same magic and technology file and EMIR's script that flattens the cell first.
`make_pair.py` also draws `pair_h.gds` (the same layout, its transistors in a subcell
with labels of its own) and `pair_d.gds` (the `out` strap continued by a 45-degree
band): the test marked `magic` extracts all three and checks that the hierarchy gives
the flat network and that the band is named (EMIR-W647).
