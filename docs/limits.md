# Limits

What EMIR does not model, does not check, or has not been tested on, in one place.
Each flow's page has its own list with the details; this page collects them, so you can
decide what a result can and cannot be used for.

**Screening, not sign-off.** That is the first limit and the one that governs the
others: EMIR finds the problems early and says what to change; the foundry-qualified
EM/IR flow signs off.

## What the physics model leaves out

- **One limit per layer and metric, in the design manual's two forms.** A wire is judged
  by its current per um of width against one value per layer and metric (mA/um), a via
  by its current per cut against one value per via type. Two forms that design manuals
  print are applied: a **width loss**, `I_max = J x (W - dW)` (`width_loss_um`: the wire
  limits apply to the drawn width less dW, and the fix widens by the ratio of `W - dW`;
  `emir import-ict` writes it from an ICT's `(w - dw)` form), and **Black's derating** of
  the average limits with temperature (`derating: {model: black, ea_eV, n}`, the
  alternative to a `jmax_factor` table) ([the process file](process-file-reference.md),
  [entering EM data by hand](pdk-onboarding.md#entering-em-data-by-hand)). Still not
  modelled: **limit tables by width range** (a J that changes with the width beyond the
  `W - dW` offset: narrow-wire rules), the **Blech length** (short-wire immunity: no
  short wire is credited, and `emir import-ict` takes an ICT's long-wire branch, the
  conservative one), and **thermal maps** (next item).
- **One temperature per run.** No thermal map, no self-heating model beyond the rms
  limit's `delta_T` budget (kept as given: `EMIR-W301` when the rms limit is then below
  the average one), no thermal coupling between wires.
- **Temperature derating.** Without a `jmax_factor` table or Black's `derating` the
  average limits are used as quoted at `em_tref` at any analysis temperature, and above a
  table's top they are clamped: both are **not conservative** above the quoted
  temperature (EM limits fall with temperature). The input check warns (`EMIR-W315`,
  `EMIR-W305`) and the run lists it under "Not checked". Black's equation is applied as
  written above `em_tref` and held at x1 below it (`EMIR-W326`; `below_tref:
  extrapolate` lifts the hold), so the limits are never loosened below the quoted
  temperature.
- **Direction.** The magnitude of the **signed** average is compared with the DC
  (unidirectional) average limit. A current that reverses cancels toward zero, so this
  is the **least conservative** reading of a bidirectional EM rule: EMIR applies no
  recovery factor and no average-of-|I| rule. Where it has waveforms it lists the
  branches whose average of |I| reaches the warn fraction of the limit while the signed
  average does not ("Not checked"): check those against the PDK's AC rule. rms covers
  their heating, not their EM.
- **A via's current per cut is the array's mean.** The cuts facing the current carry
  more. Via ratios above about 0.9x are marginal (listed under "Not checked"); confirm
  them cut by cut (the extractor's per-cut vias).
- **No lifetime model**, no mission profile: run each operating mode on its own
  ([Big blocks](big-blocks.md#operating-modes)).
- **Layers the process file gives no limit** carry their current in the IR drop but are
  not checked: on sky130, li1 and everything below it in magic's network (contacts,
  diffusion, poly, wells; see below).

## What the inputs decide

- **The limits are your process file's.** EMIR checks the file's form, not its values;
  a metric with no limit is **not checked** and listed as such, never guessed. The three
  open-PDK files publish no peak limits, no rms limits on vias and no derating tables
  ([`pdks/README.md`](../pdks/README.md)).
- **The currents are your simulation's**, under your stimulus, corner and operating
  mode. DC `--currents` make avg = rms = peak: a budget check that cannot see switching
  transients.
- **The extraction.** EMIR needs widths, cut counts and coordinates
  ([DSPF requirements](dspf-requirements.md)); `emir check` finds a missing annotation,
  not a wrong extraction setting that still writes one.
- **Resistance and temperature.** When the DSPF's stated extraction temperature differs
  from the analysis temperature, each wire resistor is scaled by its own layer's TCR
  from the DSPF's temperature (from `temp_reference` when the DSPF states none); via
  and contact resistances are not scaled, since the process file gives vias no TCR.
  Extract at the EM analysis temperature to need neither.

## Method limits

- **cc-grid is decoupled**: device currents from an ideal rail. Averages and IR were
  within about 2 % of RCC where the droop is small, not always on the conservative side;
  rms and peak are conservative on the main branches but not on every branch (down to
  -19 % on minor branches of the synthetic mesh); the error grows with the droop
  ([Methods and modes](method-modes.md#accuracy-with-its-sources)).
- **Coupling-capacitor current has no position** in a C-only extraction, so it is not
  injected into the grid.
- **Decap-aware EM** (`--dynamic`) is open loop like the rest of cc-grid (unless the
  self-consistent loop ran: it then scores the loop's reported pass, the decaps fed back
  with the grid) and rests on the
  decap model (`cap_models`: `cox` is a MOS decap's largest C; the access resistance) and
  the extraction's capacitance. Each net is solved alone, so a decap between two
  resistive rails reads its straps' current low (-5.52 % rms measured in
  `examples/13_decap_aware`, EMIR-W439) and the default basis (`worst`) is no bound
  there; coupling to signal nets is not counted as decap; block abstracts carry
  decap-free pin waveforms; one ideal feed per net
  ([Big blocks](big-blocks.md#decap-aware-em)).
- **One feed per net in a flat run**: the current that closes Kirchhoff's law enters at
  the net's first `*|P` port, or `--feed`. A net fed from several pads is fed at one of
  them; use the [hierarchical flow](chip-level.md), which holds several pads at one
  potential.
- **A device pin reaching the net at several places** cannot be split from one saved
  current in cc-grid (`EMIR-E417`).
- **Per-tap scalars** (a PSS harmonic result, `--scalars`) make rms and peak in-phase
  upper bounds of what the scalars imply, and give no dynamic IR; scalars from a
  truncated harmonic set (`EMIR-W404`) can themselves be low.
- **Self-consistency** (`--self-consistent`) feeds each pin its droop waveform and brings
  the device currents to RCC's, but it scores the branches on the R-only grid: the grid's
  own capacitance stays out of them, so minor branches can read a few percent off (up to
  11 % on the 1 632-node synthetic mesh, [Big blocks](big-blocks.md#self-consistency)).
  From an R-only DSPF the droop fed back has no capacitance either (`EMIR-W544`): the
  devices' peaks read low, up to 4.8 % with 20 fF of grid capacitance a node; from an RC
  DSPF the RC grid's droop is fed back, and with `--dynamic --em-basis decap-aware` the
  branches are scored through the RC grid too (without `--dynamic`, `EMIR-W545`: minor
  branches -19.5 % to +25.3 % at 20 fF a node, within 0.52 % with it; the default basis,
  worst, takes per segment the larger of the two and keeps the R-only grid's high
  readings). The decaps `cap_models` size are fed back with the grid; one it cannot size
  is left out (`EMIR-W547`): the devices then read low and its straps are not checked.
  Scored on the R-only grid or decap-aware, the answer is accurate, not conservative: a
  minor branch or a device can read a few percent low. Its cost was measured on small grids
  only, and grows with the transient's length (every PWL point a breakpoint). Spectre needs the
  per-pin split added to its netlist by hand, and several rails in one loop need Xyce,
  ngspice or HSPICE; Spectre and HSPICE are UNVERIFIED until `emir verify-sim --run`
  passes on site.
- **The mode** (rcc or cc-grid) is the person's choice; EMIR recommends from the net's
  size but never picks (`EMIR-E520`).
- **The hierarchical flow** back-annotates one level down; the decap lump an abstract
  records is not used at the top; a PSS harmonic result cannot be abstracted; async
  alignment is not an upper bound for related clocks ([Chip level](chip-level.md#limits)).

## The magic route

- **`emir extract --tool magic`** needs a magic built with the public `geoout` patch
  (not yet in a magic release); tested on sky130 only, with magic 8.3.515 (the patch's
  branch is on 8.3.684, which gives the test cells the same resistor network; their
  capacitances differ); a hierarchical layout is
  flattened first, which was checked on small test cells only; a net magic leaves whole
  has nothing to check (`EMIR-W645`); device models the process file does not name get
  positional pin names (`EMIR-W643`); a wire on a diagonal shape is checked at magic's
  tile extent, not its width across the current, and listed as not checked
  (`EMIR-W647`); its CC netlist holds each net's lumped capacitance, not the grid's
  ([Open-source flow](open-source-flow.md#limits-of-route-2)).

## Tools and formats

- **Text results only**: Spectre psfascii or nutascii, HSPICE ASCII (`.option post=2`),
  CSDF, Xyce and ngspice ASCII raw. Binary formats are detected and refused, never
  guessed at; one sweep table at a time (`--analysis sweep:N`).
- **The open simulators**: transient only (no PSS or harmonic balance); tested with one
  Xyce build (7.10, development) and ngspice 45; sky130 models tried, gf180mcu and IHP
  SG13G2 models not.
- **Licensed tools** (Spectre, HSPICE, Virtuoso, PVS, Quantus, OCEAN): none runs where
  EMIR is built, and nothing here claims one was verified anywhere. Each is verified
  on site by the local agent on the machine that uses it (`emir verify-sim` for
  Spectre and HSPICE, the headless SKILL self-test and the live test for Virtuoso,
  `emir check` on the DSPF for PVS and Quantus), and UNVERIFIED until then
  ([Cadence flow](cadence.md#unverified-until-checked-on-site-in-one-list),
  [Simulators](simulators.md)).
- **Simulators on your machine**: a run whose currents come from a simulator whose
  latest `emir verify-sim` record on this machine, for its version, format and
  `--sim-args`, is not a pass with this emir says so (`EMIR-W464`, `EMIR-W414` when that
  verification failed, `EMIR-W416` when the result does not say its version). The
  verification covers what its testbenches exercise (MOSFET terminals, a two-terminal
  element's pin 2, 0 V probes, branch currents), not BJT, JFET or diode terminals or a
  PDK's own models ([Simulators](simulators.md#on-site-verification)).
- **Calibre and StarRC**: notes only, UNVERIFIED until checked on site.
- **KLayout**: the marker databases were checked in batch mode and the Python package;
  the interactive Marker Browser was not exercised.
- **MCP clients**: the Python and TypeScript SDKs and Claude Code were run; Claude
  Desktop, Codex and Cursor configurations follow their published formats and were not.

## Platforms and scale

- **Linux** is what is built and tested (x86_64); macOS and Windows are not tested.
- **Python 3.10 or newer**: the full suite ran on Python 3.12, and a wheel-only install
  passed the self-test on Python 3.10.
- **Scale**: the speed figures come from synthetic meshes (up to 92 001 nodes and
  181 401 resistors for the streamed superposition, 40 032 nodes for the Xyce sweep) on
  one shared development machine. Real designs at that scale, large hierarchical tops,
  and magic's extraction of a big block have not been measured. The
  dense superposition is bounded by `--max-memory` (default 2G; a budget above 80 % of
  the physical memory is lowered to that, `EMIR-W550`) and streams beyond it;
  the viewer data keeps the worst 20 000 segments of a bigger run unless
  `--viewer-all`.
