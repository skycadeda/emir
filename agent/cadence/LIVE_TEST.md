# Live Virtuoso test of EMIR's SKILL (on site)

**Status: not run yet.** No Virtuoso exists where this repository is built and
tested; CI can only check the SKILL as text (balanced parentheses, generated
properties, quoting). The repository claims no Virtuoso was verified anywhere: the
main form, the menu, the IPC, the Explorer (highlight, list, zoom, timer, colour
packets, one-colour fallback, clean-up on close) are **UNVERIFIED until checked on
site**. This procedure is that check. The local agent runs it with the person, on
the Virtuoso the site uses: the person drives the interactive session (the agent
never starts, kills or restarts it), the agent reads the CIW lines back and records
them. Before it, the headless self-test checks that the SKILL loads at all:

```sh
python agent/cadence/python/virtuoso_batch.py --selftest      # batch Virtuoso: ask first
```

## When and where

- Run it on the commit under test, on the Virtuoso release the site uses (record
  `getVersion(t)`), and again after a Virtuoso or EMIR upgrade.
- A writable **scratch** library attached to a PDK's tech. At least once, use a
  tech other than the one the old viewer was built around: the old fallback
  depended on that tech, so passing there proves nothing.

## What to copy to the Virtuoso host

- The repository (or the offline bundle) at the commit under test.
- `agent/cadence/test/emir_manual_test.il` (the CIW script; its parentheses balance,
  it has never run).
- **Warning:** `emirT1()` creates or overwrites `<lib>/<cell>` layout and schematic
  views. Use the scratch library only.

## Step 0: clean-session collision check

1. Start Virtuoso with nothing EMIR-related in `.cdsinit`.
2. In the CIW: `getVersion(t)`, `listFunctions("^emir")`, `listVariables("^emir")`.
3. **Pass:** both lists are nil (no other `emir*` names in this session). Record the
   version.

## Step 1: shell

1. `emir selftest` prints `SELFTEST PASSED`.
2. `emir gui-install --dir /tmp/emir_manual` writes `emir_main.il`,
   `emir_explorer.il`, `emir_display.drf` and `emir_site.il`.
3. `grep -c /tmp/emir_manual /tmp/emir_manual/emir_main.il` prints `0`: the main
   file holds no path of this machine; only `emir_site.il` does.
4. Do **not** add `emir_display.drf` to `~/display.drf` yet: pass 1 tests the
   no-packet fallback.

## Step 2: pass 1, without the packets

In the CIW: `load("<repo>/agent/cadence/test/emir_manual_test.il")`, then set

```
emirT_lib    = "<scratch lib>"
emirT_cell   = "emir_selftest"
emirT_dir    = "/tmp/emir_manual"
emirT_sample = "<repo>/emir/data/sample"
emirT_lpp    = list("<a drawing layer of this PDK>" "drawing")
```

Then, in order, waiting where noted:

| call | expect |
|---|---|
| `emirT0()` | the version and every MISS. The ones that matter most: geChangeHilightPacketName, drGetPacket, hiRegZoomPanProc, hiIsFormDisplayed, hiFormCancel, deRegUserTriggers, drLoadDrf, rindex |
| `emirT1()` | two PASS lines |
| `emirT2()` | the schematic window (opened first) and the layout window each show exactly ONE `EMIR` pulldown; `emirSettings()` prints the settings; the CIW prints `emir info`. If it says no python was found, the child inherited a bad Python environment (see `SKILL_NOTES.md`, IPC) |
| `emirT3()` | the log pane streams `emir check`; a dialog says "Inputs OK." |
| `emirT4()` | the run log streams line by line, not in one burst at the end; about 2 s after the run the Explorer opens on the LAYOUT window |
| `emirT5()` | PASS: drives a maskLayout window; PASS: the layout is open read-only (mode "r"); INFO: packets found nil, colours "one colour ..."; PASS: all highlight sets valid; PASS: **10** EM rows at the default 80 %; PASS: the selection ring exists; PASS: `emirSelfTest()`. LOOK: one CIW line "colours need emir_display.drf ...; all bands drawn in ..." and markers in one colour |
| `emirT6()` | PASS: cursor 1; PASS: IR mode lists **16** segments; PASS: Clear removed every set. Then close the Explorer form by hand |
| `emirT6b()` | PASS: the form, the sets and the ring are gone. LOOK: pan and zoom both the layout and the schematic; no ring reappears and the CIW shows no error |
| `emirT7()` | after two reloads, a newly opened layout window has exactly ONE `EMIR` menu. Also open the cell in Layout XL (and EXL on IC23.1) and note whether it gets the menu |
| `emirT8()` | output directory `/tmp/emir manual/run` (with a space): "Inputs OK."; then press Run and Open HTML in the form: both work |
| `emirT9()`, then `emirT9b()` | a dialog names `<out>/emir_save.scs`; PASS: the file starts `save XDUT.` |

The expected 10 and 16 come from the bundled sample (`tests/test_cadence_viewer.py`
computes them from a run of the same sample and currents).

## Step 3: pass 2, with the packets

1. **Append** (never replace) `/tmp/emir_manual/emir_display.drf` to `~/display.drf`;
   restart Virtuoso.
2. Repeat `emirT2()` to `emirT6b()`.
3. **Pass:** `emirT5()` prints `packets found: t`; the bands on the layout match the
   legend image in the form; the blue ramp (dark to light), the warn amber and the
   failure red read well on a black canvas, as on EMIR's charts (`emir/style.py`;
   this tunes the palette); the white selection ring is visible. Take a screenshot.

## Step 4: the environment-driven entry point

1. Do not use the gui-install output this time.
2. `export EMIR_HOME=<repo> EMIR_LIB=<scratch lib> EMIR_PROCESS=<repo>/emir/data/sample/process.yaml`
   (`EMIR_BIN` defaults to `<repo>/bin/emir`).
3. Put `load(strcat(getShellEnvVar("EMIR_HOME") "/agent/cadence/skill/emir_init.il"))`
   in `.cdsinit`; start Virtuoso.
4. In the CIW: `emirSettings()`. `emirBin` and `emirInstall` must be the
   environment's values.
5. Record whether `drLoadDrf` merged the packets: no "colour packets not merged"
   line, and `emirT5()` reports `packets found: t` without the `~/display.drf`
   append. That result decides whether the docs keep the manual append step.

## Step 5: targeted checks

a. **Read-only open.** Make the scratch library read-only, or have another session
   hold its edit lock. Load the viewer (EMIR > Load results...). Highlights must
   still draw, with no lock prompt and no copy-on-write dialog.

b. **A path with a space** is `emirT8()`.

c. **Injection regression.** Copy `rail.dspf` and change its design line to
   `*|DESIGN x"printf("INJECTED\n")"z`. Run

   ```sh
   emir run <copy> --net VDD --process <sample>/process.yaml \
     --currents '{"VDD":0.004,"MP0:s":-0.001,"MP1:s":-0.001,"MP2:s":-0.001,"MP3:s":-0.001}' \
     --viewer /tmp/emir_inj --lib <scratch lib>
   ```

   and `load("/tmp/emir_inj/emir_data.il")` in the CIW. The CIW must NOT print
   INJECTED. (Use printf in such a test, never system().)

d. **Optional, for the fallback design.** On the scratch layout run
   `hs = geCreateHilightSet(cv list("y0" "drawing")) geAddHilightRectangle(hs list(0:0 10:1)) geDrawHilightSet(hiGetCurrentWindow() hs)`
   and note whether it is visible. An old note that such reserved layers draw
   nothing came from a script that never ran.

e. If IC23.1 is available, repeat step 3 there.

## What to send back

- every CIW or CDS.log line starting with `EMIRTEST`, and any `*Error*` or
  `*WARNING*` line;
- the `getVersion(t)` output and the T0 MISS list;
- screenshots from pass 1 and pass 2;
- the results of steps 4 and 5.

## Overall pass

Every T-step PASS; one EMIR menu per window; no CIW error in T2 to T9; packets
found t in pass 2; no edit lock; the path with a space works; no INJECTED line.
Then the SKILL is verified on site, on that Virtuoso version, by the local agent:
keep the record at the site (the version and the lines above). The repository's
docs do not change: they never say a Virtuoso was verified anywhere.
