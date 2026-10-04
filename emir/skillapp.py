"""The Virtuoso EMIR Explorer, as SKILL source.

Kept as a Python string so it ships with the package and is written next to
each run's data. Load order in the CIW is `emir_data.il` then
`emir_explorer.il`, then `emirExplorer()`.

Design notes that matter for usability:

* **Highlight sets, rebuilt on every change.** No tech-file layers, no edits to
  the cellview, and filtering is instant. A band is a step 0..11 of EMIR's
  colour scale (0..9 the blue ramp, 10 the warn amber, 11 the failure red:
  the scale of every chart), drawn with the display packet `EMIR_B<step>`
  (`emir_display.drf`) on one LPP that exists in the tech file; without the
  packets every band is drawn in that LPP's colour and the CIW says so.
* **Nothing is modal.** The form stays up while the engineer pans, zooms and
  edits; every control re-renders immediately.
* **The list is the navigation.** Ranked worst-first; selecting a row zooms to
  that segment and prints its numbers and its fix. `Next`/`Prev` walk the same
  order so a whole net can be worked through without touching the list.
* **EM or IR, one at a time.** They rank by different quantities (a ratio, a
  drop in mV), so the ranked list, the detail pane, Next/Prev and the colour
  bar follow the one chosen with `Show`.

Status: UNVERIFIED until checked on site. The whole Explorer -- highlight,
list, zoom, timer, the colour packets, the one-colour fallback, the window
matching and the clean-up on close -- is verified on site by the local agent
(or the person) with `agent/cadence/LIVE_TEST.md` on the Virtuoso that uses
it; the headless SKILL self-test
(`python agent/cadence/python/virtuoso_batch.py --selftest`) checks first
that the SKILL loads there.
"""
from .skillgui import HEADER

APP = HEADER + r''';; ============================================================
;; EMIR Explorer -- EM / IR results on the layout (emir_explorer.il)
;; Load emir_data.il first, then this file, then: emirExplorer()
;; Status: UNVERIFIED until checked on site: verified on site by the local agent
;; with agent/cadence/LIVE_TEST.md (highlight, list, zoom, timer, colour packets,
;; the one-colour fallback, window matching, clean-up on close).
;; ============================================================

;; Colour. A band is a step of EMIR's colour scale, the one every chart and
;; HTML page draws: steps 0..9 the blue ramp, dark to light (the IR bands; EM
;; below the warn band by the ratio's log), 10 the warn band's amber, 11 the
;; failure red. Step k is drawn with the display packet EMIR_Bk
;; (emir_display.drf, generated from
;; layoutview.BAND_RGB like the legend images); the selection ring is
;; EMIR_SEL. Every highlight set sits on ONE layer-purpose pair, emirMarkLPP:
;; the site file's mark_layer if this tech file has it, else the current
;; entry layer -- no PDK layer or purpose is assumed. With the packets the LPP
;; only has to exist; without them every band is drawn in that LPP's own
;; colour, and the CIW says so once.
emirNumBands = 10
unless( boundp('emirMarkPurpose) emirMarkPurpose = "drawing" )
unless( boundp('emirPacketsOK)   emirPacketsOK   = nil )
unless( boundp('emirMarkLPP)     emirMarkLPP     = nil )
unless( boundp('emirColourPath)  emirColourPath  = "not probed yet" )
unless( boundp('emirSkippedHS)   emirSkippedHS   = 0 )
unless( boundp('emirFormSeq)     emirFormSeq     = 0 )

procedure( emirXBlank(s) or(null(s) and(stringp(s) s == "")) )

;; band step 0..11 -> "EMIR_B<step>"; 'sel -> the selection packet
procedure( emirPacketName(b)
  if( b == 'sel "EMIR_SEL" sprintf(nil "EMIR_B%d" b) ))

;; Are the EMIR packets there? They are defined while a display.drf is read,
;; so the Explorer cannot make them; it only finds out, once per start.
;; geChangeHilightPacketName must exist and accept EMIR_B0 on a throw-away
;; set; where the packet query drGetPacket exists it must also find EMIR_B11
;; (a release that accepted any name would otherwise report the packets
;; present with no drf loaded, and every highlight would be invisible). The
;; CIW gets one line naming the colour path in use.
procedure( emirDefinePackets(@optional cv)
  let( (hs ok why r)
    ok = nil
    why = ""
    cv = or(cv emirCV())
    cond(
      ( !isCallable('geChangeHilightPacketName)
        why = "this Virtuoso has no geChangeHilightPacketName" )
      ( !cv
        why = "no layout window" )
      ( t
        unless( emirMarkLPP emirPickMarkLPP(cv) )
        hs = car(errset(geCreateHilightSet(cv emirMarkLPP)))
        when( hs
          ok = car(errset(geChangeHilightPacketName(hs "EMIR_B0")))
          errset(geDeleteHilightSet(hs)) )
        unless( ok why = "the display packet EMIR_B0 is not defined" )
        ;; a query that fails says nothing either way: the packets stay in
        ;; use, marked unconfirmed
        when( and(ok isCallable('drGetPacket))
          r = errset(drGetPacket("display" "EMIR_B11"))
          cond( ( null(r)      why = " (unconfirmed: the packet query failed)" )
                ( null(car(r)) ok = nil
                               why = "drGetPacket finds no EMIR_B11 packet" )))))
    emirPacketsOK = ok
    if( ok
      then
        emirColourPath = strcat("the EMIR_B0..11 display packets" why)
        printf("EMIR: colours from %s\n" emirColourPath)
      else
        emirColourPath = sprintf(nil "one colour, %L (%s)" emirMarkLPP why)
        printf("EMIR: colours need emir_display.drf (%s); all bands drawn in %L\n"
               why emirMarkLPP) )
    ok ))

;; The LPP the highlight sets live on: the configured one (site file
;; `mark_layer`) if a highlight set can be created on it, else whatever the
;; user is currently drawing on -- which is always valid. With packets present
;; the LPP contributes nothing but existence. With no layer configured
;; (emirMarkLayer "") the entry layer is used directly, and its layer name
;; then stands in for emirMarkLayer in the no-packet fallback.
procedure( emirPickMarkLPP(cv)
  let( (lpp hs want)
    want = and(stringp(emirMarkLayer) emirMarkLayer != "")
    lpp = list(emirMarkLayer emirMarkPurpose)
    hs = when( want car(errset(geCreateHilightSet(cv lpp))) )
    if( hs
      then errset(geDeleteHilightSet(hs))
      else
        lpp = car(errset(leGetEntryLayer()))
        unless( and(lpp listp(lpp)) lpp = list(emirMarkLayer emirMarkPurpose) )
        if( want
          then printf("EMIR: %s/%s is not in the tech file; highlights use %L\n"
                      emirMarkLayer emirMarkPurpose lpp)
          else when( stringp(car(lpp)) emirMarkLayer = car(lpp) ) ) )
    emirMarkLPP = lpp
    lpp ))

;; One highlight set on the mark LPP, in the colour of band step `b` (or
;; 'sel). nil when the set cannot be created: the caller skips it, and the
;; redraw reports how many were skipped.
procedure( emirNewHS(cv b)
  let( (hs)
    unless( emirMarkLPP emirPickMarkLPP(cv) )
    hs = car(errset(geCreateHilightSet(cv emirMarkLPP)))
    if( hs
      then
        hs->enable = t
        when( and(emirPacketsOK
                  !car(errset(geChangeHilightPacketName(hs emirPacketName(b)))))
          ;; the packet is missing after all: one colour from now on
          emirPacketsOK = nil
          emirColourPath = sprintf(nil "one colour, %L (packet %s missing)"
                                   emirMarkLPP emirPacketName(b))
          printf("EMIR: packet %s not found; all bands drawn in %L\n"
                 emirPacketName(b) emirMarkLPP) )
      else
        emirSkippedHS = emirSkippedHS + 1 )
    hs ))

;; Live state is initialised ONLY if unbound, so re-loading this file to pick
;; up an edited tunable does not wipe the running form and orphan its highlight
;; sets. Tunables below are plain assignments precisely so a reload DOES take.
unless( boundp('emirHS)      emirHS      = nil )  ; live hilight sets
unless( boundp('emirShown)   emirShown   = nil )  ; rows currently displayed
unless( boundp('emirCursor)  emirCursor  = 0   )
unless( boundp('emirForm)    emirForm    = nil )
unless( boundp('emirSelHS)   emirSelHS   = nil )

;; ---------------- accessors on a violation record ----------------
procedure( emirVNet(v)    nth(0  v))
procedure( emirVName(v)   nth(1  v))
procedure( emirVLayer(v)  nth(2  v))
procedure( emirVKind(v)   nth(3  v))
procedure( emirVMetric(v) nth(4  v))
procedure( emirVRatio(v)  nth(5  v))
procedure( emirVValue(v)  nth(6  v))
procedure( emirVCurr(v)   nth(7  v))
procedure( emirVBox(v)    list( nth(8 v):nth(9 v) nth(10 v):nth(11 v) ))
procedure( emirVBand(v)   nth(12 v))
procedure( emirVHint(v)   nth(13 v))
procedure( emirVRat(v m)
  cond( (m == "avg"  nth(14 v))
        (m == "rms"  nth(15 v))
        (m == "peak" nth(16 v))
        (t           emirVRatio(v)) ))
procedure( emirVW(v)    nth(17 v))
procedure( emirVCuts(v) nth(18 v))
procedure( emirVValM(v m)
  cond( (m == "avg" nth(19 v)) (m == "rms" nth(20 v))
        (m == "peak" nth(21 v)) (t emirVValue(v)) ))
procedure( emirVCurM(v m)
  cond( (m == "avg" nth(22 v)) (m == "rms" nth(23 v))
        (m == "peak" nth(24 v)) (t emirVCurr(v)) ))

;; The fix depends on the metric the engineer is looking at: a via at 7.55x avg
;; needs 8 cuts, the same via at 1.66x rms needs 2. Re-derived on every redraw
;; rather than baked in at export, so the hint can never disagree with the
;; ratio shown beside it.
procedure( emirHintFor(v metric)
  let( (r n need w)
    r = emirVRat(v metric)
    ;; only prescribe a fix for something that actually fails -- below 1.00x
    ;; the arithmetic would tell the engineer to make the wire NARROWER, which
    ;; is not a fix, it is a hazard
    if( or(null(r) r < 1.0) then ""
    else
      w = emirVW(v)
      n = emirVCuts(v)
      ;; strictly past the limit, as the reports say (layoutview.fix_hint):
      ;; at exactly 1.00x the item still FAILs, so floor(n r) + 1 cuts and the
      ;; next 5 nm strictly above w r
      cond(
        ( and(emirVKind(v) == "via" n > 0)
          need = floor(float(n) * r + 1e-9) + 1
          sprintf(nil "%s: %d -> %d cuts (+%d)"
                  emirVLayer(v) n need need - n) )
        ( and(emirVKind(v) != "via" w > 0.0)
          sprintf(nil "%s: widen %.3f -> %.3f um (x%.2f)"
                  emirVLayer(v) w (floor(w * r / 0.005 + 1e-9) + 1) * 0.005 r) )
        ( t
          sprintf(nil "%s: over by x%.2f" emirVLayer(v) r) )))))

;; ---------------- text, kept separate so it can be exercised headless ----
;; Every one of these runs over real records the moment the form redraws, so a
;; sprintf that cannot format some record is a crash in front of the engineer.
;; Factored out here, emirSelfTest() can format all of them before shipping.
procedure( emirStr(x)  if( stringp(x) x  if( x sprintf(nil "%L" x) "" )))

procedure( emirRowText(v n metric)
  sprintf(nil "%3d %-5s %-7s %-7s %-4s %7.3fx %9.4f  %s"
    n emirStr(emirVNet(v)) emirStr(emirVName(v)) emirStr(emirVLayer(v))
    emirStr(emirVKind(v)) float(emirVRat(v metric))
    float(emirVCurM(v metric)) emirStr(emirHintFor(v metric))))

;; Same widths as the row formats, so the header sits over its columns. It is
;; a separate field rather than row 0 of the list, which would scroll away and
;; be selectable.
procedure( emirHeaderText()
  if( emirIsIR()
    then sprintf(nil "%3s %-5s %-7s %9s"
                 "#" "net" "segment" "drop mV")
    else sprintf(nil "%3s %-5s %-7s %-7s %-4s %7s  %9s  %s"
                 "#" "net" "segment" "layer" "type" "ratio" "I (mA)"
                 "fix") ))

procedure( emirCountText(shown metric irThr)
  strcat(
    if( emirIsIR()
      then sprintf(nil "%d IR segments of %d   |   worst %.3f mV   |   >= %.2f mV"
             length(shown) length(emirIR)
             float(if(shown emirRMv(car(shown)) 0.0)) float(irThr))
      else sprintf(nil "%d shown of %d   |   %d FAIL  %d WARN   |   metric %s"
             length(shown) length(emirViol)
             length(setof(x shown emirVRat(x metric) >= 1.0))
             length(setof(x shown and(emirVRat(x metric) >= 0.8
                                      emirVRat(x metric) < 1.0)))
             emirStr(metric)) )
    if( length(shown) > emirMaxRows
        sprintf(nil "   |   list: first %d" emirMaxRows) "") ))

procedure( emirDetailText(v metric)
  let( (unit)
    unit = if( emirVKind(v) == "via" "mA/cut" "mA/um" )
    sprintf(nil
      "%s %s  %s %s   %s %.3fx   %.4f %s   I=%.4f mA   (worst: %s %.3fx)\n%s"
      emirStr(emirVNet(v)) emirStr(emirVName(v)) emirStr(emirVLayer(v))
      emirStr(emirVKind(v)) emirStr(metric) float(emirVRat(v metric))
      float(emirVValM(v metric)) unit float(emirVCurM(v metric))
      emirStr(emirVMetric(v)) float(emirVRatio(v))
      emirStr(emirHintFor(v metric)))))

;; ---------------- IR record accessors ----------------
;; (net mv band x0 y0 x1 y1 name)
procedure( emirRNet(r)   nth(0 r))
procedure( emirRMv(r)    nth(1 r))
procedure( emirRBand(r)  nth(2 r))
procedure( emirRBox(r)   list( nth(3 r):nth(4 r) nth(5 r):nth(6 r) ))
procedure( emirRName(r)  nth(7 r))

procedure( emirIrRowText(r n)
  sprintf(nil "%3d %-5s %-7s %9.4f"
    n emirStr(emirRNet(r)) emirStr(emirRName(r)) float(emirRMv(r))))

procedure( emirIrDetailText(r)
  sprintf(nil "%s %s   IR %.4f mV from the pin   band %d of %d\n%s"
    emirStr(emirRNet(r)) emirStr(emirRName(r)) float(emirRMv(r))
    emirRBand(r) + 1 emirNumBands
    "the pin is the reference: 0 mV there, worst at the far end of the feed path"))

;; Which of the two result sets the list, the detail pane and the navigation
;; are currently working on. EM and IR are mutually exclusive: they rank by
;; different quantities and a single ranked list cannot mean both at once.
unless( boundp('emirKind) emirKind = "EM" )
unless( boundp('emirLastKind) emirLastKind = nil )   ; so a mode CHANGE can reset the net selection

procedure( emirIsIR()  emirKind == "IR" )

;; EM band b -> its step of the ramp (emirBands rows: band lo label step)
procedure( emirBandStep(b)
  let( (r)
    r = 0
    foreach( e emirBands  when( car(e) == b  r = nth(3 e) ))
    r ))

;; ---------------- tracing ----------------
;; Set emirTrace = nil to silence. Each stamp prints wall-clock, so the gap
;; between two consecutive lines is the time that step actually took -- the
;; only way to see it, because the work is deferred past anything measureTime
;; can observe from inside SKILL.
emirTrace = nil   ; set to t in the CIW to trace every click and button
unless( boundp('emirBusy) emirBusy = nil )   ; set while SKILL rewrites the list, to mute changeCB
unless( boundp('emirRows) emirRows = nil )   ; the row strings currently in the list, in rank order
procedure( emirStamp(tag)
  when( emirTrace printf("EMIR-T %s  %s\n" getCurrentTime() tag) ))

;; ---------------- window / cellview ----------------
;; Find the layout window by the cell it holds, not by "current". The current
;; window follows the pointer, so with the CIW or a second layout open, zoom
;; and highlight would land somewhere else -- or nowhere. Only a maskLayout
;; window counts: the menu is on schematic windows too, and a schematic of
;; the same cell must never receive the highlights. The library must match
;; too, when the data names one.
procedure( emirIsLayoutWin(w)
  let( (cv)
    cv = when( w car(errset(w->cellView)) )
    and( cv car(errset(cv->cellViewType == "maskLayout")) ) ))

procedure( emirFindLayoutWin(lib cell)
  car(setof(w hiGetWindowList()
            and( emirIsLayoutWin(w)
                 w->cellView->cellName == cell
                 or( emirXBlank(lib) w->cellView->libName == lib ) ))))

procedure( emirWin()
  let( (best cur)
    best = emirFindLayoutWin(emirLib emirCell)
    unless( best
      ;; the current window, but only if it is a layout
      cur = hiGetCurrentWindow()
      when( emirIsLayoutWin(cur) best = cur ))
    best ))

procedure( emirCV()
  let( (w) w = emirWin()  when(w w->cellView) ))

;; ---------------- view ----------------
;; hiZoomIn(window bbox) frames a box in one call, and it is NOT what made
;; changing rows slow -- hiRedraw() was, which takes no window and repaints
;; everything. In our tests hiPan and geCenter left the view unchanged, and
;; hiZoomAbsoluteScale only magnifies about the current centre; hiZoomIn with
;; a window bbox moved it, so that is the form used.
emirZoomMin  = 0.5     ; um -- never frame tighter than this
emirZoomPad  = 3.0     ; show this many segment-widths of context

procedure( emirZoomTo(b)
  let( (w bb vw vh asp bw bh want cx cy hx hy)
    w = emirWin()
    when( w
      bb = hiGetViewBBox(w)
      vw = xCoord(cadr(bb)) - xCoord(car(bb))
      vh = yCoord(cadr(bb)) - yCoord(car(bb))
      asp = if( vh > 0.0 vw / vh 1.0 )
      bw = xCoord(cadr(b)) - xCoord(car(b))
      bh = yCoord(cadr(b)) - yCoord(car(b))
      ;; a tall segment needs the width its height implies once the window
      ;; aspect is taken into account, or it will not fit
      want = max( bw  bh * asp  emirZoomMin ) * emirZoomPad
      cx = (xCoord(car(b)) + xCoord(cadr(b))) / 2.0
      cy = (yCoord(car(b)) + yCoord(cadr(b))) / 2.0
      ;; In our tests hiPan and geCenter left the view unchanged; hiZoomIn
      ;; with a window bbox moved it, and it was never the slow part. Framing
      ;; the box directly also centres and scales in one operation, not three.
      hx = want / 2.0
      hy = if( asp > 0.0 hx / asp hx )
      errset( hiZoomIn( w list( (cx - hx):(cy - hy) (cx + hx):(cy + hy) )) ) )))

procedure( emirFit()
  let( (w cv bb bw)
    w  = emirWin()
    cv = emirCV()
    when( and(w cv)
      bb = cv->bBox
      bw = max( (xCoord(cadr(bb)) - xCoord(car(bb))) * 0.03  0.2 )
      errset( hiZoomIn( w list( (xCoord(car(bb)) - bw):(yCoord(car(bb)) - bw)
                                (xCoord(cadr(bb)) + bw):(yCoord(cadr(bb)) + bw) ))) )))

;; ---------------- selection ----------------
;; A constant white boundary, the way a selected shape reads. The EMIR_SEL
;; packet paints blank fill with a solid white outline, so the segment's own
;; band colour stays visible inside the ring. One small set, one redraw: no blink,
;; because each blink toggle forced a full window repaint.
unless( boundp('emirSelBandHS) emirSelBandHS = nil )   ; the segment's own band colour, redrawn under the ring
;; how far outside the segment the white ring sits, in um. It only has to clear
;; the segment edge -- the outline is drawn at least a pixel wide at any zoom,
;; so it stays visible without being scaled to the segment's length.
emirSelMargin  = 0.005
;; The ring is sized as a fraction of the VIEW, not of the segment, so it stays
;; the same size on screen at any zoom. A minimum-width wire is only a few px
;; wide in a full-cell view; a marker in design units is invisible there no
;; matter how it is coloured. Never smaller than the segment itself, so
;; zooming in it just hugs the shape instead of covering its neighbours.
emirSelFrac    = 0.03
;; Halo. "auto" thickness picks thin/normal/thick from how big the ring is ON
;; SCREEN: at full view the ring is only ~20 px and a thick halo swallows it
;; into a blob, while zoomed in the ring is large and thick looks right.
emirHaloPos    = "under"
emirHaloType   = "fadeout"
emirHaloThick  = "thin"      ; thin | normal | thick | auto
emirHaloAlpha  = 0
emirHaloThinPx = 45          ; ring below this many px -> thin
emirHaloNormPx = 110         ; ... below this -> normal, else thick
unless( boundp('emirSelV) emirSelV = nil )   ; record the ring is on, so a zoom/pan can resize it
unless( boundp('emirPanBusy) emirPanBusy = nil )

;; The two selection sets are created once and then reused: only their
;; rectangle is replaced. Destroying and rebuilding a highlight set on every
;; row click cost set teardown, set creation and a full-window hiRedraw each
;; time, where the view change that follows already repaints.
unless( boundp('emirSelObj) emirSelObj = nil )
unless( boundp('emirSelBandObj) emirSelBandObj = nil )

procedure( emirSelRingBox(b @optional view)
  let( (w bb vw cx cy hx hy)
    w  = emirWin()
    ;; `view` is the bbox hiRegZoomPanProc hands us. Re-querying hiGetViewBBox
    ;; inside that callback can return the PREVIOUS view -- which sized the
    ;; ring, and picked the halo thickness, from the zoom level we just left.
    bb = or( view when(w hiGetViewBBox(w)) )
    ;; px/um is the same in x and y, so one view dimension gives a marker that
    ;; is square on screen
    vw = if( bb xCoord(cadr(bb)) - xCoord(car(bb)) 0.0 )
    cx = (xCoord(car(b)) + xCoord(cadr(b))) / 2.0
    cy = (yCoord(car(b)) + yCoord(cadr(b))) / 2.0
    hx = max( (xCoord(cadr(b)) - xCoord(car(b))) / 2.0 + emirSelMargin
              vw * emirSelFrac / 2.0 )
    hy = max( (yCoord(cadr(b)) - yCoord(car(b))) / 2.0 + emirSelMargin
              vw * emirSelFrac / 2.0 )
    list( (cx - hx):(cy - hy) (cx + hx):(cy + hy) )))

;; Is the Explorer form still up? When this Virtuoso cannot say, assume so.
procedure( emirFormUp()
  let( (r)
    cond( ( null(emirForm) nil )
          ( !isCallable('hiIsFormDisplayed) t )
          ( t r = errset(hiIsFormDisplayed(emirForm))
              if( r car(r) t ) ))))

;; Resize the ring whenever the view changes. hiRegZoomPanProc hands us the
;; window and the new display bbox on every zoom, pan, fit and redraw -- of
;; EVERY window, so only the Explorer's own layout window is acted on. Only
;; the one selection set is touched; the guard stops our own redraw
;; re-entering, and it is reset even when the body fails. A callback that
;; finds the form gone cleans up instead (the form was closed and its close
;; callback did not run).
procedure( emirZoomPanCB(w bbox)
  let( (seg ring ok)
    cond(
      ( !emirFormUp()
        emirExplorerClosed() )
      ( and( emirSelV emirSelHS null(emirPanBusy) eq(w emirWin()) )
        emirPanBusy = t
        ok = errset(
               progn(
                 seg  = if( emirIsIR() emirRBox(emirSelV) emirVBox(emirSelV) )
                 ring = emirSelRingBox(seg bbox)
                 when( emirSelObj errset(geDeleteHilightObject(emirSelObj)) )
                 emirSelObj = geAddHilightRectangle(emirSelHS ring)
                 emirApplyHalo(ring bbox)
                 errset( geDrawHilightSet(w emirSelHS) )) t)
        emirPanBusy = nil
        ok ))))

;; the ring's width in screen pixels, which is what the halo has to suit
procedure( emirRingPx(b @optional view)
  let( (w vb sb vw pw rw)
    w = emirWin()
    when( w
      vb = or( view hiGetViewBBox(w) )
      sb = car(errset(hiGetAbsWindowScreenBBox(w)))
      vw = xCoord(cadr(vb)) - xCoord(car(vb))
      pw = if( sb xCoord(cadr(sb)) - xCoord(car(sb)) 800 )
      rw = xCoord(cadr(b)) - xCoord(car(b))
      when( vw > 0.0 rw / vw * float(pw) ))))

procedure( emirApplyHalo(ringBox @optional view)
  let( (px th)
    when( emirSelHS
      th = emirHaloThick
      when( th == "auto"
        px = emirRingPx(ringBox view)
        th = cond( (null(px)                "normal")
                   (px < emirHaloThinPx     "thin")
                   (px < emirHaloNormPx     "normal")
                   (t                       "thick") ))
      errset( geSetHilightSetHaloParameters(emirSelHS emirHaloPos emirHaloType
                                            th emirHaloAlpha) )
      th )))

;; Try halo settings live from the CIW, e.g.
;;   emirHaloSet("under" "smooth" "auto" 0)
procedure( emirHaloSet(pos type thick alpha)
  emirHaloPos = pos  emirHaloType = type
  emirHaloThick = thick  emirHaloAlpha = alpha
  when( emirSelV
    emirApplyHalo( emirSelRingBox( if(emirIsIR() emirRBox(emirSelV)
                                                 emirVBox(emirSelV)) ))
    hiRedraw() )
  list(pos type thick alpha))

;; a selection set (re)used for band step `b` or 'sel: recreated when it is
;; no longer valid, and given the step's packet when there are packets
procedure( emirSelSet(hs b cv)
  unless( and( hs car(errset(geIsValidHilightSet(hs))) )
    hs = emirNewHS(cv b) )
  when( and(hs emirPacketsOK)
    errset( geChangeHilightPacketName(hs emirPacketName(b)) ))
  hs )

procedure( emirSelect(v @optional (redraw t))
  let( (cv b ring)
    cv = emirCV()
    when( and(cv v)
      b = if( emirIsIR() emirRBox(v) emirVBox(v) )
      emirSelBandHS = emirSelSet(emirSelBandHS
        if( emirIsIR() emirRBand(v) emirBandStep(emirVBand(v)) ) cv)
      emirSelHS     = emirSelSet(emirSelHS 'sel cv)
      when( emirSelBandObj errset(geDeleteHilightObject(emirSelBandObj)) )
      when( emirSelObj     errset(geDeleteHilightObject(emirSelObj)) )
      emirStamp("  select sets ready")
      emirSelBandObj = when( emirSelBandHS geAddHilightRectangle(emirSelBandHS b) )
      emirSelV = v
      ring = emirSelRingBox(b)
      emirSelObj = when( emirSelHS geAddHilightRectangle(emirSelHS ring) )
      ;; a halo makes the ring read against busy layout. Haloing is costly,
      ;; so only the one selection marker gets a halo, with the tunables above.
      emirApplyHalo(ring)
      emirStamp("  select rects added")
      ;; A full repaint, not geDrawHilightSet. Redrawing a single set does not
      ;; clear what was under it, so the halo composites over the previous
      ;; one and thickens on every selection. This path was introduced when
      ;; hiRedraw was wrongly blamed for the slow row clicks -- the real cause
      ;; was the list box ?callback firing only on focus-out -- so there is no
      ;; reason to keep the cheap-but-wrong refresh.
      when( redraw hiRedraw() )
      t )))

;; ---------------- redraw ----------------
;; delete one highlight set if it is still valid (its cellview may be closed)
procedure( emirDelHS(hs)
  when( and( hs car(errset(geIsValidHilightSet(hs))) )
    errset( geDeleteHilightSet(hs) ))
  nil )

procedure( emirClear()
  emirStamp("CLEAR pressed")
  foreach( hs emirHS  emirDelHS(hs) )
  emirHS = nil
  emirSelHS = emirDelHS(emirSelHS)
  emirSelBandHS = emirDelHS(emirSelBandHS)
  emirSelObj = nil
  emirSelBandObj = nil
  emirSelV = nil
  ;; Every redraw destroys and recreates hundreds of highlight objects, and
  ;; Qt queues those deletions. Reap the deferred deletions after each
  ;; rebuild: without it the window, zoom included, slowed down the longer the
  ;; tool was open.
  errset( hiSync('deferredDelete) )
  t )

;; The Explorer form was closed (its close callback), or found gone: remove
;; every highlight and the ring, and stop resizing the ring on zoom and pan.
;; `seq` is the form's number: closing an old form never clears a newer one.
procedure( emirExplorerClosed(@optional seq)
  when( or( null(seq) seq == emirFormSeq )
    emirClear()
    errset( hiUnregZoomPanProc("emirZoomPanCB") )
    emirForm = nil
    errset( hiRedraw() ))
  t )

;; A tech file may declare an LPP with visible=nil. It is valid, so a
;; highlight drawn on it is correct -- and invisible. Switching the mark LPP
;; on is the whole difference between this and a viewer that appears to do
;; nothing.
procedure( emirShowLayers()
  when( emirMarkLPP errset( leSetLayerVisible(emirMarkLPP t) ))
  t )

;; the most rows the ranked list holds: filling a list box with tens of
;; thousands of rows is slow; Next/Prev still walk every shown segment
emirMaxRows = 2000

;; a highlight set for band step `step`, made on first use and kept in `sets`
;; (a table); nil when it cannot be made
procedure( emirBandSet(sets step cv)
  let( (hs)
    unless( sets[step]
      hs = emirNewHS(cv step)
      when( hs emirHS = cons(hs emirHS) )
      sets[step] = or(hs 'none) )
    if( sets[step] == 'none nil sets[step] ) ))

;; prog(), not let(), because these two bail out early with return() -- SKILL
;; only allows return inside a prog
procedure( emirRedraw()
  prog( (cv f metric emThr irThr showEM showIR sets hs n nets netT rows)
    cv = emirCV()
    unless( cv
      hiDisplayAppDBox(?name 'emirNoCv ?dboxBanner "EMIR"
        ?dboxText "Open the layout cellview first, then run emirExplorer()."
        ?dialogType hicMessageDialog ?buttonLayout (quote Close))
      return(nil))
    f = emirForm
    emirStamp("REDRAW enter")
    emirShowLayers()
    emirClear()
    metric = f->emirMetric->value
    emThr  = f->emirEmThr->value / 100.0     ; slider is % of the limit
    irThr  = f->emirIrThr->value             ; float field, already in mV
    ;; EM or IR, never both: the list ranks by ratio in one mode and by mV in
    ;; the other, and one ranked list cannot mean both at once.
    emirKind = f->emirMode->value
    showEM = emirKind == "EM"
    showIR = emirKind == "IR"
    ;; On entering IR, select the rails: IR is a PDN quantity and opening on
    ;; every net buries the rails among signal nets. Only on the change, so a
    ;; deliberate selection is never overwritten.
    when( and( emirKind != emirLastKind
               or(emirLastKind emirIsIR()) )
      emirBusy = t
      f->emirNetList->value = if( and(emirIsIR() emirRails) emirRails emirNets )
      emirBusy = nil )
    emirLastKind = emirKind
    nets   = f->emirNetList->value
    when( null(nets) nets = emirNets )
    ;; net membership by table: member() on every record was O(records x nets)
    netT = makeTable("emirNetT" nil)
    foreach( x nets  netT[x] = t )
    emirSkippedHS = 0
    ;; grey out the threshold that does not apply, so the live control is obvious
    errset( f->emirEmThr->enabled  = showEM )
    errset( f->emirMetric->enabled = showEM )
    errset( f->emirIrThr->enabled  = showIR )
    errset( f->emirIrSlide->enabled = showIR )

    sets = makeTable("emirSets" nil)
    emirShown = nil

    ;; ---- EM: one hilight set per ramp step, only segments at or above the threshold
    when( showEM
      foreach( v emirViol
        when( and( netT[emirVNet(v)] emirVRat(v metric) >= emThr )
          emirShown = cons(v emirShown)
          hs = emirBandSet(sets emirBandStep(emirVBand(v)) cv)
          when( hs geAddHilightRectangle(hs emirVBox(v)) ) ))
      emirShown = reverse(emirShown) )

    ;; ---- IR: ramp bands, only above the mV threshold, worst drop first
    when( showIR
      foreach( r emirIR
        when( and( netT[emirRNet(r)] emirRMv(r) >= irThr )
          emirShown = cons(r emirShown)
          hs = emirBandSet(sets emirRBand(r) cv)
          when( hs geAddHilightRectangle(hs emirRBox(r)) ) ))
      emirShown = sort(emirShown
                       lambda((a b) emirRMv(a) > emirRMv(b))) )
    when( emirSkippedHS > 0
      printf("EMIR: %d highlight set(s) could not be created on %L; those bands are not drawn\n"
             emirSkippedHS emirMarkLPP) )

    ;; ---- refresh the ranked list (at most emirMaxRows rows) and the counters
    rows = nil
    n = 0
    foreach( v emirShown
      n = n + 1
      when( n <= emirMaxRows
        rows = cons( if( showIR emirIrRowText(v n) emirRowText(v n metric) )
                     rows )))
    emirRows = reverse(rows)
    emirBusy = t
    f->emirList->choices = emirRows
    emirBusy = nil
    f->emirCount->labelText = emirCountText(emirShown metric irThr)
    errset( f->emirHdr->value = emirHeaderText() )
    ;; only the scale in play is shown -- they share one slot in the form
    errset( f->emirEmBar->invisible     = showIR )
    errset( f->emirEmTickLbl->invisible = showIR )
    errset( f->emirIrBar->invisible     = showEM )
    errset( f->emirIrTickLbl->invisible = showEM )
    emirCursor = 0
    emirStamp("REDRAW before hiRedraw")
    hiRedraw()
    emirStamp("REDRAW after hiRedraw")
    return(t) ))

;; ---------------- navigation ----------------
procedure( emirGoto(i)
  let( (v w b m metric zoomOn)
    emirStamp("goto enter")
    when( and(emirShown i >= 0 i < length(emirShown))
      emirCursor = i
      v = nth(i emirShown)
      metric = if( emirForm emirForm->emirMetric->value "avg" )
      w = emirWin()
      ;; keep the list in step with the cursor -- Next/Prev drive the cursor
      ;; directly, and without this the highlighted row and the selected row
      ;; drift apart after a couple of presses
      when( and( emirForm emirRows i < length(emirRows) )
        emirBusy = t
        emirForm->emirList->value = list(nth(i emirRows))
        emirBusy = nil )
      emirStamp("goto got window")
      ;; the record is an EM violation or an IR segment depending on the mode,
      ;; so geometry, colour and detail all come from the matching accessors
      b = if( emirIsIR() emirRBox(v) emirVBox(v) )
      ;; if the view is about to change it repaints anyway, so the selection
      ;; does not ask for a redraw of its own -- one repaint per click, not two
      zoomOn = and( w emirForm car(emirForm->emirZoom->value) )
      emirSelect(v !zoomOn)
      emirStamp("goto after select")
      when( zoomOn emirZoomTo(b) )
      emirStamp("goto after zoom")
      when( emirForm
        emirForm->emirDetail->value =
          if( emirIsIR() emirIrDetailText(v) emirDetailText(v metric) ))
      emirStamp("goto after detail")
      if( emirIsIR()
        then printf("EMIR %s %s  IR %.4f mV\n"
               emirStr(emirRNet(v)) emirStr(emirRName(v)) float(emirRMv(v)))
        else printf("EMIR %s %s  %s  %s %.3fx  -> %s\n"
               emirStr(emirVNet(v)) emirStr(emirVName(v))
               emirStr(emirVLayer(v)) emirStr(metric)
               float(emirVRat(v metric)) emirStr(emirHintFor(v metric))) )
      emirStamp("goto exit")
      t )))

procedure( emirNext()  emirStamp("NEXT pressed")  emirGoto( min(emirCursor + 1 length(emirShown) - 1) ))
procedure( emirPrev()  emirGoto( max(emirCursor - 1 0) ))

procedure( emirListCB(f)
  prog( (idx)
    ;; changeCB also fires when SKILL rewrites the list, so a redraw would
    ;; otherwise re-enter here and jump the view on its own
    when( emirBusy return(nil) )
    emirStamp("listCB enter")
    idx = f->emirList->value
    emirStamp("listCB read value")
    when( idx
      when( listp(idx) idx = car(idx) )
      ;; the choice string starts with the 1-based rank, of any width
      idx = atoi(car(parseString(idx)))
      when( idx emirGoto(idx - 1) ))
    emirStamp("listCB exit")
    return(t) ))

;; A label carrying the colour-ramp image. hiLoadIconFile needs graphics, so
;; batch runs and any load failure quietly degrade to the text form.
procedure( emirBarField(name png ticks @optional hidden)
  let( (ic)
    ic = when( png car(errset(hiLoadIconFile(png 620 28))) )
    if( ic
      then hiCreateLabel(?name name ?labelIcon ic ?invisible hidden)
      else hiCreateLabel(?name name ?labelText ticks ?invisible hidden) )))

;; the two IR controls, each writing the other's value but never its callback
procedure( emirIrFromSlider()
  emirForm->emirIrThr->value = emirForm->emirIrSlide->value / 100.0
  emirRedraw() )

procedure( emirIrFromField()
  emirForm->emirIrSlide->value = round( emirForm->emirIrThr->value * 100.0 )
  emirRedraw() )

procedure( emirWorst()
  let( ()
    emirForm->emirEmThr->value = 100
    emirRedraw()
    emirGoto(0) ))

procedure( emirAll()
  let( ()
    emirForm->emirEmThr->value = 0
    emirForm->emirIrThr->value = 0.0
    emirForm->emirIrSlide->value = 0
    emirForm->emirNetList->value = emirNets
    emirRedraw() ))

;; ---------------- the form ----------------
;; Started from a timer (emirLoadViewer), where an error would vanish without
;; a word: everything runs inside errset and a failure is printed.
procedure( emirExplorer()
  let( (r)
    r = errset(emirExplorer1() t)
    unless( r
      printf("EMIR: the Explorer did not start: %L\n" errset.errset) )
    when( r car(r) ) ))

procedure( emirExplorer1()
  prog( (netF metF emF irF irS zoomT emShow lst hdr cnt det btn legend
         irLegend emTicks irTicks closeCB)
    unless( boundp('emirViol)
      printf("EMIR: load emir_data.il first\n")
      return(nil))
    ;; a second start replaces the first form instead of stacking a second
    ;; one: its highlights go, and the old form is closed
    when( emirForm
      let( (old)
        old = emirForm
        emirExplorerClosed()
        errset( hiFormCancel(old) ) ))
    emirMarkLPP = nil          ; re-pick against the cellview about to be used
    emirDefinePackets()

    ;; ?changeCB, not ?callback: on a list box ?callback ran only once focus
    ;; left the field, so row clicks looked like a multi-second hang.
    ;; ?changeCB runs on every selection change.
    netF = hiCreateListBoxField(
      ?name 'emirNetList ?prompt "Nets"
      ?choices emirNets ?value emirNets
      ?multipleSelect t ?numRows min(6 length(emirNets))
      ?changeCB "emirRedraw()")

    metF = hiCreateCyclicField(
      ?name 'emirMetric ?prompt "EM metric"
      ?choices list("avg" "rms" "peak") ?value "avg"
      ?callback "emirRedraw()")

    ;; The scale field is integer-only (?precision just sets displayed decimals,
    ;; it does not scale), so both sliders carry integer units and the callbacks
    ;; convert: EM in % of the limit, IR in uV.
    emF = hiCreateScaleField(
      ?name 'emirEmThr ?prompt "show EM >= (% of limit)"
      ?value 80 ?range list(0 300) ?precision 0 ?isContinuous nil
      ?callback "emirRedraw()")

    ;; IR is asked for in mV, and a scale field cannot show a fraction (its
    ;; value is an integer and ?precision only adds decimal places to it), so
    ;; the mV number lives in a float field and the slider drives it in
    ;; hundredths. Setting a field's value from SKILL does not fire its own
    ;; callback, so the two stay in step without looping.
    irF = hiCreateFloatField(
      ?name 'emirIrThr ?prompt "show IR >= (mV)"
      ?value 0.0 ?range list(0.0 emirIRMax)
      ?callback "emirIrFromField()")

    irS = hiCreateScaleField(
      ?name 'emirIrSlide ?value 0
      ?range list(0 max(10 fix(emirIRMax * 100.0) + 1)) ?precision 0
      ?isContinuous nil ?callback "emirIrFromSlider()")

    ;; A radio, not two toggles: EM and IR rank by different quantities, so the
    ;; ranked list, the detail pane and Next/Prev can only mean one of them.
    emShow = hiCreateRadioField(
      ?name 'emirMode ?prompt "Show"
      ?choices list("EM" "IR") ?value "EM"
      ?itemsPerRow 2 ?callback list("emirRedraw()" "emirRedraw()"))
    zoomT = hiCreateToggleField(
      ?name 'emirZoom ?choices list( list('zoomSel "Zoom to selection") )
      ?value list(nil))

    ;; The legend is an image of the very ramp the layout paints with, sitting
    ;; in the form where it is always readable -- drawing it in the layout put
    ;; it off-screen the moment you zoomed, and cost text primitives on every
    ;; repaint. Falls back to words if the icon cannot be loaded.
    legend   = emirBarField('emirEmBar emirEmBarPng emirEmTicks)
    emTicks  = hiCreateLabel(?name 'emirEmTickLbl ?labelText emirEmTicks)
    irLegend = emirBarField('emirIrBar emirIrBarPng emirIrTicks t)
    irTicks  = hiCreateLabel(?name 'emirIrTickLbl ?labelText emirIrTicks
                             ?invisible t)

    hdr = hiCreateMLTextField(?name 'emirHdr ?value emirHeaderText()
                              ?editable nil ?hasVerticalScrollbar nil
                              ?hasHorizontalScrollbar nil ?enableWordWrap nil)

    lst = hiCreateListBoxField(
      ?name 'emirList ?prompt "" ?choices nil ?numRows 16
      ?changeCB "emirListCB(emirForm)")

    ;; a label needs non-empty text at creation -- "" reads as "not supplied"
    cnt = hiCreateLabel(?name 'emirCount ?labelText " ")
    det = hiCreateMLTextField(?name 'emirDetail ?value ""
                              ?editable nil ?hasVerticalScrollbar nil)

    btn = hiCreateButtonBoxField(
      ?name 'emirBtns
      ?choices list("Worst first" "Prev" "Next" "Show all" "Fit" "Redraw"
                    "Clear")
      ?callback list("emirWorst()" "emirPrev()" "emirNext()"
                     "emirAll()" "emirFit()" "emirRedraw()"
                     "emirClear() hiRedraw()"))

    ;; closing the form clears its highlights and the zoom/pan callback; the
    ;; number makes an old form's close leave a newer form alone
    emirFormSeq = emirFormSeq + 1
    closeCB = sprintf(nil "emirExplorerClosed(%d)" emirFormSeq)
    emirForm = hiCreateAppForm(
      ?name concat("emirExplorerForm" emirFormSeq)
      ?callback list(closeCB closeCB)
      ?formTitle sprintf(nil "EMIR Explorer -- %s (screening, not sign-off)" emirCell)
      ;; 2D layout: x:y  w:h  [prompt-box width]. The last element is required
      ;; on every field that has a prompt and forbidden on every field that
      ;; does not -- labels, toggles and the button box therefore take three.
      ;; A scale field draws its value above the bar and its prompt beside it,
      ;; so it needs ~48px, not the 26 a one-line field takes. Getting this
      ;; wrong overlaps the legend onto the slider ticks.
      ?fields list(
        list(netF     0:0     660:110  70)
        list(metF     0:118   330:26  150)
        list(emShow   340:118 300:26  60)
        list(emF      0:150   660:48  190)
        list(irF      0:202   330:26  190)
        list(zoomT    350:202 300:26)
        list(irS      0:232   660:44)
        list(legend   0:278   660:30)
        list(irLegend 0:278   660:30)
        list(emTicks  0:310   660:16)
        list(irTicks  0:310   660:16)
        list(cnt      0:332   660:18)
        list(hdr      0:352   660:20)
        list(lst      0:372   660:308)
        list(det      0:686   660:56)
        list(btn      0:748   660:32))
      ;; without an explicit size the form comes up too short and silently
      ;; clips the detail pane and the button box off the bottom
      ?initialSize 680:820
      ?minSize 680:820
      ?buttonLayout 'Close
      ?dontBlock t          ; stay usable while the engineer pans and edits
      ?unmapAfterCB nil)

    hiDisplayForm(emirForm)
    ;; keep the selection marker a constant size on screen as the view changes
    ;; (registered once the form is up: the callback cleans up when it is not)
    errset( hiUnregZoomPanProc("emirZoomPanCB") )
    errset( hiRegZoomPanProc("emirZoomPanCB") )
    ;; open on everything at or above the warn threshold, with the whole design
    ;; and its legend in view; the worst segment is selected and blinks
    emirRedraw()
    emirFit()
    emirGoto(0)
    return(emirForm) ))

;; ---------------- self test ----------------
;; Formats every violation under every metric. Runs without a window, so a bad
;; record is caught in batch instead of when the engineer clicks it.
procedure( emirSelfTest()
  prog( (n bad miss)
    n = 0
    bad = 0
    ;; the functions the Explorer calls: a missing one is a code path that
    ;; cannot run in this Virtuoso (reported, not counted as a failure)
    miss = setof(fn '(geCreateHilightSet geAddHilightRectangle geDeleteHilightSet
                      geIsValidHilightSet geDeleteHilightObject geChangeHilightPacketName
                      geSetHilightSetHaloParameters geDrawHilightSet drGetPacket
                      hiZoomIn hiGetViewBBox hiRegZoomPanProc hiUnregZoomPanProc
                      hiIsFormDisplayed hiFormCancel hiRegTimer hiLoadIconFile
                      hiCreateAppForm leGetEntryLayer leSetLayerVisible makeTable)
                 !isCallable(fn))
    printf("EMIR selftest: functions not in this Virtuoso: %L\n" miss)
    printf("EMIR selftest: colours from %s (packets found: %L)\n"
           emirColourPath emirPacketsOK)
    foreach( m list("avg" "rms" "peak")
      foreach( v emirViol
        n = n + 1
        unless( errset( emirRowText(v n m) )     bad = bad + 1 )
        unless( errset( emirDetailText(v m) )    bad = bad + 1 )
        unless( errset( emirHintFor(v m) )       bad = bad + 1 ))
      unless( errset( emirCountText(emirViol m 0.0) )  bad = bad + 1 ))
    foreach( r emirIR
      unless( and( stringp(nth(0 r)) numberp(nth(1 r)) fixp(nth(2 r))
                   numberp(nth(3 r)) numberp(nth(6 r)) stringp(nth(7 r)) )
        bad = bad + 1
        printf("EMIR selftest: malformed IR record %L\n" r )))
    printf("EMIR selftest: %d violations x 3 metrics + %d IR segments, %d failures\n"
           length(emirViol) length(emirIR) bad)
    return(zerop(bad)) ))

printf("EMIR Explorer loaded (screening, not sign-off). Run: emirExplorer()\n")
'''
