"""Readers for simulation results, in the text formats we can trust.

EM post-processing lives or dies on getting the *right numbers off the right
window*, so this module does one thing: turn a simulator output into
`(sweep vector, {signal: array})` without interpreting it. All the physics --
window selection, avg/rms/peak, sign conventions -- is in `ccsim.py`.

The formats, recognised by their first bytes (never by the file name):

* **PSF ASCII** (`spectre -format psfascii`) -- Cadence-native. A `tran` or PSS
  `td` file has real values; a PSS `fd` file has complex harmonics written
  `(re im)`. Its HEADER names the simulator and its version.
* **Nutmeg ASCII** (`spectre -format nutascii`, Xyce `-a`, ngspice
  `filetype=ascii`) -- portable. One file may hold several plots.
* **HSPICE ASCII** (`.option post=2`, a `.trN` file) -- `emir.hspice`.
* **CSDF** (Xyce `format=probe`, PSpice, WRspice, Eldo) -- `emir.csdf`.

Every reader returns the same `Waves`, so the modes, hierarchy and streaming
work the same whichever simulator wrote the currents. `meta` carries what
the file says about itself: `format`, and `simulator` / `simulator_version`
where the header names them (the on-site verification, `emir verify-sim`,
is looked up by them).

**Binary results are deliberately not read** (binary PSF, binary SPICE raw,
binary HSPICE). Their layouts are undocumented or version-dependent; guessing
at them would produce plausible-looking currents that are silently wrong,
which is the worst possible failure for a sign-off-adjacent tool. `read()`
detects them and says what to re-run, or how to convert, instead. Since
`ccsim.run_spectre()` drives the simulator itself, it always asks for
psfascii.
"""
from __future__ import annotations

import json
import os
import re

import numpy as np

from .errors import EmirError

#: a PSF ASCII section keyword, alone on its line
_SECTION = re.compile(r'^(HEADER|TYPE|SWEEP|TRACE|VALUE|END)\s*$')
#: `"name" <rest>` -- the shape of every PSF ASCII entry
_ENTRY = re.compile(r'^"((?:[^"]|"")*)"\s*(.*)$')
#: `(re im)` complex, as PSS fd writes it
_CPLX = re.compile(r'^\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)')


class Waves:
    """One analysis: a sweep vector and the signals over it.

    `x` is time (tran, PSS td) or frequency (PSS fd). `data[name]` is real for
    a time-domain sweep and complex for a harmonic one; `is_complex` says
    which, because the metric maths differs entirely between the two.
    """

    def __init__(self, x, data, name=None, kind=None, meta=None):
        self.x = np.asarray(x, dtype=float)
        self.data = data
        self.name = name
        self.kind = kind or ""
        self.meta = meta or {}

    @property
    def is_complex(self) -> bool:
        """Complex (harmonic) data rather than a real time sweep.

        Decided once per data dict, like the name index in `_lookup`: the
        scan covers every saved signal, and it is asked once per net, so
        re-scanning made a run over every net quadratic (a synthetic 4000-net
        DSPF: 8.25 s, against 2.36 s cached; 2000 nets: 2.76 s against 1.17 s).
        """
        key = (id(self.data), len(self.data))
        c = getattr(self, "_cx", None)
        if c is None or c[0] != key:
            c = (key, any(np.iscomplexobj(v) for v in self.data.values()))
            self._cx = c
        return c[1]

    @property
    def xname(self) -> str:
        return "freq" if self.is_complex else "time"

    def __contains__(self, k):
        return k in self.data

    def __getitem__(self, k):
        return self.data[k]

    def get(self, k, default=None):
        return self.data.get(k, default)

    def match(self, names) -> dict:
        """Resolve requested signal names against what was actually saved.

        Returns {requested: actual}. Spectre spells a terminal current
        `inst:term`; a caller may hand us `I(inst:term)` from a waveform tool,
        or hierarchy-flattened `X0.inst:term`. Exact match wins, then those
        rewrites, then a unique suffix match.

        The lookups go through an index built once per result (`_lookup`), so
        matching every branch of a large net is linear in the names asked for;
        scanning every saved signal for each name that missed made matching
        the pins of a big CC result quadratic.
        """
        have = self.data
        low, tails = self._lookup()
        out = {}
        for n in names:
            if n in have:
                out[n] = n
                continue
            cands = [n.lower()]
            m = re.match(r'^[iv]\s*\(\s*(.+?)\s*\)$', n, re.I)
            if m:
                cands.append(m.group(1).lower())
            hit = next((low[c] for c in cands if c in low), None)
            if hit is None:
                # a unique saved name ending in `.<base>` or `/<base>`
                tail = tails.get(cands[-1], ())
                hit = tail[0] if len(tail) == 1 else None
            if hit is not None:
                out[n] = hit
        return out

    def _lookup(self) -> tuple:
        """`(lower-case name -> name, suffix -> [names])`, cached per data dict.

        A saved name `a.b/c:d` is listed under every suffix that follows a `.`
        or `/` (`b/c:d`, `c:d`), which is exactly the set of requests the
        suffix rule in `match` accepts for it.
        """
        key = (id(self.data), len(self.data))
        if getattr(self, "_lk", None) is not None and self._lk[0] == key:
            return self._lk[1]
        low, tails = {}, {}
        for k in self.data:
            kl = k.lower()
            low[kl] = k
            for i, c in enumerate(kl):
                if c in "./":
                    tails.setdefault(kl[i + 1:], []).append(k)
        self._lk = (key, (low, tails))
        return low, tails

    def summary(self) -> str:
        span = f"{self.x[0]:.6g} .. {self.x[-1]:.6g}" if len(self.x) else "empty"
        return (f"{self.name or '?'} [{self.kind}] {len(self.data)} signals, "
                f"{len(self.x)} points, {self.xname} {span}")


# ------------------------------------------------------------------ PSF ASCII


def read_psfascii(path: str) -> Waves:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    if not text.lstrip().startswith("HEADER"):
        raise EmirError("EMIR-E408", f"{path}: not PSF ASCII (binary PSF?) -- re-run "
                         f"spectre with -format psfascii", path=path)
    section = None
    header, order, data = {}, [], {}
    sweepname = None
    cur = None                      # per-point accumulator
    rows = []
    in_prop = False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        # a PROP( ... ) block annotates the entry above it; its "units" "A"
        # lines look exactly like trace declarations and must not be read as
        # signals
        if in_prop:
            in_prop = not s.startswith(")")
            continue
        if s.endswith("PROP("):
            in_prop = True
            s = s[:-len("PROP(")].strip()
            if not s:
                continue
        m = _SECTION.match(s)
        if m:
            section = m.group(1)
            continue
        if section == "HEADER":
            e = _ENTRY.match(s)
            if e:
                header[e.group(1)] = _scalar(e.group(2))
        elif section == "SWEEP":
            e = _ENTRY.match(s)
            if e and sweepname is None:
                sweepname = e.group(1)
        elif section == "TRACE":
            e = _ENTRY.match(s)
            # a GROUP header carries no data of its own; its members follow
            if e and not e.group(2).startswith("GROUP") and e.group(1) not in order:
                order.append(e.group(1))
        elif section == "VALUE":
            e = _ENTRY.match(s)
            if not e:
                continue
            name, rest = e.group(1), e.group(2).strip()
            if not rest:
                continue            # group header inside VALUE
            val = _scalar(rest)
            if val is None:
                continue
            if name == sweepname:
                if cur is not None:
                    rows.append(cur)
                cur = {name: val}
            elif cur is not None:
                cur[name] = val
    if cur:
        rows.append(cur)
    if not rows:
        raise EmirError("EMIR-E410", f"{path}: PSF ASCII has no VALUE points", path=path)
    x = np.array([r.get(sweepname, np.nan) for r in rows], dtype=float)
    for n in order or [k for k in rows[0] if k != sweepname]:
        col = [r.get(n, np.nan) for r in rows]
        arr = np.array(col)
        data[n] = arr if np.iscomplexobj(arr) else arr.astype(float)
    return Waves(x, data, name=header.get("analysis name"),
                 kind=header.get("analysis type", ""), meta=header)


def _scalar(tok: str):
    tok = tok.strip()
    m = _CPLX.match(tok)
    if m:
        return complex(float(m.group(1)), float(m.group(2)))
    if tok.startswith('"'):
        return tok.strip('"')
    try:
        return float(tok)
    except ValueError:
        return None


# --------------------------------------------------------------- Nutmeg ASCII


def read_nutascii(path: str) -> list:
    """A nutascii file may hold several plots; all of them are returned."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    plots, i, n = [], 0, len(lines)
    while i < n:
        if not lines[i].startswith("Plotname:"):
            i += 1
            continue
        title = lines[i].split(":", 1)[1].strip()
        i += 1
        flags, nvar, npt, names, in_vars = "real", 0, 0, [], False
        while i < n and not lines[i].startswith("Values:"):
            s = lines[i].strip()
            if s.startswith("Binary:"):
                raise EmirError("EMIR-E409", f"{path}: {BINARY_RAW_HELP}", path=path)
            if s.startswith("Flags:"):
                flags = s.split(":", 1)[1].strip()
            elif s.startswith("No. Variables:"):
                nvar = int(s.split(":", 1)[1])
            elif s.startswith("No. Points:"):
                npt = int(s.split(":", 1)[1])
            elif s.startswith("Variables:"):
                # Spectre puts the first variable on this line; Xyce starts on
                # the next one.  Keying continuation off `names` being non-empty
                # (as this used to) silently yielded ZERO signals for Xyce.
                in_vars = True
                body = s.split(":", 1)[1].strip()
                if body:
                    names.append(body.split()[1])
            elif in_vars and s and len(names) < nvar:
                names.append(s.split()[1])
            i += 1
        i += 1                                             # skip "Values:"
        cplx = "complex" in flags
        toks = []
        while i < n and len(toks) < npt * (nvar + 1):
            if lines[i].startswith("Plotname:"):
                break
            toks += lines[i].split()
            i += 1
        if i > n or len(toks) < npt * (nvar + 1):
            raise EmirError("EMIR-E411",
                f"{path}: plot {title!r} announces {npt} points x {nvar} "
                f"variables but holds {len(toks)} values -- the file is "
                f"truncated (did the simulation finish?) or has no Values: "
                f"section", path=path)
        vals = np.empty((npt, nvar), dtype=complex if cplx else float)
        k = 0
        for p in range(npt):
            k += 1                                          # the point index
            for v in range(nvar):
                vals[p, v] = _nut(toks[k], cplx)
                k += 1
        data = {nm: vals[:, j] for j, nm in enumerate(names[1:], start=1)}
        plots.append(Waves(np.real(vals[:, 0]), data, name=title,
                           kind="tran" if not cplx else "fd"))
    if not plots:
        raise EmirError("EMIR-E410", f"{path}: no Plotname: found -- not nutascii", path=path)
    return plots


def _nut(tok: str, cplx: bool):
    if cplx:
        re_, _, im = tok.partition(",")
        return complex(float(re_), float(im or 0))
    return float(tok)


# ------------------------------------------------------------------- dispatch

#: analysis suffixes we know how to read, best first
_PREF = (".psfascii", ".tran.tran", ".tran", ".td.pss", ".fd.pss", ".pss", ".dc", ".raw",
         ".tr0", ".csd")

#: what to do about a binary SPICE raw file (ngspice's default, Xyce `-r`)
BINARY_RAW_HELP = (
    "binary SPICE raw is not supported -- write the result as ASCII: ngspice "
    "`set filetype=ascii` before `write` (or SPICE_ASCIIRAWFILE=1), Xyce "
    "`-r <file> -a` (or `.print tran format=raw` with `Xyce -a`), Spectre "
    "`-format nutascii` or `-format psfascii`")


def load(result, analysis: str | None = None) -> Waves:
    """`result` itself if it is already a `Waves`, else `read(result)`."""
    return result if isinstance(result, Waves) else read(result, analysis=analysis)


def read(path: str, analysis: str | None = None) -> Waves:
    """Read one analysis from a file, or from a `-raw` output directory.

    With a directory, `analysis` picks the file (substring match); without it,
    the preference order above applies -- transient first, then PSS
    time-domain, then PSS harmonics.

    Every way a result can be unusable is an `EmirError` with its own code
    (binary PSF, binary raw, truncated, unreadable, an empty directory); a
    missing file is the `OSError` the caller reports as such.
    """
    try:
        return _read(path, analysis)
    except EmirError:
        raise
    except (ValueError, IndexError, KeyError) as e:
        raise EmirError("EMIR-E410", f"{path}: cannot parse the result "
                        f"({type(e).__name__}: {e})", path=path) from e


def _read(path: str, analysis: str | None = None) -> Waves:
    from . import csdf, hspice
    if os.path.isdir(path):
        return read(_pick(path, analysis), analysis=None)
    with open(path, "rb") as f:
        head = f.read(4096)
    if hspice.is_binary(head):
        raise EmirError("EMIR-E463", f"{path}: a binary HSPICE result -- re-run HSPICE with "
                        f".option post=2 post_version=2001 (ASCII, double precision)",
                        path=path)
    if head.startswith(b"\x00") or b"PSFversion" in head and b"HEADER" not in head:
        raise EmirError("EMIR-E408",
            f"{path}: binary PSF is not read -- convert it with Cadence's psf utility "
            f"(`psf -i {path} -o {path}.psfascii`, ASCII unless -b) and pass the converted "
            f"file, or re-run spectre with -format psfascii", path=path)
    if head.lstrip().startswith(b"HEADER"):
        w = read_psfascii(path)
        w.meta.setdefault("path", path)
        w.meta["format"] = "psfascii"
        w.meta["simulator"] = _known(w.meta.get("simulator"))
        w.meta["simulator_version"] = w.meta.get("version") or None
        return w
    if hspice.is_ascii(head):
        w = hspice.read_tr0ascii(path, analysis)
        _precision_notice(w)
        return _apply_names(w, path)
    if csdf.is_csdf(head):
        return _apply_names(csdf.read_csdf(path, analysis), path)
    if b"Plotname:" in head or head.lstrip().startswith(b"Title:"):
        if b"\nBinary:" in head:
            raise EmirError("EMIR-E409", f"{path}: {BINARY_RAW_HELP}", path=path)
        plots = read_nutascii(path)
        for p in plots:
            p.meta.update(_nut_simulator(head))
        if analysis:
            for p in plots:
                if analysis in (p.name or ""):
                    return _apply_names(p, path)
        return _apply_names(plots[0], path)
    if b"\x00" in head:
        raise EmirError("EMIR-E410", f"{path}: a binary file this reader does not know "
                        f"(binary PSF XL, SST2, FSDB ...): write the result as PSF ASCII "
                        f"(spectre -format psfascii), nutascii, HSPICE ASCII (.option "
                        f"post=2) or CSDF", path=path)
    raise EmirError("EMIR-E410", f"{path}: unrecognised format (expected PSF ASCII, "
                     f"nutascii, HSPICE ASCII or CSDF; binary results are not read)",
                     path=path)


def peek(path: str, analysis: str | None = None) -> dict:
    """What a result says about itself -- `format`, `simulator`,
    `simulator_version`, `names_sidecar` -- from its first lines and its
    names sidecar, without reading its data ({} when it cannot be read)."""
    from . import csdf, hspice
    beside = None
    try:
        if os.path.isdir(path):
            # a result directory (Spectre's -raw): `emir simulate` keeps its
            # sidecar beside the directory
            beside = path.rstrip(os.sep) + ".names"
            path = _pick(path, analysis)
        with open(path, "rb") as f:
            head = f.read(65536)
    except (OSError, EmirError):
        return {}
    meta: dict = {"path": path}
    text = head.decode("latin-1")
    if head.lstrip().startswith(b"HEADER"):
        sim = re.search(r'^\s*"simulator"\s+"([^"]*)"', text, re.M)
        ver = re.search(r'^\s*"version"\s+"([^"]*)"', text, re.M)
        meta.update(format="psfascii", simulator=_known(sim and sim.group(1)),
                    simulator_version=ver.group(1) if ver else None)
    elif hspice.is_ascii(head):
        lines = text.split("\n")
        rec = "".join(ln.rstrip("\r").ljust(80) for ln in lines[:3])
        meta.update(format="tr0ascii", post_version=hspice.format_of(lines[0]),
                    copyright=rec[112:184].strip(),
                    simulator=hspice.simulator_of(rec[112:184]), simulator_version=None)
    elif csdf.is_csdf(head):
        src = re.search(r"SOURCE\s*=\s*'([^']*)'", text)
        ver = re.search(r"VERSION\s*=\s*'([^']*)'", text)
        meta.update(format="csdf", source=src and src.group(1),
                    simulator=csdf.simulator_of(src and src.group(1)),
                    simulator_version=ver.group(1) if ver else None)
    elif b"Plotname:" in head or head.lstrip().startswith(b"Title:"):
        meta.update(_nut_simulator(head))
    side = path + ".names"
    if not os.path.isfile(side) and beside and os.path.isfile(beside):
        side = beside
    if os.path.isfile(side):
        meta["names_sidecar"] = side
        try:
            with open(side, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {}
        doc = doc if isinstance(doc, dict) else {}
        for k in ("simulator", "simulator_version"):
            if not meta.get(k) and isinstance(doc.get(k), str) and doc[k]:
                meta[k] = doc[k]
                if k == "simulator":
                    meta["simulator_from"] = "names sidecar"
        if isinstance(doc.get("sim_args"), str):
            meta["sim_args"] = doc["sim_args"]
        if isinstance(doc.get("kinds"), dict):
            # how emir's route read each pin (a probe, a MOSFET terminal ...):
            # what the on-site verification is matched against
            meta["pin_kinds"] = doc["kinds"]
    return meta


#: simulators emir knows by name (the on-site verification is kept per name)
KNOWN_SIMULATORS = ("spectre", "hspice", "xyce", "ngspice")


def _known(name) -> str | None:
    n = str(name or "").strip().lower()
    for k in KNOWN_SIMULATORS:
        if k in n:
            return k
    return n or None


def _nut_simulator(head: bytes) -> dict:
    """What a nutascii header says about its writer: ngspice puts
    `Command: ngspice-45+, Build ...` there; Xyce writes no such line."""
    text = head.decode("utf-8", "replace")
    m = re.search(r"^Command:\s*(.+)$", text, re.M)
    out = {"format": "nutascii"}
    if m:
        cmd = m.group(1).strip()
        out["simulator"] = _known(cmd.split(",")[0].split()[0].split("-")[0])
        v = re.search(r"-(\d[\w.+-]*)", cmd.split(",")[0])
        out["simulator_version"] = v.group(1) if v else None
    return out


def _precision_notice(w: Waves):
    from .errors import notice
    ver = w.meta.get("post_version")
    if ver in ("9601", "9007"):
        notice("EMIR-I464", f"{w.meta.get('path') or w.name}: HSPICE ASCII format {ver} keeps "
               f"five significant digits; .option post_version=2001 keeps more", _log)


def _apply_names(w: Waves, path: str) -> Waves:
    """Rename a Xyce, ngspice or HSPICE result into Spectre's `inst:pin`
    convention.

    Xyce spells a terminal current `I(R1)` / `ID(XMP0:MN1)` and
    upper-cases everything, ngspice `i(v_mp0)` or `v_mp0#branch`, HSPICE
    `i(xi0.rm1)` / `i1(xi0.mn0)` in lower case, so a caller asking for
    `Rm1:1` would miss. Rather than guess at the correspondence, whatever
    built the deck records the mapping it used and drops it beside the
    result as `<result>.names` (`emir schema names`):
    ``{"map": {"I(V_MP0)": ["MP0:s", 1]}}``. Keys match without regard to
    case; the stored sign carries Spectre's terminal direction for pin 2 /
    pin n. Without a sidecar this is a no-op, so Spectre results are
    untouched. A sidecar that is not of that form is EMIR-E424, naming the
    sidecar (not the result).

    Two HSPICE refinements: a `kcl` entry of the sidecar sets the sign of a
    terminal current whose direction the manuals leave open (a MOSFET's I3)
    from the device's own saved terminal currents (`kcl_signs`); and in a
    format-9007 result, whose names are cut to 16 characters, a name
    matches the sidecar key it is the unique 16-character start of.
    The sidecar's `simulator` / `simulator_version` fill `meta` where the
    result's header does not say them.
    """
    w.meta["path"] = path
    side = path + ".names"
    if not os.path.exists(side):
        return w
    doc = load_sidecar(side)
    m = doc["map"]
    w.meta["names_sidecar"] = side
    for k in ("simulator", "simulator_version"):
        if not w.meta.get(k) and doc.get(k):
            w.meta[k] = doc[k]
            if k == "simulator":
                w.meta["simulator_from"] = "names sidecar"
    if doc.get("sim_args") is not None:
        w.meta["sim_args"] = doc["sim_args"]
    if doc.get("kinds"):
        w.meta["pin_kinds"] = doc["kinds"]
    if doc.get("kcl"):
        kcl_signs(w, m, doc["kcl"])
    cut = w.meta.get("post_version") == "9007"
    short = {}
    if cut:
        for key in m:
            stem = key.rstrip(")")[:16]
            short.setdefault(stem, []).append(key)
    out, matched_cut, ambiguous = {}, [], {}
    for k, v in w.data.items():
        ent = m.get(k.upper())
        if ent is None and cut and len(k.rstrip(")")) >= 16:
            keys = short.get(k.upper().rstrip(")")[:16], [])
            if len(keys) == 1:
                ent = m[keys[0]]
                matched_cut.append(k)
            elif len(keys) > 1:
                ambiguous[k] = keys
        if ent:
            out[ent[0]] = v * ent[1]
        else:
            out[k] = v
    if ambiguous:
        k0, keys = next(iter(ambiguous.items()))
        raise EmirError("EMIR-E427", f"{path}: format 9007 cut {len(ambiguous)} name(s) to 16 "
                        f"characters that several names of {side} start with ({k0!r}: "
                        f"{', '.join(keys[:4])}{' ...' if len(keys) > 4 else ''}): which "
                        f"current each column holds cannot be told; simulate with "
                        f".option post_version=2001", path=path,
                        details={"names": sorted(ambiguous)})
    if cut and any(len(k.rstrip(")")) >= 16 for k in w.data):
        from .errors import notice
        notice("EMIR-W466", f"{path}: format 9007 cuts names to 16 characters; "
               f"{len(matched_cut)} matched the names sidecar by their start", _log)
    w.data = out
    return w


#: a device's terminal currents must sum to within this fraction of its
#: largest one for KCL to decide a sign
KCL_TOL = 1e-3


def kcl_signs(w: Waves, m: dict, kcl: dict) -> dict:
    """Set the sign of each `kcl` entry's current in `m` (the sidecar map,
    upper-case keys) from KCL over the device's saved terminal currents:
    +1 when they sum to zero with it as saved (every terminal current into
    the device), -1 when they sum to zero with it negated (saved out of the
    device). A current that is negligible against the device's others keeps
    the sidecar's sign; one with which neither sign closes KCL keeps it too,
    with EMIR-W465. Returns `{key: sign or None}`; `w.meta["kcl"]` says what
    was decided per device."""
    from .errors import notice
    have = {k.upper(): k for k in w.data}
    decided, failed, report = {}, [], {}
    for key, ent in kcl.items():
        K = key.upper()
        if K not in have or K not in m:
            continue
        dev = ent.get("device") or key
        terms = [have.get(str(t).upper()) for t in ent.get("terms") or []]
        if not terms or any(t is None for t in terms):
            failed.append(f"{dev} (a terminal current is not in the result)")
            decided[K] = None
            continue
        i3 = np.real(np.asarray(w.data[have[K]], dtype=complex)).astype(float)
        rest = sum(np.real(np.asarray(w.data[t], dtype=complex)).astype(float) for t in terms)
        scale = max([float(np.max(np.abs(i3)))] +
                    [float(np.max(np.abs(np.real(w.data[t])))) for t in terms])
        if scale == 0.0 or float(np.max(np.abs(i3))) <= KCL_TOL * scale:
            report[dev] = {"sign": m[K][1], "how": "negligible: the sidecar's sign"}
            decided[K] = m[K][1]
            continue
        r_in = float(np.max(np.abs(rest + i3)))
        r_out = float(np.max(np.abs(rest - i3)))
        sign = 1.0 if r_in <= r_out else -1.0
        if min(r_in, r_out) > KCL_TOL * scale:
            failed.append(f"{dev} (residual {min(r_in, r_out) / scale:.2g} of its largest "
                          f"terminal current)")
            report[dev] = {"sign": m[K][1], "how": "KCL does not close: the sidecar's sign"}
            decided[K] = None
            continue
        m[K] = (m[K][0], sign)
        decided[K] = sign
        report[dev] = {"sign": sign, "how": "KCL",
                       "residual": min(r_in, r_out) / scale}
    w.meta["kcl"] = report
    by = [d for d, r in report.items() if r["how"] == "KCL"]
    if by:
        into = [d for d in by if report[d]["sign"] > 0]
        out_ = [d for d in by if report[d]["sign"] < 0]
        notice("EMIR-I465", f"{w.meta.get('path') or w.name}: the source (emitter) current "
               f"sign of {len(by)} device(s) set from KCL: "
               + "; ".join(x for x in (
                   f"saved into the device for {', '.join(into[:4])}"
                   f"{' ...' if len(into) > 4 else ''}" if into else "",
                   f"saved out of the device for {', '.join(out_[:4])}"
                   f"{' ...' if len(out_) > 4 else ''}" if out_ else "") if x), _log)
    if failed:
        notice("EMIR-W465", f"{w.meta.get('path') or w.name}: the terminal currents of "
               f"{len(failed)} device(s) do not sum to zero with either sign of the source "
               f"current, so the documented sign (out of the source) was used: "
               f"{', '.join(failed[:4])}{' ...' if len(failed) > 4 else ''}", _log)
    return decided


def _log(line):
    import sys
    print(line, file=sys.stderr)


def load_sidecar(side: str) -> dict:
    """A names sidecar: `{"map": {KEY: (dspf name, sign)}, "kcl": {...},
    "simulator": ..., "simulator_version": ...}` (keys of `map` upper-cased);
    EMIR-E424 when it is not of the documented form."""
    m = load_names(side)
    with open(side, encoding="utf-8") as f:
        doc = json.load(f)
    kcl = doc.get("kcl") or {}
    ok = isinstance(kcl, dict) and all(
        isinstance(v, dict) and isinstance(v.get("terms"), list)
        and all(isinstance(t, str) for t in v["terms"]) for v in kcl.values())
    if not ok:
        raise EmirError("EMIR-E424", f"{side}: `kcl` is not {{NAME: {{\"terms\": [NAME, "
                        f"...], \"device\": ...}}}}; `emir schema names` prints the format",
                        path=side)
    sim = doc.get("simulator")
    ver = doc.get("simulator_version")
    args = doc.get("sim_args")
    kinds = doc.get("kinds")
    return {"map": m, "kcl": kcl,
            "simulator": sim if isinstance(sim, str) and sim else None,
            "simulator_version": ver if isinstance(ver, str) and ver else None,
            "sim_args": args if isinstance(args, str) else None,
            "kinds": ({str(k): v for k, v in kinds.items() if isinstance(v, str)}
                      if isinstance(kinds, dict) else None)}


def load_names(side: str) -> dict:
    """The `map` of a names sidecar, keys upper-cased; EMIR-E424 when the
    file is not ``{"map": {NAME: ["inst:pin", sign]}}``."""
    def bad(why):
        return EmirError("EMIR-E424", f"{side}: {why}; `emir schema names` prints the "
                         f"format", path=side)
    try:
        with open(side, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as e:
        raise bad(f"not JSON ({e})") from e
    except (OSError, UnicodeError) as e:
        raise bad(f"cannot be read ({e})") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("map"), dict):
        raise bad('not an object with a "map" object')
    out = {}
    for k, v in doc["map"].items():
        ok = (isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str)
              and v[0] and isinstance(v[1], (int, float)) and not isinstance(v[1], bool)
              and np.isfinite(v[1]))
        if not ok:
            raise bad(f"entry {k!r}: {v!r} is not [\"inst:pin\", sign]")
        out[str(k).upper()] = (v[0], float(v[1]))
    return out


#: signal names in the style of a SPICE simulator, not Spectre's `inst:pin`:
#: `I(..)`, Xyce's `ID(..)`, HSPICE's `i1(..)` .. `i4(..)`, ngspice's `@..`
#: and `..#branch`
_SPICE_NAME = re.compile(r"^(?:i[a-z0-9]?\(|@)|#branch$", re.I)


def naming_hint(w: Waves) -> str:
    """When a result matched none of the names a net needs: a sentence on how
    it names its currents, if that is SPICE style and no sidecar maps them
    (Xyce, ngspice); else ""."""
    if w.meta.get("names_sidecar"):
        return ""
    spice = [k for k in w.data if _SPICE_NAME.search(k)]
    if not spice:
        return ""
    side = (w.meta.get("path") or "<result>") + ".names"
    return (f" The result names its currents in SPICE style ({', '.join(spice[:3])}"
            f"{' ...' if len(spice) > 3 else ''}), not as the DSPF's inst:pin or R:1 "
            f"names, and no {os.path.basename(side)} sidecar maps them: write one "
            f"beside the result (`emir schema names` prints the format)")


def _pick(d: str, analysis: str | None) -> str:
    files = [f for f in sorted(os.listdir(d))
             if not f.startswith(".") and f != "logFile"
             and os.path.isfile(os.path.join(d, f))]
    if analysis:
        hit = [f for f in files if analysis in f]
        if not hit:
            raise EmirError("EMIR-E412", f"{d}: no result file matching {analysis!r} "
                             f"(have: {', '.join(files) or 'nothing'})", path=d)
        files = hit
    for suf in _PREF:
        for f in files:
            if f.endswith(suf):
                return os.path.join(d, f)
    if not files:
        raise EmirError("EMIR-E412", f"{d}: no result files", path=d)
    return os.path.join(d, files[0])
