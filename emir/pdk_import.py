"""Generate the per-PDK EMIR data file from a foundry ICT.

Process data lives in the per-PDK YAML, never in code. This
module is how that YAML gets its EM numbers: it parses the ICT's `em_model`
blocks and merges them onto the curated geometry/resistance fields already in
the file (gds numbers, sheet resistance, via resistance, min width), which come
from the techfile and are hand-checked.

    emir import-ict \
        --ict /path/to/<pdk>_typical.ict \
        --emir /path/to/pdk/emir.yaml

What an ICT typically gives, and how it is converted to the mA/um form the
solvers use (`dw`, `a`, `b`, `Lmin` stand for the PDK's own constants):

* `em_jmax_dc_avg EQU <mult> * <J> * ( w - <dw> ) ... L > <Lmin>`
  A current limit for a wire of width w: `<J>` mA/um (its slope in w) on the
  width less `<dw>`, the form the process file's `width_loss_um` holds
  (I_max = J x (W - dW)). Both are read by evaluating the expression at two
  widths, and the offset is written as `width_loss_um` with its source (the
  largest of the avg and peak lines' offsets: the strictest), so a narrow
  wire is judged as the ICT judges it. A long-wire branch (`L > <Lmin>`) is
  the conservative rule — short wires are usually allowed more, which a
  check that does not know segment length cannot claim.
* `em_jmax_dc_peak` / `em_jmax_ac_peak` — same form, same conversion.
* `em_jmax_ac_rms EQU sqrt( K * delta_T * (w-dw)^2 * (w-dw+a)/(w-dw+b) )`
  This is the Joule self-heating limit and it is genuinely width dependent.
  Divided by w it tends to **sqrt(K * delta_T)** mA/um as w grows, and that
  asymptote is the *minimum* over width — narrow wires are allowed more. So the
  wide-wire value is the conservative one to hand a width-agnostic check.
* `jmax_factor T f T f ...` — the temperature derating table for the avg limit.
* `temp_tc1` / `temp_tc2` with the process `temp_reference` — resistance vs
  temperature, so the solver can extract at the EM temperature.

Where the ICT gives vias only `em_jmax_dc_avg`, their rms and peak limits are
left out (reported as not checked): emir never estimates a limit. Curated
(unflagged) via rms/peak values already in the file are kept; values an older
import estimated (flagged `rms_peak_estimate: true`) are removed.

ICT block names: a layer `Metal1` is looked up as the conductor block given by
`--conductor-pattern` (default `{layer}_conn`, `{layer}` = the lower-cased
name, `{Layer}` = as written), then as `metal1` and `Metal1`. A layer or via
entry can name its block outright with `ict_name:`.
"""
from __future__ import annotations

import ast
import math
import os
import re
import shlex
import sys

import yaml

from .errors import WARNING_PREFIX, notice



# --------------------------------------------------------------- ICT parse


def _blocks(txt: str, kind: str):
    for m in re.finditer(rf'{kind}\s+"([^"]+)"\s*\{{(.*?)\n\}}', txt, re.S):
        yield m.group(1), m.group(2)


_WIDE = 1e6          # "wide wire" probe width, um
_SIG = 5             # the ICT quotes 2-3 significant figures; keep 5


def _sig(x: float | None, n: int = _SIG) -> float | None:
    """Round to n significant figures.

    The wide-wire probe leaves float residue -- `<J> * (w - <dw>)` evaluated
    at w=1e6 and divided by w gives `<J>` minus a few parts in 1e9 -- which is
    noise, not data, and makes the generated YAML unreadable.
    """
    if x is None or x == 0:
        return x
    import math
    return round(x, -int(math.floor(math.log10(abs(x)))) + (n - 1))


def _rhs(line: str) -> str | None:
    """The expression between EQU and the jmax_factor / L qualifiers."""
    m = re.search(r'EQU\s+(.*)$', line)
    if not m:
        return None
    e = re.split(r'\bjmax_factor\b|\bL\s*[<>=]', m.group(1))[0]
    return e.strip() or None


#: the variables an ICT EM expression may use
_NAMES = ("w", "delta_T")
#: the operators it may use; every operand is a float, so `**` cannot run away
#: (a float power overflows at once instead of building a huge integer)
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
           ast.Pow: lambda a, b: a ** b}
_UNOPS = {ast.USub: lambda a: -a, ast.UAdd: lambda a: a}
#: longest expression accepted (an ICT limit is one short line)
_MAX_EXPR = 500


class _Refused(Exception):
    """An ICT expression outside the whitelist (caught by `_eval`)."""


def _arith(node, names: dict) -> float:
    """Evaluate a parsed expression made only of numbers, the variables `w`
    and `delta_T`, + - * / **, unary + and -, and `sqrt(<one argument>)`.
    Any other node -- an attribute, a subscript, any other call, a keyword
    argument, a string -- raises."""
    if isinstance(node, ast.Expression):
        return _arith(node.body, names)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in _NAMES and node.id in names:
        return float(names[node.id])
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_arith(node.left, names),
                                      _arith(node.right, names))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
        return _UNOPS[type(node.op)](_arith(node.operand, names))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "sqrt" and len(node.args) == 1 and not node.keywords
            and not isinstance(node.args[0], ast.Starred)):
        return math.sqrt(_arith(node.args[0], names))
    raise _Refused(f"not allowed in an ICT expression: {ast.dump(node)[:60]}")


def _eval(expr: str, w: float, delta_T: float = 1.0) -> float | None:
    """Evaluate an ICT EM expression numerically.

    An ICT writes the same kind of limit in several shapes --
    `<m> * <J> * (w - <dw>)`, `<J> * w`, `sqrt( <K> * delta_T * w * (w + <a>) )`
    -- so matching each form with its own regex silently drops the ones you
    did not anticipate (a top-metal `<J> * w` line was once missed that way,
    leaving a hand guess in place of the real limit). Evaluating instead
    handles every shape.

    The expression is parsed and walked against a whitelist (numbers, `w`,
    `delta_T`, + - * / ** and `sqrt`); anything else, and any result that is
    not a finite number, gives None. Python's `eval`, even with empty
    builtins, is not a sandbox, and an ICT is an input file.
    """
    if len(expr) > _MAX_EXPR:
        return None
    py = expr.replace("^", "**")
    try:
        v = _arith(ast.parse(py.strip(), mode="eval"), {"w": w, "delta_T": delta_T})
    except Exception:                           # noqa: BLE001
        return None
    # A fractional power of a negative number, `( 0.5 - w ) ^ 0.5` at a wide
    # w, is complex in Python: not a limit, so refused like any other
    # non-number (it used to reach `math.isfinite` and crash the import).
    if not isinstance(v, float) or not math.isfinite(v):
        return None
    return v


def _width_loss(expr: str) -> float | None:
    """The width offset dW of a limit written `J * (w - dW)` (in any shape):
    the expression evaluated at two widths gives its slope J, and dW = w -
    I(w)/J; checked at a third width (a line that is not linear in w, such
    as the rms form, gives None), and None when there is no offset."""
    w1, w2, w3 = 1e3, 1e4, 1e5
    i1, i2, i3 = (_eval(expr, w) for w in (w1, w2, w3))
    if None in (i1, i2, i3):
        return None
    j = (i2 - i1) / (w2 - w1)
    if not j > 0:
        return None
    dw = w2 - i2 / j
    if abs(i3 - j * (w3 - dw)) > 1e-9 * abs(i3) or dw < 1e-6:
        return None
    return _sig(dw)


def _per_um(expr: str, delta_T: float = 1.0) -> float | None:
    """Wide-wire limit in mA/um: lim(w->inf) expr(w)/w.

    For the usual forms that asymptote is the *minimum* over width (narrow
    wires are allowed more), so it is the conservative value to give a
    width-agnostic check.
    """
    v = _eval(expr, _WIDE, delta_T)
    return None if v is None else v / _WIDE


def _slope(expr: str) -> float | None:
    """A linear limit's density in mA/um: its slope J in w when it is `J * (w -
    dW)` (`_width_loss` finds dW), else the wide-wire limit (`_per_um`)."""
    if _width_loss(expr) is not None:
        i1, i2 = _eval(expr, 1e3), _eval(expr, 1e4)
        return (i2 - i1) / (1e4 - 1e3)
    return _per_um(expr)


#: the long-wire qualifier of an ICT limit line, e.g. `L > <Lmin>`
_LONG_WIRE = re.compile(r'\bL\s*>')


def _avg_expr(body: str, key: str = "em_jmax_dc_avg") -> str | None:
    """Prefer the conservative long-wire branch (`L > ...`); fall back to the
    first (only) one."""
    lines = [l for l in body.splitlines() if key in l]
    if not lines:
        return None
    for l in lines:
        if _LONG_WIRE.search(l):
            return _rhs(l)
    return _rhs(lines[0])


#: magic numbers of files that are not text: compressed, archived, or an
#: encrypted container (emir never opens them)
_MAGIC = ((b"\x1f\x8b", "gzip-compressed"), (b"PK\x03\x04", "a zip archive"),
          (b"BZh", "bzip2-compressed"), (b"\xfd7zXZ", "xz-compressed"),
          (b"7z\xbc\xaf", "a 7-zip archive"), (b"Salted__", "OpenSSL-encrypted"),
          (b"\x85\x01", "OpenPGP-encrypted"), (b"\x84\x8c", "OpenPGP-encrypted"),
          (b"\x8c\x0d", "OpenPGP-encrypted"), (b"\xc1", "OpenPGP-encrypted"))
#: markers of an encrypted (protected) text envelope: emir reads none of it
#: ("# Encryption: none" in a plain-text deck's header is not one)
_PROTECTED = re.compile(r"(?i)(`?pragma\s+protect\b|`protect\s+begin|begin_protected\b|"
                        r"-----BEGIN PGP MESSAGE-----|-----BEGIN ENCRYPTED|"
                        r"^\s*[#;/*!$]*\s*encrypt(?:ed|ion)\b"
                        r"(?!\s*[:=]?\s*(?:none|no|off|false|disabled)\b))", re.M)
#: OpenSSL's base64 armour of an encrypted file ("Salted__" in base64)
_B64_SALTED = "U2FsdGVkX1"
#: a body made only of base64 characters (an encoded or encrypted file)
_B64_BODY = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")
#: how much of the file is looked at to tell
_SNIFF = 65536


def _ict_text(raw: bytes) -> str | None:
    """The text of an ICT: UTF-8 (with or without a BOM), or UTF-16 with a BOM
    (declared, not guessed); None when it is neither."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"),
                     (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # a few stray bytes (a degree sign in a comment, written in another code
    # page) leave the ICT readable: its keywords and numbers are ASCII. More
    # than that is not text emir knows how to read, and it does not guess.
    bad = raw.decode("utf-8", errors="replace").count("\ufffd")
    return raw.decode("utf-8", errors="replace") if bad <= max(8, len(raw) // 1000) else None


def unreadable(path: str) -> str | None:
    """Why emir cannot read the ICT at `path` (binary, compressed, encrypted,
    not text, an undeclared encoding, an encryption marker), or None when it
    can. Nothing is decrypted, decompressed or guessed: a file emir cannot
    read is refused (EMIR-E325), and the EM limits are entered by hand."""
    with open(path, "rb") as f:
        raw = f.read()
    head = raw[:_SNIFF]
    for magic, what in _MAGIC:
        if head.startswith(magic):
            return f"it is {what}, not a text ICT"
    text = _ict_text(raw)
    # the whole file is looked at, not only its start: a protected block or
    # binary data past the first 64 KiB would otherwise be read as a partial,
    # plausible ICT (an ICT is small, and each test is linear)
    if text is None or "\0" in text:
        if b"\0" in raw and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
            return "it is a binary file (it holds NUL bytes), not a text ICT"
        return "it is not text in a known encoding (UTF-8, or UTF-16 with a byte-order mark)"
    if text:
        printable = sum(1 for c in text if c.isprintable() or c in "\t\r\n\f")
        if printable < 0.95 * len(text):
            return (f"{100 * (1 - printable / len(text)):.0f} % of its characters are not "
                    f"printable: it is not a text ICT (encrypted or binary)")
    body = text.strip()
    if body.startswith(_B64_SALTED):
        return "it is OpenSSL-encrypted (base64 armour), not a text ICT"
    if len(body) >= 64 and _B64_BODY.match(body):
        return "it is base64-encoded (an encoded or encrypted file), not a text ICT"
    m = _PROTECTED.search(text)
    if m:
        line = text.count("\n", 0, m.start()) + 1
        return (f"it carries an encryption marker ({m.group(0).strip()[:40]!r}, line "
                f"{line}): its content is protected")
    return None


def parse_ict(path: str) -> dict:
    """The EM data of an ICT. A temperature the ICT does not state is None
    (never a default), and every limit expression the whitelist refuses is
    listed in `refused` as `(block, key, expression)` so `merge` can say so.
    (`unreadable` says first whether emir can read the file at all.)"""
    with open(path, "rb") as f:
        txt = _ict_text(f.read()) or ""
    head = re.search(r'process\s+"([^"]+)"\s*\{(.*?)\n\}', txt, re.S)
    g = head.group(2) if head else ""

    def num(key, body=g, default=None):
        m = re.search(rf'{key}\s+([-\d.eE]+)', body)
        return float(m.group(1)) if m else default

    out = {
        "process": head.group(1) if head else "?",
        "temp_reference": num("temp_reference"),
        "em_tref": num("em_tref"),
        "conductor_unit": (re.search(r'em_conductor_unit\s+(\S+)', g) or [None, "mA/um"])[1]
        if re.search(r'em_conductor_unit\s+(\S+)', g) else "mA/um",
        "jmax_factor": {},
        "conductors": {},
        "vias": {},
        "refused": [],
    }

    def limit(block, key, expr, value):
        if value is None:
            out["refused"].append((block, key, expr))
        return value

    for line in txt.splitlines():                    # temperature derating table
        f = re.search(r'jmax_factor\s+((?:[\d.]+\s+[\d.]+\s*)+)', line)
        if f:
            v = f.group(1).split()
            out["jmax_factor"] = {float(v[i]): float(v[i + 1])
                                  for i in range(0, len(v) - 1, 2)}
            break

    for name, b in _blocks(txt, "conductor"):
        d = {"resistivity": num("resistivity", b), "tc1": num("temp_tc1", b),
             "tc2": num("temp_tc2", b), "min_width": num("min_width", b)}
        dws = []
        e = _avg_expr(b)
        if e:
            d["jmax_avg"] = _sig(limit(name, "jmax_avg", e, _slope(e)))
            dws.append(_width_loss(e))
        e = _avg_expr(b, "em_jmax_dc_peak")
        if e:
            d["jmax_peak"] = _sig(limit(name, "jmax_peak", e, _slope(e)))
            dws.append(_width_loss(e))
        dws = [x for x in dws if x]
        if dws:
            # the strictest offset of the lines: one width loss per layer
            d["width_loss_um"] = max(dws)
        e = _avg_expr(b, "em_jmax_ac_rms")
        if e:
            # limit = sqrt(K * delta_T) mA/um, so probe at delta_T = 1
            v = limit(name, "jmax_rms_k", e, _per_um(e, delta_T=1.0))
            if v is not None:
                d["jmax_rms_k"] = _sig(v * v)
        out["conductors"][name] = d

    for name, b in _blocks(txt, "via"):
        # a via limit is a current (mA/cut), not a density -- no division by w
        e = _avg_expr(b)
        if e:
            out["vias"][name] = {"imax_avg": _sig(limit(name, "imax_avg", e,
                                                        _eval(e, _WIDE))),
                                 "vcwidth": num("em_vcwidth", b)}
    return out


#: default ICT conductor block name for a layer; `{layer}` is the lower-cased
#: layer name and `{Layer}` the name as written in the process file
CONDUCTOR_PATTERN = "{layer}_conn"


def ict_name(layer: str, pattern: str = CONDUCTOR_PATTERN) -> str:
    """PDK layer name -> ICT conductor block name (Metal1 -> metal1_conn)."""
    return pattern.format(layer=layer.lower(), Layer=layer)


def ict_conductor(layer: str, d: dict, ict: dict,
                  pattern: str = CONDUCTOR_PATTERN) -> str | None:
    """The ICT conductor block for a process-file layer, or None.

    An explicit `ict_name:` on the layer wins; then the pattern; then the
    layer's own name, lower-cased and as written.
    """
    cands = [d.get("ict_name"), ict_name(layer, pattern), layer.lower(), layer]
    return next((c for c in cands if c and c in ict["conductors"]), None)


def ict_via_name(via: str, ict: dict, explicit: str | None = None) -> str | None:
    for cand in (explicit, via, via.lower(), f"{via.lower()}_nocap",
                 f"{via.lower()}_cap"):
        if not cand:
            continue
        if cand in ict["vias"]:
            return cand
    return None


# ------------------------------------------------------------------ merge


def _source(d: dict, key: str, text: str | None):
    """Record where `d[key]` came from (`sources:`), when the ICT is named."""
    if text:
        if not isinstance(d.get("sources"), dict):
            d["sources"] = {}
        d["sources"][key] = text


def merge(emir: dict, ict: dict, pattern: str | None = None,
          source: str | None = None) -> tuple[dict, list]:
    """Fill EM fields from the ICT; leave curated geometry/R fields alone.

    `pattern` is the conductor block-name pattern (see `ict_name`); default
    the file's own `ict_conductor_pattern:`, else `{layer}_conn`. `source`:
    the ICT's file name; each value taken from it then names it and its
    block under the entry's `sources:`, and the file's `provenance:` becomes
    imported-ict (mixed, when it held values entered by hand or the PDK's
    public data).
    """
    pattern = pattern or emir.get("ict_conductor_pattern") or CONDUCTOR_PATTERN
    log = []
    if source:
        before = emir.get("provenance")
        emir["provenance"] = ("imported-ict" if before in (None, "imported-ict")
                              else "mixed")
    emir["ict_process"] = ict["process"]
    # A value the ICT does not give never replaces one the file has: it is
    # kept, and the log says so (coded `[emir] WARNING` lines, see `notice`).
    for k in ("temp_reference", "em_tref"):
        if ict.get(k) is not None:
            emir[k] = ict[k]
            _source(emir, k, source and f"ICT {source}: process block, {k}")
        elif emir.get(k) is not None:
            notice("EMIR-W309", f"ICT has no {k}: kept {emir[k]} from the file", log.append)
        else:
            notice("EMIR-W309", f"ICT has no {k} and the file has none: set it by hand"
                   + (" (emir check FAILs without it)" if k == "em_tref" else ""), log.append)
    for block, k, expr in ict.get("refused", []):
        notice("EMIR-W310", f"ICT block {block!r}: {k} expression refused "
               f"(only numbers, w, delta_T, + - * / ^ and sqrt are evaluated, to a "
               f"finite real number): {expr[:80]} -- the file's value, if any, is kept",
               log.append)
    if ict["jmax_factor"]:
        emir["jmax_factor"] = dict(sorted(ict["jmax_factor"].items()))
        _source(emir, "jmax_factor", source and f"ICT {source}: jmax_factor")

    for lname, d in emir.get("layers", {}).items():
        key = ict_conductor(lname, d, ict, pattern)
        c = ict["conductors"].get(key) if key else None
        if not c:
            notice("EMIR-W311", f"{lname}: no ICT conductor "
                   f"{d.get('ict_name') or ict_name(lname, pattern)!r} - left as-is "
                   f"(set `ict_name:` on the layer, or --conductor-pattern)", log.append)
            continue
        for k in ("tc1", "tc2", "jmax_avg", "jmax_peak", "jmax_rms_k", "width_loss_um"):
            if c.get(k) is not None:
                if d.get(k) not in (None, c[k]):
                    log.append(f"  {lname}.{k}: {d[k]} -> {c[k]} (ICT)")
                d[k] = c[k]
                _source(d, k, source and f"ICT {source}: conductor {key}")
        # the ICT's rms form replaces a fixed `jmax_rms` (a layer may carry
        # only one of the two; the engine refuses both)
        if c.get("jmax_rms_k") is not None and d.get("jmax_rms") is not None:
            log.append(f"  {lname}.jmax_rms: {d.pop('jmax_rms')} (fixed) -> "
                       f"jmax_rms_k {c['jmax_rms_k']} (ICT)")
            if isinstance(d.get("sources"), dict):
                d["sources"].pop("jmax_rms", None)
        # A field the ICT does not supply but the YAML still carries is a
        # hand estimate, and must stay flagged. Keying the check off `is None`
        # instead let a stale hand value silently clear the flag.
        d.pop("estimate", None)
        unbacked = [k for k in ("jmax_avg", "jmax_peak", "jmax_rms_k")
                    if c.get(k) is None]
        if unbacked:
            d["estimate"] = True
            have = [k for k in unbacked if d.get(k) is not None]
            notice("EMIR-W312", f"{lname}: ICT has no {', '.join(unbacked)}"
                   + (f" (keeping hand value for {', '.join(have)})" if have
                      else "") + " - estimate", log.append)

    estimated = []
    for vname, d in emir.get("vias", {}).items():
        key = ict_via_name(vname, ict, d.get("ict_name"))
        if not key:
            d["estimate"] = True
            notice("EMIR-W311", f"{vname}: no ICT via block - imax_avg left as an estimate",
                   log.append)
        else:
            new = ict["vias"][key]["imax_avg"]
            if new is not None:
                d["imax_avg"] = new
                _source(d, "imax_avg", source and f"ICT {source}: via {key}")
            elif d.get("imax_avg") is not None:
                # refused by the whitelist (reported above): keep the file's
                d["estimate"] = True
                notice("EMIR-W310", f"{vname}: kept imax_avg {d['imax_avg']} from the "
                       f"file (ICT expression refused) - estimate", log.append)
            if ict["vias"][key].get("vcwidth"):
                d["vcwidth"] = ict["vias"][key]["vcwidth"]
                _source(d, "vcwidth", source and f"ICT {source}: via {key}, em_vcwidth")
        # An ICT often gives vias an avg limit only. Curated rms/peak values
        # (present, and not flagged as an earlier estimate) are kept; missing
        # ones stay missing (not checked), and an earlier import's estimates
        # are removed: no limit is derived from another.
        if d.get("imax_avg") is None:
            continue
        curated = (not d.get("rms_peak_estimate")
                   and (d.get("imax_rms") is not None or d.get("imax_peak") is not None))
        if curated:
            log.append(f"  {vname}: curated imax_rms/imax_peak kept "
                       f"({d.get('imax_rms')}/{d.get('imax_peak')})")
            continue
        # no limit is invented: the via's rms and peak stay unset (reported
        # as not checked). An earlier import's rule-of-thumb values go.
        if d.get("rms_peak_estimate"):
            for k in ("imax_rms", "imax_peak", "rms_peak_estimate"):
                d.pop(k, None)
                if isinstance(d.get("sources"), dict):
                    d["sources"].pop(k, None)
        estimated.append(vname)
    if estimated:
        notice("EMIR-W313", f"vias {', '.join(estimated)}: the ICT gives no rms or peak "
               f"limit, so none is written and those metrics are NOT checked on them "
               f"(emir does not estimate a limit)", log.append)
    return emir, log


HEADER = """\
# EMIR screening process data for {pdk} -- GENERATED, do not hand-edit the EM
# fields. Regenerate with:
#   emir import-ict --ict <ict> --emir <this file>
#
# Geometry and resistance (gds, sheet_res, res, min_width, below/above) are
# curated from the techfile and are preserved across regeneration.
# EM fields come from {ict}:
#   jmax_avg    mA/um  em_jmax_dc_avg, conservative long-wire branch (L > ...),
#                      its slope in w (J of J x (w - dW))
#   width_loss_um um   that line's width offset dW (I_max = J x (W - dW)), the
#                      strictest of the avg and peak lines' (none: no offset)
#   jmax_peak   mA/um  em_jmax_dc_peak, same conversion. Pulse-width and duty
#                      INDEPENDENT -- a simplification, flagged in reports.
#   jmax_rms_k  --     em_jmax_ac_rms is sqrt(k * delta_T * f(w)); the limit
#                      used is sqrt(jmax_rms_k * delta_T) mA/um, the wide-wire
#                      asymptote, which is the minimum over width.
#   tc1, tc2           resistance vs temperature about temp_reference:
#                      R(T) = R * (1 + tc1*dT + tc2*dT^2)
#   jmax_factor        avg-limit derating vs temperature (em_tref = 1.0)
# Via rms/peak: the ICT has none, so none is written (not checked) unless
# curated by hand; curated values are kept. Nothing here is estimated.
# Each value taken from the ICT names it (and its block) under its entry's
# `sources:`; `provenance:` is imported-ict, or mixed when the file also holds
# values entered by hand or from the PDK's public data.
"""


def main(argv=None, sink=None) -> int:
    """`emir import-ict`. With `sink` (the CLI's `output.Out`), the
    `emir.import_ict/1` document is emitted to it."""
    from .errors import EmirError
    from .output import Out, document, parser
    ap = parser("emir import-ict", description=__doc__)
    ap.add_argument("--ict", required=True)
    ap.add_argument("--emir", required=True, help="emir.yaml to update in place")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--conductor-pattern", default=None,
                    help="ICT conductor block name for a layer, e.g. "
                         "'{layer}_conn' (the default; {layer} lower-cased, "
                         "{Layer} as written). Also settable in the file as "
                         "`ict_conductor_pattern:`; `ict_name:` on one layer "
                         "names its block outright.")
    args = ap.parse_args(argv)
    sink = sink or Out(args.format)

    if not os.path.isfile(args.ict):
        raise EmirError("EMIR-E101", f"ICT not found: {args.ict}", path=args.ict)
    why = unreadable(args.ict)
    if why:
        # never decrypted, decompressed or guessed: the limits are typed by hand
        # from the design manual's EM tables instead (emir process new)
        raise EmirError("EMIR-E325", f"{args.ict}: {why}. emir cannot read it, and it "
                        f"never tries to decrypt it or guess its content; nothing was "
                        f"written. Enter the EM limits by hand from the design manual's EM "
                        f"tables: `emir process new --from-dspf <dspf>` writes the fill-in "
                        f"file for the layers a DSPF uses", path=args.ict,
                        details={"ict": os.path.basename(args.ict), "reason": why})
    ict = parse_ict(args.ict)
    if not (ict["conductors"] or ict["vias"]):
        # a DSPF, a YAML, an empty file: nothing to import, and writing the
        # process file back unchanged would read as a successful import
        from .errors import step
        raise EmirError("EMIR-E104", f"{args.ict} holds no `conductor \"...\" {{...}}` or "
                        f"`via \"...\" {{...}}` block: it is not an ICT, and nothing was "
                        f"written. Without a readable ICT the EM limits are typed by hand "
                        f"from the design manual: `emir process new --from-dspf <dspf> -o "
                        f"<file>` writes the fill-in file",
                        next_steps=[step("look at what the file holds",
                                         "head -n 40 " + shlex.quote(args.ict)),
                                    step("no ICT to import: the person types the limits from "
                                         "the design manual into the fill-in file `emir "
                                         "process new --from-dspf <the block's DSPF> -o "
                                         "<file>` writes (its options)",
                                         "emir process new --help")])
    try:
        with open(args.emir, encoding="utf-8") as f:
            emir = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise EmirError("EMIR-E301", f"{args.emir}: {e}") from e
    if not isinstance(emir, dict):
        raise EmirError("EMIR-E302", f"{args.emir} is not a mapping")
    from .errors import collecting
    with collecting() as notices:
        emir, log = merge(emir, ict, pattern=args.conductor_pattern,
                          source=os.path.basename(args.ict))

    out = args.out or args.emir
    body = yaml.safe_dump(emir, sort_keys=False, default_flow_style=False,
                          width=100)
    # the ICT's file NAME only: its directory is a site path, and process
    # files get passed around
    from .output import write_text
    write_text(out, HEADER.format(pdk=emir.get("pdk", "?"), ict=os.path.basename(args.ict))
               + body)
    print(f"[emir_import] {args.ict}")
    print(f"[emir_import] process {ict['process']}, temp_reference "
          f"{ict['temp_reference']} C, em_tref {ict['em_tref']} C")
    print(f"[emir_import] jmax_factor {ict['jmax_factor']}")
    for line in log:
        print(line, file=sys.stderr if line.startswith(WARNING_PREFIX) else sys.stdout)
    print(f"[emir_import] wrote {out}")
    sink.emit(document("import_ict", ict=os.path.abspath(args.ict),
                       emir=os.path.abspath(args.emir), out=os.path.abspath(out),
                       process=ict["process"], temp_reference=ict["temp_reference"],
                       em_tref=ict["em_tref"], provenance=emir.get("provenance"),
                       jmax_factor={str(k): v for k, v in ict["jmax_factor"].items()},
                       changes=[ln.strip() for ln in log
                                if not ln.startswith("[emir] ")],
                       notices=notices))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
