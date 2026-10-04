;; SPDX-License-Identifier: MIT
;; Copyright (c) 2026 Vijay Gupta and contributors
;; Part of EMIR, the open-source EM/IR screening tool. Not a Cadence-supplied file.
;; ============================================================
;; EMIR -- Virtuoso entry point for a clone or the offline bundle (emir_init.il).
;; UNVERIFIED: not run on a live Virtuoso yet (agent/cadence/LIVE_TEST.md, step 4).
;;
;; One line in ~/.cdsinit (or the site .cdsinit):
;;   load(strcat(getShellEnvVar("EMIR_HOME") "/agent/cadence/skill/emir_init.il"))
;;
;; Environment, read when Virtuoso starts (an empty value counts as unset):
;;   EMIR_HOME      the clone or the unpacked bundle (required)
;;   EMIR_BIN       the emir command (default <EMIR_HOME>/bin/emir)
;;   EMIR_WORK      root of the run directories (default emir_out)
;;   EMIR_PROCESS   default process file (your PDK's EMIR process YAML)
;;   EMIR_LIB       default OA library for the Explorer
;;   EMIR_VIEW      the layout view the Explorer opens (default layout)
;;   EMIR_UNSET_LD  1: start emir without Virtuoso's LD_LIBRARY_PATH
;; emir_main.il reads them itself; this file checks EMIR_HOME, tries to merge
;; the colour packets, and loads emir_main.il from the same directory. Nothing
;; here is site- or PDK-specific, and no path is baked in.
;; ============================================================

let( (home dir drf)
  home = getShellEnvVar("EMIR_HOME")
  if( or(null(home) home == "")
    then
      printf("EMIR: $EMIR_HOME is not set -- the EMIR menu is not loaded\n")
    else
      dir = strcat(home "/agent/cadence/skill")
      ;; The Explorer's colour packets. drLoadDrf may merge emir_display.drf
      ;; into the running session (UNVERIFIED: live test step 4 decides).
      ;; Where it is not callable or fails, the packets come from appending
      ;; emir_display.drf to ~/display.drf instead (never replace a project
      ;; display.drf with it).
      drf = strcat(dir "/emir_display.drf")
      when( isFile(drf)
        unless( and( isCallable('drLoadDrf) car(errset(drLoadDrf(drf t))) )
          printf("EMIR: colour packets not merged at runtime; append %s to ~/display.drf and restart\n"
                 drf) ))
      if( isFile(strcat(dir "/emir_main.il"))
        then load(strcat(dir "/emir_main.il"))
        else printf("EMIR: %s/emir_main.il is missing -- is EMIR_HOME a clone or the bundle?\n" dir) )))
