# Simulator result samples (tests/test_simulators.py)

Every number here is synthetic or comes from a run on the development machine;
none comes from a licensed tool, and HSPICE and Spectre did not write any of
these files.

| file | what | how it was made |
|---|---|---|
| `rcc_post2_9601.tr0` | HSPICE ASCII, format 9601, RCC mode: `TIME`, `v(n1`, `i(r0` .. `i(r3`, `i(xbuf.rpar_vdd_seg12`, `i(vdd`; a 21-character name straddles a header line break; 8 values per row against 7 per line; 20 uneven time points; KCL-consistent | written to the documented layout (the format rules described in the sources of the gwave, Electric and PyOPUS readers); the header's copyright field says it is synthetic, where HSPICE writes its vendor's notice |
| `cc_post2_2001_sweep.tr0` | HSPICE ASCII, format 2001, CC mode at net `vout`, two sweep tables (`iload_dc` 1 mA and 5 mA, one prefix value each); the currents into each element on the net sum to zero | the same |
| `rcc_xyce_style.csd` | CSDF in Xyce's `value:index` form, the RCC data above | the same |
| `cc_plain_sweep.csd` | CSDF in WRspice's plain form, two `#H .. #;` blocks (a sweep), the CC data above | the same |
| `rcc_tran.psfascii` | PSF ASCII transient with saved terminal currents `R0:1` .. `VDD:p` (`"version" "synthetic"`) | the same |
| `xyce_rcc_probe.cir.csd` | a real CSDF file (`.print tran format=probe`) | Xyce (a development build; the file says `VERSION='7.11.0'`) on `xyce_rcc_probe.cir`, beside it |

The tests also write HSPICE ASCII and CSDF files with `emir.hspice.write_tr0ascii`
and `emir.csdf.write_csdf`, which follow the same layout, and read them back.
