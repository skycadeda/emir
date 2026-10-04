"""Reading a layout (GDS or OASIS): the one path a GDS flow takes.

The magic route (`emir extract --tool magic`) opens the layout and chooses
its cell here before magic reads it, so a file that cannot be read, a layout
with several top cells or a missing cell gets the same code wherever it is
read:

* EMIR-E101 the file does not exist;
* EMIR-E170 it cannot be read as GDS or OASIS;
* EMIR-E212 it holds no cell;
* EMIR-E270 several top cells and no cell named (the next steps, when the
  caller gives its command line, re-run it with each `--cell`);
* EMIR-E106 the named cell is not in the file;
* EMIR-E603 the `klayout` Python package is missing (exit 3).

What a flow then does with the cell (magic extracts it) stays with that
flow.
"""
from __future__ import annotations

import os

from .errors import EmirError, step


def klayout_db():
    """`klayout.db`, or EMIR-E603 (exit 3) when the package is missing."""
    try:
        import klayout.db as db
    except ImportError as e:
        from .repo import install_hint
        raise EmirError("EMIR-E603", "reading a layout needs the klayout Python package: "
                        f"`{install_hint('klayout')}`") from e
    return db


def load(path: str):
    """The layout in `path` (a `klayout.db.Layout`)."""
    db = klayout_db()
    if not os.path.isfile(path):
        raise EmirError("EMIR-E101", f"layout not found: {path}", path=path)
    ly = db.Layout()
    try:
        ly.read(path)
    except RuntimeError as e:
        raise EmirError("EMIR-E170", f"{path}: {str(e).splitlines()[0]}", path=path) from e
    return ly


def top_names(ly) -> list:
    """The top cells' names, sorted."""
    return sorted(c.name for c in ly.top_cells())


def top_cell(ly, cell: str | None, path: str, command: str | None = None, argv=()):
    """The cell to read: `cell`, else the layout's only top cell.

    With `command` and its `argv`, EMIR-E270 carries next steps that re-run
    the command with each top cell's `--cell` (the first three)."""
    tops = top_names(ly)
    if cell:
        c = ly.cell(cell)
        if c is None:
            from .dspf import did_you_mean
            raise EmirError("EMIR-E106", f"no cell {cell!r} in {path}"
                            f"{did_you_mean(cell, [x.name for x in ly.each_cell()])}; top "
                            f"cells: {', '.join(tops[:12])}", path=path)
        return c
    if not tops:
        raise EmirError("EMIR-E212", f"{path} holds no cell", path=path)
    if len(tops) > 1:
        steps = []
        if command:
            from .output import command_line, strip_options
            rest = strip_options(list(argv), {"--cell": 1})
            steps = [step(f"screen top cell {t}", command_line(command, rest, "--cell", t))
                     for t in tops[:3]]
        raise EmirError("EMIR-E270", f"{path} has {len(tops)} top cells: "
                        f"{', '.join(tops[:12])}{' ...' if len(tops) > 12 else ''}; "
                        f"name one with --cell", path=path, next_steps=steps)
    return ly.cell(tops[0])


def open_cell(path: str, cell: str | None = None, command: str | None = None, argv=()):
    """`(layout, cell)` of `path` (see `load` and `top_cell`)."""
    ly = load(path)
    return ly, top_cell(ly, cell, path, command=command, argv=argv)
