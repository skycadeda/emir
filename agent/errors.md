# EMIR error-code catalog

Generated from `emir/errors.py` by `scripts/gen_errors_md.py`; do not edit by hand (a test fails when it is stale). `emir explain <code>` prints one entry; `emir explain --list` all of them.

Branch on the code, never on the message. `E` codes are errors (refusals, input-check FAILs), `W` warnings, `I` infos (the input checker's OK lines carry `I` codes too). Command templates fill `<name>` fields from the case at hand, and a `[...]` part is left out when its field is unknown; when the command cannot be filled, the `otherwise` one is used. A JSON result's `next_steps` hold only commands that run as printed (no step when none can be filled, or when the code has no command: its hint says what to do).

## Exit status

| status | meaning |
|---|---|
| 0 | done (check: the inputs are OK) |
| 1 | a FAIL verdict, an IR budget FAIL, or an EM basis asked for and not computed (em_basis.honoured false), with --fail-on-violation |
| 2 | bad input, or refused |
| 3 | a required external tool or Python package is missing |
| 70 | internal error (EX_SOFTWARE): a bug, or a failed self-test |
| 130 | interrupted (SIGINT, Ctrl-C) |
| 143 | terminated (SIGTERM) |

## 1xx files and I/O

### EMIR-E101 input file not found

- level: error, exit 2
- meaning: A path given on the command line does not exist.
- hint: Check the path; a relative path is taken from the current directory.
- command: `ls -l <path>`

### EMIR-E102 input file not readable

- level: error, exit 2
- meaning: The file exists but this user cannot read it.
- hint: Check the file's permissions.
- command: `ls -l <path>`

### EMIR-E103 required input not given

- level: error, exit 2
- meaning: A command needs an input file that was not given.
- hint: Pass the file; `emir next` lists what each step needs.
- command: `emir next[ --dspf <dspf>][ --process <process>]`

### EMIR-E104 not the expected kind of file

- level: error, exit 2
- meaning: The file was read but is not what the option expects (for example `emir report --from` given something other than a run summary).
- hint: Give the file the option names; `emir schema` prints each format.
- command: `emir schema <schema>`
- otherwise: `emir schema --list`

### EMIR-E105 cannot write an output

- level: error, exit 2
- meaning: Writing an output file or directory failed (permissions, a full disk, a file where a directory is needed).
- hint: Choose a writable output location.
- command: `ls -ld <path>`

### EMIR-E106 top cell not in the layout

- level: error, exit 2
- meaning: The GDS has no cell of the requested name.
- hint: Name a cell that is in the file, or leave the top cell to be found.
- command: `klayout <path>`

### EMIR-E107 I/O error

- level: error, exit 2
- meaning: The operating system refused a file operation (a directory where a file was expected, a broken link, and similar).
- hint: Read the message: it names the path and the reason.
- command: `ls -ld <path>`

### EMIR-E170 layout file unreadable

- level: error, exit 2
- meaning: The layout file (--gds) could not be read as GDS or OASIS.
- hint: Check that the file is a complete GDS (or OASIS) stream, not a text file or a partial copy.
- command: `ls -l <path>`

## 2xx DSPF and extracted layout

### EMIR-E201 DSPF cannot be parsed

- level: error, exit 2
- meaning: A line of the DSPF is not in the form the reader expects.
- hint: Check the line the message names; the DSPF must be text (DSPF 1.3).
- command: `head -n 40 <dspf>`

### EMIR-E202 no resistors in the DSPF

- level: error, exit 2
- meaning: The file holds no resistor at all: it is not an R or RC extraction.
- hint: Extract with resistance (R or RC), not C-only.
- command: `emir nets <dspf>`

### EMIR-E203 no wire widths in the DSPF

- level: error, exit 2
- meaning: No resistor carries a width ($W / $SI_W), so J = I/w cannot be evaluated on any wire.
- hint: Extract with geometry annotation ($W $L) enabled.
- command: `emir check <dspf>`

### EMIR-E204 net not in the DSPF

- level: error, exit 2
- meaning: The net named with --net is not in the DSPF.
- hint: Net names are case-sensitive; `emir nets` lists them.
- command: `emir nets <dspf>`

### EMIR-E205 net has no resistors

- level: error, exit 2
- meaning: The net exists but was extracted without resistance, so there is nothing to analyse.
- hint: Was the net extracted with R? Check the extraction settings.
- command: `emir nets <dspf>`

### EMIR-E206 node not on the net

- level: error, exit 2
- meaning: A node given by name (--ground, --feed, a --currents key, a feed or reference node) is not a node of this net.
- hint: Name a node on the net; `emir pins` lists its instance pins.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E207 net is in pieces

- level: error, exit 2
- meaning: Some nodes of the net are not connected to its reference node: the extracted net has an open, or the --feed is on another piece.
- hint: Check the layout for opens, and the --feed node.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-E208 net has no port and no instance pins

- level: error, exit 2
- meaning: No current can enter the net: it has neither a port nor an instance pin.
- hint: Check that this is the net you meant.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E209 no net to analyse

- level: error, exit 2
- meaning: No net in the DSPF has resistors.
- hint: Extract with resistance; `emir nets` shows what the file holds.
- command: `emir nets <dspf>`

### EMIR-E210 wires without a width

- level: error, exit 2
- meaning: Wire resistors (on a process-file layer) carry no width, so their current density cannot be evaluated; they would go unchecked.
- hint: Extract with geometry annotation ($W $L) enabled.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-E211 no instance pins on the net

- level: error, exit 2
- meaning: The net has no `*|I` instance-pin record, so there is nothing to save or to attach simulated currents to.
- hint: Drive the net with --currents, or check the extraction's pin records.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E212 no cell in the layout

- level: error, exit 2
- meaning: The GDS file holds no cell: it is empty, or not the layout that was meant.
- hint: Check that the file is the block's layout (open it in KLayout), and write the layout again from the layout editor if it is empty.
- command: `klayout <path>`

### EMIR-E216 no node coordinates

- level: error, exit 2
- meaning: No solved node has coordinates, so decap cannot be placed on the net.
- hint: Extract with node location annotation ($X $Y) enabled.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-E217 not a DSPF

- level: error, exit 2
- meaning: The file holds no DSPF record (`*|NET`, `*|P`, `*|I`, `*|S`) and no resistor, capacitor or device line: it is not a DSPF (a binary file, a process file, a netlist of another kind).
- hint: Give the extracted DSPF of the block (text DSPF 1.3).
- command: `head -n 20 <dspf>`

### EMIR-E243 name unsafe for a netlist or script

- level: error, exit 2
- meaning: The cell's name is written into magic's script, the DSPF and the netlists, and it holds a character they cannot carry as one word (white space, quotes, brackets, braces, `$`, `;`, a backslash). Such a name could also change what the script does.
- hint: Rename the cell in the layout.

### EMIR-E260 netlist is not text

- level: error, exit 2
- meaning: A file of the netlist (the testbench or a file it includes) is binary, not a SPICE netlist.
- hint: Give the SPICE netlist (text, SPICE syntax: Xyce, ngspice, a DSPF).
- command: `file <path>`

### EMIR-E261 block subcircuit not found or not unique

- level: error, exit 2
- meaning: The subcircuit named by --subckt is not defined in the netlist, or the block's subcircuit is instantiated more than once and the instance the DSPF belongs to cannot be chosen.
- hint: Pass --subckt with a .subckt the netlist defines, or --prefix with the instance path the DSPF was extracted for (e.g. XI0.).
- command: `grep -i -n -e '^ *.subckt' -e '^ *x' <path>`
- otherwise: `emir <command> --help`

### EMIR-E262 nothing to probe

- level: error, exit 2
- meaning: No terminal of a device of the chosen kinds (--types) is on the chosen nets in the chosen subcircuit (--subckt, default the DSPF's design).
- hint: Check the net names (SPICE compares them without case), --subckt and --types.
- command: `grep -i -n -e '^ *.subckt' -e '^ *x' <path>`

### EMIR-E263 element not in the testbench

- level: error, exit 2
- meaning: The print or save lines would name resistors or instances the testbench does not define: it is not the testbench of this DSPF (the RC netlist for rcc, a CC netlist for cc), or the block sits under another instance path.
- hint: Simulate the testbench of this DSPF; check --prefix and --mode.
- command: `grep -i -n -e '^ *.include' -e '^ *x' <path>`

### EMIR-E270 several top cells

- level: error, exit 2
- meaning: The layout has more than one top cell and --cell was not given, so the cell to extract is ambiguous.
- hint: Pass --cell with one of the top cells the message lists.

### EMIR-E280 LVS did not match

- level: error, exit 2
- meaning: `emir extract --run` stopped after LVS: the LVS report does not say MATCH (or there is no report), and an extraction on connectivity LVS has not verified simulates and means nothing.
- hint: Make the layout and the netlist match (the report says where they differ), then run the extraction again.
- command: `tail -n 60 <path>`

### EMIR-E281 extraction wrote no DSPF

- level: error, exit 2
- meaning: Quantus ran but the DSPF the command file names is not there.
- hint: Read the Quantus log: it names the error (a tech directory or corner that does not exist, an LVS database without connectivity data).
- command: `tail -n 40 <path>`

### EMIR-W201 no design name

- level: warning
- meaning: The DSPF has no `*|DESIGN` line; the viewer does not know the cell.
- hint: Pass --cell for the viewer.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W202 no node coordinates

- level: warning
- meaning: The DSPF has no `*|S` / `*|I` coordinates: results cannot be placed on the layout.
- hint: Extract with node location annotation ($X $Y) enabled.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W203 no instance-pin records

- level: warning
- meaning: The DSPF has no `*|I` records: simulated currents cannot be attached to any net; only --currents can drive it.
- hint: Extract with instance-pin records, or drive the net with --currents.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W204 resistors with no width or cut annotation

- level: warning
- meaning: Some resistors carry neither a width nor a cut count or area. A via among them is checked as one cut; a wire among them cannot be checked.
- hint: Give --process to have them classified by layer; extract with geometry and via annotation.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W205 net has no instance pins

- level: warning
- meaning: The net has a port but no instance pins: only --currents can drive it.
- hint: Drive it with --currents, or check the extraction's pin records.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-W206 resistors without a layer

- level: warning
- meaning: Some resistors carry no layer token and cannot be checked.
- hint: Re-extract with layer annotation enabled.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W207 vias checked as one cut

- level: warning
- meaning: Some vias carry neither a cut count nor a cut area; each is checked as ONE cut, which can only over-state the current per cut (conservative).
- hint: Extract with via annotation (cut count or area) enabled.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W208 net not fully connected

- level: warning
- meaning: Some nodes cannot be reached from the reference: the net is in pieces (an open, or a run forced past EMIR-E207). Branch currents in a piece that has current to deliver but no path to the reference are not solved (their ratios are not a number, a FAIL), and the IR covers the reference's piece only.
- hint: Check the layout for opens, and that the DSPF and the netlist match.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W260 DSPF pins without a probe

- level: warning
- meaning: Some instance pins of the DSPF on the probed nets got no probe: their currents will be missing from the result, and a run takes them as ZERO (EMIR-W403).
- hint: Check that the netlist's instance names are the DSPF's, and --subckt / --types.

### EMIR-W261 probed pins not in the DSPF

- level: warning
- meaning: Some probed terminals do not name an instance pin of the DSPF on the net; the run ignores their currents.
- hint: The netlist's instance or pin names differ from the DSPF's; --pins names a subcircuit's ports.

### EMIR-W262 pin names by position

- level: warning
- meaning: A probed subcircuit instance's .subckt is not in the netlist (a PDK device), so its terminals are named 1, 2, ... unless the DSPF settles the name.
- hint: Give --pins SUBCKT=p1,p2,... with its port names in order.

### EMIR-W263 block assumed at the top level

- level: warning
- meaning: The block's subcircuit is not instantiated in the netlist, so the simulator names written assume it is the top level of the simulation.
- hint: Give --prefix with the instance path the testbench uses (e.g. XI0.).

### EMIR-I201 capacitor devices

- level: info
- meaning: Capacitor devices with geometry but no value; sized from `cap_models` in the process file for dynamic IR.
- hint: Nothing to do unless dynamic IR needs them sized.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-I202 MOS devices

- level: info
- meaning: MOS devices are kept as possible decaps.
- hint: Nothing to do.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-I203 DSPF read

- level: info
- meaning: The DSPF parsed; the line summarises it.
- hint: Nothing to do.
- command: `emir nets <dspf>`

### EMIR-I204 net ready

- level: info
- meaning: The net exists, is connected and has a reference node.
- hint: Nothing to do.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-I261 DSPF annotations left out for Xyce

- level: info
- meaning: Xyce reads a DSPF's $KEY=value annotations as unknown parameters, so the deck's copies of those files leave them out (they are comments to the other simulators; the DSPF itself is unchanged).
- hint: Nothing to do.

### EMIR-I262 .spiceinit copied beside the deck

- level: info
- meaning: ngspice reads `.spiceinit` from the directory it runs in, and `emir simulate` runs the deck beside the result: the testbench's `.spiceinit` (an open PDK's compatibility settings) is copied there.
- hint: Nothing to do.

## 3xx process file and layer mapping

### EMIR-E301 process file is not valid YAML

- level: error, exit 2
- meaning: The process file cannot be parsed as YAML.
- hint: Fix the YAML syntax; `emir schema process` describes the format.
- command: `emir schema process`

### EMIR-E302 process file is not a mapping

- level: error, exit 2
- meaning: The process file's top level is not a mapping of keys.
- hint: See pdks/TEMPLATE.yaml (in a clone, or https://github.com/skycadeda/emir) or the bundled sample.
- command: `emir schema process`

### EMIR-E303 no layers in the process file

- level: error, exit 2
- meaning: The process file has no `layers:` section.
- hint: Add at least one metal layer.
- command: `emir schema process`

### EMIR-E304 vias section is not a mapping

- level: error, exit 2
- meaning: The process file's `vias:` is not a mapping of via names.
- hint: See pdks/TEMPLATE.yaml (in a clone, or https://github.com/skycadeda/emir) or the bundled sample.
- command: `emir schema process`

### EMIR-E305 process entry is not a mapping

- level: error, exit 2
- meaning: A layer or via entry is not a mapping of keys.
- hint: Write the entry as `<name>: {key: value, ...}`.
- command: `emir schema process`

### EMIR-E306 process entry misses a required key

- level: error, exit 2
- meaning: A layer needs `gds` and `sheet_res`; a via needs `gds`, `res`, `below` and `above`.
- hint: Add the key the message names.
- command: `emir schema process`

### EMIR-E307 gds is not a [layer, datatype] pair

- level: error, exit 2
- meaning: A `gds:` value is not a two-number list.
- hint: Write it as `gds: [<layer>, <datatype>]`.
- command: `emir schema process`

### EMIR-E308 value is not a non-negative number

- level: error, exit 2
- meaning: A resistance, width or limit is negative or not a number.
- hint: Give a number in the units `emir schema process` lists.
- command: `emir schema process`

### EMIR-E309 temperature coefficient is not a number

- level: error, exit 2
- meaning: A `tc1` or `tc2` is not a number.
- hint: Give the coefficient as a number (1/C, 1/C^2).
- command: `emir schema process`

### EMIR-E310 two rms limits on one layer

- level: error, exit 2
- meaning: A layer gives both `jmax_rms` (a fixed rms limit) and `jmax_rms_k` (sqrt(k * delta_T)).
- hint: Keep the one your PDK publishes.
- command: `emir schema process`

### EMIR-E311 warn_fraction out of range

- level: error, exit 2
- meaning: `warn_fraction` must be in (0, 1].
- hint: Use a fraction such as 0.8.
- command: `emir schema process`

### EMIR-E312 no em_tref

- level: error, exit 2
- meaning: The temperature (C) the avg EM limits are quoted at is required; there is no default, because a guessed one shifts every ratio.
- hint: Add `em_tref: <C>` at the top level (an ICT gives it).
- command: `emir schema process`

### EMIR-E313 em_tref is not a temperature

- level: error, exit 2
- meaning: `em_tref` is not a number.
- hint: Give the temperature in C.
- command: `emir schema process`

### EMIR-E314 jmax_factor keys are not temperatures

- level: error, exit 2
- meaning: The `jmax_factor` table's keys must be temperatures (C).
- hint: Write it as `jmax_factor: {<C>: <factor>, ...}`.
- command: `emir schema process`

### EMIR-E315 process file rejected

- level: error, exit 2
- meaning: The process file passed the key checks but could not be used.
- hint: Read the message; it names the entry and the value.
- command: `emir schema process`

### EMIR-E316 unknown key in a process entry

- level: error, exit 2
- meaning: A layer, via or cap_models entry holds a key the format does not have. It is most likely a typo, and a misspelt limit is a limit silently not checked.
- hint: Correct the key; `emir schema process` lists the keys of each entry.
- command: `emir schema process`

### EMIR-E317 cap_models entry not usable

- level: error, exit 2
- meaning: A `cap_models` entry holds a value the decap model cannot use: a value that is not a number, a negative capacitance (cox, cj, cjsw) or access resistance (esr_a, ron_sq), an entry whose capacitances are all 0 (its devices would be sized at 0 F, their decoupling deleted), a fit frequency (esr_fit_freq) that is not positive, a fit range that is not two numbers low to high from 0, or a fitted access resistance (esr_a + esr_w W + esr_l / L; esr_w and esr_l are signed) that is negative at a device's own geometry. A negative capacitance or resistance would reach the transient as a source of energy, and once ended in a misleading fallback (not at periodic steady state). The one check of cap_models: refused by `emir check` (and so by `emir run`), and by the dynamic solve under --no-check.
- hint: Correct the entry from the PDK's decap characterisation (pdks/TEMPLATE.yaml documents the keys); a model with cox only is an ideal capacitor, allowed and flagged (EMIR-W438).
- command: `emir schema process`

### EMIR-E320 required value not entered

- level: error, exit 2
- meaning: A value emir cannot run without is empty (null) in the process file: `em_tref`, a layer's `sheet_res`, or a via's `res`, `below` or `above`. emir never fills one in.
- hint: Ask the person for the value and the document, table or page it comes from, and have them type it (or confirm each one you transcribe from a document they give you); never fill one in. `emir process check` lists every empty value.
- command: `emir process check <process>[ --dspf <dspf>]`
- otherwise: `emir schema process`

### EMIR-E321 two temperature deratings

- level: error, exit 2
- meaning: The process file gives both `derating` (Black's equation) and a `jmax_factor` table; they are two forms of the same thing.
- hint: Keep the form your design manual prints and delete the other.
- command: `emir schema process`

### EMIR-E322 derating incomplete or unknown

- level: error, exit 2
- meaning: `derating` must be `{model: black, ea_eV: <eV>, n: <exponent>}` with both numbers positive; a half-entered or unknown form is refused, never guessed.
- hint: Give both ea_eV and n as the design manual prints them, or leave both empty (no derating).
- command: `emir schema process`

### EMIR-E323 wire not wider than its width loss

- level: error, exit 2
- meaning: A wire segment's drawn width W is at or below its layer's `width_loss_um` dW, so I_max = J x (W - dW) is zero or negative: the limit has no meaning there. Most often dW was typed in nm instead of um.
- hint: Check `width_loss_um` against the design manual (um), and the segment's width in the DSPF.
- command: `emir process check <process>[ --dspf <dspf>]`
- otherwise: `emir schema process`

### EMIR-E325 ICT not readable

- level: error, exit 2
- meaning: The ICT is not a text file emir can read: binary, compressed, encrypted (an encryption envelope or marker), or in an undeclared encoding. emir never tries to decrypt it or guess its content.
- hint: Enter the EM limits by hand from the design manual's EM tables: `emir process new` writes the fill-in file for the layers a DSPF uses.
- command: `emir process new --from-dspf <dspf>`
- otherwise: `emir process new --help`

### EMIR-E326 will not overwrite a process file

- level: error, exit 2
- meaning: `emir process new` writes a new file only: the path exists, and a process file may hold values a person typed.
- hint: Choose another -o, or move the file away yourself.
- command: `ls -l <path>`

### EMIR-E327 --interactive needs a person at a terminal

- level: error, exit 2
- meaning: `emir process new --interactive` asks a person for each value; stdin is not a terminal (a pipe, a script, an agent, the MCP server), so it refuses.
- hint: Agents never use it: write the fill-in file without --interactive and let the person type the values, or transcribe them from a document the person gives and have the person confirm each one.
- command: `emir process new --help`

### EMIR-E328 nothing to write a process file for

- level: error, exit 2
- meaning: `emir process new` needs the layers and vias to write: a DSPF whose resistors name their layers (--from-dspf), or the lists --layers and --vias.
- hint: Give --from-dspf, or --layers (and --vias).
- command: `emir process new --help`

### EMIR-E329 provenance or sources malformed

- level: error, exit 2
- meaning: `provenance` must be one of hand-entered, imported-ict, public-pdk-data, mixed (entries: the first three), and a `sources` map holds `<key>: <document, table or page>` strings.
- hint: Correct the value the message names.
- command: `emir schema process`

### EMIR-E331 a limit looks like a unit slip

- level: error, exit 2
- meaning: In the process file (whatever its `provenance:` says, or if it states none), a value that sets what the limits allow -- em_tref, temp_reference, the derating, the jmax_factor table, a jmax_* or imax_* limit -- is outside the range its quantity normally takes: most often a unit slip (K for C, uA for mA), which can make a failing wire read clean. The run is refused until the person confirms the value.
- hint: Ask the person to check the value and its unit against the design manual. A slip: they correct it. A real value: they list its path under the file's `unit_confirmed:` (it is then a warning, EMIR-W321). Never confirm one yourself.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-E332 temperature at or below absolute zero

- level: error, exit 2
- meaning: A temperature in C (em_tref, temp_reference, --em-temp) is at or below -273.15 C: most often a kelvin value with its sign or offset slipped.
- hint: Give the temperature in C.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-W301 rms limit below avg limit

- level: warning
- meaning: A layer's rms limit -- its fixed `jmax_rms`, or its self-heating limit sqrt(jmax_rms_k x delta-T) at the analysis --delta-t -- is below its `jmax_avg`, so the avg check can never bind (I_rms >= |I_avg|): rms governs. EMIR keeps the delta-T it was given; it never raises it.
- hint: Check both values, and the self-heating budget, against the PDK.
- command: `emir schema process`

### EMIR-W302 layer has no EM limit

- level: warning
- meaning: A layer has no jmax_* limit: EM is NOT checked on it.
- hint: Add jmax_avg (mA/um) at least, from your PDK.
- command: `emir schema process`

### EMIR-W303 via stack names an unknown layer

- level: warning
- meaning: A via's `below:` or `above:` is not a layer of the file; the via is still checked, only the stack description is off.
- hint: Name a layer from the `layers:` section.
- command: `emir schema process`

### EMIR-W304 via has no EM limit

- level: warning
- meaning: A via has no imax_* limit: EM is NOT checked on it.
- hint: Add imax_avg (mA/cut) at least, from your PDK.
- command: `emir schema process`

### EMIR-W305 temperature outside the derating table

- level: warning
- meaning: The analysis temperature (or em_tref) is outside the `jmax_factor` table; the derating is clamped, not extrapolated. Above the table's top that is not conservative.
- hint: Extend the table, or analyse inside its range.
- command: `emir schema process`

### EMIR-W306 illustrative process file

- level: warning
- meaning: The process file is marked ILLUSTRATIVE (the bundled sample, or an example's file): its limits are not real.
- hint: Point --process at your PDK's file for any result you intend to act on.
- command: `emir next --dspf <dspf>`

### EMIR-W307 DSPF layers not in the process file

- level: warning
- meaning: Some DSPF layers have no process entry: their segments are NOT checked.
- hint: Add them to the process file, or map them with `dspf_layers:`.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W308 cut area without vcwidth

- level: warning
- meaning: Vias give a cut area ($A) but no count, and the process file has no `vcwidth` for them: each is checked as ONE cut (conservative).
- hint: Add `vcwidth:` (the cut side, um) under those vias.
- command: `emir schema process`

### EMIR-W309 ICT lacks a temperature

- level: warning
- meaning: The ICT gives no `temp_reference` or `em_tref`; the file's value is kept, or must be set by hand.
- hint: Check the temperature against the PDK documentation.
- command: `emir schema process`

### EMIR-W310 ICT expression refused

- level: warning
- meaning: An ICT limit expression uses something other than numbers, w, delta_T, + - * / ^ and sqrt, or does not evaluate to a finite real number; the file's value, if any, is kept and flagged `estimate`.
- hint: Enter that limit by hand from the PDK documentation.
- command: `emir schema process`

### EMIR-W311 no ICT block for a layer or via

- level: warning
- meaning: No ICT conductor or via block matches the entry; it is left as it was.
- hint: Set `ict_name:` on the entry, or use --conductor-pattern.

### EMIR-W312 limit is an estimate

- level: warning
- meaning: The ICT gives no value for some EM limits of an entry; the entry is flagged `estimate` and every report says so.
- hint: Replace the estimate with the PDK's value.
- command: `emir schema process`

### EMIR-W313 via rms/peak not in the ICT

- level: warning
- meaning: The ICT gives these vias no rms or peak limit, so none is written: rms and peak are NOT checked on them. emir never estimates a limit (an older import's estimates, flagged `rms_peak_estimate`, are removed and are not applied).
- hint: Add the PDK's via rms/peak limits by hand if it publishes them.
- command: `emir schema process`

### EMIR-W314 unknown top-level key in the process file

- level: warning
- meaning: The process file has a top-level key the format does not have; it is ignored (a misspelt `warn_fraction` leaves the default in force).
- hint: Correct or remove the key; `emir schema process` lists the keys.
- command: `emir schema process`

### EMIR-W315 avg limits not derated above em_tref

- level: warning
- meaning: The analysis temperature is above `em_tref` and the process file has no `jmax_factor` table, so the avg limits are used as quoted at the cooler temperature. EM limits fall with temperature: the avg check is NOT conservative here (the run's not_checked says so too).
- hint: Add the PDK's derating as `jmax_factor`, or analyse at em_tref.
- command: `emir schema process`

### EMIR-W316 derating table not 1.0 at em_tref

- level: warning
- meaning: The `jmax_factor` table is not 1.0 at `em_tref`, the temperature the limits are quoted at: one of the two does not match the PDK.
- hint: Check `jmax_factor` and `em_tref` against the PDK.
- command: `emir schema process`

### EMIR-W320 value has no source

- level: warning
- meaning: The process file says its limits were entered by hand (`provenance: hand-entered` or `mixed`), and some values name no document, table or page they came from. Every report says so until they do.
- hint: Ask the person for each value's source (document, table or page) and record it under the entry's `sources:`; never write a source they did not give.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-W321 value outside its plausible range

- level: warning
- meaning: A value is outside the range its quantity normally takes in the unit emir reads it in: most often a unit slip (A for mA, mOhm for Ohm, K for C, nm for um, a current per line for a density per um).
- hint: Check the value and its unit against the design manual; the message names the unit emir expects and the likely slip.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-W322 EM limits out of order

- level: warning
- meaning: Where all are given, a limit should satisfy peak >= rms >= avg (I_peak >= I_rms >= |I_avg| for every waveform); this entry's do not, so one was likely typed in the wrong row or unit.
- hint: Check the entry's limits against the design manual.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-W323 upper metal more resistive than the one below

- level: warning
- meaning: Over a via, the upper layer's sheet resistance is higher than the lower one's. Upper metals are usually as thick or thicker, so this is often a typing or unit slip (a warning only: some stacks do this).
- hint: Check both sheet_res values against the design manual.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-W324 via stacking inferred, not confirmed

- level: warning
- meaning: `emir process new` inferred a via's `below`/`above` from the DSPF (the wires its resistors join) and marked it `inferred ... check`; nobody has confirmed it against the PDK's layer stack yet.
- hint: Ask the person to confirm the stacking from the design manual and replace the `inferred` source with that document; never confirm it yourself.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-W325 source for a key the entry does not have

- level: warning
- meaning: A `sources:` map names a key its entry does not hold (a typo, or a value since deleted): that source documents nothing.
- hint: Name the key exactly as the entry writes it.
- command: `emir schema process`

### EMIR-W326 Black's derating below em_tref

- level: warning
- meaning: The analysis temperature is below em_tref, where Black's equation would raise the avg limits. Most design manuals define the derating above em_tref only, so the limits are applied as quoted (x1) unless the file says `derating: below_tref: extrapolate`; with that, the warning says how far they were raised.
- hint: Analyse at em_tref or above, or confirm in the design manual that the derating holds below em_tref before setting `below_tref: extrapolate`.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-I301 limits flagged estimate

- level: info
- meaning: Some process entries carry limits flagged `estimate`.
- hint: Replace them with the PDK's values when you have them.
- command: `emir schema process`

### EMIR-I302 no temperature derating

- level: info
- meaning: No `jmax_factor` table: avg limits are not derated with temperature.
- hint: Nothing to do if the limits are quoted at the analysis temperature.
- command: `emir schema process`

### EMIR-I304 process file read

- level: info
- meaning: The process file passed every check; the line summarises it.
- hint: Nothing to do.
- command: `emir schema process`

### EMIR-I305 layer matched by a suffix rule

- level: info
- meaning: A DSPF layer name was matched to a process layer by dropping a `_` suffix, not exactly; check it carries the right limits.
- hint: Map it explicitly with `dspf_layers:` to silence this.

### EMIR-I306 every DSPF layer maps

- level: info
- meaning: Every DSPF layer maps onto the process file.
- hint: Nothing to do.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-I307 via cuts from the cut area

- level: info
- meaning: Via cut counts were derived from the cut area ($A) and `vcwidth`.
- hint: Nothing to do.

### EMIR-I308 extraction and analysis temperatures differ

- level: info
- meaning: The DSPF was extracted at another temperature than the EM analysis; R is scaled by the layer TCR.
- hint: Nothing to do.

### EMIR-I320 optional values left empty

- level: info
- meaning: Some optional values of the process file are empty (null): what each one would check or scale is not used (a limit left empty is NOT checked; an empty tc1 means no temperature scaling of that layer).
- hint: Nothing to do if the PDK's documents do not give them.
- command: `emir process check <process>`
- otherwise: `emir schema process`

### EMIR-I321 process file does not state its provenance

- level: info
- meaning: The process file has no `provenance:` (hand-entered, imported-ict, public-pdk-data or mixed), so reports cannot say where its limits came from.
- hint: Add `provenance:` (and `sources:` per value) when you know.
- command: `emir schema process`

### EMIR-I322 avg limits derated by Black's equation

- level: info
- meaning: The avg limits are derated to the analysis temperature by Black's equation, J(T)/J(Tref) = exp[(Ea/(n k)) (1/T - 1/Tref)] with T in kelvin, from the process file's `derating`.
- hint: Nothing to do.

### EMIR-I323 wire limits reduced by a width loss

- level: info
- meaning: Some layers give `width_loss_um`: their wire limits are I_max = J x (W - dW) with W the drawn width, applied to avg, rms and peak alike.
- hint: Nothing to do.

## 4xx currents and simulation results

### EMIR-E401 --currents is not a JSON object

- level: error, exit 2
- meaning: --currents must be a JSON object of node: amps, given inline or as the path of a file that holds it.
- hint: Write it inline as '{"VDD": 0.004, "MP0:s": -0.004}', or put that object in a file and give the file's path (--currents c.json; any layout of the JSON). `emir schema currents` prints the format.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E402 --currents holds NaN or Inf

- level: error, exit 2
- meaning: JSON accepts NaN and Infinity, and a NaN sum passes every balance test.
- hint: Give every node a finite current in amps.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E403 --currents needs one net

- level: error, exit 2
- meaning: --currents drives exactly one net.
- hint: Give --net.
- command: `emir nets <dspf>`

### EMIR-E404 current on a node not on the net

- level: error, exit 2
- meaning: Some --currents nodes are not on the net.
- hint: Use the net's node names; `emir pins` lists the instance pins.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E405 all currents are zero

- level: error, exit 2
- meaning: Every --currents value is zero: there is nothing to analyse.
- hint: Give the real injected currents.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E406 currents do not balance

- level: error, exit 2
- meaning: The currents into a net must sum to zero (KCL): what is injected must leave.
- hint: Make the injections sum to zero.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E407 nothing drives the net

- level: error, exit 2
- meaning: No --currents, --cc-sim or --netlist was given.
- hint: Give a simulation result (--cc-sim), a netlist to simulate (--netlist) or DC currents (--currents); `emir next` says which.
- command: `emir next --dspf <dspf>[ --process <process>]`

### EMIR-E408 binary PSF

- level: error, exit 2
- meaning: The simulation result is binary PSF (Spectre's default psfbin, or psfxl), which is not read (it is undocumented; guessing at it gives plausible, wrong currents).
- hint: Convert it with Cadence's `psf` utility, which writes PSF ASCII unless -b is given: `psf -i <dir>/tran.tran -o <dir>/tran.psfascii` (a shell command; the HSPICE manuals document it as <ADE_install>/platform/tools/dfII/bin/psf), then pass the converted file with --cc-sim. Or re-run Spectre with `-format psfascii`.
- command: `psf -i <path> -o <path>.psfascii`

### EMIR-E409 binary SPICE raw

- level: error, exit 2
- meaning: The simulation result is binary SPICE raw.
- hint: Write it as ASCII: ngspice `set filetype=ascii`, Xyce `-a`, Spectre `-format nutascii`.

### EMIR-E410 simulation result cannot be read

- level: error, exit 2
- meaning: The result is not PSF ASCII, nutascii, HSPICE ASCII or CSDF, or one of its lines does not parse.
- hint: Write the result as text: PSF ASCII or nutascii (Spectre, Xyce, ngspice), HSPICE ASCII (`.option post=2`) or CSDF.
- command: `head -n 40 <path>`

### EMIR-E411 simulation result truncated

- level: error, exit 2
- meaning: The result announces more points than it holds: the simulation did not finish, or the file was cut.
- hint: Re-run the simulation to completion.
- command: `tail -n 5 <path>`

### EMIR-E412 no result file in the directory

- level: error, exit 2
- meaning: The result directory holds no file (matching --analysis).
- hint: Name the analysis with --analysis, or point at the file itself.
- command: `ls <path>`

### EMIR-E414 rcc needs a time-domain result

- level: error, exit 2
- meaning: RCC mode reads branch currents over time; a harmonic (PSS fd) result carries no time base.
- hint: Use the transient or PSS time-domain output, or --mode cc-grid.

### EMIR-E415 branch currents not saved

- level: error, exit 2
- meaning: RCC mode needs every branch current of the net, and some (or all) are missing from the result.
- hint: Add the save lines `emir save-lines --mode rcc` prints and re-simulate.
- command: `emir save-lines <dspf>[ --net <net>] --mode rcc`

### EMIR-E416 instance-pin currents not saved

- level: error, exit 2
- meaning: None of the net's instance-pin currents are in the result.
- hint: Add the save lines `emir save-lines --mode cc-grid` prints and re-simulate.
- command: `emir save-lines <dspf>[ --net <net>] --mode cc-grid`

### EMIR-E417 instance pin with several connections

- level: error, exit 2
- meaning: An instance pin reaches the net at several DSPF connections but the simulation saves ONE current per pin; how it divides cannot be guessed. A black-boxed block whose supply pin has several shapes is the usual case.
- hint: A black-boxed block: screen the net hierarchically -- `emir abstract` the block (each pin shape a *|P port), then `emir run` the top with --abstract FILE@INST and --place for the placement. A small net: rcc mode (every branch current saved) measures how the current divides. `emir next` plans either route.
- command: `emir next --dspf <dspf>[ --net <net>][ --process <process>]`

### EMIR-E418 analysis does not fit in memory

- level: error, exit 2
- meaning: The superposition (or another array) would need more memory than is available.
- hint: Use --scalars (per-tap scalars, in-phase bounds), or one --net at a time.
- command: `emir run <args> --scalars`

### EMIR-E419 period longer than the result

- level: error, exit 2
- meaning: The --period does not fit in the simulated time.
- hint: Simulate longer, or give the right --period.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-E420 empty time window

- level: error, exit 2
- meaning: The time window selected for the metrics holds no time.
- hint: Check --period and the simulated time span.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-E421 tap file unusable

- level: error, exit 2
- meaning: A tap file (CSV or JSON) has no rows, or a row without a usable i_avg.
- hint: Write one row per tap with name, x_um, y_um, layer, i_avg.
- command: `head -n 5 <path>`

### EMIR-E422 dynamic IR needs waveforms

- level: error, exit 2
- meaning: Dynamic IR integrates tap waveforms; the scalar tap path has none.
- hint: Drop --scalars, or skip dynamic IR.

### EMIR-E423 no capacitance for dynamic IR

- level: error, exit 2
- meaning: There is no capacitance anywhere on the net, so dynamic IR is the static solve.
- hint: Use an RC DSPF or give a decap list.

### EMIR-E424 names sidecar unusable

- level: error, exit 2
- meaning: The `<result>.names` file beside a simulation result maps the simulator's signal names (I(V1), i(@m1[id]) ...) to the DSPF's inst:pin names. It is not valid JSON, or not of the form {"map": {NAME: ["inst:pin", sign]}}.
- hint: Fix or remove the sidecar; `emir schema names` prints its format.
- command: `emir schema names`

### EMIR-E425 pin name is not a terminal of the device

- level: error, exit 2
- meaning: A DSPF instance pin names a terminal its device does not have: the netlist says the element is a MOSFET, BJT, JFET, resistor, capacitor or diode, and the pin is none of its terminal names (d g s b; c b e s; 1 2, p n, a k ...), their long forms (drain, bulk, substrate, collector, anode ...) or positions (1 to 4). Its current is not taken from another terminal by a guess.
- hint: Check the DSPF's *|I records against the netlist's element (is it really a subcircuit? then `--probe` reads its pins through 0 V probes).
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E426 currents that are not numbers in the result

- level: error, exit 2
- meaning: Currents the run needs hold NaN or Inf in the simulation result (a diverged or failed simulation, or a value the simulator could not print), so every EM ratio and IR drop computed from them would be "not a number".
- hint: Re-run the simulation to a finite result (its log names the failure); never replace the values by zero.

### EMIR-E427 signal names collide in the result

- level: error, exit 2
- meaning: The result names two or more columns alike (a name written twice, or HSPICE format 9007 names, cut to 16 characters, that become equal or match several names of the names sidecar), so which column is which current cannot be told.
- hint: Simulate with `.option post_version=2001` (full names, double precision) and save each current once.
- command: `emir save-lines --help`

### EMIR-E430 EM basis needs --dynamic

- level: error, exit 2
- meaning: --em-basis decap-aware or worst reads the decap-aware branch currents, which come from the dynamic solve of cc-grid (--dynamic); the run does not have it. Refused before the input check, so --force and --no-check do not run past it. When --dynamic cannot run with the other options (--abstract, --currents, --scalars) no command is given: the message names what blocks it.
- hint: Add --dynamic (cc-grid, a transient result), or drop --em-basis: the verdict is then the static one.
- command: `emir check <args> --dynamic`

### EMIR-E460 pin current not printable

- level: error, exit 2
- meaning: A cc-mode current is the current into a subcircuit instance's pin (or into a pin that is not one of a primitive device's terminals), which neither Xyce nor ngspice prints.
- hint: Put a 0 V probe in series with each pin: `emir simulate --probe`, or `emir probe` and then --probes.
- command: `emir probe --help`

### EMIR-E461 not a probe sidecar

- level: error, exit 2
- meaning: The --probes file is a names sidecar without the `probes` list that `emir probe --names-out` writes.
- hint: Pass the --names-out file of `emir probe`.
- command: `emir probe --help`

### EMIR-E462 no transient analysis

- level: error, exit 2
- meaning: The testbench (and the files it includes) has no .tran statement at its top level, and the currents over time are needed (`emir simulate`).
- hint: Add a .tran over whole periods of the block's activity (for the operating point, a short .tran with the sources held at DC).
- command: `grep -i -n tran <path>`

### EMIR-E463 binary HSPICE result

- level: error, exit 2
- meaning: The result is HSPICE's binary waveform file (.trN written with `.option post=1`, the default when post is given without a value). EMIR reads the ASCII form.
- hint: Re-run HSPICE with `.option post=2 post_version=2001` (ASCII, double precision); `emir save-lines --sim hspice` prints that line with the probes.
- command: `emir save-lines --help`

### EMIR-E464 HSPICE ASCII result cannot be read

- level: error, exit 2
- meaning: The file starts like an HSPICE ASCII result (.trN, `.option post=2`) but its header or its data does not follow the layout: a header that never ends ($&%#), counts that are not numbers, a value of the wrong width, a table without its end marker (a truncated file), or rows that do not fill.
- hint: Re-run the simulation to completion with `.option post=2 post_version=2001`, and pass the file HSPICE wrote, unedited.
- command: `head -n 8 <path>`

### EMIR-E465 several sweep tables in the result

- level: error, exit 2
- meaning: The result holds one table per sweep value (a .tran with a sweep, or a CSDF block per step), and EM is checked on one waveform at a time.
- hint: Pick one table with --analysis sweep:N (1-based; the message lists them), or simulate the case of interest alone.

### EMIR-E466 CSDF result cannot be read

- level: error, exit 2
- meaning: The file starts like a CSDF result (#H) but does not follow the layout: a block without #;, a point whose value count differs from #C's or from the names, a value that is not a number.
- hint: Re-run the simulation to completion and pass the file it wrote, unedited.
- command: `head -n 20 <path>`

### EMIR-E467 not a transient result

- level: error, exit 2
- meaning: The HSPICE or CSDF result is swept over frequency or a DC variable, not time; EM needs the currents over time.
- hint: Pass the transient result (.trN, or the .tran's CSDF file).

### EMIR-W401 fewer than two periods simulated

- level: warning
- meaning: The result spans fewer than two periods: the window cannot settle.
- hint: Simulate longer.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W402 few time points

- level: warning
- meaning: The result has few time points: rms and peak will be coarse.
- hint: Save more points (a smaller maximum step).
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W403 some instance pins not saved

- level: warning
- meaning: Some instance-pin currents are missing from the result; their current is taken as ZERO.
- hint: Add the missing pins from `emir save-lines --mode cc` and re-simulate.
- command: `emir save-lines <dspf>[ --net <net>] --mode cc`

### EMIR-W404 harmonics truncated

- level: warning
- meaning: A large share of the AC energy sits in the top harmonics: rms is truncated LOW.
- hint: Raise the harmonic count, or read the PSS time-domain output.

### EMIR-W405 currents inconsistent with the DSPF

- level: warning
- meaning: The RCC loop residual is large: the simulated branch currents do not fit this DSPF (wrong netlist/DSPF pairing, a missing save, a sign).
- hint: Check that the simulated netlist was extracted with this DSPF.

### EMIR-W406 self-consistency diverging

- level: warning
- meaning: The self-consistent loop's guard stopped it: its residual (the droop the currents produce against the droop they were fed) grew by more than 10 % on two passes in a row, or the droop to feed back reached half the supply (where no net's supply is known, a ground net alone: three times the open loop's droop). The run reports pass 0, the open-loop cc-grid answer, instead of a pass that may be wrong; `self_consistency.stopped` says which. (A later pass whose simulation runs past its time limit or fails is EMIR-W543.)
- hint: Report the open-loop result as open loop, and which way it reads as the message says: on the measured meshes the open loop read high (conservative), but a loop diverges when the load's current rises as its supply falls (a constant-power load, a regulator), and then pass 1 drew more current than pass 0 and the open loop reads LOW, not conservative. Look at the passes in the summary (`self_consistency`: each one's residual, `total_avg_A`, `worst_ratio`).

### EMIR-W407 self-consistency did not converge

- level: warning
- meaning: The self-consistent loop stopped at --max-passes before its residual fell below its tolerance (max(--tol-rel x the largest droop, --tol-mV)) or the answer settled (EMIR-I520); the run reports pass 0 (the open loop), not the last pass: a pass between the open loop and the fixed point can be far from both. The summary's `self_consistency.passes` show each pass's residual.
- hint: Report the result as open loop; raising --max-passes helps when the residual was still falling.
- command: `emir run <args> --max-passes 16`

### EMIR-W408 decap access R extrapolated

- level: warning
- meaning: Some MOS decaps are outside the geometry range their access-R model was fitted on.
- hint: Re-fit the model over this range if it matters.

### EMIR-W409 MOS decaps excluded

- level: warning
- meaning: Some MOS decaps cannot be sized: their `cap_models` entry is missing or has no cox (a hand-entered file leaves it null until the person fills it). They are excluded, which OVERSTATES droop, and in decap-aware EM their straps carry nothing, so they are NOT checked: the run lists them under not_checked (decap_straps_not_covered), and a basis that needs decap-aware EM (worst, decap-aware) is not honoured there (em_basis.not_computed: decaps-not-sized; --fail-on-violation exits 1). `emir process check --dspf` and `emir check --dynamic` warn before the run.
- hint: Ask the person for each model's cox (F/m^2) from the PDK's decap characterisation, with its source; never estimate it.
- command: `emir schema process`

### EMIR-W410 capacitor devices excluded

- level: warning
- meaning: Some capacitor devices could not be sized (no `cap_models` entry, or no cj/cjsw, or no geometry) and are excluded, which OVERSTATES droop; in decap-aware EM their straps carry nothing and are NOT checked (as for EMIR-W409).
- hint: Ask the person for the model's cj and cjsw from the PDK, with their source.
- command: `emir schema process`

### EMIR-W411 resampling lost peak current

- level: warning
- meaning: Resampling the tap waveforms to uniform steps lost part of the peak: dynamic droop is UNDERSTATED. On a periodic window (cc-grid --dynamic) the steps are refined where the waveforms need it, and the warning says that a waveform feature (an edge, a spike's tip) still kept less than 99 % of its own height on them: decap-aware EM is then not computed (EMIR-W434).
- hint: Raise the step count (--dyn-steps; the run's next step gives one).

### EMIR-W412 decap time constants near the step

- level: warning
- meaning: Some decap lumps have R*C within 10x of the time step: neither resolved nor negligible; their droop contribution is approximate.
- hint: Raise the step count.

### EMIR-W413 averaging window not settled

- level: warning
- meaning: The averaging window could not be made long or settled enough; the metrics may still carry start-up transient.
- hint: Simulate longer, or give --period.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W414 simulator route failed its verification here

- level: warning
- meaning: The latest `emir verify-sim` record on this machine for the simulator, version, output format and simulator arguments that wrote the run's currents FAILED: the route from that simulator into EMIR gave wrong numbers here. A later failure cancels an earlier pass.
- hint: Do not rely on these numbers; tell the person. Read the failing checks of the record (verified.json in the state directory), fix the cause (a wrapper script, an option file, the simulator arguments) and verify again.
- command: `emir verify-sim --sim <sim>`

### EMIR-W415 verification records ignored

- level: warning
- meaning: The records file (verified.json in $EMIR_STATE_DIR, else ~/.emir) holds records that do not follow the `emir.verify_sim/1` record (a required field missing, `passed` not a JSON boolean or disagreeing with the checks, a passing record without its simulator's version, `covers` naming a kind no passing check proves): each counts as no record. Or the file itself cannot be read (cut short, not JSON): every record in it counts as none; runs leave it as it is (`records_problem`), and `emir verify-sim` moves it aside to verified.json.bad-<date> before it writes a new one, never overwriting it. A file this user may not read (its permissions) is left as it is by `emir verify-sim` too (EMIR-W467).
- hint: Do not edit verified.json by hand; run `emir verify-sim` again for the simulator.
- command: `emir verify-sim --sim <sim>`

### EMIR-W416 simulator version of the result unknown

- level: warning
- meaning: Neither the result's header nor a names sidecar written by `emir simulate` says which version of the simulator wrote it, so no `emir verify-sim` record (one per version) can be matched to it: the run is not verified (`simulator_verification.reason` `version unknown`).
- hint: Simulate through `emir simulate` (it records the simulator's version in the names sidecar), then run again; or tell the person that this run's simulator route is not verified.
- command: `emir simulate --help`

### EMIR-W428 decaps not covered without --dynamic

- level: warning
- meaning: A cc-grid run without --dynamic over a net with decap devices (MOS decaps, capacitor devices): its verdict is the static one (decap-free), whose superposition draws every edge of the load from the feed and gives a decap's straps nothing, so they are not checked. The summary's net carries em_basis.fallback no-dynamic; `emir next --run-summary` plans the same run with --dynamic as the person's step. Decap-aware EM, and the bases built on it (decap-aware, worst), need --dynamic.
- hint: Relay that the decaps' straps are not covered; --dynamic (the person's choice: it costs a transient solve over whole periods) gives decap-aware EM, which covers them.

### EMIR-W429 negligible waveform features ignored

- level: warning
- meaning: Waveform features (an edge, a spike's tip) narrower than the finest sub-step the dynamic solve can take were left unresolved, and each carries below 1e-6 (dynir.NEGLIGIBLE) of its pin's charge and of its pin's integral of i^2 per period, beyond the linear interpolation between the grid's points: the droop, the averages and a branch's rms^2 move by no more than that share of the pin's, so they do not keep decap-aware EM from being computed (EMIR-W434). Their own peak, for less than the finest step, is not in the decap-aware currents: a decap's strap near the pin may carry up to the feature's height for that long.
- hint: Relay it with the peaks; if the peak of so narrow a feature matters, ask the person whether it is real (a simulator's glitch is common).

### EMIR-W430 decap-aware EM not computed: no feed

- level: warning
- meaning: The net has no port, so there is no feed for the decaps' current to be drawn from: decap-aware EM is not computed, and the verdict uses decap-free there. Under a basis that needs decap-aware EM (worst or decap-aware) the net's verdict is then not on the basis asked for: the summary's em_basis.honoured is false and --fail-on-violation exits 1 (as for W431-W434).
- hint: Give the DSPF's port (*|P) if the net has one; relay that the decaps' straps are not covered on this net.

### EMIR-W431 decap-aware EM not computed: the reference is not the feed

- level: warning
- meaning: --ground puts the solve's reference somewhere other than the feed, so the decaps' charge would be drawn from the wrong node: decap-aware EM is not computed and the verdict uses decap-free on that net (not the basis asked for: em_basis.honoured false, --fail-on-violation exits 1). The run's next steps give the same run without --ground.
- hint: Drop --ground (the reference is then the feed; --feed moves both).
- command: `emir run <args>`

### EMIR-W432 decap-aware EM not computed: not at periodic steady state

- level: warning
- meaning: The RC state did not repeat within the pass limit (a decap time constant long against the window), so the branch currents still hold part of the start-up: decap-aware EM is not used, and the verdict uses decap-free on that net (not the basis asked for: em_basis.honoured false, --fail-on-violation exits 1). The droop says the same (EMIR-W451).
- hint: Simulate more periods (a longer window).

### EMIR-W433 decap-aware EM not computed: the window is not whole periods

- level: warning
- meaning: No period was given or detected, so the window is not a whole number of periods and the periodic steady state the decaps settle to belongs to another signal: decap-aware EM is not computed, the verdict uses decap-free there (not the basis asked for: em_basis.honoured false, --fail-on-violation exits 1).
- hint: Give the block's period with --period (or simulate long enough that it is detected).

### EMIR-W434 decap-aware EM not computed: the step is too coarse

- level: warning
- meaning: The dynamic transient's steps, refined where the waveforms need it, could not carry the decap-aware currents: a feature of the waveforms (an edge, a spike's tip) keeps less than 99 % of its own height on them (the finest sub-step the net's factorisations allow, or the refinement's budget, reached), or the feed's current reads above the load's own on the same steps (the trapezoidal rule rang on a decap faster than the step). Decap-aware EM is not computed and the verdict uses decap-free there (not the basis asked for: em_basis.honoured false, --fail-on-violation exits 1). A feature whose charge and energy are negligible never counts (EMIR-W429). The run's next steps give the same run with the --dyn-steps that resolves it (never fewer, so it does not repeat itself), up to 2000000 (decap.DYN_STEPS_MAX); past that none is given and the message says so.
- hint: Re-run with more steps (--dyn-steps N, as the next step gives it); when none is given, ask the person whether the feature is real.
- command: `emir run <args> --dyn-steps <n>`

### EMIR-W435 decap-aware rms corrected for the step

- level: warning
- meaning: The dynamic transient's steps lost more than 2 % of a pin's AC rms, measured like with like (the pin's waveform on the steps against on the simulator's own samples, both exact for a current linear between points): the decap-aware rms of every segment is corrected up by the worst pin's factor, and on segments near the warn fraction where it is above 10 % the larger of decap-free and decap-aware stands in. The steps resolve every waveform feature when they can, so this is rare; the run's next steps give the same run with twice the steps.
- hint: For a tighter number, re-run with more steps (--dyn-steps).
- command: `emir run <args> --dyn-steps <n>`

### EMIR-W436 segments the decap-free verdict does not cover

- level: warning
- meaning: The verdict uses decap-free (--em-basis decap-free), and on some segments the decap-aware current is higher and at or above the warn fraction: a decap's straps and the mesh around it carry the decap's charging current, which the decap-free verdict does not see. The worst basis covers them.
- hint: Show the person both bases (emir report); the basis is their choice.
- command: `emir next --run-summary <summary>`

### EMIR-W437 EM verdict on a basis the person chose

- level: warning
- meaning: The EM verdict uses a basis given with --em-basis, not the default (worst); decap-free and decap-aware each read lower than worst on some segments. A warning so that every report shows it: relay the default's verdict beside it.
- hint: Confirm the person chose it; relay both verdicts (emir report).

### EMIR-W438 decap-aware EM leans on the decap model

- level: warning
- meaning: The decap-aware numbers rest on an assumption the run flags: coupling caps are more than 25 % of the capacitance counted (their far plate an AC ground); capacitor devices sized through the `default` cap model; MOS decaps with the closed-form access resistance (R_channel/6, known to read it several-fold high: their straps' current reads low); MOS decaps with no access resistance at all (an ideal capacitor: the feed path reads low and the straps high); an ESR fitted far from the window's frequency; or decap lumps folded onto their nodes (access R*C below a tenth of the finest step: their ESR dropped, named with their share of the decap capacitance).
- hint: Give cap_models entries for the devices' own models, a fitted esr_a/esr_w/esr_l near the activity's frequency; read the decap-aware numbers with that in mind.

### EMIR-W439 decaps between two resistive rails

- level: warning
- meaning: Some decaps (MOS decaps, capacitor devices, or valued capacitors to another rail) sit between this net and another net that has resistance in the DSPF. Each net is solved alone, with the other plate an ideal AC ground; in the circuit both rails move, so the current through these decaps, their straps and the mesh near them reads LOW (by up to (R_this + R_other) / R_this below the decap's corner frequency; in examples/13_decap_aware's two-rail case the straps read low and the feed path high: results/decap.md).
- hint: Read those straps' decap-aware currents as low; check them on the other net's run too.

### EMIR-W450 dynamic IR not computed

- level: warning
- meaning: --dynamic was asked for, but this net's dynamic droop could not be integrated: the result has no time-domain waveforms (a harmonic PSS result), the net is in pieces, or there is no capacitance on it (an R-only extraction carries none). The static results stand.
- hint: For capacitance, run cc-grid on an RC extraction of the rails (the grid solve uses its R; --dynamic uses its C), or let the process file's `cap_models` size the decap devices; for waveforms, use a transient or the PSS time-domain output.
- command: `emir schema process`

### EMIR-W451 dynamic IR not at periodic steady state

- level: warning
- meaning: The cc-grid dynamic transient integrates the window repeatedly until the RC state repeats; it stopped at its pass limit first (a decap time constant long against the window), so the droop still holds part of the start-up from the DC solution.
- hint: Simulate more periods (a longer window), or read the dynamic droop as approximate.

### EMIR-W452 dense superposition over the memory budget

- level: warning
- meaning: --superposition dense was asked, but the dense transfer matrix (branches x taps x 8 bytes) exceeds --max-memory, so the run streamed in time chunks instead (the same answer; the summary's `superposition` says which path ran).
- hint: Raise --max-memory (within the machine's memory) to force the dense path, or leave --superposition auto.

### EMIR-W460 names sidecar not written

- level: warning
- meaning: save-lines for Xyce or ngspice without --names-out: the result's current names will not match the DSPF until a <result>.names sidecar maps them.
- hint: Re-run with --names-out <result>.names (or write the JSON document's `names` there as {"map": ...}).

### EMIR-W461 currents missing from the result

- level: warning
- meaning: The simulation ran, but some currents the print or save lines asked for are not in the result.
- hint: Read the simulator's log for warnings about those names; check --prefix.

### EMIR-W462 sidecar not beside the result

- level: warning
- meaning: The --names-out file is not `<result>.names` for the --raw result, and emir looks for the sidecar only there.
- hint: Write it as <result>.names, or move it there before the run.

### EMIR-W463 probed netlist has no analysis

- level: warning
- meaning: The netlist `emir probe` rewrote (and the files it includes) has no .tran: it is a block or a library, not a testbench, so `emir simulate` cannot run it as it is (EMIR-E462), and no simulate step is suggested.
- hint: Probe the testbench that includes the block instead (its included files are probed too), or include the probed copy in a testbench with a .tran.
- command: `emir probe --help`

### EMIR-W464 simulator route not verified on this machine

- level: warning
- meaning: The run's currents come from a simulator result, and this machine holds no passing `emir verify-sim` record that counts for that simulator, version, output format and simulator arguments (`simulator_verification` in the run summary; its `reason` says why: `no record`, `stale` -- a pass from another emir minor version --, or `other host`), or none that covers every kind of current the run read (`pin kinds not covered`: `partly verified` when some kinds are covered; `pin_kinds` lists them: diode, BJT and JFET terminals are never covered), or the result is in an output format emir verify-sim never writes (`format not covered`: CSDF, Spectre's nutascii). The numbers may still be right; nothing on this machine has shown that the simulator's names, signs and file format reach EMIR as EMIR reads them. A failed latest record is EMIR-W414, an unknown version EMIR-W416.
- hint: Run `emir verify-sim --sim <simulator>` once on this machine (a licensed simulator: ask the person first; it plans the run without --run), then re-run. When the summary gives no `command` (a kind or a format verify-sim does not cover), no verification helps: tell the person which currents stay unverified (the message names them), or simulate through `emir simulate`, whose output format and probes verify-sim covers.
- command: `emir verify-sim --sim <sim>`

### EMIR-W465 terminal currents do not close KCL

- level: warning
- meaning: A device's saved terminal currents (HSPICE i1..i4) sum to zero with neither sign of the source (emitter) current, so that sign could not be confirmed from the result and the documented one (out of the source) was used.
- hint: Probe the device's terminals instead (`emir probe`, a 0 V source in series: its current sign is the source's, documented), and check that every terminal current of the device was saved.
- command: `emir probe --help`

### EMIR-W466 HSPICE names cut to 16 characters

- level: warning
- meaning: The HSPICE result is format 9007, which cuts signal names to 16 characters: longer names were matched to the names sidecar by their first 16 characters where that is unique.
- hint: Simulate with `.option post_version=2001` (full names, double precision).

### EMIR-W467 verification record not kept

- level: warning
- meaning: `emir verify-sim` finished but could not write its record to the EMIR state directory ($EMIR_STATE_DIR, else ~/.emir), or the records file there cannot be read by this user (its permissions: another user's file in a shared directory, which emir leaves as it is), so later runs cannot find it.
- hint: Make the directory writable and the records file readable, or set $EMIR_STATE_DIR to a directory of this user's own, and run verify-sim again.

### EMIR-I401 DC currents balance

- level: info
- meaning: The --currents injections are on the net and balance.
- hint: Nothing to do.

### EMIR-I402 simulation result read

- level: info
- meaning: The simulation result was read; the line summarises it.
- hint: Nothing to do.

### EMIR-I404 every branch current saved

- level: info
- meaning: Every branch current of the net is in the result (rcc).
- hint: Nothing to do.

### EMIR-I405 every instance-pin current saved

- level: info
- meaning: Every instance-pin current of the net is in the result (cc-grid).
- hint: Nothing to do.

### EMIR-I430 decap-aware EM computed

- level: info
- meaning: The dynamic solve gave the net's decap-aware EM: the step, the passes, the periodic-average check, the largest step correction, the capacitance counted, and every basis's verdict.
- hint: Nothing to do; the summary's `dynamic_em` holds the numbers.

### EMIR-I431 coupling to signal nets left out of decap-aware EM

- level: info
- meaning: Coupling capacitance from the net to a signal net is not counted as decap in the decap-aware solve (in the CC simulation its current flows from the ideal rail, through no instance pin); the droop keeps it as an AC ground, as before. The decap-aware EM then has its own solve.
- hint: Nothing to do; `dynamic_em.capacitance.coupling_signal_excluded_F` says how much.

### EMIR-I432 EM basis does not apply in rcc

- level: info
- meaning: --em-basis with --mode rcc: the RC simulation drives the decaps and every branch current is measured, so the basis does not apply (`used: measured`).
- hint: Nothing to do.

### EMIR-I450 superposition streamed

- level: info
- meaning: The exact waveform superposition ran in time chunks (one multi-column back-substitution per chunk of samples) instead of building the dense transfer matrix: the matrix (branches x taps x 8 bytes) would exceed --max-memory, or the result has fewer than twice as many time samples as taps, where streaming is the cheaper path. The answer is the same; only the memory and the solve count differ.
- hint: Nothing to do. --max-memory bounds the memory; --superposition dense or stream forces a path.

### EMIR-I463 simulator route verified on this machine

- level: info
- meaning: The latest `emir verify-sim` record on this machine for the simulator, version, output format and simulator arguments that wrote the run's currents passed, with this emir minor version, and covers every kind of current the run read.
- hint: Nothing to do.

### EMIR-I464 single-precision HSPICE result

- level: info
- meaning: The HSPICE ASCII result is format 9601 or 9007: five significant digits per value (a KCL residual near 3e-5 of the current on a synthetic case).
- hint: For more digits, simulate with `.option post_version=2001`.

### EMIR-I465 terminal current sign set from KCL

- level: info
- meaning: HSPICE's manuals draw a MOSFET's I3 (a BJT's emitter current) out of the device, and whether a run reports it that way was not confirmed; EMIR took the sign that makes the device's saved terminal currents sum to zero.
- hint: Nothing to do; the message names the devices and the sign used.

### EMIR-I466 simulator of the result not identified

- level: info
- meaning: Neither the result's header nor its names sidecar says which simulator wrote it (`simulator_verification` is `unknown simulator`).
- hint: Pass --sim to `emir run` (the simulator that wrote --cc-sim) so the on-site verification can be looked up.

## 5xx options and command-line usage

### EMIR-E501 input check failed

- level: error, exit 2
- meaning: `emir run` checks its inputs first and refuses to run on a FAIL; the error's `details.findings` lists them, each with its own code.
- hint: Fix each FAIL finding (or pass --force to run anyway, not recommended).
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-E502 --self-consistent needs one net

- level: error, exit 2
- meaning: The self-consistent loop feeds the droop of the nets it is given back as their pins' supplies: name them with --net (one net; or with Xyce, ngspice or HSPICE several, comma-separated, closed in one loop). `all` (or no --net) is refused, and so are several nets with Spectre, whose split is the person's, one net at a time.
- hint: Give --net with the rail(s) to close (--net VDD, or --net VDD,VSS with Xyce, ngspice or HSPICE).
- command: `emir nets <dspf>`

### EMIR-E503 --self-consistent needs --vdd

- level: error, exit 2
- meaning: The loop reports the net's voltage in the testbench (0 for a ground net), and it was neither given nor readable from the testbench (Spectre netlists are not read; with Xyce, ngspice and HSPICE the rail must be ground or one independent DC source to ground at the top level).
- hint: Ask the person for the voltage of this net in the testbench, then give --vdd (several nets: one per net, --vdd VDD=1.2,VSS=0; a net left out is read from the testbench).

### EMIR-E504 --self-consistent needs --netlist

- level: error, exit 2
- meaning: The loop re-simulates, so it needs the netlist, not a finished result.
- hint: Give --netlist.

### EMIR-E505 --self-consistent with rcc

- level: error, exit 2
- meaning: Self-consistency is a cc-grid correction; RCC simulates the R network already.
- hint: Drop --self-consistent or --mode rcc.

### EMIR-E506 --mode rcc needs a simulation

- level: error, exit 2
- meaning: RCC mode reads branch currents from a simulation result.
- hint: Give --cc-sim (a result) or --netlist.
- command: `emir save-lines <dspf>[ --net <net>] --mode rcc`

### EMIR-E507 command-line usage error

- level: error, exit 2
- meaning: The command line does not parse: an unknown or abbreviated option, a missing value, a bad choice.
- hint: Read the usage line; `emir <command> --help` lists the options.
- command: `emir <command> --help`
- otherwise: `emir --help`

### EMIR-E508 unknown command

- level: error, exit 2
- meaning: The first argument is not an emir command.
- hint: `emir --help` lists the commands.
- command: `emir --help`

### EMIR-E509 nothing analysed

- level: error, exit 2
- meaning: `emir run` was given nothing to analyse: no process file, or nothing to drive the net.
- hint: Give --process and one of --currents, --cc-sim, --netlist.
- command: `emir next --dspf <dspf>[ --process <process>]`

### EMIR-E510 --split-stub needs --net

- level: error, exit 2
- meaning: The rail-split fragment is written for one net.
- hint: Give --net.
- command: `emir nets <dspf>`

### EMIR-E511 unknown error code

- level: error, exit 2
- meaning: `emir explain` was given a code that is not in the registry.
- hint: `emir explain --list` lists every code.
- command: `emir explain --list`

### EMIR-E512 unknown schema

- level: error, exit 2
- meaning: `emir schema` was given a name that is not a schema.
- hint: `emir schema --list` lists them.
- command: `emir schema --list`

### EMIR-E513 invalid option value

- level: error, exit 2
- meaning: A value is not one the option accepts.
- hint: Read the message; it lists the accepted values.
- command: `emir <command> --help`
- otherwise: `emir --help`

### EMIR-E520 mode not chosen

- level: error, exit 2
- meaning: The command reads, plans or runs a simulation, and its mode -- rcc or cc-grid -- was not given. The mode is the person's choice: emir never picks one (`--mode auto` is gone). A run with DC --currents needs no mode.
- hint: Ask the person, then pass --mode. rcc: simulate the RC-extracted netlist and read every branch current -- exact, but the RC transient is slow and converges poorly on big blocks. cc-grid: simulate the devices on ideal rails and superpose their pin currents through the R-only grid -- fast and scales, open loop (avg and IR close to rcc at small droop, rms and peak read high); `--mode cc-grid --self-consistent` feeds the droop back and re-simulates. `emir next` lists the routes with their measured cost and accuracy.
- command: `emir next[ --dspf <dspf>][ --process <process>][ --cc-sim <cc_sim>][ --netlist <netlist>][ --net <net>][ <plan_args>]`

### EMIR-E521 the mode cannot serve this command

- level: error, exit 2
- meaning: The command works in one mode only, and the other was asked for: a block abstract (`emir abstract`) is built from the instance-pin currents of a CC simulation superposed through the block's R-only grid, so it is cc-grid only.
- hint: Pass --mode cc-grid (with a CC simulation of the block), or screen the block itself with `emir run --mode rcc`.

### EMIR-E522 the rail split matched no instance pin

- level: error, exit 2
- meaning: The self-consistent loop gives each instance pin on the net its own supply node and source; in the testbench it found device terminals on the net, but none of them is an instance pin of the DSPF (different instance names, or the wrong subcircuit), so no current could be fed back.
- hint: Check that the testbench simulates this DSPF's CC netlist, with the DSPF's instance names; give --save-prefix with the block's instance path.
- command: `emir probe <path> -o emir_out/probe/probed.cir --dspf <dspf>[ --net <net>]`
- otherwise: `emir pins <dspf> --net <net>`

### EMIR-E523 --vdd does not match the testbench

- level: error, exit 2
- meaning: --vdd, the net's voltage in the testbench, differs (by more than 1 mV or 1 %) from the DC source the testbench drives the net with (or ground): the wrong net, the wrong testbench, or a mistyped voltage. Nothing was simulated.
- hint: Check the net and the testbench with the person; give --vdd as the testbench drives it, or leave --vdd out to take the testbench's value.

### EMIR-E524 the netlist does not hold the rail split

- level: error, exit 2
- meaning: The Spectre route of the self-consistent loop needs the per-tap rail split in the CC netlist: a `vsource type=pwl file=` per pin reading the droop file emir rewrites every pass, and every pin of the net reconnected to its split node (`emir run --split-stub` prints the lines). Without it Spectre simulates the open loop on every pass and the loop would call that converged. Raised before Spectre runs when the netlist's text names no pin's file or a split node feeds nothing, and after a pass that fed a droop and moved no pin current.
- hint: Add the `--split-stub` lines to the CC netlist, reconnect each pin as its comments list, and run again.
- command: `emir run <dspf> --net <net> --split-stub`

### EMIR-E525 --self-consistent with --cc-sim or --currents

- level: error, exit 2
- meaning: The self-consistent loop simulates --netlist on every pass and reads only those results: a --cc-sim result or --currents given with it would be ignored.
- hint: Drop --cc-sim / --currents (the loop simulates), or drop --self-consistent to screen that result open loop.

### EMIR-E526 will not overwrite a file emir did not write

- level: error, exit 2
- meaning: The self-consistent loop rewrites its droop file on every pass (the include of the split's sources; for Spectre the manifest droop.scs and each pin's .pwl file), and each pass writes its result (pass<k>.raw, Spectre's result directory, pass<k>.tr0) with its .log and .names, and the simulator's own outputs beside it (sc.raw.log; HSPICE's .lis, .st0 ...); the SPICE simulators' loop also writes its split testbench and the split copies of its includes (split.<sim>.cir, <include>.split_<sim>.sp). Where it writes them (emir_out/selfconsist/<net>/, or the --droop-file's directory) there is such a file that emir did not write (a droop file without emir's header, at the default place or the one --droop-file names: --droop-file naming the testbench, the DSPF or any file of the person's is refused before anything is written; a result without emir's stamp <result>.emir beside it, or a split file not listed in emir's stamp split.<sim>.cir.emir).
- hint: Move that file, or point the loop elsewhere with --droop-file (its directory takes the results too).
- command: `ls -l <path>`

### EMIR-E540 a terminal on the net is not a DSPF pin

- level: error, exit 2
- meaning: The waveform self-consistent loop moves every terminal on the net with its own droop waveform. In the testbench a device terminal on the net is no instance pin of the DSPF (and no other terminal of a device whose pin is): it would stay on the ideal rail while the rest of the net droops, and the charge it gives back or draws (an idle device is a decap) would be missing from the loop. Nothing was simulated.
- hint: Check that the testbench simulates this DSPF's CC netlist with the DSPF's instance names (give --save-prefix with the block's instance path); a device that is really outside the extracted block belongs on another rail in the testbench. `emir probe` shows which terminal matched which pin.
- command: `emir probe <path> -o emir_out/probe/probed.cir --dspf <dspf>[ --net <net>]`
- otherwise: `emir pins <dspf> --net <net>`

### EMIR-E541 --vdd does not name the nets

- level: error, exit 2
- meaning: --self-consistent was given several nets (--net A,B) and one --vdd, which cannot say which net it is; or a per-net --vdd (NET=V,...) names a net --net does not close.
- hint: Give one voltage per net (--vdd VDD=1.2,VSS=0; a net left out is read from the testbench's source for it), or leave --vdd out to read every rail from the testbench.

### EMIR-E550 --dynamic needs simulated waveforms

- level: error, exit 2
- meaning: --dynamic integrates the tap current waveforms through the RC grid; DC --currents and --scalars carry no time base.
- hint: Give --cc-sim with a transient (or PSS time-domain) result and drop --scalars, or drop --dynamic.

### EMIR-E580 extraction setting missing

- level: error, exit 2
- meaning: `emir extract --run` needs the LVS rules, the Quantus technology directory, the corner and the temperature, and one is not set.
- hint: Give it with its option, or in the site file (`pvs_lvs_rules`, `qrc_tech_dir`, `qrc_corner`, `extract_temperature`); the dry run lists what is missing.
- command: `emir extract --gds <gds> --cdl <cdl> --cell <cell> --kind <kind>`

### EMIR-E590 the person's go-ahead is needed

- level: error, exit 2
- meaning: An MCP tool call would start a licensed tool (Spectre), run long, rewrite a file in place, loosen the EM verdict (`emir_run` with an `em_basis` other than the default, or `dynamic` with `ground`, with `dyn_steps` below 2000 or without `period`, each of which can leave decap-aware EM not computed), or run a command the MCP server has not classified, and the call did not carry confirm=true. Nothing was run; `details.reasons` says why.
- hint: Ask the person. Only if they agree, make the same call again with confirm=true.

### EMIR-E591 not runnable over MCP

- level: error, exit 2
- meaning: `emir_cli` was given a command line that is not an emir command the MCP server runs: another program (the server has no shell), or `emir mcp` itself.
- hint: Run that command in a shell, or ask the person to; emir commands go through the emir_* tools.

### EMIR-E592 tool arguments do not match the tool

- level: error, exit 2
- meaning: An MCP tool call named an argument the tool does not have (a misspelt option, which would otherwise be dropped without a word), or left out one the tool requires. Nothing was run; `details` lists the unknown and the missing arguments and the tool's parameters.
- hint: Use the parameter names of the tool's input schema (the client's tool list; `emir mcp --list` prints them in a shell), and give every required one.

### EMIR-W520 large droop on an open-loop cc-grid run

- level: warning
- meaning: The net's IR drop is above about 2 % of its supply (--vdd), and the run was open loop: its devices drew their current from the undropped rail, so the currents and the IR drop read high by an amount that grows with the droop.
- hint: Report the result as open loop, and ask the person about the self-consistent run (`--mode cc-grid --self-consistent`): it re-simulates with each pin's supply at the rail plus its own droop waveform until the droop and the currents agree. On the synthetic inverter meshes it took the governing branch to +0.01 % (avg), +0.34 % (rms) and +0.78 % (peak) of rcc at 4.1 % droop, where the open loop read +1.0 %, +34 % and +70 % (examples/06_big_block/results/selfconsist_waveform.md); what it leaves is the grid's own capacitance, which grows with the grid (and is not fed back from an R-only DSPF: EMIR-W544).
- command: `emir next --dspf <dspf>[ --process <process>][ --net <net>] --mode cc-grid --self-consistent[ --vdd <vdd>]`

### EMIR-W540 the testbench's reltol is above 1e-4

- level: warning
- meaning: The self-consistent loop simulates the testbench with its own options, and its relative tolerance is above 1e-4 (or not set: the simulator's default is 1e-3 in Xyce, ngspice and Spectre). On a synthetic mesh of examples/06_big_block (results/selfconsist_waveform.csv, k32_n20_reltol3), Xyce at 1e-3 moved the open loop's governing avg, rms and peak against rcc by 0.29 to 0.41 points and the converged loop's by 0.09 to 0.45: more than the loop's own residual error.
- hint: Set the relative tolerance to 1e-4 or below in the testbench (Xyce: `.options timeint reltol=1e-4`; ngspice and HSPICE: `.options reltol=1e-4`; Spectre: reltol=1e-4 in its options statement), and simulate the RCC reference the same way when comparing.

### EMIR-W541 the split's KCL does not close

- level: warning
- meaning: Every pass checks that the split pins' currents add up to the current of the testbench's rail source (the window average within 1e-5, every instant within 1e-4 of its peak). They do not: part of the rail's current reaches it some other way -- a terminal the split does not move (an element type it does not probe), a load, capacitor or block of the testbench on the same rail. That part stays on the ideal rail and its droop is not fed back.
- hint: Look at the pass's `kcl` entry in the summary (`self_consistency.passes`); move the other loads off the block's rail in the testbench, or extract them with the block.

### EMIR-W542 droop waveform compressed more coarsely

- level: warning
- meaning: The droop fed back on a pass needed more PWL points per pin than the loop allows (max(1 500, 0.3 x pass 0's time points); every point is a simulator breakpoint) at its compression tolerance (0.3 x the loop's tolerance, at least 0.05 mV), so it was compressed again, coarser, until it fits: the pass's `pwl_tol_mV` says how coarse. Accuracy is lost: the droop fed is less exact. Within the loop's own tolerance the loop goes on as before; coarser than it, the loop can no longer settle (EMIR-I520's metric rule), only converge by its residual, which is measured against the PWL as written and so holds that error, else it ends at --max-passes and reports pass 0 (EMIR-W407). Measured: a 40 ns transient of a synthetic mesh (examples/06_big_block/results/selfconsist_waveform.md, k32_n10_long) was fed at about 0.67 x its tolerance with 7 400 points a pin, and settled within 0.06 % of rcc on the governing branch.
- hint: Look at the passes' `pwl_points` and `pwl_tol_mV`. Every period the transient holds adds points: a CC testbench simulated for fewer periods (the window the run chooses is its settled tail) needs fewer.

### EMIR-W543 a self-consistent pass's simulation did not finish

- level: warning
- meaning: A pass after pass 0 ran past its time limit (max(20 x pass 0's simulation time, 60 s, --sim-timeout)) or its simulation failed. A diverging droop makes a simulator crawl or fail, but a long block's pass can also be just slow: a pass took 2.7 to 4.2 times the open loop's simulation time over 4 ns on one net of the synthetic meshes at reltol 1e-4 (5.1 to 5.5 with both rails), and about 9.5 times over 40 ns, every PWL point a breakpoint (examples/06_big_block/results/selfconsist_waveform.md). The loop stops and the run reports pass 0, the open loop (`self_consistency.stopped`: `time limit` or `simulation failed`); the passes before are in the summary.
- hint: Report the result as open loop. When the passes' residuals were falling, the person may give the passes longer (--sim-timeout) or look at the failed pass's simulator log (the message names it).

### EMIR-W544 the droop fed back has no grid capacitance

- level: warning
- meaning: The DSPF has no capacitance on the net (an R-only extraction), so the loop feeds back the R-only grid's droop. A real grid's capacitance gives charge locally, so the droop at the pins is shallower than the R-only grid's: the loop's devices then draw too little at their peaks, and the device contacts and the branches that capacitance would feed read low, more so the more capacitance the grid has. Measured on a synthetic 384-node mesh at 4.1 % droop with 20 fF per grid node (examples/06_big_block/results/selfconsist_waveform.md, hd16b_c20): device peaks -1.1 % to -4.8 % (median -4.2 %), minor-branch peaks down to -20 %, the governing branch conservative; the open loop read those devices +15 % to +30 % high. Fed back from the RC DSPF of the same mesh (hd16b_c20rc) the device peaks were within 0.1 % (-0.08 % to +0.01 %). The decaps the process file sizes are fed back with the grid either way (EMIR-W547 when some cannot be sized).
- hint: Give the RC extraction of the rail (the loop then feeds back the RC grid's droop, each capacitor to ground), or report the device and minor-branch peaks as possibly low.

### EMIR-W545 the loop's branches are scored on the R-only grid

- level: warning
- meaning: The DSPF has capacitance on the net, so the self-consistent loop fed back the RC grid's droop and the devices' currents agree with it; but without --dynamic (or with --em-basis decap-free) the branch currents are scored on the R-only grid, so the current the grid's own capacitors carry is missing from every branch, and a branch that capacitance feeds (a minor branch) reads low or high. The open loop's high device currents hid this; the loop removes them. Measured on a synthetic 384-node mesh at 4.1 % droop with 20 fF per grid node (examples/06_big_block/results/selfconsist_waveform.md, hd16b_c20rc): scored on the R-only grid, minor-branch peaks -19.5 % to +25.3 % against RCC (the open loop's -7.1 % to +90.1 %) and the governing branch's peak +3.9 %; scored through the RC grid, as --dynamic's decap-aware EM scores them, within 0.52 % and +0.25 %.
- hint: Report the minor branches as possibly low, and offer the person the same run with --dynamic and the block's period (--period: decap-aware EM needs a window of whole periods, EMIR-W433; it re-simulates the loop): its default EM basis, worst, takes per segment the larger of the R-only and the RC grid's current (conservative: it keeps the R-only grid's high readings); decap-aware takes the RC grid's alone (within 0.52 % there). The basis is the person's choice. Not raised when the loop converged at pass 0 (nothing was fed back) or the pins' currents are constant (no capacitor carries any).

### EMIR-W546 the transient is not saved from 0

- level: warning
- meaning: The testbench's .tran saves from a start time above 0 (its tstart): the loop knows no currents before it, and every pin's PWL source holds its first value from 0, so every pass runs the start with the droop of tstart held, not its own: the state the window starts from is not the loop's, and the IR and the currents can read low (non-conservative).
- hint: Save the transient from 0 (no tstart on the .tran line) for the self-consistent run.

### EMIR-W547 decaps left out of the loop's feedback

- level: warning
- meaning: The self-consistent loop feeds back the droop of the grid with its capacitance: the DSPF's capacitors, the capacitor devices and the MOS decaps the process file's cap_models size (each MOS decap behind its access resistance, as --dynamic models it). Some decap devices on the net cannot be sized (no cap_models entry for their model, no geometry, or a fit negative at their geometry), so they are left out: the droop fed back is deeper than the real one, so the devices draw too little and read LOW (not conservative); the governing branch, without the decaps' local charge, reads high; and the decaps' straps are not checked (decap-aware EM gives them nothing, EMIR-W409): neither accurate nor conservative. Measured with all 8 decaps of a synthetic 4 % droop mesh left out (5 pF behind 2 ohm on 0.5 ohm straps; examples/13_decap_aware/results/decap_loop.md): the devices' peaks -5.87 % to -1.00 % against RCC, the governing branch +13.90 % rms and +31.94 % peak on the decap-aware basis; with them sized and fed back, every branch class, and the straps' rms and peak, within 1.2 % and the devices' peaks -0.04 % to +0.02 %.
- hint: Add the decaps' cap_models entries (cox, and the access resistance fit, from the PDK's decap characterisation, with its source), check them with `emir process check --dspf`, and run the loop again; until then report the devices as possibly low, the governing branch as possibly high and the decaps' straps as not checked.
- command: `emir process check <process> --dspf <dspf>`

### EMIR-W550 --max-memory above the physical memory

- level: warning
- meaning: --max-memory is more than this machine's physical memory allows, and a dense matrix that size would be killed by the operating system (exit 137, with no error document): the budget was lowered to 80 % of the physical memory.
- hint: Give --max-memory within the machine's memory (`free -g` shows it).

### EMIR-I520 self-consistency converged

- level: info
- meaning: The self-consistent loop stopped because the droop the pass's currents produce is within its tolerance (max(--tol-rel x the largest droop, --tol-mV)) of the droop the pass simulated, and the governing branch's avg, rms and peak moved less than 0.5 % from the pass before (`stop_rule: residual`); or it settled (`stopped: settled`, `stop_rule: metrics`): on two passes in a row the governing branch, every item the run reports and every device's own avg, rms and peak current each moved less than 0.5 %, with the residual under 5 % of the droop and not growing. A settled loop's residual stays above its tolerance (the message gives both); the answer held still. The run reports that pass, and every pass is in the summary (`at_pass_limit` when it was the last one --max-passes allowed).
- hint: Nothing to do.

### EMIR-I521 the testbench's rail

- level: info
- meaning: --vdd of a self-consistent run (Xyce, ngspice, HSPICE), against the DC source the testbench drives the net with: they match, or --vdd was not given and the testbench's value is used, or the testbench's rail could not be read and --vdd is used as given. The loop does not depend on it: each pin's source sits in series with the testbench's own rail.
- hint: Nothing to do.

## 6xx external tools

### EMIR-E601 Spectre not found

- level: error, exit 3
- meaning: --netlist needs Spectre, and none was found.
- hint: Set $SPECTRE_BIN, `spectre:` in the site file, or put it on $PATH; or pass a finished result with --cc-sim.
- command: `emir info`

### EMIR-E602 simulation failed

- level: error, exit 2
- meaning: The simulator ran and exited with an error.
- hint: Read the simulator's log; the message quotes its error lines.
- command: `tail -n 40 <path>`

### EMIR-E603 Python package missing

- level: error, exit 3
- meaning: A Python package this step needs is not installed.
- hint: The message names the command that installs it (from a clone: `pip install -e '<clone>[all]'`; otherwise the package itself, since emir is not on PyPI); `emir info` lists what is there.
- command: `emir info`

### EMIR-E604 unsupported simulator

- level: error, exit 2
- meaning: `emir run --netlist` without --self-consistent drives Spectre only (results from any simulator can be read if written as PSF ASCII, nutascii, HSPICE ASCII or CSDF; `emir simulate` drives HSPICE, Xyce and ngspice; the self-consistent loop, `--mode cc-grid --self-consistent`, drives all four).
- hint: Simulate with `emir simulate` (or yourself) and pass the result with --cc-sim.
- command: `emir info`

### EMIR-E605 simulation timed out

- level: error, exit 2
- meaning: The simulator ran longer than the time allowed and was stopped.
- hint: Run the simulation yourself (a big block can take hours) and pass its result with --cc-sim.
- command: `tail -n 40 <path>`

### EMIR-E640 magic extracted nothing

- level: error, exit 2
- meaning: magic ran but wrote no extracted netlist (or one with no .subckt for the cell): the technology does not match the layout, the cell is empty, or magic crashed (an old magic, EMIR-W640).
- hint: Read magic's log (magic.log in magic/ under the --out directory); check --magicrc and --cell; `emir info` shows which magic is found and warns about an old one.
- command: `tail -n 40 <path>`
- otherwise: `emir info`

### EMIR-E641 no magic technology

- level: error, exit 2
- meaning: magic needs the PDK's startup file (.magicrc), and none was given or found from $PDK_ROOT and the process file's `pdk:` name.
- hint: Give --magicrc, or set PDK_ROOT to the directory that holds the PDK.
- command: `emir info`

### EMIR-E642 magic cannot be started

- level: error, exit 3
- meaning: The magic chosen (it answered the geometry probe) could not be started for the extraction.
- hint: Check the path and its permissions; `emir info` shows where magic is found.
- command: `emir info`

### EMIR-E643 no magic that writes resistor geometry

- level: error, exit 3
- meaning: `emir extract --tool magic` needs a magic built with the `geoout` patch: it writes each resistor's layer, width, length and place (`extresist geoout on`, <cell>.res.geo), which EMIR's per-segment check needs. No magic tried accepted the option (`details.tried` lists each, where it came from and what it said). The patch is public: branch extresist-geoout of https://github.com/codervijay/magic, offered upstream as https://github.com/RTimothyEdwards/magic/pull/562.
- hint: Build it: `git clone -b extresist-geoout https://github.com/codervijay/magic`, then `./configure --prefix=$HOME/magic-geo && make && make install` in it, and name it with --magic, $EMIR_MAGIC or the process file's `magic: binary:`. Without it, extract a DSPF with another tool (playbook 06).
- command: `emir info`

### EMIR-E644 magic wrote no resistor geometry

- level: error, exit 2
- meaning: magic extracted the cell but wrote no `.res.geo` (no net was split into resistors), or output EMIR cannot read (a .res.geo line, a value in its netlists).
- hint: Read magic's log in magic/ under the --out directory: a cell with no devices has no net for extresist to split; a lower --tolerance splits more nets.
- command: `tail -n 40 <path>`

### EMIR-E645 magic timed out

- level: error, exit 2
- meaning: magic's extraction ran longer than --timeout allows and was stopped.
- hint: Give a longer --timeout (a big block's extraction with every resistor kept can take long), or extract a smaller cell.
- command: `tail -n 40 <path>`

### EMIR-E646 will not overwrite a file EMIR did not write (magic route)

- level: error, exit 2
- meaning: `emir extract --tool magic` writes <cell>.dspf and <cell>.cc.spice into --out, and magic runs in `magic/` under it, a directory of EMIR's own (a `.emir-magic-work` file marks it) where EMIR replaces magic's files on every run. There is a <cell>.dspf or <cell>.cc.spice that EMIR did not write (it lacks EMIR's header), or a `magic/` directory that holds files and is not EMIR's: EMIR never overwrites them.
- hint: Move that file or directory, or give another --out (the default is emir_out/extract_<cell>).
- command: `ls -la <path>`

### EMIR-E647 magic's netlist is a hierarchy

- level: error, exit 2
- meaning: magic's netlists for the cell define several subcircuits: the layout was extracted cell by cell, and the netlists would mix the subcells' own nets with the top's (a simulator rejects them: the same capacitor names twice, a subcircuit with no definition). EMIR reads a flat cell. `emir extract --tool magic` flattens the layout first (magic's `flatten -dotoplabels`), so this comes from magic outputs made by another script.
- hint: Extract the layout again with `emir extract --tool magic`.
- command: `tail -n 40 <path>`

### EMIR-E648 the process file names a program that is not a magic

- level: error, exit 2
- meaning: A process file's `magic: binary:` may name only a magic program on $PATH, by its bare name starting with `magic` (`magic`, `magic-geo`). EMIR runs the program it names, and a process file comes with a PDK or a layout, not from the person's own setup, so a path or another program there is refused.
- hint: Remove it from the process file, and name the magic with --magic, $EMIR_MAGIC or the site file's `magic:` (`emir info` shows the site file).
- command: `emir info`

### EMIR-E660 open simulator not found

- level: error, exit 3
- meaning: `emir simulate` needs Xyce or ngspice, and the one asked for was not found.
- hint: Install it (both are open source), or point $XYCE_BIN / $NGSPICE_BIN or the site file at it; `emir info` shows what is found. --sim takes the other.
- command: `emir info`

### EMIR-E661 simulator wrote no result

- level: error, exit 2
- meaning: The simulator exited without an error but wrote no result file.
- hint: Check that the testbench runs a .tran analysis, and read the log.
- command: `tail -n 40 <path>`

### EMIR-E662 licensed simulator not found

- level: error, exit 3
- meaning: `emir simulate --run` or `emir verify-sim --run` needs Spectre or HSPICE, and the one asked for was not found.
- hint: Point $SPECTRE_BIN / $HSPICE_BIN, the site file (`spectre:`, `hspice:`) or $PATH at it; `emir info` shows what is found. Without --run the command plans the run and needs no simulator.
- command: `emir info`

### EMIR-E663 simulator route verification failed

- level: error, exit 70
- meaning: `emir verify-sim` ran the simulator on its known testbench, and a current, a verdict, a ratio or an IR drop read back through EMIR differs from the analytic answer (or the two modes differ) beyond the stated tolerance: on this machine the route from that simulator to EMIR does not give the right numbers.
- hint: Do not rely on runs from this simulator here. Read the failing checks (the document's `checks`), keep the output (`--out`), and report it with the simulator's version.

### EMIR-E680 extraction tool not found

- level: error, exit 3
- meaning: `emir extract --run` needs PVS and Quantus (`pvs`, `qrc`), and one was not found.
- hint: Set $PVS_BIN / $QRC_BIN, `pvs:` / `quantus:` in the site file, or put them on $PATH; the dry run shows where each was looked for.
- command: `emir extract --gds <gds> --cdl <cdl> --cell <cell> --kind <kind>`
- otherwise: `emir info`

### EMIR-E681 extraction timed out

- level: error, exit 2
- meaning: LVS or Quantus ran longer than --timeout allows and was stopped.
- hint: Give a longer --timeout (a big block can take hours), or run the tool by hand from the command file the dry run prints.
- command: `tail -n 40 <path>`

### EMIR-W640 magic older than the version checked

- level: warning
- meaning: The magic found is older than the oldest version EMIR was checked with (`site.MAGIC_CHECKED`). An older magic may not read a current PDK's technology file: one crashed on the sky130 technology file during development, and the run then read as EMIR-E640.
- hint: Use a newer magic (--magic, $EMIR_MAGIC, `magic:` in the site file or $MAGIC_BIN).
- command: `emir info`

### EMIR-W643 device terminals named by position

- level: warning
- meaning: The process file's `magic: devices:` names no terminals for some device models magic extracted, so their `*|I` pins are named t1, t2 ... by position. A cc-grid simulation must then name them the same way (`emir simulate --probe --pins MODEL=t1,t2,...`).
- hint: Add the models' terminal names (in the order magic writes the nodes, as the PDK's .subckt lists them) under `magic: devices:` in the process file.
- command: `emir schema process`

### EMIR-W644 resistors merged by magic

- level: warning
- meaning: Some resistors in magic's .res.geo were merged by its simplifier: their width and length no longer describe one drawn shape, so their current density is approximate.
- hint: Extract with `extresist simplify off` (what `emir extract --tool magic` runs); a .res.geo from another script may have it on.

### EMIR-W645 nets not split into resistors

- level: warning
- meaning: Some nets with devices have no resistor in magic's network: extresist leaves a net whole when its resistance is small next to its devices' (--tolerance). EMIR has nothing to check on them.
- hint: Extract again with a lower --tolerance to split them (more resistors, a slower simulation).

### EMIR-W646 capacitance on no net of the network

- level: warning
- meaning: Some capacitor nodes of magic's extraction are on no net of its devices, resistors or ports (a piece of metal that connects to nothing, such as fill, or a net named differently in magic's two netlists that EMIR could not match): their capacitance charges nothing EMIR checks, and a simulator may find the node floating.
- hint: Look at the nodes named: unconnected metal is expected to carry no current; a net of the circuit there means its capacitance is missing from it. Labelling every net in the layout gives magic's netlists one name for it.

### EMIR-W647 wires on diagonal shapes

- level: warning
- meaning: Some of magic's resistors lie on diagonal (non-Manhattan) shapes, which magic keeps as split tiles. The width magic gives such a resistor is its tiles' extent along an axis, not the width across the current (a 45-degree band 0.6 um high is 0.42 um wide), and its resistance is magic's estimate. EMIR checks them at magic's width, so their current density can read low (by 29 % on a 45-degree band): `emir run` lists them under not_checked (`diagonal_wires`). A label on a diagonal shape may also not attach to its net in magic.
- hint: Check those wires by hand at their real width, or draw the path with Manhattan shapes; put labels on Manhattan shapes.

### EMIR-W660 .control section runs too

- level: warning
- meaning: The testbench has an ngspice .control section. ngspice in batch mode with a rawfile (-b -r) runs its commands as well as the netlist's analysis (checked with ngspice 45): what it sets applies to the result (`set filetype=binary` makes it unreadable, EMIR-E409), its own `run` and `write` run again, and a `shell` line runs a program.
- hint: Make sure the person trusts the deck (it is code). Move what the section sets into netlist statements (.options, .param ...) and drop the section if the result is not what emir planned.

### EMIR-I601 Spectre found

- level: info
- meaning: Spectre was found for --netlist.
- hint: Nothing to do.
- command: `emir info`

### EMIR-I610 save lines placed before .alter

- level: info
- meaning: The testbench has `.alter` cases: the save, print or probe lines go before the first `.alter`, so the main case and every altered case save the currents (HSPICE writes the main case to .tr0 and the altered ones to .tr1, .tr2 ...).
- hint: Nothing to do; pass the file of the case to screen to --cc-sim.

### EMIR-I660 open simulator found

- level: info
- meaning: Xyce or ngspice was found for `emir simulate`.
- hint: Nothing to do.
- command: `emir info`

### EMIR-I661 plan only: the licensed simulator was not run

- level: info
- meaning: Spectre and HSPICE need a licence, so `emir simulate` and `emir verify-sim` write the deck and print the exact command, and run it only with --run (over MCP, with confirm=true): the person's go-ahead first.
- hint: Ask the person; then re-run the same command with --run.

### EMIR-I680 extraction plan only

- level: info
- meaning: `emir extract` without --run prints what it would run and changes nothing: PVS and Quantus are licensed tools.
- hint: Check the plan; add --run once the person agrees to run the licensed tools.

### EMIR-I690 MCP server started

- level: info
- meaning: `emir mcp` is serving its tools over stdio: an MCP client talks to it on stdin and stdout; logs go to stderr.
- hint: Nothing to do; the client ends the server by closing stdin.
- command: `emir mcp --list`

### EMIR-I691 agent documents not found

- level: info
- meaning: `emir mcp` found no AGENTS.md or agent/playbooks beside the package (an installed wheel has none), so its resources are the error catalog and the schemas only.
- hint: Pass --agent-dir with a clone of the repository (https://github.com/skycadeda/emir) to serve the playbooks too; without one, read them on the repository's pages.
- command: `emir mcp --list`

## 7xx outputs and viewers

### EMIR-E701 output directory not writable

- level: error, exit 2
- meaning: An --html or --viewer directory cannot be written.
- hint: Choose another directory.
- command: `ls -ld <path>`

### EMIR-E702 name unsafe for the viewer

- level: error, exit 2
- meaning: --lib, --cell or the DSPF design name holds a control character; it would be written into SKILL.
- hint: Use a plain library or cell name (--cell overrides the design name).

### EMIR-E703 non-finite result

- level: error, exit 2
- meaning: A result holds NaN or Inf; the viewer data is not written (it would draw a number that is not one).
- hint: Check the currents and the simulation result.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W701 viewer without a library

- level: warning
- meaning: --viewer without --lib: the Explorer will not know which OA library to open.
- hint: Pass --lib, or set `lib:` in the site file.
- command: `emir info`

### EMIR-W702 viewer without a cell

- level: warning
- meaning: --viewer with no --cell, and the DSPF has no *|DESIGN.
- hint: Pass --cell.

### EMIR-W703 KLayout marker database not written

- level: warning
- meaning: --viewer wrote the Virtuoso Explorer data but not the KLayout marker database (emir.lyrdb): the klayout Python package is not installed.
- hint: Install the klayout Python package (from a clone: `pip install -e '<clone>[klayout]'`; else `pip install klayout`), then re-run with --viewer.
- command: `emir info`

### EMIR-W720 chart not drawn

- level: warning
- meaning: Drawing an EM/IR chart (a PNG of --html) failed. The run's numbers, its HTML pages, its viewer data and its summary are not affected; only the picture is missing.
- hint: Read the message; run the command again with EMIR_DEBUG=1 for the traceback, and report it with the inputs that cause it.

### EMIR-I720 chart not drawn: no matplotlib

- level: info
- meaning: --html wrote the HTML pages but not the EM/IR chart (emir_chart.png and emir_chart_light.png): drawing it needs matplotlib, which is not installed. The HTML pages draw the nets without it.
- hint: Install matplotlib (from a clone: `pip install -e '<clone>[report]'`; else `pip install matplotlib`), then re-run with --html.
- command: `emir info`

### EMIR-I721 chart drawn without the layout

- level: info
- meaning: The chart shows the results on the black canvas alone, without the layout dimmed under them: neither the klayout nor the gdstk Python package is installed to draw the GDS.
- hint: Install the klayout Python package (from a clone: `pip install -e '<clone>[klayout]'`; else `pip install klayout`) to see the layout under the results.
- command: `emir info`

### EMIR-I780 viewer data trimmed

- level: info
- meaning: The run has more segments than the viewer data holds by default, so the clean segments (below 0.5x) and the IR segments off the rails were left out, worst first up to the cap: the Explorer and the marker database stay responsive on a big block.
- hint: Nothing to do; --viewer-all writes every segment.

## 8xx hierarchy

### EMIR-E801 block abstract unusable

- level: error, exit 2
- meaning: A file given to --abstract is not an emir.abstract/1 document: not JSON, another document, or a field the run needs is missing.
- hint: Write it with `emir abstract`; `emir schema abstract` prints the format.
- command: `emir schema abstract`

### EMIR-E802 abstract instance not in the top DSPF

- level: error, exit 2
- meaning: The INST of --abstract FILE@INST (or of --place, --phase, --phase-sweep) has no instance-pin record on the top-level net(s) analysed.
- hint: Name the instance as the top DSPF writes it in its *|I records; `emir pins` lists them.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E803 block net has no port

- level: error, exit 2
- meaning: A block net to abstract has no *|P port on its resistors: the current the block draws must leave through its pins, and the pins must be DSPF ports (with coordinates) to be found.
- hint: Extract the block with every supply pin shape as a port (*|P with coordinates); `emir nets` marks the nets that have one.
- command: `emir nets <dspf>`

### EMIR-E804 nothing to abstract

- level: error, exit 2
- meaning: No net of the block DSPF has both a *|P port and instance pins, so no pin current can be characterised.
- hint: Give --net with the block's supply nets; `emir nets` lists them.
- command: `emir nets <dspf>`

### EMIR-E805 abstract needs a time-domain result

- level: error, exit 2
- meaning: An abstract keeps one period of every pin's current waveform; a harmonic (PSS fd) result carries no time base.
- hint: Use the transient or the PSS time-domain output of the block's simulation.

### EMIR-E806 block pin at several connections, no placement

- level: error, exit 2
- meaning: A block pin reaches the top net at several *|I records and the abstract has several ports for it: which port lands on which record is decided by coordinates, which needs the block's placement in the top frame.
- hint: Give --place INST=dx,dy,orient (the block's origin in the top frame and its orientation, R0 R90 R180 R270 MX MY MXR90 MYR90); `emir pins` lists the records with their coordinates.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E807 abstract ports do not match the top connections

- level: error, exit 2
- meaning: The abstract has one port for a block pin that the top DSPF connects at several places, or several ports for a pin the top connects at one: how the current divides between them cannot be decided.
- hint: One port, several records: re-extract the block with every pin shape a *|P port of its own and run `emir abstract` again. Several ports, one record: re-extract the top keeping each pin shape a connection of its own.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E808 placed port lands on no record

- level: error, exit 2
- meaning: With --place, a block port moved into the top frame has no *|I record of its pin within the tolerance, more than one, or the same record as another port.
- hint: Check the origin and orientation in --place against `emir pins`; --place-tol sets the tolerance (um).
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E809 no abstract lands on the net

- level: error, exit 2
- meaning: None of the --abstract instances has a pin record on the top-level net(s) to analyse.
- hint: Check the instance names and --net; `emir pins` lists the records of the net.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-E810 sync alignment impossible

- level: error, exit 2
- meaning: --align sync places every block's stored period on one common time grid: a period is not a whole number of the common step, or the common period of all blocks is too long to hold.
- hint: Blocks on unrelated clocks: --align async (and read its warning); otherwise --align inphase, the upper bound.
- command: `emir run <args> --align inphase`

### EMIR-E811 option not combined with --abstract

- level: error, exit 2
- meaning: --abstract drives the top-level run from block abstracts (and the top's own devices from --cc-sim); --currents, --netlist, --self-consistent, --scalars with another --align, --ground, --taps-out and --mode rcc drive or reference it otherwise, and are not combined with it. --dynamic is not either: the top-level solve is resistive, and each block's internal dynamic droop is in its abstract.
- hint: Drop the other option. Devices of the top outside the blocks take their currents from the top's CC simulation (--cc-sim).

### EMIR-E812 port-voltages file unusable

- level: error, exit 2
- meaning: The --port-voltages file is not an emir.port_voltages/1 document, has no entry for the instance or the block net, names other ports, or was written from another result of this block (its period or phase reference differ).
- hint: Write it with `emir run ... --port-voltages-out F` from the same abstract and pass F@INST; `emir schema port_voltages` prints the format.
- command: `emir schema port_voltages`

### EMIR-E813 top net has no feed

- level: error, exit 2
- meaning: The top-level net has no *|P port, so nothing says where its current enters (the pad).
- hint: Give --feed NODE, the node the net is fed at.
- command: `emir nets <dspf>`

### EMIR-W801 aperiodic abstract repeated

- level: warning
- meaning: A block's abstract has no period (its simulation window was not periodic); --align sync repeats its whole window as if it were one period.
- hint: Characterise the block with --period over whole periods of its activity, or use --align inphase.

### EMIR-W802 async is not an upper bound for related clocks

- level: warning
- meaning: --align async combines the blocks as if their clocks were unrelated: avg is exact, rms the long-time value and peak the worst over every relative phase. Blocks on related clocks can line up worse than that average on rms.
- hint: Related clocks: --align inphase (the upper bound) or --align sync with --phase-sweep.
- command: `emir run <args> --align inphase`

### EMIR-W803 stored pin-current period lost peak

- level: warning
- meaning: Putting a pin current on the uniform grid kept less than 99 % of its peak: rms and peak downstream read low.
- hint: Raise --points at `emir abstract` (one period is stored with that many samples).

### EMIR-W804 block verdict with its pins shorted

- level: warning
- meaning: A block net with several ports was judged with every port at one ideal potential. A supply fed from several sides also carries pass-through current from the top level, which this verdict does not include (it can read low).
- hint: After the top run, re-check the block with the top's pin voltages: `emir run ... --port-voltages-out F`, then `emir abstract ... --port-voltages F@INST`.

### EMIR-W805 large droop at a block pin

- level: warning
- meaning: The top-level droop at a block pin exceeds 2 % of the block's nominal supply (--vdd at `emir abstract`). The block's currents were characterised at the nominal supply.
- hint: Close the loop: re-simulate the block at the delivered voltage, re-abstract and re-run the top until the pin voltages settle (the first pass over-reads current, the conservative direction).

### EMIR-W806 block pin with no current

- level: warning
- meaning: A top-level record of an abstracted instance names a pin its abstract does not cover, or an abstract net lands on no analysed top net: its current is taken as zero there.
- hint: Abstract every supply net of the block (--net), and check the pin names against `emir pins`.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-W807 top-level pins with no current

- level: warning
- meaning: Instance pins on the top net belong to no --abstract instance; with abstracts driving the run their current is taken as zero.
- hint: Abstract those instances too, if they draw current from the net.
- command: `emir pins <dspf> --net <net>`
- otherwise: `emir nets <dspf>`

### EMIR-W808 abstract at another temperature

- level: warning
- meaning: A block abstract was characterised at another EM analysis temperature than this run: its port conductances and internal verdict are at that temperature.
- hint: Abstract the blocks with the same process file and --em-temp as the top run.

### EMIR-I801 pads held at one potential

- level: info
- meaning: The top net has several *|P ports (pads); they are held at one ideal potential, the feed of the run.
- hint: Nothing to do; --feed NODE feeds the net at one node instead.

### EMIR-I802 block ports matched by coordinates

- level: info
- meaning: The ports of a block pin with several connections were matched to the top's *|I records through --place.
- hint: Nothing to do.

### EMIR-I803 IR budget of a one-pin supply is exact

- level: info
- meaning: For a one-pin supply, top drop at the pin plus the block's internal drop is the exact worst droop; for several pins it is an upper bound (an internal node's top-induced shift is a weighted mean of its pin voltages).
- hint: Nothing to do.

### EMIR-I804 back-annotation of a one-pin supply

- level: info
- meaning: A one-pin supply carries no pass-through current: its back-annotated branch currents equal the block-alone ones; only the droop adds the top-level drop at the pin.
- hint: Nothing to do.

### EMIR-I805 block abstract read

- level: info
- meaning: An --abstract file was read: its block, nets and ports.
- hint: Nothing to do.

## 9xx internal

### EMIR-E901 internal error

- level: error, exit 70
- meaning: An exception reached the command line that EMIR did not anticipate: a bug.
- hint: Re-run with EMIR_DEBUG=1 for the traceback and report it.
- command: `emir selftest`

### EMIR-E902 self-test failed

- level: error, exit 70
- meaning: `emir selftest` did not reproduce the sample's hand-checked numbers: the install is broken.
- hint: Reinstall; run `emir info` to see which packages were found.
- command: `emir info`

### EMIR-E903 validation failed

- level: error, exit 70
- meaning: A case of `emir validate` differs from its closed-form answer.
- hint: Report it with the output of `emir validate --keep`.
- command: `emir validate --keep`

### EMIR-E904 interrupted

- level: error, exit 130
- meaning: The command was interrupted (Ctrl-C or SIGINT).
- hint: Re-run it.

### EMIR-E905 internal consistency check failed

- level: error, exit 70
- meaning: A consistency check inside EMIR failed -- two arrays built for one analysis disagree in size or sum, or an internal writer (the synthetic results of the tests and examples) was handed a table it cannot write: a bug.
- hint: Re-run with EMIR_DEBUG=1 for the traceback and report it.
- command: `emir selftest`

### EMIR-E906 terminated

- level: error, exit 143
- meaning: The command was stopped by SIGTERM (a timeout wrapper, a job scheduler, kill).
- hint: Re-run it, with more time if a wrapper stopped it.

## Retired codes

- EMIR-E213: `emir layout` was removed: a port with no text label on the conductors
- EMIR-E214: `emir mesh` was removed: a port label not on any conductor
- EMIR-E215: `emir mesh` was removed: ports on disconnected pieces of metal
- EMIR-E240: `emir layout` was removed: the block's instance in its testbench
- EMIR-E241: `emir layout` was removed: the port order of the block in its testbench
- EMIR-E242: `emir layout` was removed: which label feeds a net (--ports)
- EMIR-E324: `emir mesh` was removed: the width loss on its raster's section density (a DSPF segment no wider than its width loss is EMIR-E323)
- EMIR-E330: `emir layout` was removed: a process entry with no GDS numbers (the magic route reads magic's layer names, `magic: layers:`)
- EMIR-E370: `emir layout` was removed: a --layers or --taps layer not in the process file
- EMIR-E413: --mode auto was removed: the person chooses the mode (EMIR-E520); a result that cannot serve it is EMIR-E415 (rcc) or EMIR-E416 (cc-grid)
- EMIR-E440: `emir layout` was removed: probe currents missing from its testbench's output (`emir simulate`'s missing currents are EMIR-W461)
- EMIR-E441: `emir layout` was removed: no tap or device current placed
- EMIR-E470: `emir mesh` was removed: a tap file of several nets
- EMIR-E570: `emir mesh` was removed: its choice of --currents or --taps
- EMIR-E571: `emir mesh` was removed: its --ports input
- EMIR-E572: `emir mesh` was removed: its --h, --port-radius and --probe options
- EMIR-E770: `emir mesh` was removed: the marker and IR-bin layers of its annotated layout
- EMIR-I205: --mode auto was removed: the person chooses rcc or cc-grid (EMIR-E520); --self-consistent takes --mode cc-grid
- EMIR-I206: --mode auto was removed: a run with DC --currents needs no mode at all
- EMIR-I270: `emir mesh` was removed: a port placed at its layout label
- EMIR-I370: `emir mesh` was removed: a --ports or tap layer name mapped by rule
- EMIR-I403: --mode auto was removed: the person chooses rcc or cc-grid (EMIR-E520)
- EMIR-I406: --mode auto was removed: with --netlist the mode is given (EMIR-E520)
- EMIR-I470: `emir mesh` was removed: per-tap scalars read as in-phase upper bounds
- EMIR-W209: `emir mesh` was removed: its raster grid coarsened
- EMIR-W240: `emir layout` was removed: angled shapes modelled as their bounding box
- EMIR-W241: `emir layout` was removed: labels naming one net differently
- EMIR-W242: `emir layout` was removed: a net in pieces on the modelled layers
- EMIR-W243: `emir layout` was removed: via cuts on no metal
- EMIR-W270: `emir mesh` was removed: a port label found several times
- EMIR-W340: `emir layout` was removed: via arrays grouped by the measured cut
- EMIR-W370: `emir mesh` was removed: a traced net on a layer or via without a limit
- EMIR-W440: `emir layout` was removed: tap rows not placed
- EMIR-W441: `emir layout` was removed: device currents not placed
- EMIR-W442: `emir layout` was removed: taps on no shape
- EMIR-W443: `emir layout` was removed: taps on an isolated shape
- EMIR-W444: `emir layout` was removed: device currents placed far from the device
- EMIR-W445: `emir layout` was removed: device terminals off the metal
- EMIR-W470: `emir mesh` was removed: tap metrics no waveform can produce
