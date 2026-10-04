"""Options that take JSON: written inline, or the path of a file holding it.

`--currents` (`emir run`, `check`, `next`, and their MCP tools) takes either
form, and every command reads it here, so a plan `emir next` prints with a
file runs as printed:

* a value that starts with ``{`` or ``[`` is the JSON itself;
* anything else is the path of a file that holds it (pretty-printed or not).
  A leading ``@`` is accepted too (``--currents=@c.json``): over MCP no
  argument-file expansion happens, and on the command line ``@c.json`` is
  argparse's argument file (one argument per line), which only worked for a
  one-line file.
"""
from __future__ import annotations

import json
import math
import os

from .errors import EmirError, step

#: what the --currents options say in their help
CURRENTS_FORM = ('inline JSON, e.g. \'{"VDD": 0.004, "MP0:s": -0.004}\', or the path of a '
                 'file holding it')


def is_inline(value) -> bool:
    """Whether `value` is the JSON itself (not a file path)."""
    return str(value).lstrip().startswith(("{", "["))


def text_of(value, what: str) -> tuple:
    """`(JSON text, absolute file path or None)` of an option's value.
    EMIR-E101 when it is neither inline JSON nor a file, EMIR-E102 when the
    file cannot be read."""
    v = str(value)
    if is_inline(v):
        return v, None
    p = v.strip()
    if p.startswith("@") and not os.path.exists(p) and os.path.isfile(p[1:]):
        p = p[1:]
    if not os.path.isfile(p):
        raise EmirError("EMIR-E101", f"{what} {v!r} is neither inline JSON (it starts with "
                        f"{{ or [) nor a file", path=p)
    try:
        with open(p, encoding="utf-8") as f:
            return f.read(), os.path.abspath(p)
    except (OSError, UnicodeError) as e:
        raise EmirError("EMIR-E102", f"{what} file {p}: {getattr(e, 'strerror', None) or e}",
                        path=p) from e


def load(value, what: str, code: str, schema: str | None = None, **ctx) -> tuple:
    """`(object, absolute file path or None)`: the JSON of an option that
    takes it inline or from a file. Text that is not JSON is the coded error
    `code`, with `emir schema <schema>` as its next step."""
    text, path = text_of(value, what)
    try:
        return json.loads(text), path
    except json.JSONDecodeError as e:
        from .output import command_line
        steps = [step("the format", command_line("schema", schema))] if schema else None
        raise EmirError(code, f"{what}{' (' + path + ')' if path else ''} is not JSON: {e}",
                        next_steps=steps, **ctx) from e


def currents(value, what: str = "--currents", **ctx) -> tuple:
    """`({name: amps}, path or None)` of a --currents value: a non-empty JSON
    object of numbers (EMIR-E401 otherwise; NaN and Inf are EMIR-E402)."""
    from .output import command_line
    obj, path = load(value, what, "EMIR-E401", schema="currents", **ctx)
    fmt = [step("the format of --currents", command_line("schema", "currents"))]
    if not isinstance(obj, dict) or not obj:
        raise EmirError("EMIR-E401", f"{what} must be a non-empty JSON object of name: amps"
                        + (f" ({path})" if path else ""), next_steps=fmt, **ctx)
    out = {}
    for k, v in obj.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise EmirError("EMIR-E401", f"{what}: {k!r} is {v!r}, not a number of amps",
                            next_steps=fmt, **ctx)
        out[str(k)] = float(v)
    bad = sorted(k for k, v in out.items() if not math.isfinite(v))
    if bad:
        raise EmirError("EMIR-E402", f"{what} holds NaN or Inf for: {', '.join(bad[:8])}",
                        **ctx)
    return out, path
