# SKILL notes for contributors

Lessons from building EMIR's Virtuoso Explorer and form. Each one cost a debugging
round, and each failure was silent or misleading. They are notes from development,
**UNVERIFIED until checked on site**: another release may behave differently, and
the repository claims no Virtuoso behaviour as verified. Read them before changing
`emir/skillgui.py` (the form and menu) or `emir/skillapp.py` (the Explorer), and
have every change checked on a real Virtuoso, on site (`LIVE_TEST.md`, after the
headless self-test `python agent/cadence/python/virtuoso_batch.py --selftest`): CI
only checks the SKILL as text.

## Callbacks

- A list box's `?callback` ran only once focus left the field, so clicking rows
  looked like a hang of several seconds and quick clicks merged into one call.
  `?changeCB` runs on every selection change; use it.
- `?changeCB` also runs when SKILL itself sets `->choices` or `->value`. Guard it
  with a busy flag, or a redraw re-enters it and moves the view on its own.
- To tell "slow code" from "late callback": click N rows quickly and count the
  callbacks. N clicks giving one callback means the dispatch waits, not the code.
  A button running the same code instantly confirms it.

## Field types

- `hiCreateScaleField` holds integers. `?precision` changed only the decimals
  shown (250 showed as 250 at precision 0 and at 2). Carry integer units and
  convert in the callback.
- `hiCreateToggleField ?choices` takes each item as its own list. Its `->value` is
  a list, and `(nil)` is not nil: take `car()` of it, or an unticked box reads as
  ticked.
- `hiCreateRadioField ?callback` is a list, one callback per choice.
- `hiCreateLabel` refused an empty `?labelText`; its text is `->labelText`, and
  `?labelIcon` takes an image.
- `hiCreateMLTextField` uses a fixed-width font (a label does not), which makes it
  the field for a column header over a list box. Set `?hasHorizontalScrollbar nil`,
  or it reserves a visible gap.

## Forms

- In a 2D form layout every prompted field needs a prompt-box width, and every
  other field must not have one.
- Without `?initialSize` and `?minSize` the form came up too short and clipped its
  bottom fields without a word.
- Two alternatives can share one slot: overlap them and toggle `->invisible`.
- `return()` works inside `prog()`, not inside `let()` (a test in
  `tests/test_cadence_skill.py` checks every procedure for this).
- An error inside a timer callback vanished with no message: nothing appeared.
  The Explorer is started from a timer, so its whole start runs inside `errset`
  and prints any error.

## Drawing and the view

- `hiRedraw()` takes no window and repaints everything.
- `geDrawHilightSet(window set)` refreshed one set without clearing what was under
  it: halos composited over the previous one and thickened on every update. Use it
  only where nothing else repaints.
- `hiZoomIn(window bbox)` framed a box and moved the view. `hiPan` and `geCenter`
  left the view unchanged in our tests, and `hiZoomAbsoluteScale` magnified about
  the current centre only.
- The `hiRegZoomPanProc` callback got the window and the new view box on every
  zoom, pan, fit and redraw. Asking `hiGetViewBBox` inside the callback returned
  the view being left. The callback runs for every window: act only on your own.
- `geSetHilightSetHaloParameters` took strings; the symbol form returned nil and
  did nothing. Alpha blending took three values (halo, fill, outline), on a scale
  of about 0 to 1000.
- Haloing was costly: halo the one selection marker, never a band of markers.
- A tech file can declare a layer-purpose pair invisible. A highlight drawn on it
  is correct and invisible; `leSetLayerVisible` on the pair fixed it.
- Rebuilding hundreds of highlight objects queues Qt deletions; without
  `hiSync('deferredDelete)` after each rebuild the window, zoom included, slowed
  down the longer the tool was open.
- A tech file's layer list read from SKILL did not list every layer-purpose pair
  the PDK defines, so it cannot prove a pair exists. The Explorer tries to create
  a highlight set on the configured pair and falls back to the entry layer.

## Timing and state

- `hiRegTimer(command n)` counts tenths of a second.
- `geOpen` returned before the window's first zoom-to-fit, which then undid any
  zoom done inline after it. Start the Explorer from a timer.
- Initialise live state with `unless(boundp('x) x = ...)`, so reloading a file to
  pick up an edit does not wipe a running form and orphan its highlight sets; keep
  tunables as plain assignments, so a reload does change them.

## Menus (notes from development, UNVERIFIED)

- Register a user menu trigger once per session, behind a flag. Each reload
  otherwise registers it again, and windows opened later get one more copy of the
  menu.
- Never call `deUnRegUserTriggers` to clean up: it removes every user trigger on
  that view type, other tools' included.
- Build a new pulldown in each trigger call: a menu object was not shared between
  windows.
- A window opened before the file was loaded keeps the menus it was built with.
- UNVERIFIED: the view-type string for schematic windows. EMIR registers
  `"schematic"` and `"maskLayout"`; live test T2 and T7 settle it, and whether
  Layout XL / EXL windows need their own.

## The colour packets (UNVERIFIED)

- Display packets are defined while a display.drf is read, so the Explorer cannot
  create them at runtime; it probes for them. `emir_display.drf` is appended to
  `~/display.drf`, never put in place of a project or PDK display.drf (that removes
  every layer's colours).
- Whether `drLoadDrf` merges packets into a running session, and what
  `drGetPacket` takes, are open: live test step 4 and T0 decide.

## IPC (UNVERIFIED: written for this release of EMIR, not yet run)

- `ipcBeginProcess` runs its command string through a shell. Every path and value
  in it is single-quoted (`emirShQ`); the arguments themselves travel in an
  `@file`, one per line.
- The child inherits Virtuoso's environment. A `PYTHONHOME` or `PYTHONPATH` meant
  for Virtuoso's own Python can break python3 or numpy, and then reads as "no
  python found"; the command runs under `env -u PYTHONHOME -u PYTHONPATH`, and
  `bin/emir` drops them too. `EMIR_UNSET_LD=1` also drops `LD_LIBRARY_PATH`.
- Python's output to a pipe is block-buffered, so a log meant to stream arrived in
  bursts or only at the end; `PYTHONUNBUFFERED=1` is set.
- Output arrives in chunks that need not end at a line end; the part after the
  last newline waits for the rest of its line.

## Batch Virtuoso

- `virtuoso -nograph -restore x.il`: wrap the body in `errset` and end with
  `exit()`, or an error stops at an interactive prompt and the job never ends.
- A batch launch took from about 25 s to about 3 minutes of wall time, most of it
  after the SKILL had finished.
- The `-log` transcript prefixes each line with a backslash and a letter (or `#`);
  strip `\\[a-z#]` to read it (the full set of letters is UNVERIFIED).
  `agent/cadence/python/virtuoso_batch.py` does all three.
