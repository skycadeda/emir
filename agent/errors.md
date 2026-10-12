# EMIR error-code catalog

Generated from `emir/errors.py` by `scripts/gen_errors_md.py`; do not edit by hand (a test fails when it is stale). `emir explain <code>` prints one entry; `emir explain --list` all of them.

Branch on the code, never on the message. `E` codes are errors (refusals, input-check FAILs), `W` warnings, `I` infos (the input checker's OK lines carry `I` codes too). Command templates fill `<name>` fields from the case at hand, and a `[...]` part is left out when its field is unknown; when the command cannot be filled, the `otherwise` one is used. A JSON result's `next_steps` hold only commands that run as printed (no step when none can be filled, or when the code has no command: its hint says what to do).

## Exit status

| status | meaning |
|---|---|
| 0 | done (check: the inputs are OK) |
| 1 | a FAIL verdict, with --fail-on-violation |
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

### EMIR-E212 no cell in the layout

- level: error, exit 2
- meaning: The GDS file holds no cell: it is empty, or not the layout that was meant.
- hint: Check that the file is the block's layout (open it in KLayout), and write the layout again from the layout editor if it is empty.
- command: `klayout <path>`

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

### EMIR-E263 element not in the testbench

- level: error, exit 2
- meaning: The print or save lines would name resistors the testbench does not define: it is not the testbench of this DSPF (the RC netlist rcc simulates), or the block sits under another instance path.
- hint: Simulate the testbench of this DSPF; check --prefix.
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
- meaning: The DSPF has no `*|I` records: it names no device pin, so DC --currents can name only its nodes, and rcc's KCL check counts every device node as one nothing drives.
- hint: Extract with instance-pin records.
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

### EMIR-W263 block assumed at the top level

- level: warning
- meaning: The block's subcircuit is not instantiated in the netlist, so the simulator names written assume it is the top level of the simulation.
- hint: Give --prefix with the instance path the testbench uses (e.g. XI0.).

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
- hint: Add the key the message names, its value from the PDK's documents.
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
- meaning: A layer or via entry holds a key the format does not have. It is most likely a typo, and a misspelt limit is a limit silently not checked.
- hint: Correct the key; `emir schema process` lists the keys of each entry.
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
- meaning: The DSPF was extracted at another temperature than the EM analysis. A DC solve (--currents) scales each wire's R by its layer's TCR; rcc (a simulation) keeps the resistances the simulation ran (EMIR-I407).
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
- meaning: The result directory holds no file (matching --analysis), or a result file of several analyses (nutascii plots) holds none matching --analysis, or none over time when no --analysis is given.
- hint: Name the analysis with --analysis (the message lists the file's plots), or point at the file itself.
- command: `ls <path>`

### EMIR-E414 rcc needs a time-domain result

- level: error, exit 2
- meaning: RCC mode reads branch currents over time; a harmonic (PSS fd) result carries no time base.
- hint: Use the transient output, two or more whole periods long (a PSS time-domain file holds one period: refused, EMIR-E431).

### EMIR-E415 branch currents not saved

- level: error, exit 2
- meaning: RCC mode needs every branch current of the net, and some (or all) are missing from the result.
- hint: Add the save lines `emir save-lines` prints and re-simulate.
- command: `emir save-lines <dspf>[ --net <net>]`

### EMIR-E418 analysis does not fit in memory

- level: error, exit 2
- meaning: An array of the analysis (an rcc result's branch currents, a grid's factorisation) would need more memory than is available.
- hint: Screen one --net at a time.

### EMIR-E420 empty time window

- level: error, exit 2
- meaning: The time window selected for the metrics holds no time.
- hint: Check --period and the simulated time span.
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-E424 names sidecar unusable

- level: error, exit 2
- meaning: The `<result>.names` file beside a simulation result maps the simulator's signal names (I(XI0:RM1), @r.xi0.rm1[i] ...) to the DSPF's R:1 names. It is not valid JSON, or not of the form {"map": {NAME: ["R:1", sign]}}.
- hint: Fix or remove the sidecar; `emir schema names` prints its format.
- command: `emir schema names`

### EMIR-E426 currents that are not numbers in the result

- level: error, exit 2
- meaning: Currents the run needs hold NaN or Inf in the simulation result (a diverged or failed simulation, or a value the simulator could not print), so every EM ratio and IR drop computed from them would be "not a number".
- hint: Re-run the simulation to a finite result (its log names the failure); never replace the values by zero.

### EMIR-E427 signal names collide in the result

- level: error, exit 2
- meaning: The result names two or more columns alike (a name written twice, or HSPICE format 9007 names, cut to 16 characters, that become equal or match several names of the names sidecar), so which column is which current cannot be told.
- hint: Simulate with `.option post_version=2001` (full names, double precision) and save each current once.
- command: `emir save-lines --help`

### EMIR-E428 no period for a transient

- level: error, exit 2
- meaning: rcc averages every branch current over the record's last whole periods of the block's activity, and EMIR never guesses that period: a transient result needs --period, and so does a --netlist run (its simulation is a transient: nothing is simulated). (An operating point, one time point, needs none.) rcc is exact for the simulated record over the whole periods you give (--period); whether the testbench has settled is yours to establish, as for any .meas. The run is refused: no number is printed.
- hint: Give --period, the period of the block's activity: its clock, or the period of the testbench's PULSE or SIN sources. Ask the person; never guess it from the waveforms. A record held at DC: any --period with two or more whole ones in the record gives the same numbers.
- command: `emir next --dspf <dspf>[ --process <process>][ --cc-sim <cc_sim>][ --netlist <netlist>][ --net <net>][ --sim <sim>]`
- otherwise: `emir next --dspf <dspf>`

### EMIR-E429 the last two periods differ

- level: error, exit 2
- meaning: The record's last two whole periods of --period differ by more than 2 % in the average, rms or peak of a branch that matters (one carrying 1 % of its net's largest current; the message names it and the change): the period is not the block's, or the block is still starting up at the end of the record. The run is refused: no number is printed.
- hint: Give the right --period (ask the person; never guess it), or simulate longer if the block is still starting up. A peak that differs on a coarse record: simulate with a smaller maximum time step.
- command: `emir next --dspf <dspf>[ --process <process>][ --cc-sim <cc_sim>][ --net <net>][ --period <period>][ --sim <sim>]`
- otherwise: `emir next --dspf <dspf>`

### EMIR-E431 record too short for the period

- level: error, exit 2
- meaning: rcc needs at least two whole periods of --period in the record, so that the last two can be compared (its window is the last whole periods, up to three). The simulation holds fewer. The run is refused: no number is printed.
- hint: Simulate at least two periods (the message says to what time); a period longer than the whole record may be the wrong --period.
- command: `emir next --dspf <dspf>[ --process <process>][ --cc-sim <cc_sim>][ --net <net>][ --period <period>][ --sim <sim>]`
- otherwise: `emir next --dspf <dspf>`

### EMIR-E462 no transient analysis

- level: error, exit 2
- meaning: The testbench (and the files it includes) has no .tran statement at its top level, and the currents over time are needed (`emir simulate`).
- hint: Add a .tran over whole periods of the block's activity (for a DC bias, a short .tran with the sources held at DC, and any --period that fits twice in it).
- command: `grep -i -n tran <path>`

### EMIR-E463 binary HSPICE result

- level: error, exit 2
- meaning: The result is HSPICE's binary waveform file (.trN written with `.option post=1`, the default when post is given without a value). EMIR reads the ASCII form.
- hint: Re-run HSPICE with `.option post=2 post_version=2001` (ASCII, double precision); `emir save-lines --sim hspice` prints that line with the .probe lines.
- command: `emir save-lines --help`

### EMIR-E464 HSPICE ASCII result cannot be read

- level: error, exit 2
- meaning: The file starts like an HSPICE ASCII result (.trN, `.option post=2`) but its header or its data does not follow the layout: a header that never ends ($&%#), counts that are not numbers, a value of the wrong width, a table without its end marker (a truncated file), or rows that do not fill.
- hint: Re-run the simulation to completion with `.option post=2 post_version=2001`, and pass the file HSPICE wrote, unedited.
- command: `head -n 8 <path>`

### EMIR-E465 several sweep tables in the result

- level: error, exit 2
- meaning: The result holds one table per sweep value (a .tran with a sweep, or a CSDF block per step), or several plots a nutascii file could be read from, and EM is checked on one waveform at a time.
- hint: Pick one table with --analysis sweep:N (1-based; the message lists them), or simulate the case of interest alone.

### EMIR-E466 CSDF result cannot be read

- level: error, exit 2
- meaning: The file starts like a CSDF result (#H) but does not follow the layout: a block without #;, a point whose value count differs from #C's or from the names, a value that is not a number.
- hint: Re-run the simulation to completion and pass the file it wrote, unedited.
- command: `head -n 20 <path>`

### EMIR-E467 not a transient result

- level: error, exit 2
- meaning: The HSPICE, CSDF or nutascii result is swept over frequency or a DC variable, not time; EM needs the currents over time.
- hint: Pass the transient result (.trN, the .tran's CSDF file, or the transient plot of a nutascii file: --analysis tran).

### EMIR-W402 few time points

- level: warning
- meaning: The result has few time points: rms and peak will be coarse.
- hint: Save more points (a smaller maximum step).
- command: `emir check <args>`
- otherwise: `emir check <dspf>`

### EMIR-W405 currents inconsistent with the DSPF

- level: warning
- meaning: The RCC loop residual is large: the simulated branch currents do not fit this DSPF (wrong netlist/DSPF pairing, a missing save, a sign).
- hint: Check that the simulated netlist was extracted with this DSPF.

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

### EMIR-W464 simulator route not verified on this machine

- level: warning
- meaning: The run's currents come from a simulator result, and this machine holds no passing `emir verify-sim` record that counts for that simulator, version, output format and simulator arguments (`simulator_verification` in the run summary; its `reason` says why: `no record`, `stale` -- a pass from another emir minor version --, or `other host`), or none that covers the branch currents the run read (`current kinds not covered`: a record made before records said what they cover), or the result is in an output format emir verify-sim never writes (`format not covered`: CSDF, Spectre's nutascii). The numbers may still be right; nothing on this machine has shown that the simulator's names, signs and file format reach EMIR as EMIR reads them. A failed latest record is EMIR-W414, an unknown version EMIR-W416.
- hint: Run `emir verify-sim --sim <simulator>` once on this machine (a licensed simulator: ask the person first; it plans the run without --run), then re-run. When the summary gives no `command` (a format verify-sim does not cover), no verification helps: tell the person the currents stay unverified, or simulate through `emir simulate`, whose output format verify-sim covers.
- command: `emir verify-sim --sim <sim>`

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

### EMIR-I407 rcc's IR at the simulated resistances

- level: info
- meaning: rcc computes the static and dynamic IR from the resistances the simulation ran, the DSPF's, at the temperature the DSPF states (else the process file's temp_reference): the IR is the simulated circuit's. The EM limits are taken at the analysis temperature (--em-temp), which differs from it; the run summary records both (`ir_resistance`).
- hint: Nothing to do for the simulated circuit. For IR at the analysis temperature, extract (or simulate) the block at that temperature and run again.

### EMIR-I463 simulator route verified on this machine

- level: info
- meaning: The latest `emir verify-sim` record on this machine for the simulator, version, output format and simulator arguments that wrote the run's currents passed, with this emir minor version, and covers every kind of current the run read.
- hint: Nothing to do.

### EMIR-I464 single-precision HSPICE result

- level: info
- meaning: The HSPICE ASCII result is format 9601 or 9007: five significant digits per value (a KCL residual near 3e-5 of the current on a synthetic case).
- hint: For more digits, simulate with `.option post_version=2001`.

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

### EMIR-E506 --mode rcc needs a simulation

- level: error, exit 2
- meaning: RCC mode reads branch currents from a simulation result; DC --currents take no mode.
- hint: Give --cc-sim (a result) or --netlist, or drop --mode rcc.
- command: `emir save-lines <dspf>[ --net <net>]`

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

### EMIR-E514 two sources of currents

- level: error, exit 2
- meaning: DC currents (--currents) and a simulation (--cc-sim or --netlist) were both given: a run takes its currents from one of them, and the summary would name the other.
- hint: Give --cc-sim (or --netlist) for rcc, or --currents for a DC screen, not both; ask the person which they meant.
- command: `emir next --dspf <dspf>[ --process <process>]`

### EMIR-E580 extraction setting missing

- level: error, exit 2
- meaning: `emir extract --run` needs the LVS rules, the Quantus technology directory, the corner and the temperature, and one is not set.
- hint: Give it with its option, or in the site file (`pvs_lvs_rules`, `qrc_tech_dir`, `qrc_corner`, `extract_temperature`); the dry run lists what is missing.
- command: `emir extract --gds <gds> --cdl <cdl> --cell <cell> --kind <kind>`

### EMIR-E590 the person's go-ahead is needed

- level: error, exit 2
- meaning: An MCP tool call would start a licensed tool (Spectre), run long, rewrite a file in place, or run a command the MCP server has not classified, and the call did not carry confirm=true. Nothing was run; `details.reasons` says why.
- hint: Ask the person. Only if they agree, make the same call again with confirm=true.

### EMIR-E591 not runnable over MCP

- level: error, exit 2
- meaning: `emir_cli` was given a command line that is not an emir command the MCP server runs: another program (the server has no shell), or `emir mcp` itself.
- hint: Run that command in a shell, or ask the person to; emir commands go through the emir_* tools.

### EMIR-E592 tool arguments do not match the tool

- level: error, exit 2
- meaning: An MCP tool call named an argument the tool does not have (a misspelt option, which would otherwise be dropped without a word), left out one the tool requires, or gave a value outside a parameter's enum. Nothing was run; `details` lists the unknown, the missing and the invalid arguments and the tool's parameters.
- hint: Use the parameter names and values of the tool's input schema (the client's tool list; `emir mcp --list` prints them in a shell), and give every required one.

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
- meaning: `emir run --netlist` drives Spectre only (results from any simulator can be read if written as PSF ASCII, nutascii, HSPICE ASCII or CSDF; `emir simulate` drives HSPICE, Xyce and ngspice).
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
- hint: Build it: `git clone -b extresist-geoout https://github.com/codervijay/magic`, then `./configure --prefix=$HOME/magic-geo && make && make install` in it, and name it with --magic, $EMIR_MAGIC or the process file's `magic: binary:`. Without it, extract a DSPF with another tool (playbook 04).
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
- meaning: `emir extract --tool magic` writes <cell>.dspf into --out, and magic runs in `magic/` under it, a directory of EMIR's own (a `.emir-magic-work` file marks it) where EMIR replaces magic's files on every run. There is a <cell>.dspf that EMIR did not write (it lacks EMIR's header), or a `magic/` directory that holds files and is not EMIR's: EMIR never overwrites them.
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
- meaning: `emir verify-sim` ran the simulator on its known testbench, and a current, a verdict, a ratio or an IR drop read back through EMIR differs from the analytic answer beyond the stated tolerance: on this machine the route from that simulator to EMIR does not give the right numbers.
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
- meaning: The process file's `magic: devices:` names no terminals for some device models magic extracted, so their `*|I` pins are named t1, t2 ... by position (DC --currents name them so).
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

## 8xx hierarchy (removed)

No codes: 27 retired (below).

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

- EMIR-E211: removed with cc-grid, which read each instance pin's current: 1.1 checks with rcc, which reads every branch current of an RC simulation, or with DC --currents
- EMIR-E213: `emir layout` was removed: a port with no text label on the conductors
- EMIR-E214: `emir mesh` was removed: a port label not on any conductor
- EMIR-E215: `emir mesh` was removed: ports on disconnected pieces of metal
- EMIR-E216: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-E240: `emir layout` was removed: the block's instance in its testbench
- EMIR-E241: `emir layout` was removed: the port order of the block in its testbench
- EMIR-E242: `emir layout` was removed: which label feeds a net (--ports)
- EMIR-E262: `emir probe` and the per-pin probes of `emir simulate` were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, branch currents every simulator prints directly
- EMIR-E317: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-E324: `emir mesh` was removed: the width loss on its raster's section density (a DSPF segment no wider than its width loss is EMIR-E323)
- EMIR-E330: `emir layout` was removed: a process entry with no GDS numbers (the magic route reads magic's layer names, `magic: layers:`)
- EMIR-E370: `emir layout` was removed: a --layers or --taps layer not in the process file
- EMIR-E413: --mode auto was removed: 1.1 has one method of a simulated run, rcc: there is no mode to choose; a result that cannot serve rcc is EMIR-E415
- EMIR-E416: removed with cc-grid, which read each instance pin's current: 1.1 checks with rcc, which reads every branch current of an RC simulation, or with DC --currents
- EMIR-E417: removed with cc-grid, which read each instance pin's current: 1.1 checks with rcc, which reads every branch current of an RC simulation, or with DC --currents
- EMIR-E419: a period longer than the record is EMIR-E431: rcc's window is the record's last whole periods of --period, or the run is refused with no number (EMIR-E428 no period, E429 the last two periods differ, E431 a record too short)
- EMIR-E421: the dense and streamed waveform superposition (--superposition, --max-memory, --scalars) were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E422: cc-grid --dynamic was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, dynamic IR included
- EMIR-E423: cc-grid --dynamic was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, dynamic IR included
- EMIR-E425: `emir probe` and the per-pin probes of `emir simulate` were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, branch currents every simulator prints directly
- EMIR-E430: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-E432: a record not settled (a drift projected from how it shrinks): rcc no longer judges whether the testbench has settled (decided 2026-10-11): rcc is exact for the simulated record over the whole periods you give (--period); whether the testbench has settled is yours to establish, as for any .meas
- EMIR-E440: `emir layout` was removed: probe currents missing from its testbench's output (`emir simulate`'s missing currents are EMIR-W461)
- EMIR-E441: `emir layout` was removed: no tap or device current placed
- EMIR-E460: `emir probe` and the per-pin probes of `emir simulate` were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, branch currents every simulator prints directly
- EMIR-E461: `emir probe` and the per-pin probes of `emir simulate` were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, branch currents every simulator prints directly
- EMIR-E470: `emir mesh` was removed: a tap file of several nets
- EMIR-E502: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E503: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E504: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E505: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E510: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E520: 1.1 has one method of a simulated run, rcc: there is no mode to choose
- EMIR-E521: 1.1 has one method of a simulated run, rcc: there is no mode to choose
- EMIR-E522: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E523: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E524: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E525: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E526: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E530: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E531: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E532: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E533: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E534: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E535: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E540: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E541: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-E550: cc-grid --dynamic was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, dynamic IR included
- EMIR-E570: `emir mesh` was removed: its choice of --currents or --taps
- EMIR-E571: `emir mesh` was removed: its --ports input
- EMIR-E572: `emir mesh` was removed: its --h, --port-radius and --probe options
- EMIR-E770: `emir mesh` was removed: the marker and IR-bin layers of its annotated layout
- EMIR-E801: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E802: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E803: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E804: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E805: removed with the chip-level hierarchy (emir abstract), which was built on cc-grid
- EMIR-E806: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E807: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E808: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E809: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E810: the hierarchy's alignment (--align, --phase, --phase-sweep, --points) was removed with the chip-level hierarchy
- EMIR-E811: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E812: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E813: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-E814: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-I201: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-I202: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-I205: --mode auto was removed: 1.1 has one method of a simulated run, rcc: there is no mode to choose
- EMIR-I206: --mode auto was removed: a run with DC --currents needs no mode at all
- EMIR-I270: `emir mesh` was removed: a port placed at its layout label
- EMIR-I370: `emir mesh` was removed: a --ports or tap layer name mapped by rule
- EMIR-I403: --mode auto was removed: 1.1 has one method of a simulated run, rcc: there is no mode to choose
- EMIR-I405: removed with cc-grid, which read each instance pin's current: 1.1 checks with rcc, which reads every branch current of an RC simulation, or with DC --currents
- EMIR-I406: --mode auto was removed: 1.1 has one method of a simulated run, rcc: there is no mode to choose
- EMIR-I430: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-I431: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-I432: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-I450: the dense and streamed waveform superposition (--superposition, --max-memory, --scalars) were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-I465: the HSPICE terminal-current sign from KCL was removed with cc-grid's pin currents: rcc reads branch currents
- EMIR-I470: `emir mesh` was removed: per-tap scalars read as in-phase upper bounds
- EMIR-I520: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-I521: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-I530: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-I531: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-I801: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-I802: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-I803: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-I804: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-I805: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-W209: `emir mesh` was removed: its raster grid coarsened
- EMIR-W240: `emir layout` was removed: angled shapes modelled as their bounding box
- EMIR-W241: `emir layout` was removed: labels naming one net differently
- EMIR-W242: `emir layout` was removed: a net in pieces on the modelled layers
- EMIR-W243: `emir layout` was removed: via cuts on no metal
- EMIR-W260: `emir probe` and the per-pin probes of `emir simulate` were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, branch currents every simulator prints directly
- EMIR-W261: `emir probe` and the per-pin probes of `emir simulate` were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, branch currents every simulator prints directly
- EMIR-W262: `emir probe` and the per-pin probes of `emir simulate` were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, branch currents every simulator prints directly
- EMIR-W270: `emir mesh` was removed: a port label found several times
- EMIR-W340: `emir layout` was removed: via arrays grouped by the measured cut
- EMIR-W370: `emir mesh` was removed: a traced net on a layer or via without a limit
- EMIR-W401: the input check's warning for a record under two periods: rcc's window is the record's last whole periods of --period, or the run is refused with no number (EMIR-E428 no period, E429 the last two periods differ, E431 a record too short); a record under two periods is the FAIL EMIR-E431
- EMIR-W403: removed with cc-grid, which read each instance pin's current: 1.1 checks with rcc, which reads every branch current of an RC simulation, or with DC --currents
- EMIR-W404: removed with cc-grid's reading of PSS pin currents: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W406: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W407: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W408: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W409: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W410: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W411: cc-grid --dynamic was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, dynamic IR included
- EMIR-W412: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W413: an averaging window was taken with this warning: rcc's window is the record's last whole periods of --period, or the run is refused with no number (EMIR-E428 no period, E429 the last two periods differ, E431 a record too short)
- EMIR-W428: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W429: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W430: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W431: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W432: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W433: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W434: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W435: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W436: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W437: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W438: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W439: decap-aware EM and --em-basis were removed, with cap_models: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, on the run's own currents
- EMIR-W440: `emir layout` was removed: tap rows not placed
- EMIR-W441: `emir layout` was removed: device currents not placed
- EMIR-W442: `emir layout` was removed: taps on no shape
- EMIR-W443: `emir layout` was removed: taps on an isolated shape
- EMIR-W444: `emir layout` was removed: device currents placed far from the device
- EMIR-W445: `emir layout` was removed: device terminals off the metal
- EMIR-W450: cc-grid --dynamic was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, dynamic IR included
- EMIR-W451: cc-grid --dynamic was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, dynamic IR included
- EMIR-W452: the dense and streamed waveform superposition (--superposition, --max-memory, --scalars) were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W463: `emir probe` and the per-pin probes of `emir simulate` were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents, branch currents every simulator prints directly
- EMIR-W465: the HSPICE terminal-current sign from KCL was removed with cc-grid's pin currents: rcc reads branch currents
- EMIR-W470: `emir mesh` was removed: tap metrics no waveform can produce
- EMIR-W520: cc-grid's droop warning was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W531: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W535: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W536: cc-grid's guard was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W540: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W541: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W542: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W543: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W544: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W545: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W546: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W547: the self-consistent cc-grid loop (--self-consistent) was removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W550: the dense and streamed waveform superposition (--superposition, --max-memory, --scalars) were removed with cc-grid: 1.1 checks with rcc (every branch current of an RC simulation) or DC --currents
- EMIR-W801: the hierarchy's alignment (--align, --phase, --phase-sweep, --points) was removed with the chip-level hierarchy
- EMIR-W802: the hierarchy's alignment (--align, --phase, --phase-sweep, --points) was removed with the chip-level hierarchy
- EMIR-W803: the hierarchy's alignment (--align, --phase, --phase-sweep, --points) was removed with the chip-level hierarchy
- EMIR-W804: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-W805: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-W806: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-W807: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
- EMIR-W808: the chip-level hierarchy (emir abstract, emir run --abstract) was removed: it was built on cc-grid
