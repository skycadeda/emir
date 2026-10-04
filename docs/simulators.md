# Simulators

EMIR takes its currents from a transient simulation of the block (or from DC values).
Four simulators have a route into it: **Spectre**, **HSPICE**, **Xyce** and **ngspice**.
Each names its currents its own way, writes its own file format and has its own sign
conventions, and a release, an option file or a site's wrapper script can change any of
them. So **support for a simulator is proven on the machine that uses it**:
`emir verify-sim` runs a testbench whose answer is known by hand through that simulator,
on that machine, and every run whose currents come from a simulator that has not passed
there says so.

> **EMIR is a research project, provided as is, with no warranty or guarantee of
> accuracy. It is your responsibility to test and verify its results for your process,
> tools and design before relying on them.** Screening, not sign-off.

## The support matrix

| | Spectre | HSPICE | Xyce | ngspice |
|---|---|---|---|---|
| licence | licensed | licensed | open source | open source |
| rcc: every branch current | `save XI0.Rm1:1 ...` | `.probe tran i(xi0.rm1) ...` | `.print tran I(XI0:RM1) ...` | `.save @r.xi0.rm1[i] ...` |
| cc-grid: the instance-pin currents | `save XI0.MN0:d ...`, a subcircuit's pin too | `i1(...)` .. `i4(...)` of a primitive device (the source's sign set from KCL, below), `-i1(...)` for pin 2 of a two-terminal element; a 0 V probe for a subcircuit's pin | `ID(...)` / `IS` / `IG` / `IB` of a primitive, `I(...)` of a two-terminal element; a 0 V probe for a subcircuit's pin | `@r...[i]` of a two-terminal element; a 0 V probe for a MOSFET's, BJT's or JFET's pin (ngspice's `@m...[id]` carries the model's polarity: below) and for a subcircuit's pin |
| analyses | transient; PSS (PSF ASCII `td` / `fd`) | transient | transient | transient |
| what EMIR reads | PSF ASCII (`-format psfascii`), nutascii (`-format nutascii`: read, never verifiable) | HSPICE ASCII `.trN` (`.option post=2`, best with `post_version=2001`); CSDF is read, but `emir verify-sim` cannot cover it: a run on HSPICE's own CSDF says `format not covered` and stays UNVERIFIED | ASCII raw (`Xyce -a`); CSDF (`format=probe`: read, never verifiable) | ASCII raw (`filetype=ascii`) |
| what EMIR refuses, and says how to fix | binary PSF (EMIR-E408: convert it with the `psf` utility, or `-format psfascii`); other binary formats (EMIR-E410) | the binary `.trN` (EMIR-E463: `.option post=2`) | binary raw (EMIR-E409: `-a`) | binary raw (EMIR-E409: `filetype=ascii`) |
| save lines | `emir save-lines` | `emir save-lines --sim hspice` | `emir save-lines --sim xyce` | `emir save-lines --sim ngspice` |
| `emir simulate` | writes the deck and prints the command; runs Spectre only with `--run` | writes the deck and prints the command; runs HSPICE only with `--run` | runs Xyce | runs ngspice |
| self-consistent cc-grid (`emir run --mode cc-grid --self-consistent --sim ...`) | the person adds the per-pin split to the CC netlist once (`--split-stub` prints it: a `vsource type=pwl file=` per pin; EMIR writes it itself in `emir verify-sim`'s testbench); each pass rewrites every pin's PWL file; runs only with `--run`; UNVERIFIED until checked on site | EMIR writes the split copy of the testbench and an include of the split's sources with inline PWL (written from the HSPICE manuals' syntax); runs only with `--run`; UNVERIFIED until checked on site | EMIR writes the split copy and the include of its sources (inline PWL) and runs it; verified here (`emir verify-sim --sim xyce`, its self-consistent case; [the constructs](#the-self-consistent-loops-pwl-sources)) | the same, with ngspice; verified here |
| tested in this repository | readers on synthetic PSF ASCII files; the deck, the save statements, the result path and the self-consistent loop with a stand-in executable (`tests/_sim_fakes.py`); Spectre itself never runs here | readers on synthetic files written to the documented layout (and, privately, on real third-party files: below); the deck, probes and names with a stand-in executable; HSPICE itself never runs here | real runs, where Xyce is installed (the development machine); CI has no Xyce package, says so and skips those tests | real runs, in CI's full job and on the development machine |
| verified on your machine by | `emir verify-sim --sim spectre --run` (ask the person first) | `emir verify-sim --sim hspice --run` (ask the person first) | `emir verify-sim --sim xyce` | `emir verify-sim --sim ngspice` |

Nothing in this repository claims that Spectre or HSPICE works with EMIR anywhere. The
numbers quoted from the original development site (on [the Cadence page](cadence.md),
in the blog) are measurement provenance, not support: these routes are UNVERIFIED until
`emir verify-sim` passes on the machine that runs them. Xyce and ngspice are run by this
repository's tests and examples, and still verified on site, because the build on your
machine is not the one tested here.

## On-site verification

```sh
emir verify-sim --sim xyce                      # open simulators run directly (seconds)
emir verify-sim --sim spectre                   # licensed: writes the decks, prints the commands
emir verify-sim --sim spectre --run             # ... runs them, after the person's go-ahead
emir verify-sim --sim spectre --run --sim-args '+preset=cx +postlpreset=off'
```

**The testbenches** are the bundled sample rail (`emir/data/sample/rail.dspf`: twelve
1-ohm metal1 segments fed at VDD, four contacts) instantiated as `XI0` in testbenches
`emir verify-sim` writes, with ideal current sources ILOAD0..3 on the contacts (dc,
pulse) or real devices (dev). Six simulations, then the self-consistent loop's passes,
go through the same route a run takes (`emir simulate`: save or probe lines, 0 V
probes, the names sidecar, the reader):

| case | netlist | what is saved | the analytic answer |
|---|---|---|---|
| dc, rcc | the rail's resistors and the loads, 1 mA each | every branch current | Rm1..Rm3 4 mA, Rm4..Rm6 3 mA, Rm7..Rm9 2 mA, Rm10..Rm12 and Rc0..Rc3 1 mA, positive from the feed; worst ratio 4 / 2.4 = 1.6667 (avg, with the sample's illustrative limits); IR 54.0 mV |
| dc, cc | the loads on an ideal rail | each load's current (a probe, or Spectre's `inst:pin`) | +1 mA into each load; the same ratio and IR through cc-grid |
| pulse, rcc | the RC variant (twelve 1 fF caps), each load a 0 to 2 mA trapezoid (10 ns period, 0.1 ns edges) | every branch current | per load avg 1.0 mA, peak 2 mA, rms 1.409492 mA; Rm1 avg 1.6667, peak 0.6667, rms 0.62644; Rc3 avg 1.3333, peak 0.8333, rms 0.93966 |
| pulse, cc | the pulsed loads on an ideal rail | each load's current | the same, through cc-grid |
| dev, rcc | the rail's resistors with real devices on its contacts (below), each fed by an ideal 1 mA DC source | every branch current, and three extra contacts that carry none | the dc case's branch currents, ratio and IR; the extra contacts 0 |
| dev, cc | the same devices on an ideal rail | each device pin, with the form emir's cc route uses for that simulator | the current into each pin, with its direction (below) |
| self-consistent | resistive loads RLOAD0..3 (1 200 ohm) on an ideal rail, run through `emir run --mode cc-grid --self-consistent` (several passes) | each load's current, its supply at the pass's droop | pass 0 (the open loop) as the dc case: IR 54.0 mV, ratio 1.6667; the fixed point is the resistive network itself: loads 0.98050, 0.97332, 0.96856 and 0.95672 mA, Rm1..Rm3 3.87909 mA (ratio 1.61629), IR at RLOAD3 51.940 mV, Rc3 avg ratio 1.27562. The loads are DC, so each pass feeds a constant droop (a two-point PWL): this case proves the split, the include and its sources, not a long time-varying PWL (hundreds to thousands of points a pin, `+` continuation lines, every point a breakpoint), which only the loop's runs on switching loads exercise |

**The dev case's devices** (built-in level-1 models, no PDK data), one per contact:

| pin on the rail | the device | known current into the pin | read in the cc case by |
|---|---|---|---|
| `MP0:s` | a diode-connected PMOS (source and bulk on the rail; gate and drain tied, fed to ground by 1 mA) | +1 mA | HSPICE `i3` (sign set from KCL over `i1`..`i4`); Xyce `IS`; Spectre `MP0:s`; ngspice a 0 V probe |
| `MP0:b` | its bulk, on a contact of its own | 0 | HSPICE `i4`; Xyce `IB`; Spectre `MP0:b`; ngspice a probe |
| `MN1:d` | a diode-connected NMOS (drain and gate on the rail; its source fed to ground by 1 mA) | +1 mA | HSPICE `i1`; Xyce `ID`; Spectre `MN1:d`; ngspice a probe |
| `MN1:g` | its gate, on a contact of its own | 0 | HSPICE `i2`; Xyce `IG`; Spectre `MN1:g`; ngspice a probe |
| `RL2:2` | pin 2 of a 100 ohm resistor whose pin 1 is fed to ground by 1 mA | +1 mA | HSPICE `i1`, Xyce `I(...)`, ngspice `@r...[i]`, each negated; Spectre `RL2:2` |
| `XM3:d`, `XM3:g` | an NMOS wrapped in a subcircuit (ports d g s b), connected as MN1 | +1 mA, 0 | `emir probe`'s 0 V sources (HSPICE, Xyce, ngspice); Spectre `XM3:d`, `XM3:g` |

Each pin's current is compared with its known value **and direction** (into the
device), not only with KCL: a simulator that reported every terminal of a device with
the opposite sign would still close KCL, and fails here.

The module docstring of `emir/verify_sim.py` works every number out. The checks: the
simulator printed its version; each result is read in the format expected, names the
simulator (in its header or its names sidecar), holds every current asked for; every
current, ratio and IR drop matches the analytic answer to 1e-3 (a current whose answer
is 0 to 1e-6 A, rms ratios to 1e-2, for the simulator's time steps on the edges); rcc
and cc-grid agree metric by metric to 1e-3; the self-consistent loop converges (to
0.001 mV) with a falling droop change, its pass 0 on the open loop and its result on the
closed-loop answer, to 1e-3. 154 checks in all.

**What it covers, and what not.** A pass shows, on this machine, for this simulator
version, output format and `--sim-args`: the branch currents rcc reads (`R:1`); the
current into a MOSFET's drain, gate, source and bulk through the forms above; pin 2 of a
two-terminal element; a subcircuit pin through `emir probe`'s 0 V sources; the reader,
the names sidecar, and rcc and cc-grid on the result; the self-consistent loop on
resistive loads (the split, the droop file and its PWL sources, the passes). It does **not** cover:
BJT, JFET and diode terminals, MOSFET models other than the built-in level 1 (a PDK's own
models, `isub`), Spectre's PSS and nutascii outputs, CSDF, `.alter` and sweeps, any
simulator option or option file but the `--sim-args` given, or the person's own
testbench. Those are UNVERIFIED until checked against known currents on site.

**Coverage is per kind of current.** Each check of a verification names the kind of
current it proves, and the record lists the kinds it covers (`covers`):

| kind | what | covered by a pass of |
|---|---|---|
| `branch` | resistor branch currents (rcc) | every simulator |
| `mos` | MOSFET terminals read directly (HSPICE `i1`..`i4`, Xyce `ID`/`IS`/`IG`/`IB`, Spectre `inst:pin`) | Spectre, HSPICE, Xyce |
| `two-terminal` | resistor and capacitor pins (`I(R)`, `-i1`, `@r[i]`, `inst:pin`) | every simulator |
| `probe` | any pin read through emir's 0 V probes (`emir simulate --probe`), whatever the device | HSPICE, Xyce, ngspice |
| `subcircuit`, `source` | a subcircuit's or a source's pin read directly (`inst:pin`) | Spectre |
| `diode`, `bjt`, `jfet`, `other` | diode, BJT, JFET terminals, other elements, read directly | **none**: no testbench holds them |

The names sidecar of `emir simulate` and `emir save-lines` records how each pin is read
(`kinds`); without it, a pin's kind is its instance name's SPICE letter. A run whose
currents are not all of covered kinds is **`partly verified`** (or `not verified` when
none is), `reason` `pin kinds not covered`, never `verified`: its summary's `pin_kinds`
lists each kind it read, how many pins, examples, and whether the record covers it, and
EMIR-W464 names the pins that stay unverified. A diode, BJT or JFET pin read through a
probe (`emir simulate --probe --types ...`) is of kind `probe`, which a pass covers.

**The record.** A finished verification is kept in the EMIR state directory
(`$EMIR_STATE_DIR`, else `~/.emir`), in `verified.json` (`emir schema verified`): the
simulator, its version (what its own version flag prints: `spectre -V`, `hspice -v`,
`Xyce -v`, `ngspice -v`), the output format, `--sim-args`, the emir version, the date,
pass or fail, every check (with the kind it proves) and the kinds covered. The host name
is kept only with `--record-host`. A failed verification exits 70 with EMIR-E663 and is
recorded as failed; a simulator that prints no version fails (a record is per version).
The file is written under a lock (`verified.json.lock`) and replaced atomically, so two
verifications that finish together both keep their records, and the file keeps its mode
(a group-shared state directory stays readable to the group). A damaged file (cut short,
not JSON) is never treated as empty: runs say so (EMIR-W415, `records_problem`), and the
next `emir verify-sim` moves it aside to `verified.json.bad-<date>` before it writes a new
one. A file this user may not read (its permissions) is not damaged: it is left as it
is, runs say so (`reason` `records unreadable`, with no verification suggested: `emir
next` asks the person for access instead), and `emir verify-sim` refuses to start
(EMIR-E102; EMIR-E105 for a state directory it may not write) rather than simulate for a
record it could not keep (EMIR-W467 when a write fails all the same).

**What a run does with it.** Every run whose currents come from a simulator result
looks up this machine's records by a key: the simulator, its version (as the result's
header says it, else the names sidecar `emir simulate` writes; `emir simulate` records
the version it ran), the output format and the simulator arguments (`--sim-args`, as
the sidecar records them; a result that does not say is matched as given none, and the
summary says so). The rules:

- **the latest record for the key decides**: a later failure cancels an earlier pass,
  and a later pass counts again;
- every record is **validated strictly** (`emir.verify_sim/1`: every field present,
  `passed` a JSON boolean that agrees with its checks, a passing record with its
  version, each kind in `covers` backed by passing checks of that kind); an invalid one
  counts as no record (EMIR-W415);
- a result in an output format `emir verify-sim` never writes (CSDF, Spectre's
  nutascii) is **`format not covered`**: no record can match it, no verification is
  suggested or planned, and the run says its currents stay UNVERIFIED (EMIR-W464);
- a pass made by **another emir minor version is stale**: not verified, and `emir next`
  plans the verification again;
- a result whose **version cannot be told** is never matched to a record: "not verified:
  version unknown" (EMIR-W416), and no verification is suggested or planned (none could
  match it); simulate through `emir simulate` to record it;
- a record that names a **host** (`--record-host`) counts only on that host.

The run summary carries `simulator_verification` (`verified`, `partly verified`, `not
verified` with its `reason` -- `no record`, `failed`, `stale`, `version unknown`,
`other host`, `format not covered`, `pin kinds not covered`, `records unreadable` --,
`unknown simulator` or
`not applicable`, with the record and the run's `pin_kinds`). Anything but `verified`
adds one warning: EMIR-W464 ("simulator route not verified on this machine: run emir
verify-sim --sim X", or, when no verification can help, which currents stay
unverified), EMIR-W414 when the latest verification failed (do not rely on the
numbers), or EMIR-W416 when the version is unknown; `emir report` puts the same line
next to the verdict. `emir next` plans
`emir verify-sim --sim X` before the first simulation or run that uses X, as a step that
needs the person's go-ahead for Spectre and HSPICE. Before a run says which kinds of
current it read, `emir next` and `emir info` (`tools[].verified`) count a record only
when it covers every kind this emir's verification covers (one that lists no `covers`
is verified again); for a finished result `emir next` judges the kinds the planned run
reads, as the run does. After a run, the run's `next_steps` and `emir next
--run-summary` plan the verification where one would change its answer (once it has
passed, the same run again records it in the summary). Which simulator wrote a result comes
from its header (PSF ASCII names the simulator and version; ngspice's raw file its
version; CSDF its source; an HSPICE ASCII file its vendor in the copyright field), else
from its names sidecar (`emir simulate` and `emir save-lines` write the simulator there),
else from `emir run --sim`.

**What the agent reports** after a verification: the simulator and the version it
printed, PASS or FAIL with the number of checks, the failing checks by name with their
expected and measured values, and where the record was kept. A FAIL means: do not rely
on runs from that simulator on this machine; keep `--out` and report it.

## Spectre

- **Save lines.** `emir save-lines <dspf> --mode rcc|cc-grid --prefix XI0. --out
  emir_save.scs` writes `save` statements under the DSPF's own names (`R:1`, `inst:pin`).
  Spectre counts a terminal current positive when it flows into the terminal, which is
  EMIR's convention, so no sidecar is needed. Spectre can also save every element
  current (`options currents=all`) or every terminal on a net (`save <net>:currents
  probelvl=N`); EMIR's lines name what the run needs instead.
- **`emir simulate --sim spectre`** does not read the testbench (it may be in Spectre's
  own syntax). It writes `<result>.save.scs` and a deck that includes your testbench and
  then that file, and the command `spectre <deck> -format psfascii -raw <result>`. Spectre
  reads an included file in Spectre syntax when its name ends in `.scs` or it starts
  with `simulator lang=spectre`, and in SPICE syntax otherwise (the Spectre Circuit
  Simulator Reference 19.1, "Include File"). `--prefix` is required (`--prefix ''` for a
  block at the top level). Without `--run` nothing runs.
- **Formats.** Keep `-format psfascii` (or `nutascii`). The default binary PSF is
  refused (EMIR-E408); Cadence's `psf` utility converts it (`psf -i tran.tran -o
  tran.psfascii` writes ASCII unless `-b` is given), then pass the converted file. Spectre
  also lists `tr0ascii` among its formats; how it names terminal currents there was not
  checked, so that route is UNVERIFIED and not documented further.
- **APS and Spectre X.** The 19.1 reference documents one `save` statement for Spectre,
  APS and Spectre X. Two differences matter for EMIR: APS's default `subcktiprobes` is
  `save`, Spectre's `all` (how a subcircuit terminal's current is computed), and the
  post-layout optimisations reduce the parasitic resistors whose currents rcc needs:
  `+postlayout=upa` disables RC reduction completely, and `+postlpreset=off` disables
  Spectre X's parasitic optimisation (which it otherwise turns on when it detects
  post-layout content). Pass them with `--sim-args` to `emir simulate` and to
  `emir verify-sim`, so the verification covers the configuration you simulate with.
- **PSS.** The PSF ASCII reader reads a PSS's time-domain (`td`) and harmonic (`fd`)
  files; the file name Spectre gives them was not confirmed.
- **The self-consistent loop.** EMIR does not read a Spectre netlist, so the per-pin split
  (each instance pin on the rail its own node, fed from the rail by a `vsource type=pwl
  file="<dir>/vd_<pin>.pwl"` at the pin's droop waveform, 0 V on pass 0) goes into the CC
  netlist once, by hand: `emir run <dspf> --net VDD --split-stub` prints it. Each pass
  rewrites every pin's two-column file (time, value; in `emir_out/selfconsist/<net>/`,
  beside the manifest `droop.scs`). `emir run ... --mode cc-grid --self-consistent
  --netlist <cc.scs> --vdd V --run` runs Spectre once per pass, each pass's result kept as
  `pass<k>.raw` there; it refuses a netlist that names no pin's file or has a split node
  that feeds no pin (`EMIR-E524`). The file form of `type=pwl` is from the Spectre
  reference, not run here; in its own testbench `emir verify-sim` writes the split itself;
  that case is UNVERIFIED until `emir verify-sim --sim spectre --run` passes on site.
- **`emir run --netlist`** (one Spectre run, then the screening) runs Spectre only with
  `--run`, as `emir simulate` does; without it the run plans: it prints the command and
  runs nothing.

## HSPICE

- **The lines.** `emir save-lines <dspf> --sim hspice --mode rcc|cc-grid --prefix XI0.
  --names-out <stem>.tr0.names` prints

  ```text
  .option probe post=2 post_version=2001
  .probe tran i(xi0.rm1) i(xi0.rm2) ...
  + ...
  ```

  `post=2` writes the ASCII `.tr0`; `post_version=2001` keeps double precision (the
  default 9601 keeps five significant digits: EMIR-I464; 9007 also cuts names to 16
  characters, EMIR-W466); `.option probe` writes only the probed signals, which keeps
  the file small (it grows as signals x time points x 13 bytes). HSPICE names are lower
  case with `.` between hierarchy levels; the file leaves out each name's closing
  parenthesis (`i(xi0.rm1`), which the reader puts back. The names sidecar maps them to
  the DSPF's names (keys matched without regard to case).
- **Signs** (HSPICE Reference Manual: Commands and Control Options, B-2008.09 and
  E-2010.12; the Star-Hspice Manual 1998.2, chapters 4 and 15):

  | current | HSPICE | into the DSPF pin |
  |---|---|---|
  | a resistor, from its first node to its second | `i(R)` (= `i1(R)`) | + |
  | pin 1 / pin 2 of a two-terminal element | `i1(...)` for both: the current into pin 1 | + / - (pin 2's current is the negative of pin 1's, so how HSPICE draws `i2` is never relied on; with both pins on the net the second gets a 0 V probe) |
  | MOS drain, gate, bulk; BJT collector, base, substrate | `i1`, `i2`, `i4` (drawn into the device) | + |
  | MOS source, BJT emitter, JFET source | `i3` | drawn out of the device; not confirmed on a run, so EMIR saves every terminal of the device and sets the sign from KCL when it reads the result (EMIR-I465; EMIR-W465 when the terminals do not sum to zero with either sign) |
  | a voltage source (an `emir probe` 0 V probe), from + to - | `i(V)` | + (confirmed on a real netlist and output pair: a supply sourcing current reads negative) |

- **Subcircuit pins** (how open PDKs wrap their transistors): HSPICE has `isub(x1.pin)`,
  but how those names appear in a `.tr0` was not confirmed, so EMIR uses its 0 V probes
  (`emir simulate --probe`, or `emir probe`), whose current sign is the voltage source's.
- **`emir simulate --sim hspice`** writes the deck (your testbench's copy with the lines
  before `.end`, or before the first `.alter` when it has `.alter` cases, so the main case
  and every altered one save them: EMIR-I610; `.include` and `.lib` paths keep the quotes
  they were written with) and the command `hspice -i <deck> -o <stem>`, which writes
  `<stem>.tr0` and `<stem>.lis`; with `--run` it runs it, records HSPICE's version in the
  names sidecar, reads the result back and checks every current is there. Threads and
  the like (`-mt N`, `-hpp`) go in `--sim-args` (recorded in the sidecar too: the
  verification is keyed on them).
- **Sweeps.** A transient swept over a parameter writes one table per value; EMIR checks
  one waveform at a time, so `--analysis sweep:N` picks the table (EMIR-E465 lists them).
  `.ALTER` cases go to separate `.tr1`, `.tr2` files; pass the case's file.
- **Time points** are HSPICE's own, unevenly spaced: averages and rms are integrated over
  time, never per sample.
- **RC reduction** (`.option SIM_LA`) is off by default and does not reduce a node an
  analysis statement such as `.probe` uses; keep it off for rcc.
- **CSDF.** HSPICE manuals up to E-2010.12 list `.option post=csdf`; HSPICE's own CSDF
  flavour was not found, so that output is UNVERIFIED. EMIR's CSDF reader is tested on
  Xyce's file and on WRspice's form.
- **The reader** follows the format rules described in the sources of the open-source
  readers of gwave, Electric and PyOPUS (it was not run against those programs), and
  was tried, privately (their licences unknown, so they are not in this repository), on
  real third-party HSPICE ASCII files of formats 9601 and 2001: every complete transient
  file read; truncated downloads were refused with EMIR-E464. A field that reads NaN or
  Inf is kept as a value and `emir check` FAILs on it where a net needs it (EMIR-E426);
  two columns of one name, or 9007 names cut alike, are refused (EMIR-E427). The header
  parses in time linear in the signal count.
- **Not confirmed:** the sign HSPICE gives `i3` on a run, the type code it gives
  `i1`..`i4` in a 9601 or 2001 file, how `isub` names appear in a `.tr0`, and HSPICE's
  CSDF. `emir verify-sim --sim hspice --run` checks, on your installation, exactly what
  [its testbenches](#on-site-verification) cover: the forms above on its devices, not
  every device or option of your own runs.
- **The self-consistent loop.** `emir run ... --mode cc-grid --self-consistent --sim
  hspice --netlist <tb.sp> --vdd V` writes a split copy of the testbench (each instance
  pin on the rail its own node and a 0 V probe), the include of the split's sources it
  rewrites each pass (`VEMIRSC<k> ... PWL(t v ...)` with `+` continuation lines,
  `.include`d in the block's subcircuit), and runs HSPICE once per pass only with
  `--run`. Written from the manuals' `PWL` and `.INCLUDE` syntax, not run here:
  UNVERIFIED until `emir verify-sim --sim hspice --run` (its self-consistent case)
  passes on site.

## Xyce and ngspice

The [open-source flow](open-source-flow.md) describes their route in full: `emir
simulate` runs them, `emir probe` adds the 0 V probes, the names sidecar maps their
names. Xyce writes ASCII raw with `-a` and CSDF with `.print tran format=probe`; ngspice
writes ASCII raw with `filetype=ascii`.

**ngspice's device currents carry the model's polarity.** `@m...[id]` (and `[is]`,
`[ig]`, `[ib]`) is the model's internal current, not the current into the pin: for a
PMOS it has the opposite sign (ngspice 45, a diode-connected PMOS fed by 1 mA: `[id]`
reads +1 mA where 1 mA flows out of its drain; measured by
`tests/test_verify_findings.py`), and nothing in the netlist emir reads
tells a model's polarity reliably. So emir reads an ngspice MOSFET's, BJT's or JFET's pin
through a 0 V probe (`emir simulate --probe`, or `emir probe`), whose sign is the voltage
source's; `emir save-lines --sim ngspice --mode cc-grid` refuses such a pin with EMIR-E460 and
says so. Xyce's `ID` / `IS` / `IG` / `IB` are the currents into the pins (the same
measurement: `IS` +1 mA into that PMOS's source). Both run in this repository's tests
and examples (Xyce where it is installed), and `emir verify-sim --sim xyce` / `--sim ngspice` proves
them on your machine. The self-consistent loop (`--mode cc-grid --self-consistent --sim
xyce|ngspice`) needs no edit of the testbench: EMIR writes the split copy (its sources
in an included file, each pin's droop as an inline PWL) and runs each pass; both pass
`emir verify-sim`'s self-consistent case here, and
[`examples/12_self_consistent`](../examples/12_self_consistent/) runs the loop on both.
On switching inverters the loop was measured on both too (`examples/06_big_block`'s
`k32_n10` and `k32_n10_ng`, RCC on Xyce): the governing branch within 0.04 % of RCC and
each device's peak within 0.1 % on either.

## The self-consistent loop's PWL sources

The loop feeds each instance pin its own droop waveform as a voltage source in series
with the testbench's rail. Every PWL point is a simulator breakpoint, so the points are
chosen on one time grid shared by every pin of every net closed (`emir/pwl.py`: at 0.3
times the loop's tolerance; at most max(1 500, 0.3 x pass 0's time points) a pin, coarser
when that cap binds, `EMIR-W542`). Each point costs simulation time: on the synthetic
meshes a pass took about 3 times the open loop's over 4 ns (about 1 000 points a pin) and
9.5 times over 40 ns (7 400). How each simulator gets them, and what has been checked:

| simulator | construct EMIR writes | rewritten each pass | status |
|---|---|---|---|
| Xyce | `VEMIRSC<k> <pin node> <rail> PWL(t0 v0 t1 v1 ...)` with `+` continuation lines, in an include the split's subcircuit includes (`DC 0` on pass 0). Xyce also reads `PWL FILE "f"` (two columns), which EMIR's writer produces | the include | **confirmed** on Xyce 7.10: the source equals the file at every corner and holds its last value after the last point, inside a subcircuit (`tests/test_tdloop_sims.py`); the loop runs in `examples/06_big_block` and example 12 |
| ngspice | the same include (ngspice rejects `PWL file=`; XSPICE `filesource` set no breakpoints when tried while the loop was developed -- that probe is not committed --, so it is not used) | the include | **confirmed** on ngspice 45 (`tests/test_tdloop_sims.py`); the loop runs in example 12 |
| HSPICE | the same include, `.include '<file>'` | the include | **UNVERIFIED**: from the manuals' syntax, not run here; `emir verify-sim --sim hspice --run` on site |
| Spectre | `vsource type=pwl file="<dir>/vd_<pin>.pwl"` per pin, in the person's split (`--split-stub`) | each pin's file | **UNVERIFIED**: from the Spectre reference, not run here; `emir verify-sim --sim spectre --run` on site |

The testbench's own options stay as written: EMIR warns when its relative tolerance is
above 1e-4 or unset (`EMIR-W540`; Xyce reads `.options timeint reltol=`, ngspice and
HSPICE `.options reltol=`, Spectre `reltol=` in its options statement).

## Other simulators

A result in PSF ASCII, nutascii, HSPICE ASCII or CSDF is read whoever wrote it (Eldo,
for instance, documents CSDF and PSF ASCII output), with a names sidecar that maps its
names; `emir verify-sim` does not cover it, so its runs say `unknown simulator`, and the
route is UNVERIFIED until you check it against known currents yourself.

## Sources

The syntax above follows the vendors' published manuals (cited where used), the
open-source readers and writers of gwave, Electric, PyOPUS, WRspice and Xyce, and the
`psf_utils` project's notes; anything that could not be confirmed is marked
UNVERIFIED or "not confirmed". Example 11 (`examples/11_hspice_offline`) runs the HSPICE
route offline on a synthetic `.tr0`: HSPICE itself is not run.
