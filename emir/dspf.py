"""DSPF reader: an extracted resistor network with the geometry EM needs.

SPEF carries no geometry, so it is useless for EM -- `J = I / w` needs the
width of the wire each resistor represents. DSPF carries it. A Quantus R line
looks like::

    Rm7 VDD:6 VDD:7 1.000000 $metal1 $L=5.0 $W=0.750000 $SI_W=0.75 $TYPE=10 \\
          $DIR=0 $LLX=30.000 $LLY=-0.375 $URX=35.000 $URY=0.375 $lvl=1

and a via resistor additionally carries `$VC=` (cut count), which is what the
per-cut via limit is checked against.

The other half of what this module is for is the **tap mapping**. `*|I` records
name every instance pin on the net and where it lands::

    *|I (MP2:s MP2 s I 0 45.0000 0.0000)

so DC currents (`--currents`) attach to grid nodes by instance-pin name, with
no labels and no LVS markers, and an RC simulation's branch currents check
the network's own KCL at the nodes no pin drives (`rcc.internal_kcl`).

Parsed, not simulated: this module builds the network and solves it. Nothing
here invokes an extractor.
"""
from __future__ import annotations

import os
import re

import numpy as np

from . import RCC_CONTRACT
from .errors import EmirError, notice

#: a number as an annotation's value: digits required, so a word value
#: (`$dir=ew`, magic's DSPF) is no number -- `[-\d.eE+]+` read its `e` as
#: one and the line failed to parse (EMIR-E201)
_NUM = r'([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(?![\w.])'
#: `$KEY=value` annotations on an element line
_ANN = re.compile(r'\$([A-Za-z_]+)=' + _NUM)
#: a bare `$layername` token (Quantus writes the conductor name that way)
_BARE = re.compile(r'[A-Za-z][\w.\-]*')
#: `$KEY=<name>` keys whose value names the conductor, for extractors that
#: write the layer as an annotation rather than as a bare token
_LAYER_KEYS = ("layer", "lyr")
#: prefix of the synthetic layer token given to a resistor that carries only a
#: `$lvl=N` number; `dspf_layers: {N: Metal1}` (or `"lvl:N"`) maps it
LVL_TOKEN = "lvl:"


def _layer_token(tokens) -> str | None:
    """The conductor layer named among an element line's tokens, or None.

    Quantus writes it as the first bare `$name` token (`$metal1`); other
    extractors write `$layer=metal1` or `$lyr=metal1`, and an explicit key wins
    over a bare token. Every other `$KEY=value` is an annotation, never a
    layer. Tokens are classified whole: the regex this replaces backtracked
    into annotation keys, so `$LLX=1 $metal1` read as layer 'LL' and
    `$layer=M1` as 'laye' -- junk names that then failed to map, silently
    leaving the segment unchecked.
    """
    bare = None
    for i, tok in enumerate(tokens):
        if len(tok) < 2 or not tok.startswith("$"):
            continue
        key, eq, val = tok[1:].partition("=")
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if not eq and nxt.startswith("="):
            # `$KEY = value`, written with spaces around the '='
            eq = "="
            val = nxt[1:] or (tokens[i + 2] if i + 2 < len(tokens) else "")
        if eq:
            # `$layer="M1"` and `$layer='M1'` name M1: the quotes are syntax
            val = val.strip("\"'")
            if key.lower() in _LAYER_KEYS and val:
                return val
            continue
        if bare is None and _BARE.fullmatch(key):
            bare = key
    return bare


class Dspf:
    """Nodes, resistors and instance pins of one extracted design."""

    def __init__(self):
        self.design = None
        self.temperature = None
        self.nets: dict[str, float] = {}
        self.node_xy: dict[str, tuple] = {}      # node -> (x, y)
        self.node_net: dict[str, str] = {}
        self.pins: dict[str, dict] = {}          # top-level port -> info
        self.inst_pins: dict[str, dict] = {}     # "Minst:g" -> info
        self.res: list[dict] = []                # resistors
        self.cap: list[dict] = []                # capacitors
        self.dev: list[dict] = []                # transistors / other devices
        #: pairs of nodes joined by a 0 V source (a port's further pins, as
        #: `emir extract --tool magic` ties them): one node to the network
        self.ties: list[tuple] = []
        self.path = None                         # the file it was read from
        self._ix = None                          # per-net index, see _index()

    @property
    def caps(self) -> int:
        return len(self.cap)

    # ------------------------------------------------------------ queries
    #
    # Per-net lookups go through one index built on first use, so asking about
    # every net costs one pass over the file rather than one pass per net: with
    # a full scan per net, `emir nets` on a synthetic 3000-net DSPF took 2.0 s
    # against 0.03 s to parse it (0.04 s in all with the index), and the cost
    # grows with the square of the net count. The index rebuilds itself when
    # resistors, pins or node assignments are added; call `reindex()` after
    # changing them in place.

    def reindex(self):
        """Drop the per-net index (after editing resistors or pins in place)."""
        self._ix = None

    def _index(self) -> dict:
        stamp = (len(self.res), len(self.node_net), len(self.inst_pins),
                 len(self.pins))
        if self._ix is not None and self._ix["stamp"] == stamp:
            return self._ix
        res_by, taps_by, ports_by = {}, {}, {}
        for r in self.res:
            a = self.node_net.get(r["n1"])
            b = self.node_net.get(r["n2"])
            res_by.setdefault(a, []).append(r)
            if b != a:
                res_by.setdefault(b, []).append(r)
        for name, d in self.inst_pins.items():
            taps_by.setdefault(d.get("net"), []).append((name, d))
        for p, d in self.pins.items():
            ports_by.setdefault(d.get("net"), []).append(p)
        self._ix = {"stamp": stamp, "res": res_by, "taps": taps_by,
                    "ports": {k: sorted(v) for k, v in ports_by.items()},
                    "node_nets": set(self.node_net.values()),
                    "layers": sorted({r["layer"] for r in self.res
                                      if r["layer"]})}
        return self._ix

    def tie_alias(self, net: str) -> dict:
        """{node: the node it is joined to} for `net`'s 0 V ties (`ties`):
        each group of tied nodes is one node, its first name in sort order."""
        parent: dict = {}

        def root(n):
            while parent.get(n, n) != n:
                n = parent[n]
            return n
        for a, b in self.ties:
            if self.node_net.get(a) == net and self.node_net.get(b) == net:
                ra, rb = root(a), root(b)
                if ra != rb:
                    lo, hi = sorted((ra, rb))
                    parent[hi] = lo
        return {n: root(n) for n in parent}

    def net_resistors(self, net: str) -> list:
        """Resistors with at least one node on `net`, in file order."""
        return list(self._index()["res"].get(net, ()))

    def has_net(self, net: str) -> bool:
        """`net` is declared by `*|NET` or owns at least one node."""
        return net in self.nets or net in self._index()["node_nets"]

    def net_ports(self, net: str) -> list:
        """The `*|P` port nodes on `net`, sorted."""
        return list(self._index()["ports"].get(net, ()))

    def layer_tokens(self) -> list:
        """Every distinct layer token on a resistor, sorted."""
        return list(self._index()["layers"])

    def net_taps(self, net: str) -> list:
        """Instance pins on `net`, in tap form (see `taps.load_taps`)."""
        out = []
        for name, d in self._index()["taps"].get(net, ()):
            out.append({"name": name, "node": d["node"], "xy": d["xy"],
                        "layer": d.get("layer"), "inst": d["inst"],
                        "pin": d["pin"]})
        return out

    def pin_groups(self, net: str) -> dict:
        """`{"inst:pin": [tap, ...]}` -- the instance pins on `net` by name.

        A simulation saves ONE current per instance terminal, but a terminal
        can reach the net at several places: a black-boxed block whose supply
        pin has two physical connections writes two `*|I` records with the same
        `inst:pin` and different nodes. Grouping makes that visible; keying by
        name alone let the later record silently overwrite the earlier one, so
        the whole current landed on one connection.
        """
        out: dict = {}
        for t in self.net_taps(net):
            out.setdefault(f"{t['inst']}:{t['pin']}", []).append(t)
        return out

    def split_pins(self, net: str) -> dict:
        """The `pin_groups` entries with more than one DSPF connection."""
        return {k: v for k, v in self.pin_groups(net).items() if len(v) > 1}

    def summary(self) -> str:
        vias = sum(1 for r in self.res if r.get("via"))
        bare = sum(1 for r in self.res if r.get("via") is None)
        wires = len(self.res) - vias - bare
        geo = sum(1 for r in self.res if r.get("w"))
        return (f"{self.design}: {len(self.nets)} nets, {len(self.node_xy)} nodes, "
                f"{wires} wire R + {vias} via R ({geo} with geometry)"
                + (f" + {bare} R with neither a width nor a cut annotation" if bare else "")
                + f", {len(self.inst_pins)} instance pins, {self.caps} caps")


def _logical_lines(f):
    """Join SPICE `+` continuations onto the element line they belong to.

    Quantus wraps long device records, and it chooses where: one cell carries
    `L=` and `W=` on the first line while another (a decap cell) pushes them
    onto a continuation. Parsing only the first line therefore drops geometry from
    some devices and not others -- silently, and differently per design.
    """
    cur, start = None, 0
    for n, raw in enumerate(f, 1):
        line = raw.rstrip("\n").strip()
        if not line:
            continue
        if line.startswith("+") and cur is not None:
            cur += " " + line[1:].strip()
            continue
        if cur is not None:
            yield start, cur
        cur, start = line, n
    if cur is not None:
        yield start, cur


def parse(path: str) -> Dspf:
    """Read a DSPF. A line that does not parse is an `EmirError` (EMIR-E201)
    naming the file and line number; a file that holds nothing a DSPF holds
    (a binary file, a process file ...) is EMIR-E217 -- it once read as a
    DSPF with no nets, and `emir nets` exited 0 on it."""
    d = Dspf()
    d.path = path
    cur_net = None
    markers = 0
    with open(path, "rb") as f:
        if b"\0" in f.read(8192):
            raise EmirError("EMIR-E217", f"{path} is a binary file, not a DSPF (DSPF is "
                            f"text)", dspf=path)
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in _logical_lines(f):
            markers += line.startswith("*|")
            try:
                cur_net = _line(d, line, cur_net)
            except (ValueError, IndexError) as e:
                raise EmirError("EMIR-E201", f"{path}:{n}: cannot parse {line[:60]!r} "
                                f"({type(e).__name__}: {e})", dspf=path) from e
    if not (markers or d.res or d.cap or d.dev):
        raise EmirError("EMIR-E217", f"{path} holds no DSPF record (*|NET, *|P, *|I, "
                        f"*|S) and no R, C or device line: it is not a DSPF", dspf=path)
    # every node a resistor or capacitor touches belongs to the net its name
    # prefixes; setdefault protects the *|I / *|S / *|P assignments above
    for e in (*d.res, *d.cap):
        for n in (e["n1"], e["n2"]):
            d.node_net.setdefault(n, _net_of(n))
    return d


def _line(d: Dspf, line: str, cur_net):
    """Add one logical line to `d`; returns the current `*|NET`."""
    if line.startswith("*|"):
        _meta(d, line, cur_net)
        if line.startswith("*|NET"):
            cur_net = line.split()[1]
        return cur_net
    if line.startswith("*") or line.startswith("."):
        return cur_net
    k = line[0].upper()
    if k == "R":
        r = _resistor(line)
        if r:
            d.res.append(r)
    elif k == "C":
        c = _capacitor(line)
        if c:
            d.cap.append(c)
    elif k == "M":
        m = _mosfet(line)
        if m:
            d.dev.append(m)
    elif k == "V":
        t = _tie(line)
        if t:
            d.ties.append(t)
    return cur_net


def _tie(line: str) -> tuple | None:
    """`Vpin0 vss.p2 vss 0`: two nodes joined by a 0 V source (a tie), or
    None for any other source (a value other than 0, a waveform)."""
    f = line.split()
    if len(f) < 4 or f[1] == f[2]:
        return None
    rest = [w for w in f[3:] if w.upper() != "DC"]
    if len(rest) != 1:
        return None
    try:
        return (f[1], f[2]) if float(rest[0]) == 0.0 else None
    except ValueError:
        return None


def _net_of(node: str) -> str:
    """`C:44` -> `C`, `Mmp2@3:g` -> `Mmp2@3` (an instance pin, not a net)."""
    return node.split(":")[0]


def _meta(d: Dspf, line: str, cur_net):
    parts = line.split()
    tag = parts[0]
    if tag == "*|DESIGN" and len(parts) > 1:
        d.design = parts[1].strip('"')
    elif tag in ("*|OPERATING_TEMPERATURE", "*|GLOBAL_TEMPERATURE"):
        try:
            d.temperature = float(parts[1])
        except (IndexError, ValueError):
            pass
    elif tag == "*|NET" and len(parts) > 2:
        try:
            d.nets[parts[1]] = float(parts[2])
        except ValueError:
            d.nets[parts[1]] = 0.0
    elif tag in ("*|P", "*|I", "*|S"):
        inner = line[line.find("(") + 1: line.rfind(")")] if "(" in line else ""
        f = inner.split()
        ann = dict(_ANN.findall(line))
        lvl = ann.get("lvl")
        if tag == "*|S" and len(f) >= 3:
            d.node_xy[f[0]] = (float(f[1]), float(f[2]))
            if cur_net:
                d.node_net[f[0]] = cur_net
        elif tag == "*|P" and len(f) >= 5:
            d.node_xy[f[0]] = (float(f[-2]), float(f[-1]))
            d.pins[f[0]] = {"node": f[0], "dir": f[1], "net": cur_net,
                            "xy": (float(f[-2]), float(f[-1])), "layer": lvl}
            if cur_net:
                d.node_net[f[0]] = cur_net
        elif tag == "*|I" and len(f) >= 6:
            # (<node> <inst> <pin> <dir> <?> <x> <y>)
            xy = (float(f[-2]), float(f[-1]))
            d.node_xy[f[0]] = xy
            # a node two device terminals share (magic's network can join them
            # on one node) keeps both: the second under its inst:pin
            key = f[0]
            old = d.inst_pins.get(key)
            if old is not None and (old["inst"], old["pin"]) != (f[1], f[2]):
                key = f"{f[1]}:{f[2]}"
            d.inst_pins[key] = {"node": f[0], "inst": f[1], "pin": f[2],
                                "dir": f[3], "net": cur_net, "xy": xy,
                                "layer": lvl}
            if cur_net:
                d.node_net[f[0]] = cur_net


#: `KEY=value` device parameters (no `$`), as DSPF writes them on device lines
_KV = re.compile(r'\b([A-Za-z_]+)=' + _NUM)


def _capacitor(line: str) -> dict | None:
    """A parasitic `Cc12 VDD:3 0 1.234e-15`, or a capacitor **device**.

    A device cap has no numeric value in that slot -- it carries a model and
    geometry instead::

        CCC  N2  OUT  L=5e-06 W=5e-06 area=2.5e-11 pj=2e-05

    Returning None for those drops them silently; they are kept with `c=None`
    and their geometry, so the input check can count them.
    """
    f = line.split()
    if len(f) < 4:
        return None
    kv = {k.lower(): float(v) for k, v in _KV.findall(line)}
    model, val = None, None
    try:
        val = float(f[3])
    except ValueError:
        # a bare token before the parameters is the model name
        model = f[3] if "=" not in f[3] else None
    return {"name": f[0], "n1": f[1], "n2": f[2], "c": val, "model": model,
            "area": kv.get("area"), "perim": kv.get("pj"),
            "w": kv.get("w"), "l": kv.get("l")}


def _mosfet(line: str) -> dict | None:
    """`MM8 MM8:d MM8:g MM8:s VSS nch L=2e-07 W=5e-07`.

    Kept so the input check can count the devices the DSPF carries.
    """
    f = line.split()
    if len(f) < 6:
        return None
    kv = {k.lower(): float(v) for k, v in _KV.findall(line)}
    return {"name": f[0], "d": f[1], "g": f[2], "s": f[3], "b": f[4],
            "model": f[5], "w": kv.get("w"), "l": kv.get("l")}


def _resistor(line: str) -> dict | None:
    f = line.split()
    if len(f) < 4:
        return None
    try:
        val = float(f[3])
    except ValueError:
        return None
    ann = {k.upper(): float(v) for k, v in _ANN.findall(line)}
    # the annotations are the tokens after the value (searching the line for
    # the value's text could land inside the element or node names instead)
    lyr = _layer_token(f[4:])
    lvl = int(ann["LVL"]) if "LVL" in ann else None
    if lyr is None and lvl is not None:
        # no layer name, only a level number: keep it addressable, so the
        # process file can map it (`dspf_layers: {1: Metal1}`) instead of the
        # segment being unmappable
        lyr = f"{LVL_TOKEN}{lvl}"
    bbox = None
    if all(k in ann for k in ("LLX", "LLY", "URX", "URY")):
        bbox = (ann["LLX"], ann["LLY"], ann["URX"], ann["URY"])
    elif "X" in ann and "Y" in ann:
        # Quantus writes a single $X/$Y per resistor rather than a box; the
        # segment still has $L and $W, so a box can be recovered, but only the
        # centre is certain -- keep it a point and let the caller decide.
        bbox = (ann["X"], ann["Y"], ann["X"], ann["Y"])
    # Only via annotation marks a via: a cut AREA ($A) or a cut count ($N,
    # $VC, $SAVC) -- the area matters because Quantus does not always write
    # the count. A resistor with neither and no width is NOT assumed to be a
    # via (`via` is None): calling every width-less resistor a via silently
    # dropped metal segments whose $W was missing from the EM check. What it
    # is comes from its layer in the process file (`map_layers`), and a wire
    # with no width is then an input-check FAIL (`check.check_layer_map`).
    area = ann.get("A") or ann.get("SI_A")
    w = ann.get("W") or ann.get("SI_W")
    # cut count: Quantus 21.1 writes $N; $VC/$SAVC appear in other vintages
    vc = ann.get("N") or ann.get("VC") or ann.get("SAVC")
    is_via = area is not None or bool(vc and vc > 0)
    return {
        "name": f[0], "n1": f[1], "n2": f[2], "r": val,
        "layer": lyr,
        "w": None if is_via else w,
        "l": ann.get("L"),
        # True: via annotation; False: a width; None: neither (see above)
        "via": True if is_via else (False if w is not None else None),
        "area": area,
        "cuts": int(vc) if vc and vc > 0 else None,
        "lvl": lvl,
        "bbox": bbox,
        "xy": (ann["X"], ann["Y"]) if "X" in ann and "Y" in ann else None,
        # a wire on a diagonal shape (the magic route's `$dir=diag`): its width
        # is magic's tile extent, not the width across the current (EMIR-W647)
        "diag": any(t.lower() == "$dir=diag" for t in f[4:]),
    }


# ------------------------------------------------------------------ solve


class Network:
    """Nodal solve over a DSPF resistor network.

    One unknown per extracted node, and each branch already knows its width,
    so `J = I / w` is exact.
    """

    def __init__(self, dspf: Dspf, net: str, rscale=1.0):
        self.dspf, self.net = dspf, net
        res = dspf.net_resistors(net)
        if not res:
            raise EmirError("EMIR-E205", f"no resistors on net {net!r}", net=net)
        # nodes joined by a 0 V source (a port's tied pins) are one node: the
        # source is a short, and a current entering at one pin may leave by
        # any of them (`Dspf.tie_alias`)
        alias = dspf.tie_alias(net) if getattr(dspf, "ties", None) else {}
        names = sorted({alias.get(n, n) for r in res for n in (r["n1"], r["n2"])})
        self.idx = {n: i for i, n in enumerate(names)}
        self.idx.update({n: self.idx[a] for n, a in alias.items() if a in self.idx})
        self.alias = alias
        self.nodes = names
        self.res = res
        # a number, or {DSPF layer token: factor} (`net_scaling`): each wire by
        # its own layer's TCR, vias unscaled
        self.rscale = rscale
        self.g = np.array([1.0 / (r["r"] * scale_of(rscale, r.get("layer")))
                           if r["r"] > 0 else 1e12 for r in res])
        self.a = np.array([self.idx[r["n1"]] for r in res])
        self.b = np.array([self.idx[r["n2"]] for r in res])
        self._lu = {}                      # ground node -> (free idx, factor)
        self.n_solves = 0
        self._pieces = None

    def pieces(self) -> np.ndarray:
        """The connected piece of every node (`labels[i]`), computed once."""
        if self._pieces is None:
            import scipy.sparse as sp
            from scipy.sparse.csgraph import connected_components
            n = len(self.nodes)
            A = sp.coo_matrix((np.ones(len(self.a)), (self.a, self.b)), shape=(n, n))
            self._pieces = connected_components(A, directed=False)[1]
        return self._pieces

    def unreached(self, ground: str) -> list:
        """The nodes not connected to `ground` (empty on a whole net)."""
        lab = self.pieces()
        g = lab[self.idx[ground]]
        return [self.nodes[i] for i in np.flatnonzero(lab != g)]

    def _factor(self, ground: str):
        """Factorise once per ground node, then reuse.

        Each connected piece is factorised on its own, with `ground` the
        reference of its piece and the first node the reference of any other
        piece (a run forced past EMIR-E207). Factorising the whole matrix of a
        net in pieces factorised a singular matrix: the solve then returned
        potentials of 1e16 V and branch currents that looked plausible.
        """
        if ground in self._lu:
            return self._lu[ground]
        import scipy.sparse as sp
        from scipy.sparse.linalg import splu
        if ground not in self.idx:
            raise EmirError("EMIR-E206", f"ground node {ground!r} not on net {self.net!r}", net=self.net)
        n = len(self.nodes)
        G = sp.coo_matrix(
            (np.concatenate([self.g, self.g, -self.g, -self.g]),
             (np.concatenate([self.a, self.b, self.a, self.b]),
              np.concatenate([self.a, self.b, self.b, self.a]))),
            shape=(n, n)).tocsr()
        lab = self.pieces()
        gi = self.idx[ground]
        out = []
        for p in [lab[gi], *sorted(set(lab.tolist()) - {lab[gi]})]:
            members = np.flatnonzero(lab == p)
            ref = gi if p == lab[gi] else int(members[0])
            fi = members[members != ref]
            lu = splu(G[fi][:, fi].tocsc()) if len(fi) else None
            out.append((p == lab[gi], members, fi, lu))
        self._lu[ground] = out
        return out

    def solve(self, inj: dict, ground: str):
        """`inj`: {node: amps into the net}. Returns (V per node, I per branch).

        On a net in pieces (only a run forced past EMIR-E207 gets here), the
        reference's piece is solved as usual. Another piece has no path to the
        reference: its potentials are not defined (NaN), and its branch
        currents are solved only when the current injected into it sums to
        zero (it then flows inside the piece); otherwise they are NaN -- a
        current with nowhere to go has no answer, and a NaN ratio is a FAIL.
        """
        pieces = self._factor(ground)
        n = len(self.nodes)
        I = np.zeros(n)
        for k, amps in inj.items():
            if k not in self.idx:
                raise EmirError("EMIR-E206", f"injection node {k!r} not on net {self.net!r}", net=self.net)
            I[self.idx[k]] += amps
        V = np.zeros(n)
        U = np.zeros(n)                    # V, with the other pieces' own potentials
        bad = np.zeros(n, bool)            # nodes of pieces with unbalanced current
        fin = np.abs(I[np.isfinite(I)])
        scale = max(float(fin.max()) if fin.size else 0.0, 1e-30)
        for main, members, fi, lu in pieces:
            if lu is not None:
                U[fi] = lu.solve(I[fi])
            if main:
                V[members] = U[members]
            else:
                V[members] = np.nan
                tot = float(I[members].sum())
                if not np.isfinite(tot) or abs(tot) > 1e-9 * scale:
                    bad[members] = True
        self.n_solves += 1
        Ib = self.g * (U[self.a] - U[self.b])
        Ib[bad[self.a] | bad[self.b]] = np.nan
        return V, Ib

    def main_piece(self, ground: str) -> np.ndarray:
        """Mask of the nodes connected to `ground`."""
        lab = self.pieces()
        return lab == lab[self.idx[ground]]

    def kcl_residual(self, Ib: np.ndarray, inj: dict, ground: str) -> float:
        """Max |sum of branch currents - injection| over the non-ground nodes
        whose branch currents were solved (see `solve`)."""
        n = len(self.nodes)
        net_in = np.zeros(n)
        np.add.at(net_in, self.a, Ib)
        np.add.at(net_in, self.b, -Ib)
        for k, amps in inj.items():
            net_in[self.idx[k]] -= amps
        net_in[self.idx[ground]] = 0.0
        for main, members, fi, lu in self._factor(ground):
            if not main and len(members):
                net_in[members[0]] = 0.0   # that piece's own reference
        fin = net_in[np.isfinite(net_in)]
        return float(np.abs(fin).max()) if fin.size else 0.0


def build_parser(prog="emir run"):
    """The `run` option set. Shared with `emir check` and the Virtuoso form."""
    import argparse

    from . import site
    from .output import ArgParser, add_dspf, add_format, seconds

    class _Parser(ArgParser):
        def parse_args(self, args=None, namespace=None):
            self._argv = list(args or [])
            return super().parse_args(args, namespace)

        def error(self, message):
            given = {t.split("=", 1)[0] for t in getattr(self, "_argv", [])}
            if "dspf" in message and "required" in message and given & {"--html", "--viewer"}:
                # `--html x.dspf` reads x.dspf as the directory (both options
                # take a value, always), which leaves the DSPF missing
                message += ("; note that --html and --viewer each take a "
                            "directory, so write `--html DIR`")
            super().error(message)

    # ArgParser refuses option abbreviations: with them on, `--taps f` (an
    # option of another command) silently became --taps-out, and a run with
    # --cc-sim then overwrote f with the ingested taps. `emir run @args.txt`
    # reads the arguments from a file, one per line.
    ap = _Parser(
        prog=prog, description="EM / IR screening of one DSPF",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_format(ap)
    add_dspf(ap, "extracted DSPF (R or RC)")
    ap.add_argument("--net", default=None,
                    help="net to analyse, or `all` (the default when the run "
                         "is driven: every net with resistors)")
    ap.add_argument("--process", default=site.get("process")[0],
                    help="PDK EM/IR data file (emir.yaml); default from the "
                         "site file")
    ap.add_argument("--em-temp", type=float, default=None, metavar="C",
                    help="the temperature (C) to analyse EM at (default: the "
                         "process file's em_tref): R is scaled by the "
                         "layer TCR, avg limits are derated by `jmax_factor`")
    ap.add_argument("--delta-t", type=float, default=5.0, metavar="C",
                    help="the self-heating temperature rise (C) the rms limits "
                         "allow, for layers whose rms limit is sqrt(k * delta_T) "
                         "(`jmax_rms_k`); default 5")
    ap.add_argument("--currents", default=None, metavar="JSON|FILE",
                    help='DC currents {"node": amps} into the net, inline JSON or the '
                         'path of a file holding it; must balance (`emir schema currents`)')
    ap.add_argument("--ground", default=None, help="reference node")
    ap.add_argument("--cc-sim", default=None,
                    help="the simulation result rcc reads: a transient of the RC-extracted "
                         "netlist with every branch current saved (`emir save-lines`), as "
                         "PSF ASCII or nutascii (a file, or a Spectre -raw directory), "
                         "HSPICE ASCII (.tr0, .option post=2) or CSDF. The currents come "
                         "from here instead of --currents.")
    ap.add_argument("--analysis", default=None,
                    help="which analysis to read from a --cc-sim directory; for a result "
                         "with several sweep tables, sweep:N picks one")
    ap.add_argument("--sim", default=None, choices=("spectre", "hspice", "xyce", "ngspice"),
                    help="the simulator that wrote --cc-sim, when neither the result's "
                         "header nor its names sidecar says (for the on-site "
                         "verification, `emir verify-sim`); with --netlist, the simulator "
                         "that runs it (default spectre)")
    ap.add_argument("--period", type=seconds, default=None, metavar="S",
                    help="the block's period in seconds (1e-08; on the command line also "
                         "10n or 10ns), needed for a transient result and a --netlist run "
                         "(EMIR-E428): every average is over the record's last whole periods "
                         "of it, up to three. " + RCC_CONTRACT + " Refused, with no number: "
                         "fewer than two whole periods (EMIR-E431), or the last two differ "
                         "by more than 2 %% on a branch carrying 1 %% of its net's largest "
                         "current (EMIR-E429)")
    ap.add_argument("--feed", default=None,
                    help="node the net is fed at (default: the DSPF port): the IR "
                         "reference unless --ground is given. One net only.")
    ap.add_argument("--netlist", default=None,
                    help="the testbench of the RC netlist to SIMULATE with Spectre instead of "
                         "reading a finished --cc-sim result (Spectre is licensed: it runs "
                         "only with --run; `emir simulate` drives HSPICE, Xyce and ngspice)")
    ap.add_argument("--mode", default=None, choices=("rcc",),
                    help="rcc, the one method of a simulated run (it may be left out): the RC "
                         "netlist was simulated with every branch current saved, and every "
                         "metric (avg, rms and peak EM, static and dynamic IR) is read from "
                         "them, exactly. A DC run (--currents) takes no mode")
    ap.add_argument("--run", action="store_true",
                    help="spectre (licensed): run it on --netlist; without it the command is "
                         "printed and nothing runs (ask the person first)")
    ap.add_argument("--sim-timeout", type=seconds, default=None, metavar="S",
                    help="stop the Spectre run of --netlist after this many seconds "
                         "(default 3600)")
    ap.add_argument("--save-branches", action="store_true",
                    help="print the Spectre `save` lines an RCC run needs. "
                         "With --net all (or no --net) that covers EVERY net, "
                         "which is what EM needs -- any wire carrying current "
                         "is subject to it, not just the rails.")
    ap.add_argument("--save-prefix", default="",
                    help="instance path to prepend to saved names when the "
                         'extracted subckt sits in a testbench, e.g. "XI0."')
    # --html and --viewer take a directory, always: an optional value
    # (nargs='?') swallowed the DSPF in `emir run --html x.dspf --net VDD`
    ap.add_argument("--html", default=None, metavar="DIR",
                    help="write a self-contained HTML report to this directory "
                         "(e.g. emir_out/nets). The net map is inline SVG, "
                         "so it zooms losslessly in a browser; with matplotlib, the "
                         "run's EM/IR chart beside it (emir_chart.png and "
                         "emir_chart_light.png: every net, the worst items numbered).")
    ap.add_argument("--gds", default=None, metavar="GDS",
                    help="with --html: the layout under the chart, drawn by KLayout (a "
                         "DSPF whose coordinates are the layout's: `emir extract --tool "
                         "magic` writes them so); --cell names its cell")
    ap.add_argument("--open", action="store_true",
                    help="open the HTML report in a browser when done")
    ap.add_argument("--viewer", default=None, metavar="DIR",
                    help="write the layout-viewer data to this directory "
                         "(e.g. emir_out/viewer): "
                         "SKILL for the Virtuoso EMIR Explorer and a marker "
                         "database for KLayout. Results land on the layout "
                         "itself, which is where the fix happens.")
    ap.add_argument("--viewer-all", action="store_true",
                    help="write every segment into the viewer data; by default a "
                         "run with more than 20000 segments keeps the worst "
                         "(clean ones and IR off the rails left out)")
    ap.add_argument("--lib", default=site.get("lib")[0],
                    help="OA library the viewer should open (default from the "
                         "site file)")
    ap.add_argument("--spectre", default=None,
                    help="spectre executable for --netlist (default: "
                         "$SPECTRE_BIN, site file, then $PATH)")
    ap.add_argument("--no-check", action="store_true",
                    help="skip the input checker (not recommended)")
    ap.add_argument("--force", action="store_true",
                    help="run even if the input checker reports FAIL")
    ap.add_argument("--summary-out", default=None, metavar="FILE",
                    help="write the run summary (the emir.run/1 JSON document, "
                         "the same one --format json prints) to this file; "
                         "`emir report --from FILE` turns it into the summary "
                         "for a person")
    # the old name of --summary-out, kept so existing argument files parse
    ap.add_argument("--json", dest="summary_out", default=None,
                    help=argparse.SUPPRESS)
    ap.add_argument("--power-nets", default=None,
                    help="comma-separated supply/ground nets. IR is a PDN "
                         "quantity by convention, so these set the IR colour "
                         "scale and are what the viewer opens on; other nets "
                         "stay selectable (analog bias and reference lines "
                         "have real drop). Inferred from net names if omitted.")
    ap.add_argument("--cell", default=None,
                    help="cell the viewer should open, and the --gds cell under the chart "
                         "(default: the DSPF design name)")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    return run(a)


class RunResult:
    """Everything one `emir run` produced, for the summary document.

    `solved` is `{net: result}` (what `run` returns), `report` the input
    check (None with --no-check), `viewer` what `layoutview.export` wrote,
    `overall` the verdict over every net, `notices` the coded warnings met on
    the way, `summary_path` the --summary-out file if one was written.
    """

    def __init__(self, a, dspf, proc=None):
        self.args, self.dspf, self.proc = a, dspf, proc
        self.solved: dict = {}
        self.report = None
        self.viewer = None
        self.overall = None
        self.notices: list = []
        self.summary_path = None
        self.charts: list = []       # the --html chart PNGs (emir.chart)
        self.stub = None            # (mode, text) for --save-branches
        self.waves = None            # the --cc-sim result, when it was read
        self.sim_verification = None  # emir verify-sim's record for its simulator
        # a licensed --netlist without --run: what would run (the
        # `emir.simulate/1` document's fields), and nothing ran
        self.planned = None


def run(a):
    """`emir run` with a parsed namespace. Returns the per-net results."""
    return analyse(a).solved


def analyse(a) -> RunResult:
    """`emir run` with a parsed namespace: check, analyse, report. Prints as
    it goes and returns a `RunResult`; refusals are `EmirError`s."""
    from .errors import collecting
    with collecting() as notices:
        _chart_gds(a)               # a --gds that is not there, before the run
        res = _analyse(a)
        res.notices = notices
        if res.solved:
            # whether this machine has verified the simulator that wrote the
            # currents (emir verify-sim): a coded notice, and the summary's
            # `simulator_verification`
            from .verify_sim import report_status, run_status
            res.sim_verification = run_status(a, res.waves, res)
            report_status(res.sim_verification)
        if a.summary_out and res.solved:
            from .output import write_json
            from .summary import summary_document
            res.summary_path = os.path.abspath(a.summary_out)
            write_json(a.summary_out, summary_document(res))
            print(f"  summary        : {res.summary_path}")
    if res.solved:
        res.overall = print_overall(res.solved, res.proc, res.sim_verification)
    return res


def _analyse(a) -> RunResult:
    import sys
    d = parse(a.dspf)
    res = RunResult(a, d)
    # Save lines are meant to be redirected into a netlist, so there the
    # summary goes to stderr and stdout stays SPICE.
    machine = a.save_branches
    print(d.summary(), file=sys.stderr if machine else sys.stdout)
    every = a.net in (None, "all")         # no --net means every net
    driven = bool(a.process and (a.currents or a.cc_sim or a.netlist))
    if a.currents and (a.cc_sim or a.netlist) and not machine:
        # one source of currents: refused even past the input check (--force,
        # --no-check), or the summary would name the one not used
        raise EmirError("EMIR-E514", "--currents and a simulation ("
                        + ("--cc-sim" if a.cc_sim else "--netlist") + ") were both given: "
                        "give one", dspf=a.dspf, process=a.process)
    if every and not machine and not driven:
        # nothing to analyse: describe the file, and say that nothing ran
        for n in sorted(d.nets):
            print(f"  net {n:16s} {len(d.net_resistors(n)):5d} R  "
                  f"{len(d.net_taps(n)):4d} inst pins")
        _not_analysed(a)
    if a.save_branches:
        from .rcc import save_branches
        res.stub = ("rcc", save_branches(d, None if every else a.net,
                                         prefix=a.save_prefix))
        print(res.stub[1])
        return res
    if not driven:
        for t in d.net_taps(a.net):
            print(f"  tap {t['name']:24s} node {t['node']:20s} "
                  f"({t['xy'][0]:.4f}, {t['xy'][1]:.4f})")
        _not_analysed(a)

    if not a.no_check:
        from .check import from_args
        rep = res.report = from_args(a, in_run=True)
        a._report = rep
        print("\n-- input check --")
        print(rep.table(show_ok=False))
        if not rep.passed and not a.force:
            err = refusal(rep)
            err.details.setdefault("run", run_context(a))
            raise err
        print()

    from .engine import Process
    # a hand-entered file's unsourced values: the input check says it as a
    # finding; without one (--no-check) the run says it (EMIR-W320)
    proc = res.proc = Process.load(a.process, temp=a.em_temp, delta_t=a.delta_t,
                                   warn_provenance=res.report is None)
    # the simulation runs once, whatever the net count
    if a.netlist:
        if (a.sim or "spectre") != "spectre":
            raise EmirError("EMIR-E604", f"--netlist with --sim {a.sim}: emir run drives "
                            f"Spectre only; simulate with `emir simulate {a.netlist} --sim "
                            f"{a.sim} --dspf {a.dspf}` and pass the result with --cc-sim")
        if a.period is None and not a.currents:
            # the transient Spectre would run is refused without a period
            # (the input check says so too): nothing is simulated
            from .check import netlist_period_error
            raise netlist_period_error(a.netlist, run_context(a))
        if not a.run:
            res.planned = _plan_spectre(a, d)
            return res
        from .opensim import run_spectre
        a.cc_sim = run_spectre(a.netlist, spectre=a.spectre,
                               timeout=getattr(a, "sim_timeout", None) or 3600.0)["raw"]

    # EM applies to every net that carries current, so `all` is the default
    # when no net is named
    nets = ([n for n in sorted(d.nets) if d.net_resistors(n)]
            if a.net in (None, "all") else [a.net])
    if not (a.cc_sim or a.currents):
        raise EmirError("EMIR-E407", "nothing to analyse: give --currents, --cc-sim "
                        "or --netlist", dspf=a.dspf, process=a.process)
    w = None
    if a.cc_sim:
        # read the result once (the input check's read, when it ran); every
        # net's currents use it
        from . import waves
        w = checked_waves(res, a) or waves.read(a.cc_sim, analysis=a.analysis)
        res.waves = w
    multi = len(nets) > 1
    # every net's averaging window first: a run whose window is refused on
    # some net is refused before any number is printed
    windows = run_windows(d, nets, w, a) if w is not None else {}
    solved = res.solved
    for net in nets:
        if multi:
            print(f"\n=== {net} ===")
        r = _analyse_net(d, net, proc, a, single=not multi, w=w, win=windows.get(net))
        solved[net] = r
        _maybe_html(r, d, a, proc, net=net, quiet=multi)
    _charts(res, a, gds=_chart_gds(a), cell=a.cell or res.dspf.design)
    if a.viewer:
        from .layoutview import export
        res.viewer = export(solved, d, proc, a.viewer, lib=a.lib, cell=a.cell,
                            rails=power_net_list(a),
                            all_segments=getattr(a, "viewer_all", False))
    return res


def checked_waves(res, a):
    """The result the input check read for this run (`check.Report.waves`),
    when it is the run's --cc-sim and --analysis, else None: a run reads its
    result once, so the reader's notices (EMIR-W466 ...) are said once."""
    rep = getattr(res, "report", None)
    if rep is None or getattr(rep, "waves", None) is None:
        return None
    return rep.waves if rep.waves_for == (a.cc_sim, getattr(a, "analysis", None)) else None


def _plan_doc(a, sim, netlist, deck, files, command, cwd, result, nets,
              names=None) -> dict:
    """The fields of the `emir.simulate/1` document a licensed --netlist run
    without --run prints: what would run, and the next step (the same command
    with --run, after the person's go-ahead)."""
    from .errors import notice
    from .output import command_line, strip_options
    notice("EMIR-I661", f"{sim} needs a licence: nothing was run. Ask the person; then "
           f"re-run with --run (or run `{' '.join(command)}` in {cwd})")
    argv = strip_options(list(getattr(a, "argv", None) or []), {"--format": 1})
    fmt = ["--format", "json"] if getattr(a, "format", "text") == "json" else []
    steps = ([{"why": f"ask the person first ({sim} is licensed); then run it: the same "
                      f"command with --run",
               "command": command_line("run", argv, "--run", fmt),
               "needs_human": True, "licensed_tool": sim}] if argv else [])
    print(f"[emir] {sim} (licensed): nothing was run")
    print(f"  deck    : {deck}\n  command : {' '.join(command)}\n  in      : {cwd}")
    return dict(simulator=sim, ran=False, executable=None, simulator_version=None,
                command=list(command), cwd=cwd, testbench=os.path.abspath(netlist),
                deck=deck, files=files, result=result, names_sidecar=names, log=None,
                mode="rcc", nets=list(nets), scope=None, path=[], where="--netlist",
                signals=0, direct=0, found=None, missing=[], seconds=None, points=None,
                analysis=None, notes=[], next_steps=steps)


def _plan_spectre(a, d) -> dict:
    """`emir run --netlist` on Spectre without --run: the command, nothing run."""
    from . import site
    exe, _src = site.tool("spectre", a.spectre)
    netlist = os.path.abspath(a.netlist)
    out = os.path.splitext(netlist)[0] + ".raw"      # what opensim.run_spectre writes
    command = [exe or "spectre", netlist, "-format", "psfascii", "-raw", out,
               "+escchars", "=log", out + ".log"]
    nets = ([n for n in sorted(d.nets) if d.net_resistors(n)]
            if a.net in (None, "all") else [a.net])
    return _plan_doc(a, "spectre", a.netlist, netlist, [], command,
                     os.path.dirname(netlist), out, nets)


def refusal(rep) -> EmirError:
    """The error a run refused by its input check raises: EMIR-E501, with every
    FAIL's code in `details.causes` and the findings in `details.findings`
    -- unless a required tool is missing, which keeps its own code (and so
    the contract's exit status 3, not 2), or the only problem is the window
    (EMIR-E428, E429, E431: the run's own refusal, as the run raises it)."""
    from .errors import EXIT_TOOL, lookup
    causes = rep.codes("FAIL")
    details = {"causes": causes,
               "findings": [f.as_dict(**rep.args) for f in rep.sorted()
                            if f.level in ("FAIL", "WARN")]}
    tool = [c for c in causes if lookup(c).exit_code == EXIT_TOOL]
    # a window the run would refuse is refused with its own code and details,
    # as the run itself refuses it: the code says what to give or simulate
    from .waveform import REFUSALS
    err = getattr(rep, "window_error", None)
    if not tool and causes and set(causes) <= set(REFUSALS) and err is not None:
        err.details.pop("run", None)            # the caller adds the run's own
        return err
    if tool:
        f = next(x for x in rep.sorted() if x.code == tool[0])
        return EmirError(tool[0], f"{f.message}; the run is refused"
                         + (f" ({len(causes) - 1} other blocking problem(s) in "
                            f"details.findings)" if len(causes) > 1 else ""),
                         next_steps=rep.next_steps(), details=details)
    return EmirError("EMIR-E501", f"input check failed: {rep.n_fail} blocking problem(s); "
                     f"fix the FAIL lines (or --force to run anyway)",
                     next_steps=rep.next_steps(), details=details)


def power_net_list(a) -> list | None:
    """`--power-nets` as a list, or None (then inferred from the names)."""
    if not getattr(a, "power_nets", None):
        return None
    return [n.strip() for n in a.power_nets.split(",") if n.strip()]


def _not_analysed(a):
    """End an `emir run` that had nothing to analyse: a verdict line that says
    so, then a refusal (EMIR-E509, exit 2) -- never a quiet exit 0 that a
    script or an agent could read as "no violations"."""
    need = []
    if not a.process:
        need.append("--process")
    if not (a.currents or a.cc_sim or a.netlist):
        need.append("--currents, --cc-sim or --netlist")
    print(f"  verdict        : NOT ANALYSED (no {' and no '.join(need)})")
    raise EmirError("EMIR-E509", f"nothing analysed: give {' and '.join(need)}",
                    dspf=a.dspf, details={"missing": need})


def overall_verdict(solved: dict, proc=None) -> tuple:
    """`(verdict, counts, worst_net, worst_ratio)` over every analysed net.
    A net whose worst ratio is not a number ranks worst of all."""
    vs = {n: _verdict(r, proc) for n, r in solved.items()}
    k = {v: sum(1 for x in vs.values() if x == v) for v in ("FAIL", "WARN", "CLEAN")}
    overall = "FAIL" if k["FAIL"] else ("WARN" if k["WARN"] else "CLEAN")

    def rank(n):
        v = worst_ratio(solved[n])
        return float("inf") if np.isnan(v) else v
    top = max(solved, key=rank, default=None)
    return overall, k, top, (worst_ratio(solved[top]) if top is not None else None)


def worst_ratio(r) -> float:
    """A net's worst EM ratio over every checked segment (NaN when one of
    them is not a number)."""
    mr = r.get("max_ratio") or 0.0
    return float(mr.get("all", 0.0) if isinstance(mr, dict) else mr)


def ratio_text(v) -> str:
    """`1.234x`, or "not a number"."""
    return f"{v:.3f}x" if v is not None and np.isfinite(v) else "not a number"


def _ratio4(v) -> str:
    """`1.2345x`, or "not a number" (a loop pass's line)."""
    return f"{v:.4f}x" if v is not None and np.isfinite(v) else "not a number"


def print_overall(solved: dict, proc, verification: dict | None = None) -> str:
    """The run's last lines: one verdict over every analysed net, whether the
    simulator that wrote the currents is verified on this machine, and the
    research-project line."""
    from . import RESEARCH_LINE, SCREENING_NOTE
    overall, k, top, worst = overall_verdict(solved, proc)
    v = verification or {}
    if v.get("status") in ("not verified", "partly verified", "unknown simulator"):
        print(f"  simulator route: {v['status']} on this machine"
              + (f" ({v['reason']})" if v.get("reason") in ("format not covered",
                                                            "current kinds not covered") else "")
              + (f" -- {v['command']}" if v.get("command") else ""))
    sus = []
    if proc is not None and isinstance(getattr(proc, "data", None), dict):
        from .process_file import suspects
        sus = [x for x in suspects(proc.data) if not x["confirmed"]]
    if sus:
        print(f"  limits SUSPECT : {', '.join(x['path'] for x in sus[:4])} outside the "
              f"usual range (a unit slip? EMIR-W321)")
    print(f"\n{RESEARCH_LINE}")
    print(f"overall verdict: {overall}"
          + f" -- {len(solved)} net(s): {k['FAIL']} FAIL, "
          f"{k['WARN']} WARN, {k['CLEAN']} CLEAN"
          + (f"; worst {ratio_text(worst)} on {top}" if top is not None else "")
          + (f"; the limits are SUSPECT ({len(sus)} value(s) outside the usual range: "
             f"EMIR-W321)" if sus else "")
          + f". {SCREENING_NOTE}")
    return overall


def write_summary(path, solved, d, proc, a, viewer=None) -> str:
    """Write the run summary (`emir.run/1`) for results computed outside
    `analyse` (kept for callers of the 1.0 API)."""
    from .output import write_json
    from .summary import summary_document
    res = RunResult(a, d, proc)
    res.solved, res.viewer = solved, viewer
    res.overall = overall_verdict(solved, proc)[0]
    return write_json(path, summary_document(res))


def _verdict(r, proc) -> str:
    """Same rule as `_print_net`: FAIL / WARN / CLEAN from the clusters. A
    wire that could not be checked (no width) is a FAIL, never a pass."""
    cl = r.get("clusters") or []
    if r.get("no_width") or any(c["severity"] == "FAIL" for c in cl):
        return "FAIL"
    return "WARN" if cl else "CLEAN"


def did_you_mean(name: str, choices) -> str:
    """` (did you mean 'X'?)` for the closest of `choices`, or ''.

    A case-insensitive match wins; otherwise `difflib`'s closest names."""
    import difflib
    choices = sorted(choices)
    same = [c for c in choices if c.lower() == name.lower()]
    near = same or difflib.get_close_matches(name, choices, n=3, cutoff=0.6)
    return f" (did you mean {', '.join(repr(c) for c in near)}?)" if near else ""


def result_holds(dspf: Dspf, net: str, w) -> dict:
    """What a simulation result holds for one net: rcc needs every branch
    current, in the time domain. `emir next` uses this to plan only what the
    check accepts."""
    names = [r["name"] for r in dspf.net_resistors(net)]
    br = w.match([f"{n}:1" for n in names]) if names else {}
    return {"branches": [len(br), len(names)], "time_domain": not w.is_complex,
            "rcc": bool(names) and len(br) == len(names) and not w.is_complex}


def run_windows(d, nets, w, a) -> dict:
    """`{net: window}`: every net's averaging window (`rcc.net_windows`), or
    the run's refusal (EMIR-E428, E429, E431), whose context names the run's
    inputs so its next step (`emir next`) plans the fix."""
    from .rcc import net_windows
    rep = getattr(a, "_report", None)
    pre = getattr(rep, "windows", None) if rep is not None else None
    if pre and pre["key"] == (a.cc_sim, getattr(a, "analysis", None), a.period, tuple(nets)):
        return pre["wins"]                  # the input check took them already
    return net_windows(d, nets, w, period=a.period, ctx=run_context(a))


def run_context(a) -> dict:
    """The run's inputs a refusal carries (`details.run`), so `emir next
    --run-summary` can plan its fix from the error document alone."""
    ab = lambda p: os.path.abspath(p) if p else None  # noqa: E731
    return {"dspf": ab(a.dspf), "process": ab(a.process), "cc_sim": ab(a.cc_sim),
            "netlist": ab(getattr(a, "netlist", None)),
            "net": None if a.net in (None, "all") else a.net, "period": a.period,
            "analysis": getattr(a, "analysis", None), "sim": getattr(a, "sim", None)}


def _analyse_net(d, net, proc, a, single=True, w=None, win=None):
    """One net: rcc from a simulation result, else DC currents. Prints as it
    goes, returns `res`. `w` is the `--cc-sim` result already read by `run`
    (read here if None); `win` its averaging window (`run_windows`; computed
    here if None)."""

    from .rcc import reference_node
    # the reference is always a node on *this* net -- taking the first port in
    # the DSPF would ground a signal net at some other net's pin
    gnd = reference_node(d, net, (a.ground or a.feed) if single else None)
    if a.cc_sim:
        from .rcc import rcc_check
        r = rcc_check(d, net, proc, w if w is not None else a.cc_sim, gnd,
                      analysis=a.analysis, period=a.period, window_sel=win)
        r["mode_reason"] = ("a simulation result: every branch current of the RC netlist, "
                            "read directly")
        _print_net(r, proc, a, d, net=net)
        if r.get("dynamic"):
            dy = r["dynamic"]
            print(f"  dynamic IR     : {dy['worst_V'] * 1e3:.4f} mV at "
                  f"{dy['worst_node']} at t={dy['worst_t']:.4g} s"
                  f"{_dyn_ratio(dy['worst_V'], r['ir_drop'])}")
        print(f"  loop residual  : {r['loop_residual'] * 1e3:.3e} mV over "
              f"{r['n_offtree']} off-tree branches"
              + ("  (loop-free net: vacuous)" if not r["n_offtree"] else ""))
        return r

    from .jsonarg import load
    obj, _path = load(a.currents, "--currents", "EMIR-E401", schema="currents",
                      dspf=a.dspf, net=net)
    try:
        inj = {str(k): float(v) for k, v in obj.items()}
    except (ValueError, TypeError, AttributeError) as e:
        raise EmirError("EMIR-E401", f"--currents is not a JSON object of node: "
                        f"amps ({e})", dspf=a.dspf, net=net) from e
    r = check_net(d, net, proc, inj, a.ground or gnd)
    # one label for this route everywhere (summary, HTML, viewer)
    r["mode"] = "dc"
    r["mode_reason"] = "DC --currents: the R grid is solved directly"
    _print_net(r, proc, a, d, net=net)
    return r


def _chart_gds(a) -> str | None:
    """--gds, checked: the layout under the chart."""
    g = getattr(a, "gds", None)
    if not g:
        return None
    if not os.path.isfile(g):
        raise EmirError("EMIR-E101", f"--gds {g}: no such file", path=g)
    return os.path.abspath(g)


def _charts(res, a, gds=None, cell=None, what="emir run"):
    """--html: the run's EM and IR chart PNGs beside the pages (`emir.chart`),
    then the index again, which shows them. A multi-net run prints the index."""
    if not a.html or not res.solved:
        return
    from .netreport import index_dir
    from .scene import write_charts
    res.charts = write_charts(res.solved, res.dspf, res.proc, a.html, gds=gds, cell=cell,
                              command=getattr(a, "command", None) or "", what=what)
    p = index_dir(a.html)
    if len(res.solved) > 1:
        print(f"\n  index          : {os.path.abspath(p)}")


def _maybe_html(r, d, a, proc, net=None, quiet=False):
    if not a.html:
        return
    import shlex
    import sys

    from .netreport import index_dir, net_report
    net = net or a.net
    # provenance: the CLI records the command it parsed (`a.command`); joining
    # sys.argv after "emir run" repeated the subcommand ("emir run run ...")
    cmd = getattr(a, "command", None) or ("emir run " + shlex.join(sys.argv[1:]))
    from .output import safe_component
    p = net_report(r, d, net, proc, a.html,
                   file_name=f"{safe_component(d.design or 'net')}_{safe_component(net)}.html",
                   command=cmd)
    print(f"  html           : {os.path.abspath(p)}")
    if not quiet:
        print(f"  index          : {os.path.abspath(index_dir(a.html))}")
    if a.open and not quiet:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(p))


#: below this fraction of the dynamic excursion, the average is numerical
#: noise and the ratio between them is meaningless
DYN_RATIO_FLOOR = 0.02


def dyn_ratio(dynamic, average):
    """`(ratio, note)` -- the ratio only where the average is real.

    On a gate input the DC current is essentially zero, so `dynamic/average`
    runs to five figures and says nothing except that the denominator vanished.
    Reporting that as "88825x the average" would read as an alarm.
    """
    if average > DYN_RATIO_FLOOR * max(dynamic, 1e-30):
        return dynamic / average, None
    return None, "no meaningful DC current on this net -- purely dynamic"


def _dyn_ratio(dynamic, average):
    ratio, note = dyn_ratio(dynamic, average)
    return f" ({ratio:.2f}x the average)" if ratio else f"  [{note}]"


def _print_net(r, proc, a, d, net=None):
    """One report for every mode -- the verdict must not look different
    depending on which route produced the currents."""
    net = net or a.net
    lm = {k: v[1] for k, v in r["layer_map"].items()}
    if not a.html or a.net != "all":
        print(f"  layer map      : {lm}")
    label, rtext = r_text(r, proc)
    print(f"  {label:<15}: {rtext}")
    print(f"  KCL residual   : {r['kcl_residual']:.2e} A")
    # On a net with no port there is no feed to measure against, so the figure
    # is the spread across the net -- which is invariant to the reference and
    # therefore still meaningful, but it is not "IR drop from the supply".
    fed = bool(d.net_ports(net))
    print(f"  {'IR drop' if fed else 'R spread'}        : "
          f"{r['ir_drop'] * 1e3:.4f} mV"
          + ("" if fed else "  (no port on this net: spread, not drop)"))
    w = r["worst"]
    nowidth = r.get("no_width") or []
    detail = ([f"worst {ratio_text(w['ratio'])} {w['metric']} {w['kind']} on {w['layer']}"]
              if w else [])
    if nowidth:
        detail.append(f"{len(nowidth)} wire(s) NOT checked (no width)")
    print(f"  verdict        : {_verdict(r, proc)}"
          + (", " + "; ".join(detail) if detail else ""))
    for c in r["clusters"][:10]:
        rt = f"{c['ratio']:7.3f}x" if np.isfinite(c["ratio"]) else "     NaN"
        print(f"    {c['severity']:4s} {rt} {c['metric']:4s} "
              f"{c['kind']:4s} {c['layer']:8s} {c['name']}")
    if nowidth:
        print(f"  wire width     : {len(nowidth)} wire resistor(s) carry no width "
              f"($W / $SI_W), so J = I/w cannot be evaluated and they were NOT "
              f"checked: {', '.join(nowidth[:6])}{' ...' if len(nowidth) > 6 else ''}")
    vc = r.get("via_cuts") or {}
    if vc.get("assumed"):
        n = vc["assumed"]
        print(f"  via cuts       : {len(n)} via(s) with no cut count and no "
              f"usable cut area, checked as ONE cut (conservative): "
              f"{', '.join(n[:6])}{' ...' if len(n) > 6 else ''}")
    if vc.get("area"):
        print(f"  via cuts       : {len(vc['area'])} via(s) counted from the "
              f"cut area ($A) and the process vcwidth")
    print(f"  currents       : {r['metric_note']}")


# --------------------------------------------------- PDK-aware net analysis


def explicit_layers(proc) -> dict:
    """The process file's explicit layer map: its `magic: layers:` (magic's
    names, for a DSPF from `emir extract --tool magic`) under its
    `dspf_layers:` (which wins where both name a token)."""
    data = getattr(proc, "data", None) or {}
    magic = (data.get("magic") or {}).get("layers") or {}
    return {**magic, **(data.get("dspf_layers") or {})}


def map_layers(dspf: Dspf, proc, explicit: dict | None = None,
               log=None) -> dict:
    """DSPF layer token -> PDK layer/via name.

    The extractor names conductors after the *connectivity* layers of the LVS
    deck, not the techfile: an extractor typically emits names such as
    `metal1_conn`, `poly_conn`, `cont_ndiff`, `Via1`. So an exact match is
    tried first, then progressively shorter `_`-suffixed forms (`cont_ndiff`
    -> `cont` -> Cont), which also folds the several contact flavours onto the
    one PDK contact.

    Anything matched by a rule rather than exactly is **reported**, because a
    guessed layer silently applies the wrong current limit. `emir.yaml:
    dspf_layers` overrides any of it. Its keys are DSPF layer tokens, or
    `$lvl` numbers (`1:` or `"lvl:1"`) for resistors that carry a level and
    no layer name -- those are never matched by rule, only explicitly.
    """
    out, inferred = {}, {}
    if explicit is None:
        explicit = explicit_layers(proc) or None
    if explicit:
        # an integer key (YAML `1: Metal1`) means a `$lvl` level number
        explicit = {(f"{LVL_TOKEN}{int(k)}" if isinstance(k, int) else str(k)): v
                    for k, v in explicit.items()}
    keys = {k.lower(): ("layer", k) for k in proc.layers}
    keys.update({k.lower(): ("via", k) for k in proc.vias})
    for tok in dspf.layer_tokens():
        if tok.startswith(LVL_TOKEN):
            continue                    # a level number names no layer by itself
        parts = tok.lower().split("_")
        for n in range(len(parts), 0, -1):
            hit = keys.get("_".join(parts[:n]))
            if hit:
                out[tok] = hit
                if n != len(parts):
                    inferred[tok] = hit[1]
                break
    for tok, name in (explicit or {}).items():
        if name in proc.layers:
            out[tok] = ("layer", name)
        elif name in proc.vias:
            out[tok] = ("via", name)
        inferred.pop(tok, None)
    if inferred and log:
        log("[emir] layer names matched by suffix rule, not exactly -- check "
            "these carry the right limits: "
            + ", ".join(f"{k} -> {v}" for k, v in sorted(inferred.items())))
    return out


def via_cuts(r: dict, via: dict) -> tuple:
    """`(cuts, source)` for via resistor `r`, checked against PDK via `via`.

    * ``count``: the extractor wrote the cut count (`$N`, `$VC`, `$SAVC`);
    * ``area``: recovered from the cut area `$A` and the process file's
      `vcwidth` (cut side, um). Only the process value is used: a built-in
      default cut size silently scaled every via ratio (a default smaller than
      the real cut counts too many cuts, so the current per cut reads low --
      the non-conservative direction);
    * ``assumed``: neither is known, so the via is checked as ONE cut. One is
      the fewest a via can have, so this can only over-state the current per
      cut: conservative, and reported -- such vias used to be skipped silently.
    """
    if r.get("cuts"):
        return int(r["cuts"]), "count"
    vcw = via.get("vcwidth")
    if r.get("area") and vcw:
        return max(int(round(r["area"] / vcw ** 2)), 1), "area"
    return 1, "assumed"


def em_clusters(nw, Ie_m: dict, proc, lmap: dict, notes: dict | None = None) -> tuple:
    """`(violations, max_ratio)` from per-metric branch currents.

    Deliberately shared by every path that produces branch currents -- the
    DC grid solve and reading them straight off an RCC sim. Where
    the current came from must not change how it is judged, and one
    implementation is the only way to guarantee that.

    `max_ratio` is `{"all", "wire", "via"}` over **every** checked resistor, not
    just the ones that breached the warn fraction. Two reasons it is split by
    kind: a clean net whose worst is 0.79x and one whose worst is 0.05x are not
    the same result, and a via failure sitting behind a larger wire failure is
    invisible in a single worst-of number -- which is exactly how a real
    single-cut via violation went unnoticed here.

    Pass a dict as `notes` to learn how each via's cut count was found:
    `notes["via_cuts"]` becomes `{"area": [names], "assumed": [names]}` (see
    `via_cuts`; vias with an extracted count are not listed), and
    `notes["no_width"]` the wires that carry no width: `J = I / w` cannot be
    evaluated for them, so they are NOT checked -- the input check FAILs on
    them, and a run forced past it reports them and gives a FAIL verdict.
    """
    from .engine import METRICS
    viol = []
    max_ratio = {"all": 0.0, "wire": 0.0, "via": 0.0}
    how = {"area": [], "assumed": []}
    no_width = []
    # the time average of |I| beside the signed one, where the path computed
    # it (rcc's simulated waveforms): a reversing current cancels in the signed
    # average
    absavg = Ie_m.get("absavg")
    bidirectional, crowding = [], []
    for i, r in enumerate(nw.res):
        hit = lmap.get(r["layer"])
        if not hit:
            continue
        kind, pdkname = hit
        ratios, vals = {}, {}
        cuts = src = None
        weff = None
        if kind == "via":
            cuts, src = via_cuts(r, proc.vias[pdkname])
            if src != "count":
                how[src].append(r["name"])
        elif not r.get("w"):
            no_width.append(r["name"])
            continue
        else:
            # the width the limit applies to: W - dW (`width_loss_um`), as the
            # design manual prints I_max = J x (W - dW); W itself without one.
            # A wire no wider than dW is refused (EMIR-E323)
            weff = proc.wire_width(pdkname, r["w"])
        for m in METRICS:
            if m not in Ie_m:
                continue                # a metric the mode does not check
            if kind == "via":
                lim = proc.vias[pdkname]["imax"].get(m)
                if not lim:
                    continue
                vals[m] = abs(Ie_m[m][i]) / cuts
            else:
                lim = proc.layers[pdkname]["jmax"].get(m)
                if not (lim and r.get("w")):
                    continue
                vals[m] = abs(Ie_m[m][i]) / weff
            ratios[m] = vals[m] / lim
        if not ratios:
            continue
        if absavg is not None and "avg" in ratios and np.isfinite(ratios["avg"]):
            lim = (proc.vias[pdkname]["imax"] if kind == "via"
                   else proc.layers[pdkname]["jmax"]).get("avg")
            ra = abs(absavg[i]) / (cuts if kind == "via" else weff) / lim
            if ra >= proc.warn_fraction and ra > 1.5 * ratios["avg"]:
                bidirectional.append({"name": r["name"], "layer": pdkname,
                                      "avg_ratio": float(ratios["avg"]),
                                      "abs_avg_ratio": float(ra)})
        # a non-finite ratio governs: max() would rank a NaN below anything
        nonfinite = [m for m, v in ratios.items() if not np.isfinite(v)]
        gov = nonfinite[0] if nonfinite else max(ratios, key=ratios.get)
        _k = "via" if kind == "via" else "wire"
        max_ratio[_k] = _worse(max_ratio[_k], float(ratios[gov]))
        max_ratio["all"] = _worse(max_ratio["all"], float(ratios[gov]))
        if kind == "via" and cuts and cuts > 1 and 0.9 <= ratios[gov] < 1.0:
            # marginal: the per-cut check is the array's mean, edge cuts carry more
            crowding.append({"name": r["name"], "layer": pdkname,
                             "ratio": float(ratios[gov]), "cuts": int(cuts)})
        if ratios[gov] < proc.warn_fraction:
            continue
        b = r.get("bbox") or (0, 0, 0, 0)
        viol.append({"name": r["name"], "layer": pdkname, "metric": gov,
                     "kind": "via" if kind == "via" else "wire",
                     "ratio": float(ratios[gov]), "ratios": ratios,
                     "value": vals[gov], "w": r.get("w"),
                     # the layer's width loss (um), when it has one: the limit
                     # applied to W - dW, and the fix widens by it
                     "dw": (proc.layers[pdkname]["width_loss"] or None)
                     if kind != "via" else None,
                     # the cut count the check used, and where it came from
                     "cuts": cuts if kind == "via" else r.get("cuts"),
                     "cuts_from": src,
                     # a NaN ratio is a failure, never a pass or a warning
                     "severity": "WARN" if ratios[gov] < 1.0 else "FAIL",
                     "bbox": list(b),
                     # where it is, from the DSPF's $LLX..$URY (or $X/$Y); None
                     # when the extraction wrote no coordinates
                     "bbox_um": [float(v) for v in r["bbox"]] if r.get("bbox") else None,
                     "peak": [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]})
    # worst first; a ratio that is not a number is a FAIL, ranked above any
    viol.sort(key=lambda c: (not np.isnan(c["ratio"]), -np.nan_to_num(c["ratio"])))
    if notes is not None:
        notes["via_cuts"] = how
        notes["no_width"] = no_width
        notes["bidirectional"] = sorted(bidirectional, key=lambda x: -x["abs_avg_ratio"])
        notes["via_crowding"] = sorted(crowding, key=lambda x: -x["ratio"])
    return viol, max_ratio


def _worse(a: float, b: float) -> float:
    """The worse of two ratios, a NaN being worse than any number (so it is
    never hidden behind a finite maximum: `max(x, nan)` is x)."""
    if np.isnan(a) or np.isnan(b):
        return float("nan")
    return max(a, b)


def net_scaling(dspf: Dspf, proc, lmap: dict) -> tuple:
    """`(r_at_temp, rscale)` -- who owns the temperature correction.

    An extractor that ran at the analysis temperature has already applied it;
    applying it again is a silent ~1.25x on every voltage. Decided from the
    DSPF's own stated temperature (`*|OPERATING_TEMPERATURE`) rather than a
    flag someone has to remember. Otherwise each wire's resistance goes from
    the temperature it was extracted at -- the DSPF's stated one, else the
    process file's `temp_reference` -- to the analysis temperature by its
    own layer's TCR (`rscale` maps each DSPF layer token to its factor); via
    and contact resistances are not scaled (the process file gives vias no
    TCR). It once applied one mean factor to every resistor of the net, vias
    included, from `temp_reference` whatever the DSPF said.
    """
    t_x = dspf.temperature if dspf.temperature is not None else proc.temp_ref
    r_at_temp = abs(t_x - proc.temp) < 0.5
    if r_at_temp:
        return True, 1.0
    return False, {tok: proc.r_factor(n, t_x, proc.temp)
                   for tok, (kind, n) in lmap.items() if kind == "layer"}


def scale_of(rscale, token) -> float:
    """The resistance factor of one DSPF layer token under `net_scaling`'s
    `rscale` (a number, or {token: factor}; a token not in it: 1.0)."""
    if isinstance(rscale, dict):
        return float(rscale.get(token, 1.0))
    return float(rscale)


def scale_text(r_at_temp, rscale, lmap=None) -> str:
    """What the temperature correction did, for a person."""
    if r_at_temp:
        return "as extracted (the DSPF states the analysis temperature)"
    if not isinstance(rscale, dict):
        return f"scaled x{float(rscale):.4f}"
    names = {}
    for tok, f in sorted(rscale.items()):
        name = (lmap or {}).get(tok, (None, tok))[1]
        names[name] = f
    if not names:
        return "no wire layer to scale; vias not scaled"
    return ("wires scaled by layer TCR: " + ", ".join(f"{n} x{f:.4f}" for n, f in
                                                      sorted(names.items()))
            + "; vias not scaled")


def r_text(r: dict, proc) -> tuple:
    """`(label, text)`: the resistances a net's IR is computed with, for a
    person. rcc's are those the simulation ran (`rcc.r_basis`); a DC
    solve's are scaled to the analysis temperature (`scale_text`)."""
    b = r.get("r_basis")
    if b:
        src = "" if b.get("stated") else ", the process file's temp_reference"
        return "R", (f"as simulated: the DSPF's, at {b['temp_C']:g} C{src}; the IR is "
                     f"the simulated circuit's")
    return (f"R at {proc.temp:g} C",
            scale_text(r["r_at_temp"], r["rscale"], r.get("layer_map")))


def check_net(dspf: Dspf, net: str, proc, inj: dict, ground: str,
              explicit_layers=None, log=print) -> dict:
    """Full EM/IR on one extracted net from one DC solve.

    Returns what every report consumes: per-metric ratios, a governing
    metric, clusters (here one per resistor) and the IR spread. `inj` is
    {node: amps into the net}: DC currents, so each is its own avg, rms and
    peak, and every metric is checked.
    """
    from .engine import METRICS
    lmap = map_layers(dspf, proc, explicit_layers, log=log)
    unmapped = [t for t in dspf.layer_tokens() if t not in lmap]
    r_at_temp, rscale = net_scaling(dspf, proc, lmap)
    nw = Network(dspf, net, rscale=rscale)
    V, Ib = nw.solve(inj, ground)
    Ie_m = {m: np.abs(Ib) for m in METRICS}
    note = "DC solve: avg = rms = peak = |I|"

    notes = {}
    viol, max_ratio = em_clusters(nw, Ie_m, proc, lmap, notes=notes)
    if unmapped:
        notice("EMIR-W307", f"DSPF layers with no PDK match (not checked): "
            f"{', '.join(unmapped)}", log)
    unsolved = None
    away = nw.unreached(ground)
    if away:
        # only a run forced past EMIR-E207 gets here (see `Network.solve`)
        k = len(set(nw.pieces().tolist()))
        unsolved = {"pieces": k, "n_nodes": len(away), "nodes": away[:20]}
        notice("EMIR-W208", f"net {net!r} is in {k} pieces: {len(away)} of "
               f"{len(nw.nodes)} nodes are not connected to the reference {ground!r} "
               f"(e.g. {', '.join(away[:3])}). Branch currents in a piece with current "
               f"but no path to the reference are not solved (not a number: FAIL); "
               f"the IR covers the reference's piece only", log)
    # the IR of the reference's piece (all of a whole net); NaN potentials
    # there (NaN currents) make it NaN, never a finite number
    Vm = V[nw.main_piece(ground)]
    return {"clusters": viol, "worst": viol[0] if viol else None,
            "max_ratio": max_ratio,
            "ir_drop": float(Vm.max() - Vm.min()),
            "V": V, "Ib": Ib, "Ie": Ie_m, "metrics": list(METRICS),
            "network": nw, "layer_map": lmap, "unmapped": unmapped,
            "r_at_temp": r_at_temp, "rscale": rscale, "metric_note": note,
            "kcl_residual": nw.kcl_residual(Ib, inj, ground),
            "via_cuts": notes["via_cuts"], "no_width": notes["no_width"],
            "bidirectional": notes["bidirectional"], "via_crowding": notes["via_crowding"],
            "unsolved": unsolved}

if __name__ == "__main__":
    main()
