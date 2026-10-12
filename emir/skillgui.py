"""The Virtuoso front end: an `EMIR` pulldown in the schematic and layout
windows, and the Screening form that drives the `emir` command over IPC.

`MAIN` is the SKILL source of `emir_main.il`. It holds no path and no site
value: at load it reads each setting from what is already set (by a loader or
.cdsinit), else the environment (`EMIR_HOME`, `EMIR_BIN`, ...), else a default.
Two ways to load it:

* from a clone or the offline bundle: `EMIR_HOME=<clone>` and one line in
  `.cdsinit`, ``load(strcat(getShellEnvVar("EMIR_HOME")
  "/agent/cadence/skill/emir_init.il"))`` -- `emir_init.il` loads the
  committed copy `agent/cadence/skill/emir_main.il` (kept equal to `MAIN` by
  `scripts/gen_cadence_skill.py` and a test);
* from any install: `emir gui-install` writes `emir_main.il`, the Explorer,
  `emir_display.drf` and a site stub `emir_site.il` (the one file with this
  machine's paths) into `<clone or bundle>/skill/` or `~/.emir/skill/`, and
  `.cdsinit` loads the stub.

What it gives the engineer:

* **EMIR > Screening...** on a schematic or layout window -- the form,
  pre-filled with the window's lib/cell and the defaults. `Check inputs`
  runs the checker; `Run` runs the analysis, streams the log into the form,
  and on success opens the Explorer on the layout, read-only.
* **Save lines** (the form button; the menu item **Spectre save lines...**
  opens the form) -- writes the `save` statements the run needs to
  `<output>/emir_save.scs`, for the testbench to include, with the testbench
  instance prefix applied: every branch current of the RC netlist, which a
  run on the simulation result reads (rcc).
* **EMIR > Load results...** -- opens an existing run's viewer data.
* **EMIR > Site info** -- what `emir info` says, in the CIW.

Nothing here parses results: the Python side writes SKILL literals
(`emir_data.il`) and the Explorer reads them. The form only builds the
command line, watches the process, and hands over.

Status: UNVERIFIED until checked on site -- the menu, the form and the IPC are
verified on site by the local agent (or the person) on the Virtuoso that uses
them: the headless SKILL self-test
(`python agent/cadence/python/virtuoso_batch.py --selftest`), then
`agent/cadence/LIVE_TEST.md`.
"""
from __future__ import annotations

import os

from .repo import REPO_URL

#: the licence header of every generated SKILL and display-resource file
HEADER = (";; SPDX-License-Identifier: MIT\n"
          ";; Copyright (c) 2026 Vijay Gupta and contributors\n"
          ";; Part of EMIR, the open-source EM/IR screening tool. "
          "Not a Cadence-supplied file.\n")

MAIN = HEADER + r''';; ============================================================
;; EMIR -- the EMIR menu and the Screening form (emir_main.il).
;; UNVERIFIED until checked on site: the local agent verifies it with the
;; headless self-test (agent/cadence/python/virtuoso_batch.py --selftest) and
;; agent/cadence/LIVE_TEST.md on the Virtuoso that loads it.
;;
;; Loaded by agent/cadence/skill/emir_init.il, or by the emir_site.il stub
;; `emir gui-install` writes. This file holds no path: each setting below is
;; taken, once, from what is already set (by the loader or .cdsinit), else
;; from the environment, else the default. An empty value counts as unset.
;;   emirInstall  EMIR_HOME      the clone or bundle               ""
;;   emirBin      EMIR_BIN       the emir command                  <EMIR_HOME>/bin/emir, else emir
;;   emirWorkDir  EMIR_WORK      root of the run directories       emir_out
;;   emirDefProc  EMIR_PROCESS   default process file              ""
;;   emirDefLib   EMIR_LIB       default OA library                ""
;;   emirView     EMIR_VIEW      the layout view to open           layout
;;   emirUnsetLD  EMIR_UNSET_LD  "1": start emir without Virtuoso's LD_LIBRARY_PATH
;; ============================================================

procedure( emirSetting(sym var dflt)
  ;; a non-empty value already set wins, then the environment, then dflt
  let( (cur env)
    cur = when( boundp(sym) symeval(sym) )
    unless( and(stringp(cur) cur != "")
      env = getShellEnvVar(var)
      set(sym if( and(stringp(env) env != "") env dflt )) )
    symeval(sym) ))

emirSetting('emirInstall "EMIR_HOME" "")
emirSetting('emirBin "EMIR_BIN"
            if( and(emirInstall != "" isFile(strcat(emirInstall "/bin/emir")))
                strcat(emirInstall "/bin/emir") "emir" ))
emirSetting('emirWorkDir "EMIR_WORK" "emir_out")
emirSetting('emirDefProc "EMIR_PROCESS" "")
emirSetting('emirDefLib "EMIR_LIB" "")
emirSetting('emirView "EMIR_VIEW" "layout")
unless( boundp('emirUnsetLD) emirUnsetLD = (getShellEnvVar("EMIR_UNSET_LD") == "1") )

unless( boundp('emirMainForm)  emirMainForm  = nil )
unless( boundp('emirMainProc)  emirMainProc  = nil )   ; running child, if any
unless( boundp('emirMainLog)   emirMainLog   = nil )   ; log lines, newest last
unless( boundp('emirMainPend)  emirMainPend  = nil )   ; what to do on exit
unless( boundp('emirMainOut)   emirMainOut   = nil )   ; output dir of the last run
unless( boundp('emirMainSaveFile) emirMainSaveFile = nil )   ; the last save-lines file

;; print every setting and where the menu comes from (the live test reads it)
procedure( emirSettings()
  printf("EMIR settings: emirInstall=%L emirBin=%L emirWorkDir=%L emirDefProc=%L emirDefLib=%L emirView=%L emirUnsetLD=%L\n"
         emirInstall emirBin emirWorkDir emirDefProc emirDefLib emirView emirUnsetLD)
  t )

;; ---------------- small helpers ----------------
;; One word for /bin/sh: wrapped in single quotes, and a ' inside is closed,
;; escaped and reopened. ipcBeginProcess runs its command through a shell, so
;; every path and value that goes into a command line goes through here.
procedure( emirShQ(s)
  let( (out c)
    out = "'"
    for( i 1 strlen(s)
      c = substring(s i 1)
      out = strcat(out if( c == "'" "'\\''" c )) )
    strcat(out "'") ))

procedure( emirTrim(s)
  if( stringp(s) pcreReplace(pcreCompile("^\\s+|\\s+$") s "" 0) "" ))

procedure( emirBlank(s) or(null(s) emirTrim(s) == "") )

procedure( emirFileExists(p) and(stringp(p) p != "" isFile(p)) )

procedure( emirCurLibCell()
  ;; the cellview of the window the menu was picked in
  let( (w cv)
    w = hiGetCurrentWindow()
    cv = when( w car(errset(w->cellView)) )
    if( cv list(cv->libName cv->cellName) list(emirDefLib "") )))

;; guess the DSPF from the conventions the guide describes:
;;   <work>/<cell>/<cell>.dspf   then   <work>/<cell>.dspf
procedure( emirGuessDspf(cell)
  let( (c)
    c = list( sprintf(nil "%s/%s/%s.dspf" emirWorkDir cell cell)
              sprintf(nil "%s/%s.dspf" emirWorkDir cell) )
    car(setof(p c emirFileExists(p))) ))

;; ---------------- the command line ----------------
;; The child must not inherit Virtuoso's Python environment: PYTHONHOME or
;; PYTHONPATH can break python3 or numpy (bin/emir drops them too), and an
;; unbuffered child streams its log line by line instead of in bursts.
procedure( emirEnvPrefix()
  strcat("env -u PYTHONHOME -u PYTHONPATH"
         if( emirUnsetLD " -u LD_LIBRARY_PATH" "")
         " PYTHONUNBUFFERED=1 "))

;; `emir <sub> @<args file>`: every argument is in the file, one per line
;; (emir reads it back verbatim, so JSON with quotes survives); the file's
;; own path and emirBin are shell-quoted.
procedure( emirCmdline(sub @optional argsFile)
  strcat(emirEnvPrefix() emirShQ(emirBin) " " sub
         if( argsFile strcat(" " emirShQ(strcat("@" argsFile))) "") ))

;; ---------------- log pane ----------------
procedure( emirMainSay(line)
  emirMainLog = append1(emirMainLog line)
  when( length(emirMainLog) > 400
    emirMainLog = nthcdr(length(emirMainLog) - 400 emirMainLog) )
  when( and(emirMainForm car(errset(emirMainForm->emirMainLogList)))
    emirMainForm->emirMainLogList->choices = emirMainLog
    ;; keep the newest line in view
    errset( emirMainForm->emirMainLogList->value =
            list(car(last(emirMainLog))) ))
  t )

procedure( emirMainClearLog()
  emirMainLog = nil
  when( emirMainForm errset(emirMainForm->emirMainLogList->choices = nil) )
  t )

;; ---------------- IPC ----------------
;; Output arrives in chunks that need not end on a line boundary: the part
;; after the last newline waits in emirIpcTail for the rest of its line.
unless( boundp('emirIpcTail) emirIpcTail = "" )

procedure( emirIpcData(cid data)
  let( (buf rest done)
    buf = strcat(or(emirIpcTail "") or(data ""))
    rest = rindex(buf "\n")               ; from the last newline on, or nil
    if( rest
      then
        done = substring(buf 1 strlen(buf) - strlen(rest))
        emirIpcTail = if( strlen(rest) > 1 substring(rest 2 strlen(rest) - 1) "" )
        foreach( l parseString(or(done "") "\n" t) emirMainSay(l) )
      else
        emirIpcTail = buf )
    t ))

procedure( emirIpcErr(cid data)
  emirIpcData(cid data) )

procedure( emirIpcExit(cid status)
  let( (pend)
    unless( emirBlank(emirIpcTail) emirMainSay(emirIpcTail) )
    emirIpcTail = ""
    emirMainSay( sprintf(nil "-- finished, exit %L --" status) )
    emirMainProc = nil
    pend = emirMainPend
    emirMainPend = nil
    when( emirMainForm
      errset( emirMainForm->emirMainBtns->enabled = t ) )
    cond(
      ( status != 0
        hiDisplayAppDBox(?name 'emirRunFailed ?dboxBanner "EMIR"
          ?dboxText cond( (pend == "check" "Input check found blocking problems -- see the log.")
                          (pend == "save"  "Save lines were not written -- see the log.")
                          (t               "The run did not complete -- see the log."))
          ?dialogType hicErrorDialog ?buttonLayout 'Close) )
      ( pend == "check"
        hiDisplayAppDBox(?name 'emirCheckOk ?dboxBanner "EMIR"
          ?dboxText "Inputs OK." ?dialogType hicMessageDialog
          ?buttonLayout 'Close) )
      ( pend == "save"
        hiDisplayAppDBox(?name 'emirSaveOk ?dboxBanner "EMIR"
          ?dboxText sprintf(nil "Save lines written to\n%s\nInclude it in the testbench."
                            emirMainSaveFile)
          ?dialogType hicMessageDialog ?buttonLayout 'Close) )
      ( pend == "run"
        when( and(emirMainForm emirMainForm->emirMainOpen->value)
          emirLoadViewer(strcat(emirMainOut "/viewer")) ) )
      ( t nil ))
    t ))

procedure( emirIpcStart(cmd pend)
 prog( ()
  when( emirMainProc
    hiDisplayAppDBox(?name 'emirBusyBox ?dboxBanner "EMIR"
      ?dboxText "A run is still in progress." ?dialogType hicWarningDialog
      ?buttonLayout 'Close)
    return(nil) )
  emirMainClearLog()
  emirMainSay( strcat("$ " cmd) )
  emirIpcTail = ""
  emirMainPend = pend
  errset( emirMainForm->emirMainBtns->enabled = nil )
  emirMainProc = ipcBeginProcess(cmd "" 'emirIpcData 'emirIpcErr 'emirIpcExit "")
  unless( emirMainProc
    emirMainSay("could not start the process")
    errset( emirMainForm->emirMainBtns->enabled = t )
    emirMainPend = nil )
  return(emirMainProc) ))

;; ---------------- arguments from the form ----------------
;; The output directory from the form ("." when blank).
procedure( emirFormOut()
  let( (out)
    out = emirTrim(emirMainForm->emirMainOut->value)
    if( out == "" "." out ) ))

;; Returns the list of arguments common to `check` and `run`, or nil after
;; reporting what is missing. The Python checker does the real validation;
;; this only refuses to build a command that could not possibly run.
procedure( emirMainArgs()
  prog( (f dspf proc sim per cur nets temp pwr out lib cell s)
    f = emirMainForm
    dspf = emirTrim(f->emirMainDspf->value)
    proc = emirTrim(f->emirMainProc->value)
    sim  = emirTrim(f->emirMainSim->value)
    per  = emirTrim(f->emirMainPeriod->value)
    cur  = emirTrim(f->emirMainCur->value)
    nets = emirTrim(f->emirMainNets->value)
    temp = emirTrim(f->emirMainTemp->value)
    pwr  = emirTrim(f->emirMainPower->value)
    out  = emirFormOut()
    lib  = emirTrim(f->emirMainLib->value)
    cell = emirTrim(f->emirMainCell->value)
    when( dspf == ""
      hiDisplayAppDBox(?name 'emirNoDspf ?dboxBanner "EMIR"
        ?dboxText "Give the DSPF file." ?dialogType hicErrorDialog
        ?buttonLayout 'Close)
      return(nil) )
    s = list(dspf)
    unless( proc == "" s = append(s list("--process" proc)) )
    unless( sim == ""  s = append(s list("--cc-sim" sim)) )
    unless( per == ""  s = append(s list("--period" per)) )
    unless( cur == ""  s = append(s list("--currents" cur)) )
    s = append(s list("--net" if( nets == "" "all" nets)))
    unless( temp == "" s = append(s list("--em-temp" temp)) )
    unless( pwr == ""  s = append(s list("--power-nets" pwr)) )
    unless( lib == ""  s = append(s list("--lib" lib)) )
    unless( cell == "" s = append(s list("--cell" cell)) )
    when( f->emirMainHtml->value
      s = append(s list("--html" strcat(out "/nets"))) )
    when( f->emirMainViewer->value
      s = append(s list("--viewer" strcat(out "/viewer"))) )
    emirMainOut = out
    return(s) ))

;; One argument per line; `emir <cmd> @file` reads it back verbatim.
procedure( emirWriteArgs(args path)
  prog( (p)
    errset( createDirHier(emirMainOut) )
    p = car(errset(outfile(path "w")))
    unless( p
      hiDisplayAppDBox(?name 'emirNoArgs ?dboxBanner "EMIR"
        ?dboxText sprintf(nil "Cannot write %s -- check the Output directory." path)
        ?dialogType hicErrorDialog ?buttonLayout 'Close)
      return(nil) )
    foreach( a args fprintf(p "%s\n" a) )
    close(p)
    return(path) ))

;; the command line for `emir <sub>`, or nil when the args file failed
procedure( emirCmd(sub args extra)
  let( (af)
    af = emirWriteArgs(append(args extra) strcat(emirMainOut "/emir_args.txt"))
    when( af emirCmdline(sub af) )))

procedure( emirDoCheck()
  let( (a c)
    a = emirMainArgs()
    c = when( a emirCmd("check" a list("--all")) )
    when( c emirIpcStart(c "check") )))

procedure( emirDoRun()
  let( (a c)
    a = emirMainArgs()
    c = when( a emirCmd("run" a list("--summary-out" strcat(emirMainOut "/summary.json"))) )
    when( c emirIpcStart(c "run") )))

;; Save lines go to <output>/emir_save.scs, not only to the log: on a real
;; block the 400-line log would lose the start, and a pin that was never
;; saved would fail the run's check. Every branch current is saved (rcc); the
;; testbench instance prefix is what the testbench calls the extracted block
;; (e.g. XDUT.).
procedure( emirDoSaveLines()
  prog( (f dspf nets prefix a c)
    f = emirMainForm
    dspf   = emirTrim(f->emirMainDspf->value)
    nets   = emirTrim(f->emirMainNets->value)
    prefix = emirTrim(f->emirMainPrefix->value)
    emirMainOut = emirFormOut()
    when( dspf == ""
      hiDisplayAppDBox(?name 'emirNoDspf2 ?dboxBanner "EMIR"
        ?dboxText "Give the DSPF file." ?dialogType hicErrorDialog
        ?buttonLayout 'Close)
      return(nil) )
    emirMainSaveFile = strcat(emirMainOut "/emir_save.scs")
    a = list(dspf "--out" emirMainSaveFile)
    unless( or(nets == "" nets == "all") a = append(a list("--net" nets)) )
    unless( prefix == "" a = append(a list("--prefix" prefix)) )
    c = emirCmd("save-lines" a nil)
    when( c emirIpcStart(c "save") )
    return(t) ))

procedure( emirDoHtml()
  let( (idx)
    idx = strcat(emirFormOut() "/nets/index.html")
    if( emirFileExists(idx)
      then
        emirMainSay( strcat("opening " idx) )
        ipcBeginProcess( strcat("xdg-open " emirShQ(idx)) )
      else
        hiDisplayAppDBox(?name 'emirNoHtml ?dboxBanner "EMIR"
          ?dboxText sprintf(nil "No report at %s -- run with HTML enabled." idx)
          ?dialogType hicWarningDialog ?buttonLayout 'Close) )))

;; ---------------- viewer hand-over ----------------
;; Load the run's data and app, make sure the layout is open, and start the
;; Explorer from a timer: geOpen returns before the window has done its first
;; zoom-to-fit, and an inline call would be undone by it. The layout is
;; opened read-only: highlight sets never change the cellview, and an edit
;; lock would fail on a read-only library and block whoever owns it.
procedure( emirLoadViewer(dir)
  prog( (d a lib cell)
    d = strcat(dir "/emir_data.il")
    a = strcat(dir "/emir_explorer.il")
    unless( and(emirFileExists(d) emirFileExists(a))
      hiDisplayAppDBox(?name 'emirNoViewer ?dboxBanner "EMIR"
        ?dboxText sprintf(nil "No viewer data in %s" dir)
        ?dialogType hicWarningDialog ?buttonLayout 'Close)
      return(nil) )
    load(d)
    load(a)
    lib  = emirLib
    cell = emirCell
    ;; a run without --lib writes emirLib = nil: take the form's Library
    ;; field, then the library of the window the menu was picked in
    when( and(emirBlank(lib) emirMainForm)
      lib = emirTrim(emirMainForm->emirMainLib->value) )
    when( emirBlank(lib)
      lib = car(emirCurLibCell()) )
    when( emirBlank(lib)
      hiDisplayAppDBox(?name 'emirNoLib ?dboxBanner "EMIR"
        ?dboxText "The run named no library (--lib): set Library in the EMIR form"
        ?dialogType hicErrorDialog ?buttonLayout 'Close)
      return(nil) )
    emirLib = lib
    ;; is this library's layout of the cell already up?
    unless( emirFindLayoutWin(lib cell)
      unless( ddGetObj(lib cell emirView)
        hiDisplayAppDBox(?name 'emirNoLayout ?dboxBanner "EMIR"
          ?dboxText sprintf(nil "%s/%s has no %s view in this cds.lib" lib cell emirView)
          ?dialogType hicErrorDialog ?buttonLayout 'Close)
        return(nil) )
      unless( car(errset(geOpen(?lib lib ?cell cell ?view emirView ?mode "r") t))
        hiDisplayAppDBox(?name 'emirNoOpen ?dboxBanner "EMIR"
          ?dboxText sprintf(nil "Could not open %s/%s/%s read-only -- see the CIW." lib cell emirView)
          ?dialogType hicErrorDialog ?buttonLayout 'Close)
        return(nil) ))
    hiRegTimer("emirExplorer()" 20)
    return(t) ))

procedure( emirDoViewer()
  emirLoadViewer( strcat(emirFormOut() "/viewer") ))

procedure( emirPickViewerDir()
  ;; menu entry: ask for a run directory, then load its viewer
  let( (f)
    f = hiCreateAppForm(?name 'emirPickForm ?formTitle "EMIR -- load results"
          ?fields list( hiCreateStringField(?name 'emirPickDir
                          ?prompt "run directory (holds viewer/)"
                          ?value or(emirMainOut emirWorkDir)) )
          ?callback "emirLoadViewer(strcat(emirTrim(emirPickForm->emirPickDir->value) \"/viewer\"))")
    hiDisplayForm(f) ))

;; ---------------- the form ----------------
procedure( emirMainFill(lib cell)
  ;; (re)populate from the current window and the defaults
  let( (f g)
    f = emirMainForm
    f->emirMainLib->value  = or(lib "")
    f->emirMainCell->value = or(cell "")
    when( emirBlank(f->emirMainDspf->value)
      g = when( cell emirGuessDspf(cell) )
      when( g f->emirMainDspf->value = g ) )
    when( emirBlank(f->emirMainProc->value)
      f->emirMainProc->value = emirDefProc )
    when( emirBlank(f->emirMainOut->value)
      f->emirMainOut->value = sprintf(nil "%s/%s" emirWorkDir or(cell "run")) )
    t ))

procedure( emirMain(@optional lib cell)
  prog( (lc libF cellF dspfF procF simF perF curF netF prefF tempF pwrF outF
         htmlT viewT openT btn lst)
    unless( lib
      lc = emirCurLibCell()
      lib = car(lc)  cell = cadr(lc) )
    when( and(emirMainForm car(errset(hiIsForm(emirMainForm))))
      emirMainFill(lib cell)
      hiDisplayForm(emirMainForm)
      return(emirMainForm) )

    libF  = hiCreateStringField(?name 'emirMainLib  ?prompt "Library" ?value "")
    cellF = hiCreateStringField(?name 'emirMainCell ?prompt "Cell"    ?value "")
    dspfF = hiCreateStringField(?name 'emirMainDspf ?prompt "DSPF"    ?value "")
    procF = hiCreateStringField(?name 'emirMainProc ?prompt "Process file" ?value "")
    simF  = hiCreateStringField(?name 'emirMainSim
              ?prompt "Spectre result (psfascii file or -raw dir)" ?value "")
    perF  = hiCreateStringField(?name 'emirMainPeriod
              ?prompt "Period of the block (s, e.g. 10n; a transient needs it)" ?value "")
    curF  = hiCreateStringField(?name 'emirMainCur
              ?prompt "or DC currents JSON {\"node\": A}" ?value "")
    netF  = hiCreateStringField(?name 'emirMainNets ?prompt "Net (or all)" ?value "all")
    prefF = hiCreateStringField(?name 'emirMainPrefix
              ?prompt "Testbench instance prefix (save lines), e.g. XDUT." ?value "")
    tempF = hiCreateStringField(?name 'emirMainTemp
              ?prompt "EM temperature C (blank = process default)" ?value "")
    pwrF  = hiCreateStringField(?name 'emirMainPower
              ?prompt "Power nets (comma; blank = infer)" ?value "")
    outF  = hiCreateStringField(?name 'emirMainOut ?prompt "Output directory" ?value "")
    htmlT = hiCreateBooleanButton(?name 'emirMainHtml ?buttonText "HTML report" ?value t)
    viewT = hiCreateBooleanButton(?name 'emirMainViewer ?buttonText "Viewer data" ?value t)
    openT = hiCreateBooleanButton(?name 'emirMainOpen
              ?buttonText "Open Explorer when done" ?value t)
    btn = hiCreateButtonBoxField(?name 'emirMainBtns
            ?choices list("Check inputs" "Run" "Save lines" "Open HTML" "Load viewer")
            ?callback list("emirDoCheck()" "emirDoRun()" "emirDoSaveLines()"
                           "emirDoHtml()" "emirDoViewer()"))
    lst = hiCreateListBoxField(?name 'emirMainLogList ?prompt ""
            ?choices nil ?numRows 16)

    emirMainForm = hiCreateAppForm(
      ?name 'emirMainForm
      ?formTitle "EMIR Screening (screening, not sign-off)"
      ?fields list(
        list(libF   0:0    330:26  110)
        list(cellF  340:0  330:26  60)
        list(dspfF  0:30   670:26  110)
        list(procF  0:60   670:26  110)
        list(simF   0:90   670:26  270)
        list(perF   0:120  670:26  330)
        list(curF   0:150  670:26  270)
        list(netF   0:180  670:26  110)
        list(prefF  0:210  670:26  330)
        list(tempF  0:240  670:26  270)
        list(pwrF   0:270  670:26  270)
        list(outF   0:300  670:26  110)
        list(htmlT  0:332  150:26)
        list(viewT  160:332 150:26)
        list(openT  320:332 250:26)
        list(btn    0:364  670:32)
        list(lst    0:402  670:290))
      ?initialSize 690:720
      ?minSize 690:690
      ?buttonLayout 'Close
      ?dontBlock t
      ?unmapAfterCB nil)
    emirMainFill(lib cell)
    hiDisplayForm(emirMainForm)
    when( emirMainLog emirMainForm->emirMainLogList->choices = emirMainLog )
    return(emirMainForm) ))

;; the "Spectre save lines..." menu item: the form, and what to fill in
procedure( emirSaveLinesForm()
  emirMain()
  emirMainSay("Save lines: set DSPF, Net and the testbench instance prefix, then press Save lines.")
  t )

;; ---------------- menus ----------------
procedure( emirCiwData(cid data) printf("%s" data) t )

procedure( emirSiteInfo()
  ipcBeginProcess( emirCmdline("info") "" 'emirCiwData 'emirCiwData nil "" ))

procedure( emirOpenNotes()
  let( (f)
    f = strcat(emirInstall "/agent/cadence/README.md")
    if( and(emirInstall != "" isFile(f))
      then ipcBeginProcess( strcat("xdg-open " emirShQ(f)) )
      else printf("EMIR: the Cadence notes are agent/cadence/README.md in the EMIR repository, @REPO_URL@ (set EMIR_HOME to a clone to open them from here)\n") )
    t ))

;; A new pulldown for every window: hiCreatePulldownMenu redefines the
;; symbol, and one menu object cannot be shared between windows.
procedure( emirBuildMenu()
  hiCreatePulldownMenu( 'emirPulldown "EMIR"
    list(
      hiCreateMenuItem(?name 'emirMenuMain ?itemText "Screening..."
                       ?callback "emirMain()")
      hiCreateMenuItem(?name 'emirMenuSave ?itemText "Spectre save lines..."
                       ?callback "emirSaveLinesForm()")
      hiCreateMenuItem(?name 'emirMenuLoad ?itemText "Load results..."
                       ?callback "emirPickViewerDir()")
      hiCreateMenuItem(?name 'emirMenuInfo ?itemText "Site info (CIW)"
                       ?callback "emirSiteInfo()")
      hiCreateMenuItem(?name 'emirMenuGuide ?itemText "Cadence integration notes"
                       ?callback "emirOpenNotes()") )))

;; the user menu trigger returns the list of menus to add to the banner
procedure( emirMenuTrigger(args)
  let( (m)
    m = car(errset(emirBuildMenu() t))
    if( m list(m) nil ) ))

;; Schematic and layout windows both get the pulldown. Registered ONCE per
;; session: re-loading this file (after an edit) must not register the
;; trigger again, or new windows get a second EMIR menu. Never
;; deUnRegUserTriggers: it removes every user trigger on the view type,
;; anyone else's included. A view type that cannot be registered (a -nograph
;; session) is not an error: emirAddMenu() puts the menu on a window by hand.
unless( and(boundp('emirMenuRegistered) emirMenuRegistered)
  foreach( vt list("schematic" "maskLayout")
    unless( car(errset(deRegUserTriggers(vt nil 'emirMenuTrigger)))
      printf("EMIR: no menu trigger on %s windows -- use emirAddMenu() on an open window\n" vt) ))
  emirMenuRegistered = t )

procedure( emirAddMenu(@optional w)
  let( (m)
    w = or(w hiGetCurrentWindow())
    m = car(errset(emirBuildMenu() t))
    when( and(w m) errset(hiInsertBannerMenu(w m 10)) )
    t ))

printf("EMIR: loaded (emir command %s). The EMIR menu is on schematic and layout windows opened from now on; emirMain() from the CIW.\n"
       emirBin)
'''
# the repository's address (emir/repo.py), for a Virtuoso user who installed
# the wheel: the Cadence notes are not on their disk
MAIN = MAIN.replace("@REPO_URL@", REPO_URL)


def display_drf() -> str:
    """The display-resource fragment that defines the Explorer's colour
    packets `EMIR_B0..11` (`layoutview.BAND_RGB`: EMIR's colour scale, the
    blue ramp, the warn amber, the failure red) and `EMIR_SEL`.

    Packets are defined while a display.drf is read, so the fragment is
    appended to the user's `~/display.drf` (created if there is none) --
    never put in place of a project or PDK display.drf, which would remove
    every layer's colours. `emir_init.il` also tries `drLoadDrf` on it at
    load (UNVERIFIED: whether that merges packets into a running session).
    Without the packets the Explorer draws every band in the one colour of
    its mark layer, and says so in the CIW.
    """
    from .layoutview import BAND_RGB
    L = [*HEADER.rstrip("\n").split("\n"),
         ";; EMIR Explorer colour packets EMIR_B0..11 (EMIR's colour scale: the blue",
         ";; ramp 0..9, the warn amber 10, the failure red 11) and EMIR_SEL.",
         ";; APPEND this to ~/display.drf (create the file if there is none) and",
         ";; restart Virtuoso. Never replace a project or PDK display.drf with it:",
         ";; that removes every layer's colours. UNVERIFIED on a live Virtuoso",
         ";; until agent/cadence/LIVE_TEST.md passes.",
         "",
         "drDefineColor(",
         *[f" ( display EMIR_C{i:<2d} {r:3d} {g:3d} {b:3d} )" for i, (r, g, b) in enumerate(BAND_RGB)],
         " ( display EMIR_WHITE 255 255 255 )",
         ")",
         "",
         "drDefinePacket(",
         *[f" ( display EMIR_B{i:<2d} solid solid EMIR_C{i} EMIR_C{i} )" for i in range(len(BAND_RGB))],
         " ( display EMIR_SEL blank solid EMIR_WHITE EMIR_WHITE )",
         ")", ""]
    return "\n".join(L)


def site_stub(main: str, emir_bin: str, install_root: str = "", settings=None) -> str:
    """`emir_site.il`: the one generated file with this machine's paths. It
    sets the emir command (and the clone, and any site-file default) and
    loads `emir_main.il`. Every value is a SKILL literal (`layoutview._sk`)."""
    from .layoutview import _sk
    L = [*HEADER.rstrip("\n").split("\n"),
         ";; EMIR site stub, written by `emir gui-install` for this machine (it holds",
         ";; this machine's paths: do not commit it). Load it from .cdsinit:",
         f";;   load({_sk(os.path.join(os.path.dirname(main), 'emir_site.il'))})",
         f"emirBin = {_sk(emir_bin)}"]
    if install_root:
        L.append(f"emirInstall = {_sk(install_root)}")
    for sym, v in (settings or {}).items():
        if v not in (None, ""):
            L.append(f"{sym} = {_sk(str(v))}")
    L.append(f"load({_sk(main)})")
    return "\n".join(L) + "\n"


def default_dir() -> str:
    """Where `emir gui-install` writes when no `--dir` is given.

    `<root>/skill` when the package runs from a source tree or the offline
    bundle (both have `bin/emir` next to the package), else `~/.emir/skill`:
    a pip install must never write into site-packages.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, ".."))
    if os.path.isfile(os.path.join(root, "bin", "emir")):
        return os.path.join(root, "skill")
    return os.path.join(os.path.expanduser("~"), ".emir", "skill")


#: the launcher `gui-install` writes when there is neither bin/emir (a clone
#: or the bundle) nor an `emir` console script next to the interpreter
LAUNCHER = """#!/bin/sh
# EMIR launcher written by `emir gui-install` (this machine's interpreter).
unset PYTHONHOME PYTHONPATH
PYTHONUNBUFFERED=1 exec {python} -m emir "$@"
"""


def _emir_command(out_dir: str) -> tuple:
    """`(emir command, clone root or "")` for the site stub: bin/emir of a
    clone or the bundle, else the `emir` script next to this interpreter,
    else a launcher written into `out_dir`."""
    import shlex
    import sys

    from .output import write_text
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, ".."))
    b = os.path.join(root, "bin", "emir")
    if os.path.isfile(b):
        return b, root
    script = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "emir")
    if os.path.isfile(script) and os.access(script, os.X_OK):
        return script, ""
    path = write_text(os.path.join(out_dir, "emir_launch.sh"),
                      LAUNCHER.format(python=shlex.quote(sys.executable)))
    os.chmod(path, 0o755)
    return path, ""


def install(out_dir: str | None = None) -> dict:
    """Write `emir_main.il`, the Explorer, `emir_display.drf` and the site
    stub `emir_site.il` into `out_dir`. `{"main", "app", "drf", "site"}`."""
    from . import site
    from .output import make_dir, write_text
    from .skillapp import APP
    out_dir = os.path.abspath(out_dir or default_dir())
    make_dir(out_dir)
    emir_bin, root = _emir_command(out_dir)
    # the site FILE's defaults only: the environment is read by emir_main.il
    # itself, at every Virtuoso start, and must not be frozen in here
    s = site.load()
    defaults = {"emirWorkDir": s.get("work_dir"), "emirDefProc": s.get("process"),
                "emirDefLib": s.get("lib")}
    main = write_text(os.path.join(out_dir, "emir_main.il"), MAIN)
    app = write_text(os.path.join(out_dir, "emir_explorer.il"), APP)
    drf = write_text(os.path.join(out_dir, "emir_display.drf"), display_drf())
    stub = write_text(os.path.join(out_dir, "emir_site.il"),
                      site_stub(main, emir_bin, root, defaults))
    print(f'add to .cdsinit:      load("{stub}")')
    print(f"append to ~/display.drf (create it if absent; never replace a project "
          f"display.drf): {drf}   (the Explorer colours)")
    return {"main": main, "app": app, "drf": drf, "site": stub}
