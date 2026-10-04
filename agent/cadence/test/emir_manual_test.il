;; SPDX-License-Identifier: MIT
;; Copyright (c) 2026 Vijay Gupta and contributors
;; Part of EMIR, the open-source EM/IR screening tool. Not a Cadence-supplied file.
;; ============================================================
;; emir_manual_test.il -- the one-time live check of EMIR's Virtuoso SKILL.
;; UNVERIFIED: written without a Virtuoso (its parentheses balance; it has not
;; run). The procedure around it is agent/cadence/LIVE_TEST.md.
;;
;; Before Virtuoso, in a shell:
;;   emir selftest                                  # must print SELFTEST PASSED
;;   emir gui-install --dir /tmp/emir_manual        # emir_main.il, emir_explorer.il,
;;                                                  # emir_display.drf, emir_site.il
;;   (pass 1: do NOT add emir_display.drf to ~/display.drf yet)
;;
;; In the CIW of an interactive Virtuoso:
;;   load("<this file>"), set the emirT_* variables below, then in order
;;   emirT0() emirT1() emirT2()   -- look at the windows --
;;   emirT3()   -- wait for "Inputs OK." --
;;   emirT4()   -- wait for the Explorer --
;;   emirT5() emirT6()   -- close the Explorer form by hand --   emirT6b()
;;   emirT7() emirT8() emirT9()   -- close the dialog --   emirT9a()   -- wait --   emirT9b()
;; Paste every line starting with EMIRTEST (and any *Error* line) back.
;;
;; WARNING: emirT1() creates (overwrites) <emirT_lib>/<emirT_cell> layout and
;; schematic views. Use a scratch library attached to your PDK's tech.
;; ============================================================

emirT_lib    = "MYSCRATCH"            ; writable scratch library, attached to your PDK tech
emirT_cell   = "emir_selftest"        ; created by emirT1()
emirT_dir    = "/tmp/emir_manual"     ; where `emir gui-install --dir` wrote the files
emirT_sample = "/path/to/emir/emir/data/sample"   ; the folder with rail.dspf and process.yaml
emirT_lpp    = nil                    ; e.g. list("M1" "drawing"): gives the scratch cell a bBox

;; the sample's DC currents (the same as `emir selftest` uses)
emirT_cur = "{\"VDD\": 0.004, \"MP0:s\": -0.001, \"MP1:s\": -0.001, \"MP2:s\": -0.001, \"MP3:s\": -0.001}"

procedure( emirTSay(step verdict fmt @rest args)
  printf("EMIRTEST %s %-4s %s\n" step verdict apply('sprintf cons(nil cons(fmt args)))) )

;; T0 -- which functions this Virtuoso has (MISS = that code path cannot run here)
procedure( emirT0()
  printf("EMIRTEST T0 INFO version %L\n" getVersion(t))
  foreach( f '(geCreateHilightSet geAddHilightRectangle geDeleteHilightSet
               geDeleteHilightObject geIsValidHilightSet geChangeHilightPacketName
               geSetHilightSetHaloParameters geDrawHilightSet hiZoomIn hiGetViewBBox
               hiGetAbsWindowScreenBBox hiRegZoomPanProc hiUnregZoomPanProc hiRegTimer
               hiSync hiLoadIconFile hiIsForm hiIsFormDisplayed hiFormCancel
               hiCreateAppForm hiCreateScaleField hiCreateMLTextField
               hiCreateBooleanButton hiCreateButtonBoxField hiCreatePulldownMenu
               hiInsertBannerMenu deRegUserTriggers leGetEntryLayer leSetLayerVisible
               ipcBeginProcess pcreCompile pcreReplace createDirHier drLoadDrf
               drGetPacket getShellEnvVar symeval rindex makeTable)
    emirTSay("T0" if(isCallable(f) "ok" "MISS") "%s" f) )
  emirTSay("T0" "INFO" "emir functions before loading: %L" listFunctions("^emir"))
  t )

;; T1 -- a scratch layout and a same-named schematic (to catch the window-picking bug)
procedure( emirT1()
  let( (cv)
    cv = car(errset(dbOpenCellViewByType(emirT_lib emirT_cell "layout" "maskLayout" "w") t))
    if( cv
      then
        when( emirT_lpp errset(dbCreateRect(cv emirT_lpp list(-1.0:-2.0 41.0:2.0)) t) )
        dbSave(cv) dbClose(cv)
        emirTSay("T1" "PASS" "layout %s/%s created" emirT_lib emirT_cell)
      else
        emirTSay("T1" "FAIL" "cannot create %s/%s layout -- writable, tech-attached library?"
                 emirT_lib emirT_cell) )
    cv = car(errset(dbOpenCellViewByType(emirT_lib emirT_cell "schematic" "schematic" "w") t))
    when( cv dbSave(cv) dbClose(cv) )
    emirTSay("T1" if(cv "PASS" "FAIL") "schematic %s/%s created" emirT_lib emirT_cell)
    t ))

;; T2 -- load the site stub (it loads emir_main.il), open the schematic FIRST, then the layout
procedure( emirT2()
  let( (ok)
    ok = car(errset(load(strcat(emirT_dir "/emir_site.il")) t))
    emirTSay("T2" if(and(ok isCallable('emirMain)) "PASS" "FAIL") "emir_site.il + emir_main.il loaded")
    emirSettings()
    geOpen(?lib emirT_lib ?cell emirT_cell ?view "schematic" ?mode "r")
    geOpen(?lib emirT_lib ?cell emirT_cell ?view "layout" ?mode "r")
    emirTSay("T2" "LOOK" "both new windows (schematic AND layout) show exactly ONE EMIR pulldown")
    emirTSay("T2" "LOOK" "the CIW prints `emir info` below (the child Python starts under Virtuoso's environment)")
    emirSiteInfo()
    t ))

;; the Screening form on the sample, with `out` as the output directory
procedure( emirTForm(out)
  let( (f)
    f = emirMain(emirT_lib emirT_cell)
    f->emirMainDspf->value = strcat(emirT_sample "/rail.dspf")
    f->emirMainProc->value = strcat(emirT_sample "/process.yaml")
    f->emirMainCur->value  = emirT_cur
    f->emirMainNets->value = "VDD"
    f->emirMainOut->value  = out
    f->emirMainHtml->value = t
    f ))

;; T3 -- Check inputs
procedure( emirT3()
  emirTForm(strcat(emirT_dir "/run"))
  emirDoCheck()
  emirTSay("T3" "LOOK" "the log pane streams `emir check`, then a dialog says \"Inputs OK.\"")
  t )

;; T4 -- Run; when it ends the Explorer must open on the LAYOUT window
procedure( emirT4()
  emirDoRun()
  emirTSay("T4" "LOOK" "the run log streams line by line (not one burst at the end); about 2 s after it ends the Explorer form opens")
  t )

;; T5 -- the Explorer's state (expected numbers: tests/test_cadence_viewer.py)
procedure( emirT5()
  let( (w cv)
    w  = emirWin()
    cv = when( w w->cellView )
    emirTSay("T5" if(and(cv cv->cellViewType == "maskLayout") "PASS" "FAIL")
             "the Explorer drives a %L window (must be maskLayout)" when(cv cv->cellViewType))
    emirTSay("T5" if(and(cv cv->mode == "r") "PASS" "FAIL")
             "the layout is open read-only (mode %L, must be \"r\")" when(cv cv->mode))
    emirTSay("T5" "INFO" "packets found: %L   colours from: %s   mark LPP: %L"
             emirPacketsOK emirColourPath emirMarkLPP)
    emirTSay("T5" if(and(emirHS forall(h emirHS car(errset(geIsValidHilightSet(h))))) "PASS" "FAIL")
             "%d band highlight sets, all valid" length(emirHS))
    emirTSay("T5" if(length(emirShown) == 10 "PASS" "FAIL")
             "%d EM rows at the default 80%% (expect 10)" length(emirShown))
    emirTSay("T5" if(emirSelHS "PASS" "FAIL") "the selection ring exists")
    emirTSay("T5" if(emirSelfTest() "PASS" "FAIL") "emirSelfTest()")
    emirTSay("T5" "LOOK" "bars along x = 0..40 um, a white ring on the worst one (Rm1); pass 1: one colour and a CIW line \"colours need emir_display.drf\"; pass 2: the colours of the legend")
    t ))

;; T6 -- navigation, IR mode, Clear
procedure( emirT6()
  emirNext() emirNext() emirPrev()
  emirTSay("T6" if(emirCursor == 1 "PASS" "FAIL") "cursor after Next Next Prev = %d (expect 1)" emirCursor)
  emirForm->emirMode->value = "IR"  emirRedraw()
  emirTSay("T6" if(length(emirShown) == 16 "PASS" "FAIL") "IR mode lists %d segments (expect 16)" length(emirShown))
  emirForm->emirMode->value = "EM"  emirRedraw()
  emirClear() hiRedraw()
  emirTSay("T6" if(and(null(emirHS) null(emirSelHS)) "PASS" "FAIL") "Clear removed every highlight set")
  emirTSay("T6" "LOOK" "now CLOSE the Explorer form by hand, then run emirT6b()")
  t )

;; T6b -- after closing the Explorer form by hand
procedure( emirT6b()
  emirTSay("T6" if(and(null(emirForm) null(emirHS) null(emirSelV)) "PASS" "FAIL")
           "closing the form cleared it (emirForm %L, %d sets, ring on %L)"
           emirForm length(emirHS) emirSelV)
  emirTSay("T6" "LOOK" "pan and zoom the layout AND the schematic: no ring may reappear, and no CIW errors")
  t )

;; T7 -- reload twice, then a new window: exactly ONE EMIR menu
procedure( emirT7()
  load(strcat(emirT_dir "/emir_site.il"))
  load(strcat(emirT_dir "/emir_site.il"))
  geOpen(?lib emirT_lib ?cell emirT_cell ?view "layout" ?mode "r")
  emirTSay("T7" "LOOK" "the window just opened has exactly ONE EMIR pulldown; earlier windows still have theirs")
  emirTSay("T7" "LOOK" "also open the layout in Layout XL (and EXL on IC23.1): does it get the menu?")
  t )

;; T8 -- an output directory with a space in its name: Check, Run and Open HTML must work
procedure( emirT8()
  emirTForm("/tmp/emir manual/run")
  emirDoCheck()
  emirTSay("T8" "LOOK" "\"Inputs OK.\"; then press Run, wait, press Open HTML: all three work with the space")
  t )

;; T9 -- Save lines with a testbench prefix, written to a file. The Mode
;; starts at "choose": Save lines must refuse until a mode is picked
procedure( emirT9()
  let( (f)
    f = emirTForm(strcat(emirT_dir "/run"))
    f->emirMainPrefix->value = "XDUT."
    f->emirMainMode->value = "choose"
    emirDoSaveLines()
    emirTSay("T9" "LOOK" "a dialog says to choose the Mode first (rcc or cc-grid); close it, then run emirT9a()")
    t ))

procedure( emirT9a()
  let( (f)
    f = emirMainForm
    f->emirMainMode->value = "rcc"
    emirDoSaveLines()
    emirTSay("T9" "LOOK" "a dialog names <out>/emir_save.scs; then run emirT9b()")
    t ))

procedure( emirT9b()
  let( (p line)
    p = when( emirFileExists(emirMainSaveFile) infile(emirMainSaveFile) )
    when( p gets(line p) close(p) )
    emirTSay("T9" if( and(stringp(line) strlen(line) >= 10 substring(line 1 10) == "save XDUT.")
                      "PASS" "FAIL")
             "%L starts %L (expect \"save XDUT.Rm1:1 ...\")" emirMainSaveFile line)
    t ))

printf("EMIRTEST loaded: set emirT_lib, emirT_cell, emirT_dir, emirT_sample (and emirT_lpp), then emirT0() ... emirT9b()\n")
