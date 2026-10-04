# 07 Report to a human: relay `emir report`

A person reads what you say, not the JSON. `emir report` turns a run summary into the summary they need, in the
order they need it, with the disclaimers in it. Relay it; do not rewrite its numbers. **Screening, not sign-off.**
EMIR is a research project, provided as is, with no warranty or guarantee of accuracy: every result you relay says
that the person tests and verifies it before relying on it.

## When to use

- After every `emir run` you were asked to do.
- When the person hands you a summary file (`summary.json`, a block abstract) and asks what it says.
- When a manager, a reviewer or a channel needs the status of a block.

## Inputs

| input | what it is |
|---|---|
| a run summary | `emir_out/summary.json` (the `emir.run/1` document `emir run --summary-out` wrote; also a layout run's `--summary-out`) |
| how much detail | `--top N` worst items (default 10) |

## Commands

### Worked example (runs as printed from the repository root)

<!-- test: run -->
```sh
emir run emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --currents '{"VDD": 0.004, "MP0:s": -0.001, "MP1:s": -0.001, "MP2:s": -0.001, "MP3:s": -0.001}' --summary-out emir_out/summary.json --format json
emir report --from emir_out/summary.json
emir report --from emir_out/summary.json --top 3 --format json
```

<!-- test: expect -->
```json
{"schema": "emir.report/1", "verdict": "FAIL", "disclaimer": "Screening, not sign-off.",
 "modes": ["dc"], "current_source": {"kind": "dc"}, "temp_C": 100.0, "em_tref_C": 100.0,
 "counts": {"FAIL": 1, "WARN": 0, "CLEAN": 0}, "worst_net": {"net": "VDD", "ratio": 1.667},
 "nets": [{"net": "VDD", "verdict": "FAIL", "mode": "dc", "governing_metric": "avg", "worst_ratio": 1.667,
           "ir_drop_mV": 54.0}],
 "worst_items": [{"name": "Rm1", "metric": "avg", "ratio": 1.667, "fix": "Metal1: widen 0.750 -> 1.255 um (x1.67)"},
                 {"name": "Rm2"}, {"name": "Rm3"}],
 "ir_by_power_net": [{"net": "VDD", "ir_drop_mV": 54.0}]}
```

The JSON form adds `markdown` (the same text the default prints), `not_checked`, `input_warnings` (here
EMIR-W306: the ILLUSTRATIVE process file) and `run_warnings`. A summary file that holds a refused run's error is
not a result:

<!-- test: run -->
```sh
emir run emir/data/sample/rail.dspf --process emir/data/sample/process.yaml --net VSS --currents '{"VSS": 0.001}' --summary-out emir_out/refused.json --format json   # exit 2
emir report --from emir_out/refused.json --format json   # exit 2
```

<!-- test: expect -->
```json
{"schema": "emir.error/1", "code": "EMIR-E104"}
```

### On the person's files

```sh
emir report --from emir_out/summary.json
emir report --from <summary.json> --format json
```

## Decision points

| you see | do |
|---|---|
| EMIR-E104 the file holds a refused run's error | report that the run did not finish, with `details.run_error` (its code and message) and its next steps; there is no verdict |
| a "Decap-aware EM" section (`em_bases`, a `--dynamic` run) | relay the headline as it is (it names the EM basis and the other bases' verdicts) and the section; when it holds "The person's choice", put the choices to the person: the basis is theirs, never yours |
| `verdict` FAIL | lead with it; list each FAIL item with its fix; a FAIL is a result, not an error |
| `verdict` WARN | below the limits, inside the warn margin: say which items and by how much |
| `verdict` CLEAN | say so, then what was not checked: CLEAN covers only what was checked |
| `not_checked` non-empty | list every entry: layers or metrics without limits, unmapped layers, unsaved pins, vias assumed one cut, unsolved pieces, decaps whose straps the EM basis does not cover (`decap_straps_not_covered`) |
| `input_warnings` with EMIR-W306 | the limits are illustrative: say the numbers mean nothing for silicon |
| `limits` says "entered by hand" | say per layer that its limits were typed by hand and from which document (the report's "Where the limits came from"); a value with NO SOURCE (EMIR-W320) is named as such |
| `simulator_verification.status` `not verified` (EMIR-W464) | say that the simulator's route is not verified on this machine and that `emir verify-sim --sim xyce` (its simulator's name) is the check (the report's line beside the verdict); `reason` `stale`: the verification is from another EMIR version |
| `simulator_verification.status` `partly verified`, or `reason` `pin kinds not covered` (EMIR-W464) | say which kinds of current are verified and which are NOT (`pin_kinds`: diode, BJT and JFET terminals are never covered), naming the unverified pins the message lists; those currents are unverified however the run reads |
| `simulator_verification.reason` `format not covered` (EMIR-W464) | say the result's format (CSDF, Spectre's nutascii) is one no verification covers, so its currents are unverified; no `emir verify-sim` helps |
| `simulator_verification.reason` `failed` (EMIR-W414) | say plainly that the simulator's route FAILED its verification on this machine and the numbers must not be relied on |
| `simulator_verification.reason` `version unknown` (EMIR-W416) | say the run is not verified because the result does not say which simulator version wrote it; no `emir verify-sim` helps (simulating through `emir simulate` records the version) |
| `simulator_verification.reason` `records unreadable` (EMIR-W415, EMIR-W464) | say this user may not read the verification records, so no verification counts here; the person gives read access or sets `EMIR_STATE_DIR` to a directory of their own |
| `simulator_verification.status` `unknown simulator` | say the result does not name its simulator; `--sim` on the run lets the verification be looked up |
| a number is null | say "not a number" (a net in pieces, a failed solve), never 0 |
| the person asks about one segment | `emir report --format json` holds every worst item; `--html` pages have the net maps |
| the person can see images (a review, a channel, a chat that takes attachments) | attach the chart the run drew, beside the text, never instead of it: the report's "Chart" line, `charts` in `emir report --format json` (a run with `--html`: `emir_chart.png`, every net on the layout, EM and IR, the worst items numbered as in the report; `emir_chart_light.png` for a light page). Its colours: blue by ratio below the warn fraction, amber in the warn band, red at 1x and over or not a number, grey no limit stated; name a colour only with its number. Its subtitle names the mode, the loop and the EM basis; on a `--dynamic` run its IR panel is the average drop, not the droop: quote the dynamic droop from the report (the chart's note gives only its peak). No `charts` and EMIR-I720 in `notices`: matplotlib is missing; relay the report alone |

## Success criteria

- The message you send states, from the report: the verdict; per net the governing metric and worst ratio with the
  item and its fix; the IR drop per power net; the mode and where the currents came from, and whether that
  simulator is verified on this machine; the analysis temperature; what was not checked; "Screening, not
  sign-off."; and the research-project disclaimer.
- Every number in it is one EMIR printed (rounded as EMIR rounded it); none is yours.

## What to report

Relay the Markdown as it is, or this shape filled from `emir report --format json`:

```text
EMIR screening of <design>: <verdict> (<counts>). Screening, not sign-off.
- Worst: <net> <metric> <ratio>x on <layer> <name> -> fix: <fix>
- IR drop: <net> <ir_drop_mV> mV
- Currents: <current_source.description>; mode <modes>; analysed at <temp_C> C (limits quoted at <em_tref_C> C)
- Simulator route: <simulator_verification.status> on this machine (<simulator> <version>)
- Limits: <limits, one line each: entered by hand / imported / public PDK data, and from which document>
- Not checked: <not_checked, one line each>
- Warnings: <input_warnings and run_warnings>
EMIR is a research project, provided as is, with no guarantee of accuracy: test and verify these results for your
process, tools and design before relying on them.
```

For the worked example: "EMIR screening of railblock: FAIL (1 net: 1 FAIL). Screening, not sign-off. Worst: VDD
avg 1.667x on Metal1 Rm1-Rm3 -> widen 0.750 -> 1.255 um; Cont Rc3 1.333x -> 1 -> 2 cuts. IR drop: VDD 54.0 mV.
Currents: DC, given with --currents (avg = rms = peak); analysed at 100 C. Not checked: switching transients
(DC currents). Warning: the process file is the illustrative sample; its limits are invented. EMIR is a research
project, provided as is, with no guarantee of accuracy: test and verify these results before relying on them."

## Failure modes

| code | exit | meaning | do |
|---|---|---|---|
| EMIR-E104 | 2 | not a run summary (an error document, or another file) | see above; or point `--from` at the summary |
| EMIR-E101 | 2 | the file does not exist | the path `--summary-out` was given |
| EMIR-E103 | 2 | `--from -` with nothing on stdin | give the file |
