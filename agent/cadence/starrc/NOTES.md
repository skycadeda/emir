# EMIR with StarRC extraction (UNVERIFIED)

**Status: UNVERIFIED until checked on site.** No StarRC tool was available where EMIR
was developed, and nothing on this page has been run. On the machine that has StarRC,
the local agent verifies the DSPF it writes with `emir check <dspf> --process
<pdk.yaml>`, which names every annotation EMIR needs that is missing. Option names marked "from a search snippet" were
seen only in a search result of a StarRC guide that was not read in full; confirm
each one in your release's StarRC documentation.

From the public Synopsys StarRC Custom datasheet (cited below): StarRC writes DSPF
(and SPEF, SPICE netlists), takes LVS databases from several LVS tools (Calibre and
IC Validator among them), has user-controlled reduction, and extracts at several
temperatures.

## What EMIR's DSPF reader needs, and the StarRC option believed to give it

| EMIR needs (reader in `emir/dspf.py`) | StarRC (status) |
|---|---|
| R lines, no series merging | `EXTRACTION: R`, `REDUCTION: NO` (UNVERIFIED) |
| `$W` / `$L` on each wire resistor | `NETLIST_TAIL_COMMENTS: YES` (from a search snippet: writes length, width, is_via) |
| segment coordinates | `EXTRA_GEOMETRY_INFO: RES` (from a search snippet: x/y min/max) |
| the conductor layer per resistor: a bare `$name`, or `$layer=` / `$lyr=`, or `$lvl=N` mapped with `dspf_layers` | the tail comments (check the token form) |
| `*\|S` sub-nodes with x y | `NETLIST_NODE_SECTION: YES` (UNVERIFIED) |
| `*\|I` instance pins WITH x y (without them no simulated current attaches) | `NETLIST_INSTANCE_SECTION: YES` (UNVERIFIED) |
| a via cut count (`$N`, `$VC`, `$SAVC`, or `$A` with `vcwidth`); otherwise each via is ONE cut (EMIR-W207) | UNVERIFIED |
| `*\|OPERATING_TEMPERATURE` | `OPERATING_TEMPERATURE` (UNVERIFIED) |
| a node without a `*\|S` record is put on its net by splitting its name at `:` | keep the node section on, so the delimiter does not matter |

For the big-block (cc-grid) flow run it twice from one LVS database: `EXTRACTION: R`
for the grid DSPF and `EXTRACTION: C` (coupling kept) for the netlist to simulate.
Check the result with `emir check <dspf> --process <process.yaml>`.

`emir_starrc.cmd.tmpl` is a sketch of such a command file.

## Sources

- Synopsys, StarRC Custom datasheet:
  https://www.synopsys.com/content/dam/synopsys/implementation&signoff/datasheets/starrc-custom-ds.pdf
- The tail-comment and geometry options: a search-result snippet of a StarRC guide
  only (not read in full).

StarRC and IC Validator are trademarks of Synopsys, Inc. EMIR is not affiliated
with Synopsys.
