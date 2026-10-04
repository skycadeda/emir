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

so the currents from a CC simulation of the same design attach to grid nodes by
instance-pin name, with no labels and no LVS markers.

Parsed, not simulated: this module builds the network and solves it. Nothing
here invokes an extractor.
"""
from __future__ import annotations

import os
import re

import numpy as np

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

    def net_caps(self, net: str) -> list:
        """Capacitors with at least one plate on `net`.

        A cap between `net` and anything else -- ground, a quiet net, a signal
        -- is decoupling from this net's point of view, so both are returned
        and the caller decides. `to_ground` says whether the far plate is a
        real AC ground (see `dynir.grounded_caps` for why that matters).
        """
        out = []
        for c in self.cap:
            a = self.node_net.get(c["n1"]) == net
            b = self.node_net.get(c["n2"]) == net
            if not (a or b):
                continue
            near, far = (c["n1"], c["n2"]) if a else (c["n2"], c["n1"])
            if a and b:
                continue                 # intra-net: no net charge exchange
            out.append({**c, "node": near, "far": far,
                        "to_ground": far in ("0", "gnd", "GND", "VSS!")})
        return out

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

    Returning None for those (as this did) drops them silently, and a decap
    *cell* extracts in exactly that form -- so the capacitance most relevant to
    dynamic IR was the capacitance being discarded. They are now kept with
    `c=None` and their geometry, for `dynir` to resolve or complain about.
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

    Kept because a **MOS decap** is a transistor, not a `C` line: gate on one
    rail, source/drain/bulk on the other. Nothing else in the flow would ever
    see it, and it is often the largest capacitance on a power net.
    """
    f = line.split()
    if len(f) < 6:
        return None
    kv = {k.lower(): float(v) for k, v in _KV.findall(line)}
    return {"name": f[0], "d": f[1], "g": f[2], "s": f[3], "b": f[4],
            "model": f[5], "w": kv.get("w"), "l": kv.get("l")}


def mos_decaps(dspf: Dspf, net: str) -> list:
    """MOSFETs on `net` wired as capacitors: gate one side, d/s/b the other.

    A transistor is a decap exactly when its gate sits on one net and all three
    of drain, source and bulk sit on a single *different* net -- it can carry no
    DC and presents `Cox * W * L` between them. A device with its gate on a
    signal is an ordinary transistor and is excluded, so this does not
    mis-count the circuit's own devices as decoupling.
    """
    out = []
    for m in dspf.dev:
        ng = dspf.node_net.get(m["g"])
        others = {dspf.node_net.get(m[t]) for t in ("d", "s", "b")}
        if len(others) != 1:
            continue
        no = others.pop()
        if ng is None or no is None or ng == no:
            continue
        if net not in (ng, no):
            continue
        out.append({**m, "gate_net": ng, "body_net": no,
                    "node": m["g"] if net == ng else m["d"]})
    return out


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
        # its own layer's TCR, vias and virtual resistors unscaled
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

        This is the whole reason the grid is kept separate from the device
        simulation: superposition needs one solve *per tap*, and self-
        consistency needs that again *per pass*. Refactorising each time would
        make both scale with the product instead of with the back-substitutions.

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
    from .output import ArgParser, add_dspf, add_format

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
                         "is driven: every net with resistors); with --self-consistent "
                         "and Xyce, ngspice or HSPICE several, comma-separated (--net "
                         "VDD,VSS), closed in one loop")
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
                    help="a simulation result: PSF ASCII or nutascii (a file, or a "
                         "Spectre -raw directory), HSPICE ASCII (.tr0, .option post=2) "
                         "or CSDF. Device currents come from here instead of "
                         "--currents.")
    ap.add_argument("--analysis", default=None,
                    help="which analysis to read from a --cc-sim directory; for a result "
                         "with several sweep tables, sweep:N picks one")
    ap.add_argument("--sim", default=None, choices=("spectre", "hspice", "xyce", "ngspice"),
                    help="the simulator that wrote --cc-sim, when neither the result's "
                         "header nor its names sidecar says (for the on-site "
                         "verification, `emir verify-sim`); with --netlist, the simulator "
                         "that runs it (default spectre)")
    ap.add_argument("--period", type=float, default=None,
                    help="fundamental period in s; the window is aligned to a "
                         "whole number of these (auto-detected if omitted)")
    ap.add_argument("--feed", default=None,
                    help="node the net is fed at (default: the DSPF port): "
                         "where the current that closes KCL enters. Also the "
                         "IR reference unless --ground is given. One net only.")
    ap.add_argument("--scalars", action="store_true",
                    help="use per-tap scalars instead of waveforms: rms/peak "
                         "become in-phase upper bounds rather than exact")
    ap.add_argument("--save-list", action="store_true",
                    help="print the Spectre `save` line for this net and exit")
    ap.add_argument("--netlist", default=None,
                    help="a netlist to SIMULATE instead of reading a finished --cc-sim "
                         "result: Spectre's CC netlist, or (--self-consistent with --sim "
                         "xyce, ngspice, hspice) the CC testbench. With --self-consistent "
                         "it is simulated again on every pass. Spectre (the default) and "
                         "HSPICE are licensed: they run only with --run")
    from .modes import mode_arg
    from .selfconsist import MAX_PASSES, TOL_MV, TOL_REL
    ap.add_argument("--mode", default=None, type=mode_arg, choices=("rcc", "cc-grid"),
                    help="how the simulated currents are used: required with --cc-sim "
                         "or --netlist, and the person's choice (emir does not pick one; "
                         "`emir next` lists the trade-offs). rcc: the RC netlist was "
                         "simulated and every branch current is read directly -- exact, "
                         "but the RC transient is slow and converges poorly on big "
                         "blocks. cc-grid: the devices were simulated on ideal rails and "
                         "their pin currents are superposed through the R grid -- fast, "
                         "scales, open loop (add --self-consistent to feed the droop "
                         "back). A DC run (--currents) needs no mode")
    g = ap.add_argument_group(
        "self-consistent cc-grid: re-simulate with each pin's supply at the testbench's "
        "rail plus its own droop waveform until the droop settles")
    g.add_argument("--self-consistent", action="store_true",
                   help="with --mode cc-grid: simulate --netlist (pass 0, the open loop), "
                        "solve the DSPF's grid over the whole transient (R-only, or RC when "
                        "the DSPF has capacitance on the net, with the decaps the process "
                        "file's cap_models size), feed each instance "
                        "pin its own droop waveform (a PWL source in series with the "
                        "testbench's rail) and simulate again, until the droop fed in and "
                        "the droop the currents produce agree within --tol-rel / --tol-mV "
                        "(after the first update each pin's device admittance, fitted from "
                        "the last two passes, steadies the update). Spectre: the netlist "
                        "holds the per-tap split (--split-stub); Xyce, ngspice, HSPICE: emir "
                        "writes the split testbench itself (--sim)")
    from .selfconsist import vdd_arg
    g.add_argument("--vdd", type=vdd_arg, default=None, metavar="V|NET=V,...",
                   help="the voltage of the net in the testbench, in V (0 for a ground "
                        "net), or with several nets one per net (VDD=1.2,VSS=0). With "
                        "--self-consistent and Xyce, ngspice or HSPICE it is checked against "
                        "the testbench's source (EMIR-E523), and read from it when not given "
                        "(a net left out of a per-net --vdd too); Spectre needs it given. On "
                        "an open-loop cc-grid run, the supply its droop is compared with "
                        "(above about 2 %% the run proposes the self-consistent route, "
                        "EMIR-W520)")
    g.add_argument("--droop-file", default=None,
                   help="the file rewritten between passes: for Xyce, ngspice and HSPICE "
                        "the include holding the split's sources, each pin's droop as an "
                        "inline PWL (default: emir_out/selfconsist/<net>/droop.inc, "
                        "droop.sp for HSPICE, with the split testbench and each pass's "
                        "result beside it); for Spectre the manifest beside each pin's "
                        ".pwl file (default droop.scs there). Here or at the default place "
                        "emir overwrites only a file it wrote: an existing file without "
                        "its header (the testbench, the DSPF, any file of yours) is "
                        "refused before anything runs, EMIR-E526")
    g.add_argument("--max-passes", type=int, default=MAX_PASSES, metavar="N",
                   help=f"the most simulations the loop runs, pass 0 (the open loop) "
                        f"included (default {MAX_PASSES})")
    g.add_argument("--tol-mV", dest="tol_mV", type=float, default=TOL_MV, metavar="MV",
                   help=f"the floor of the loop's tolerance: it has converged when, at "
                        f"every pin and over the whole transient, the droop the pass's "
                        f"currents produce is within max(--tol-rel x the largest droop, "
                        f"this) of the droop it simulated, and the governing branch's avg, "
                        f"rms and peak moved less than 0.5 points (default {TOL_MV:g} mV)")
    g.add_argument("--tol-rel", dest="tol_rel", type=float, default=TOL_REL,
                   metavar="FRACTION",
                   help=f"the loop's tolerance relative to the largest droop (default "
                        f"{TOL_REL:g}: 1 %%)")
    g.add_argument("--run", action="store_true",
                   help="spectre, hspice (licensed): run the simulator on --netlist; "
                        "without it the files are written and the command printed, and "
                        "nothing runs (ask the person first). Xyce and ngspice always run")
    g.add_argument("--sim-timeout", type=float, default=None, metavar="S",
                   help="stop a simulation of --netlist after this many seconds (default "
                        "3600). With --self-consistent it is pass 0's limit, and each later "
                        "pass may run max(20 x pass 0's time, 60 s, this): a pass past its "
                        "limit stops the loop (pass 0 reported, EMIR-W543)")
    ap.add_argument("--split-stub", action="store_true",
                    help="print the per-tap rail-split fragment to add to your Spectre CC "
                         "netlist for --self-consistent, and exit (Xyce, ngspice and "
                         "HSPICE need none: emir writes the split)")
    ap.add_argument("--rcc-nodes", type=int, default=2000,
                    help="no longer used (the mode is the person's choice, --mode). "
                         "Accepted so existing argument files still parse.")
    ap.add_argument("--save-branches", action="store_true",
                    help="print the Spectre `save` lines an RCC run needs. "
                         "With --net all (or no --net) that covers EVERY net, "
                         "which is what EM needs -- any wire carrying current "
                         "is subject to it, not just the rails.")
    ap.add_argument("--save-prefix", default="",
                    help="instance path to prepend to saved names when the "
                         'extracted subckt sits in a testbench, e.g. "XI0."; with '
                         "--self-consistent and Xyce, ngspice or HSPICE, the block's "
                         "instance path in the testbench (default: found in it)")
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
    ap.add_argument("--taps-out", default=None,
                    help="write the ingested taps (cc-grid) as CSV: each instance pin's "
                         "avg, rms and peak current where it enters the grid; one file per "
                         "net -- with several nets <stem>_<net><ext>, or put {net} in the "
                         "path")
    from .bigblock import add_options as _bigblock_options
    _bigblock_options(ap)
    # --- feature hierarchy ---
    from .hier_cli import add_run_options
    add_run_options(ap)
    # --- end feature hierarchy ---
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
        self.stub = None            # (mode, text) for --save-list/--save-branches/--split-stub
        # --- feature hierarchy --- (emir/hier.py: analyse_top)
        self.hierarchy = None       # the summary's `hierarchy` section
        self.extra_steps: list = []
        self.budget_failed = False
        self.waves = None            # the --cc-sim result, when it was read
        self.sim_verification = None  # emir verify-sim's record for its simulator
        # --- feature modes --- a licensed --netlist without --run: what would
        # run (the `emir.simulate/1` document's fields), and nothing ran
        self.planned = None


def run(a):
    """`emir run` with a parsed namespace. Returns the per-net results."""
    return analyse(a).solved


def analyse(a) -> RunResult:
    """`emir run` with a parsed namespace: check, analyse, report. Prints as
    it goes and returns a `RunResult`; refusals are `EmirError`s."""
    from .errors import collecting
    with collecting() as notices:
        # --- feature decap --- a basis that needs --dynamic without it is
        # refused before the input check (--force does not run past it)
        from .decap import after_analyse, refuse
        refuse(a)
        _chart_gds(a)               # a --gds that is not there, before the run
        res = _analyse(a)
        after_analyse(res)          # --- feature decap --- decaps without --dynamic
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
    if getattr(a, "self_consistent", False) and a.net and "," in a.net:
        # the loop's nets once each (`--net VDD,VDD` closes VDD): every path
        # below, single-net ones too, reads them from --net
        from .selfconsist import net_list
        a.net = ",".join(net_list(a))
    d = parse(a.dspf)
    res = RunResult(a, d)
    # Save lines and the rail-split fragment are meant to be redirected into a
    # netlist, so there the summary goes to stderr and stdout stays SPICE.
    machine = a.save_list or a.save_branches or a.split_stub
    print(d.summary(), file=sys.stderr if machine else sys.stdout)
    every = a.net in (None, "all")         # no --net means every net
    driven = bool(a.process and (a.currents or a.cc_sim or a.netlist
                                 or getattr(a, "abstract", None)))
    if every and not machine and not driven:
        # nothing to analyse: describe the file, and say that nothing ran
        for n in sorted(d.nets):
            print(f"  net {n:16s} {len(d.net_resistors(n)):5d} R  "
                  f"{len(d.net_taps(n)):4d} inst pins")
        _not_analysed(a)
    if a.save_list:
        from .ccsim import save_directives
        res.stub = ("cc-grid", save_directives(d, None if every else a.net,
                                          prefix=a.save_prefix))
        print(res.stub[1])
        return res
    if a.save_branches:
        from .rcc import save_branches
        res.stub = ("rcc", save_branches(d, None if every else a.net,
                                         prefix=a.save_prefix))
        print(res.stub[1])
        return res
    if a.split_stub:
        if every:
            raise EmirError("EMIR-E510", "--split-stub writes one net's rail split: "
                            "give --net", dspf=a.dspf)
        from .selfconsist import rail_split_fragment
        res.stub = ("split-stub", rail_split_fragment(
            d, a.net, a.vdd, os.path.abspath(a.droop_file) if a.droop_file else None))
        print(res.stub[1])
        return res
    if not driven:
        for t in d.net_taps(a.net):
            print(f"  tap {t['name']:24s} node {t['node']:20s} "
                  f"({t['xy'][0]:.4f}, {t['xy'][1]:.4f})")
        _not_analysed(a)
    # --- feature modes --- the mode of a simulated run is the person's choice
    _mode_given(a, d)

    if not a.no_check:
        from .check import from_args
        rep = res.report = from_args(a)
        print("\n-- input check --")
        print(rep.table(show_ok=False))
        if not rep.passed and not a.force:
            raise refusal(rep)
        print()

    from .engine import Process
    # a hand-entered file's unsourced values: the input check says it as a
    # finding; without one (--no-check) the run says it (EMIR-W320)
    proc = res.proc = Process.load(a.process, temp=a.em_temp, delta_t=a.delta_t,
                                   warn_provenance=res.report is None)
    # --- feature hierarchy ---
    if getattr(a, "abstract", None):
        # the blocks black-boxed: their abstracts drive the top (emir/hier.py)
        from .hier import analyse_top
        return analyse_top(d, proc, a, res)
    # --- feature hierarchy ---
    # The simulation runs once, whatever the net count (the self-consistent
    # loop runs its own passes, per net)
    if a.netlist and not a.self_consistent:
        if (a.sim or "spectre") != "spectre":
            raise EmirError("EMIR-E604", f"--netlist with --sim {a.sim}: without "
                            f"--self-consistent emir run drives Spectre only; simulate with "
                            f"`emir simulate {a.netlist} --sim {a.sim} --dspf {a.dspf} "
                            f"--mode {a.mode}` and pass the result with --cc-sim")
        if not a.run:
            res.planned = _plan_spectre(a, d)
            return res
        from .ccsim import run_spectre
        a.cc_sim = run_spectre(a.netlist, spectre=a.spectre,
                               timeout=getattr(a, "sim_timeout", None) or 3600.0)["raw"]

    # EM applies to every net that carries current, so `all` is the default
    # when no net is named -- and the net loop wraps *every* mode, because
    # putting it inside one branch is how cc-grid silently stayed rails-only.
    driven = a.cc_sim or a.currents
    from .selfconsist import net_list as sc_nets
    nets = ([n for n in sorted(d.nets) if d.net_resistors(n)]
            if a.net in (None, "all") else sc_nets(a))
    if a.self_consistent and (a.net in (None, "all") or (
            len(nets) > 1 and (a.sim or "spectre") == "spectre")):
        raise EmirError(
            "EMIR-E502",
            "--self-consistent feeds the solved droop back to the nets it is given: name "
            "them with --net (one net, or with Xyce, ngspice or HSPICE several, "
            "--net VDD,VSS; Spectre's split is the person's, one net at a time)",
            dspf=a.dspf)
    if not driven and not a.self_consistent:
        raise EmirError("EMIR-E407", "nothing to analyse: give --currents, --cc-sim "
                        "or --netlist", dspf=a.dspf, process=a.process)
    if a.self_consistent:
        from .selfconsist import LICENSED
        if (a.sim or "spectre") in LICENSED and not a.run:
            res.planned = _plan_self_consistent(a, d, nets[0])
            return res
    w = None
    if a.cc_sim and not a.self_consistent:
        # read the result once (the input check's read, when it ran); every
        # net's mode choice and currents use it
        from . import waves
        w = checked_waves(res, a) or waves.read(a.cc_sim, analysis=a.analysis)
        res.waves = w
    multi = len(nets) > 1
    solved = res.solved
    if a.self_consistent:
        # the loop runs once for every net it closes, before each net is reported
        a._sc_results = _self_consistent_loop(a, d, proc, nets)
    for net in nets:
        if multi:
            print(f"\n=== {net} ===")
        r = _analyse_net(d, net, proc, a, single=not multi, w=w)
        # --- feature decap --- the net's EM basis (and the decaps it does not
        # cover) before its page, the chart and the viewer data name them
        from .decap import after_net
        after_net(a, d, net, r)
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
    result once, so the reader's notices (EMIR-I465 ...) are said once."""
    rep = getattr(res, "report", None)
    if rep is None or getattr(rep, "waves", None) is None:
        return None
    return rep.waves if rep.waves_for == (a.cc_sim, getattr(a, "analysis", None)) else None


# --- feature modes ---------------------------------------------------------


def _mode_given(a, d=None):
    """Refuse a run whose currents come from a simulation without the
    person's mode (EMIR-E520), and the self-consistent options that cannot
    work together, before anything is checked or run. A DC run (--currents)
    needs no mode."""
    # a hierarchical run (--abstract) is cc-grid by construction: its blocks
    # come as abstracts, characterised with `emir abstract --mode cc-grid` (the
    # person's choice made there), and the top's own --cc-sim joins them as
    # pin currents; rcc is refused there (EMIR-E811)
    sim_driven = (a.cc_sim or a.netlist or a.self_consistent) and \
        not getattr(a, "abstract", None)
    if sim_driven and not a.mode:
        from .modes import not_chosen, plan_args
        raise not_chosen("run", dspf=a.dspf, process=a.process, cc_sim=a.cc_sim,
                         netlist=a.netlist, plan_args=plan_args(a),
                         net=a.net if a.net not in (None, "all") else None)
    if not a.self_consistent:
        return
    if a.mode == "rcc":
        raise EmirError("EMIR-E505", "--self-consistent is a cc-grid correction; RCC "
                        "already simulates the R network, so there is nothing to make "
                        "self-consistent")
    if a.cc_sim or a.currents:
        given = " and ".join(o for o, v in (("--cc-sim", a.cc_sim),
                                            ("--currents", a.currents)) if v)
        raise EmirError("EMIR-E525", f"--self-consistent simulates --netlist on every pass "
                        f"and reads only those results: {given} would be ignored; drop "
                        f"{given}, or drop --self-consistent to screen that result", dspf=a.dspf)
    if not a.netlist:
        raise EmirError("EMIR-E504", "--self-consistent re-simulates: give --netlist (the "
                        "CC netlist, or with --sim xyce, ngspice, hspice the CC testbench)")
    _rail_given(a, d)
    if a.max_passes < 1:
        raise EmirError("EMIR-E513", "--max-passes must be at least 1", command="run")
    if not (np.isfinite(a.tol_mV) and a.tol_mV > 0):
        raise EmirError("EMIR-E513", "--tol-mV must be a positive number of mV",
                        command="run")
    if not (np.isfinite(a.tol_rel) and 0 <= a.tol_rel < 1):
        raise EmirError("EMIR-E513", "--tol-rel must be a fraction of the droop, 0 to below 1 "
                        "(0.01: 1 %)", command="run")


def _rail_given(a, d):
    """--vdd of a self-consistent run: Spectre needs it given (its netlist is
    not read); Xyce, ngspice and HSPICE check it against the testbench's
    source (EMIR-E523 when they differ), or take the testbench's value when
    it is not given (EMIR-I521), or refuse when neither says (EMIR-E503).
    The loop does not depend on it (each pin's source is in series with the
    testbench's own rail): it is the rail the summary and the report quote."""
    from .errors import notice
    from .selfconsist import SPICE_SIMS, net_list, rail_check, vdd_for
    sim = a.sim or "spectre"
    net = a.net if a.net not in (None, "all") else None
    nets = net_list(a) if net else []
    if net is not None:
        per, problem = vdd_for(a.vdd, nets)
        if problem:
            raise EmirError("EMIR-E541", problem, dspf=a.dspf)
        if len(nets) == 1:
            a.vdd = per[nets[0]]
    if len(nets) > 1:
        # a per-net --vdd stays as given (the input check reads it); the loop
        # takes `vdd_by_net`
        if sim not in SPICE_SIMS or d is None or not os.path.isfile(a.netlist):
            return
        a.vdd_by_net, a.rails = {}, {}
        for n in nets:
            rc = rail_check(a.netlist, d, n, per[n], prefix=a.save_prefix or None)
            if rc["code"] == "EMIR-E523":
                raise EmirError("EMIR-E523", rc["message"], path=a.netlist, dspf=a.dspf, net=n)
            if rc["code"] == "EMIR-E503":
                raise EmirError("EMIR-E503", rc["message"] + f"; with several nets give it as "
                                f"--vdd {n}=<volts> (a net left out is read from the "
                                f"testbench)", dspf=a.dspf, net=n)
            a.vdd_by_net[n], a.rails[n] = rc["vdd"], rc["rail"]
            notice("EMIR-I521", f"{n}: {rc['message']}")
        return
    if sim not in SPICE_SIMS or net is None or d is None or not os.path.isfile(a.netlist):
        if a.vdd is None:
            raise EmirError("EMIR-E503", "--self-consistent needs --vdd, the voltage of the "
                            "net in the testbench (0 for a ground net)"
                            + (": emir does not read a Spectre netlist" if sim == "spectre"
                               else ""), dspf=a.dspf)
        return
    rc = rail_check(a.netlist, d, net, a.vdd, prefix=a.save_prefix or None)
    a.rail = rc["rail"]
    if rc["code"] == "EMIR-E523":
        raise EmirError("EMIR-E523", rc["message"], path=a.netlist, dspf=a.dspf, net=net)
    if rc["code"] == "EMIR-E503":
        raise EmirError("EMIR-E503", rc["message"], dspf=a.dspf)
    if a.vdd is None:
        a.vdd = rc["vdd"]
        notice("EMIR-I521", rc["message"])


def _sc_driver(a, d, net):
    """The self-consistent loop's simulator driver for `net` (one net, or a
    list: several nets in one loop, Xyce, ngspice, HSPICE)."""
    from .selfconsist import (SpectreDriver, SpiceDriver, _spectre_texts, default_dir,
                              reltol_notice, spectre_split_problems)
    sim = a.sim or "spectre"
    timeout = getattr(a, "sim_timeout", None) or 3600.0
    if sim == "spectre":
        net = net if isinstance(net, str) else net[0]
        out = (os.path.dirname(os.path.abspath(a.droop_file)) if a.droop_file
               else default_dir(net))
        droop = os.path.abspath(a.droop_file) if a.droop_file else \
            os.path.join(out, "droop.scs")
        pins = sorted({f"{t['inst']}:{t['pin']}" for t in d.net_taps(net)})
        bad = spectre_split_problems(a.netlist, droop, pins)
        if bad:
            raise EmirError("EMIR-E524", f"{a.netlist} does not hold the per-tap rail split "
                            f"of net {net}: " + "; ".join(bad) + ". Spectre would simulate "
                            "the open loop on every pass", path=a.netlist, net=net,
                            dspf=a.dspf)
        reltol_notice(_spectre_texts(a.netlist), "spectre", f"the netlist {a.netlist}")
        return SpectreDriver(a.netlist, droop, pins, spectre=a.spectre, timeout=timeout,
                             out_dir=out, guard=True, net=net)
    out = (os.path.dirname(os.path.abspath(a.droop_file)) if a.droop_file else
           default_dir(net))
    # `sc_types` is not an option: `emir verify-sim` sets it for its resistive
    # loads (the split probes devices, MXQJDN, otherwise)
    return SpiceDriver(sim, a.netlist, d, net, a.vdd, out, prefix=a.save_prefix or None,
                       timeout=timeout, droop_path=a.droop_file,
                       types=getattr(a, "sc_types", None))


def _self_consistent_loop(a, d, proc, nets) -> dict:
    """The waveform loop on the run's nets (`emir.selfconsist`,
    `emir.tdloop`): `{net: per-net result}`, each with its summary entry
    (`self_consistency`). The droop fed back is relative to each net's feed,
    where the rail is nominal (the reference on a portless net)."""
    from .rcc import net_port, reference_node
    from .selfconsist import SPICE_SIMS, converge, converge_nets, supply_span
    single = len(nets) == 1
    feeds = {}
    for net in nets:
        gnd = reference_node(d, net, (a.ground or a.feed) if single else None)
        feed = (reference_node(d, net, a.feed) if (single and a.feed) else net_port(d, net))
        feeds[net] = feed or gnd
    drv = _sc_driver(a, d, nets if not single else nets[0])
    kw = dict(period=a.period, max_passes=a.max_passes, tol_mV=a.tol_mV,
              tol_rel=a.tol_rel, analysis=a.analysis, waveforms=not a.scalars,
              max_bytes=getattr(a, "max_memory", None),
              superposition=getattr(a, "superposition", "auto"),
              sim_timeout=getattr(a, "sim_timeout", None))
    # a ground net alone (or nets whose rails all read 0 V): the guard is half
    # the testbench's largest supply, not half of 0 V
    vs = ([a.vdd] if single else list((getattr(a, "vdd_by_net", None) or {}).values()))
    if not any(v for v in vs if v is not None) and (a.sim or "spectre") in SPICE_SIMS:
        span = supply_span(a.netlist)
        if span:
            kw["guard_V"] = span / 2
    if single:
        out = {nets[0]: converge(a.netlist, drv.droop, d, nets[0], proc, feeds[nets[0]],
                                 a.vdd, driver=drv, **kw)}
        out[nets[0]]["rail"] = getattr(a, "rail", None)
    else:
        vdds = getattr(a, "vdd_by_net", None) or {n: None for n in nets}
        out = converge_nets(drv, d, nets, proc, feeds, vdds, netlist=a.netlist, **kw)
        for n in nets:
            out[n]["rail"] = (getattr(a, "rails", None) or {}).get(n)
    return out


def _plan_doc(a, sim, netlist, deck, files, command, cwd, result, nets, names=None,
              loop=None) -> dict:
    """The fields of the `emir.simulate/1` document a licensed --netlist run
    without --run prints: what would run, and the next step (the same command
    with --run, after the person's go-ahead)."""
    from .errors import notice
    from .output import command_line, strip_options
    notice("EMIR-I661", f"{sim} needs a licence: nothing was run. Ask the person; then "
           f"re-run with --run (or run `{' '.join(command)}` in {cwd})")
    argv = strip_options(list(getattr(a, "argv", None) or []), {"--format": 1})
    fmt = ["--format", "json"] if getattr(a, "format", "text") == "json" else []
    steps = ([{"why": f"ask the person first ({sim} is licensed"
                      + (", and the loop runs it once per pass" if loop else "")
                      + "); then run it: the same command with --run",
               "command": command_line("run", argv, "--run", fmt),
               "needs_human": True, "licensed_tool": sim,
               **({"long_run": True} if loop else {})}] if argv else [])
    print(f"[emir] {sim} (licensed): nothing was run"
          + (" -- the self-consistent loop is ready" if loop else ""))
    print(f"  deck    : {deck}\n  command : {' '.join(command)}\n  in      : {cwd}")
    return dict(simulator=sim, ran=False, executable=None, simulator_version=None,
                command=list(command), cwd=cwd, testbench=os.path.abspath(netlist),
                deck=deck, files=files, result=result, names_sidecar=names, log=None,
                mode="cc-grid" if a.mode == "cc-grid" else a.mode, mode_reason=f"--mode {a.mode}",
                nets=list(nets), scope=None, path=[], where="--netlist", signals=0,
                direct=0, probed=0, found=None, missing=[], seconds=None, points=None,
                analysis=None, notes=[], self_consistent=loop, next_steps=steps)


def _plan_spectre(a, d) -> dict:
    """`emir run --netlist` on Spectre without --run: the command, nothing run."""
    from . import site
    exe, _src = site.tool("spectre", a.spectre)
    netlist = os.path.abspath(a.netlist)
    out = os.path.splitext(netlist)[0] + ".raw"      # what ccsim.run_spectre writes
    command = [exe or "spectre", netlist, "-format", "psfascii", "-raw", out,
               "+escchars", "=log", out + ".log"]
    nets = ([n for n in sorted(d.nets) if d.net_resistors(n)]
            if a.net in (None, "all") else [a.net])
    return _plan_doc(a, "spectre", a.netlist, netlist, [], command,
                     os.path.dirname(netlist), out, nets)


def _plan_self_consistent(a, d, net) -> dict:
    """The self-consistent loop on Spectre or HSPICE without --run: the split
    (HSPICE) and the droop file(s) at 0 V (pass 0) are written,
    the command printed; nothing runs."""
    from . import site
    from .selfconsist import net_list
    nets = net_list(a)
    drv = _sc_driver(a, d, nets if len(nets) > 1 else net)
    if drv.sim == "spectre":
        drv.write(0, None)
    exe, _src = site.tool(drv.sim, a.spectre if drv.sim == "spectre" else None)
    deck = getattr(drv, "deck", os.path.abspath(a.netlist))
    loop = {"net": net, "vdd": a.vdd if not isinstance(a.vdd, dict) else None,
            "max_passes": a.max_passes, "tol_mV": a.tol_mV,
            "tol_rel": a.tol_rel, "droop_file": drv.droop, "pins": len(drv.names),
            "nets": nets}
    return _plan_doc(a, drv.sim, a.netlist, deck, drv.files_written(), drv.command(exe),
                     os.path.dirname(drv.result_path()), drv.result_path(), nets,
                     loop=loop)


def _droop_check(r, a, net):
    """An open-loop cc-grid run with --vdd: a droop above about 2 % of the
    supply is a coded warning (EMIR-W520), and the summary's next steps
    propose the self-consistent route (`summary.next_steps`)."""
    from .plan_bigblock import DROOP_FRACTION
    from .selfconsist import vdd_of
    v = vdd_of(a.vdd, net)
    ir = r.get("ir_drop")
    if v is None or not v or ir is None or not np.isfinite(ir):
        return
    frac = abs(ir) / abs(v)
    r["droop_fraction"] = frac
    if frac > DROOP_FRACTION:
        from .errors import notice
        notice("EMIR-W520", f"net {net!r}: the IR drop {ir * 1e3:.4g} mV is {frac:.1%} of "
               f"the {v:g} V supply (above about {DROOP_FRACTION:.0%}) on an open-loop "
               f"cc-grid run: its currents and droop read high; ask the person about the "
               f"self-consistent run (it re-simulates)")


def refusal(rep) -> EmirError:
    """The error a run refused by its input check raises: EMIR-E501, with every
    FAIL's code in `details.causes` and the findings in `details.findings`
    -- unless a required tool is missing, which keeps its own code (and so
    the contract's exit status 3, not 2)."""
    from .errors import EXIT_TOOL, lookup
    causes = rep.codes("FAIL")
    details = {"causes": causes,
               "findings": [f.as_dict(**rep.args) for f in rep.sorted()
                            if f.level in ("FAIL", "WARN")]}
    tool = [c for c in causes if lookup(c).exit_code == EXIT_TOOL]
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
                                                            "pin kinds not covered") else "")
              + (f" -- {v['command']}" if v.get("command") else ""))
    sus = []
    if proc is not None and isinstance(getattr(proc, "data", None), dict):
        from .process_file import suspects
        sus = [x for x in suspects(proc.data) if not x["confirmed"]]
    if sus:
        print(f"  limits SUSPECT : {', '.join(x['path'] for x in sus[:4])} outside the "
              f"usual range (a unit slip? EMIR-W321)")
    print(f"\n{RESEARCH_LINE}")
    print(f"overall verdict: {overall} -- {len(solved)} net(s): {k['FAIL']} FAIL, "
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


def split_pin_message(net: str, split: dict) -> str:
    """Why a CC run refuses instance pins with several DSPF connections.

    `split` is `Dspf.split_pins(net)`. Shared by the checker and the tap
    ingest so the run and `emir check` say the same thing.
    """
    items = sorted(split.items())
    shown = "; ".join(f"{k} at {', '.join(sorted(t['node'] for t in v))}"
                      for k, v in items[:4])
    more = f" (+{len(items) - 4} more)" if len(items) > 4 else ""
    return (f"net {net!r}: {len(items)} instance pin(s) reach the net at "
            f"several DSPF connections, but the simulation saves ONE terminal "
            f"current per pin: {shown}{more}. How that current divides between "
            f"the connections depends on the block's own grid, so it cannot be "
            f"guessed (all of it on one connection under-reads the others). "
            f"A black-boxed block: screen the net hierarchically -- characterise "
            f"the block once (`emir abstract <block.dspf> --process <P> --cc-sim "
            f"<block.raw> --mode cc-grid -o <block.abstract.json>`, every pin shape a "
            f"*|P port), "
            f"then `emir run <top.dspf> --net {net} --abstract "
            f"<block.abstract.json>@<INST> --place <INST>=<dx>,<dy>,<orient>`. A "
            f"small net: screen it in rcc mode, saving every branch current "
            f"(`emir save-lines <dspf> --net {net} --mode rcc`), which measures how "
            f"the current divides. `emir next` plans either route.")


def result_holds(dspf: Dspf, net: str, w) -> dict:
    """What a simulation result holds for one net, and so which modes can
    screen it: `rcc` needs every branch current in the time domain; `cc-grid`
    needs instance-pin currents, and no pin that reaches the net at several
    DSPF connections (EMIR-E417). The mode is the person's choice (`--mode`);
    `emir next` uses this to say which modes the result can serve, and to
    plan only what the check accepts."""
    names = [r["name"] for r in dspf.net_resistors(net)]
    br = w.match([f"{n}:1" for n in names]) if names else {}
    pins = sorted(dspf.pin_groups(net))
    got = w.match(pins) if pins else {}
    split = dspf.split_pins(net)
    return {"branches": [len(br), len(names)], "pins": [len(got), len(pins)],
            "split_pins": len(split), "time_domain": not w.is_complex,
            "can": {"rcc": bool(names) and len(br) == len(names) and not w.is_complex,
                    "cc-grid": bool(got) and not split}}


def _analyse_net(d, net, proc, a, single=True, w=None):
    """One net, in whichever mode applies. Prints as it goes, returns `res`.

    `w` is the `--cc-sim` result already read by `run` (read here if None).
    """

    from .rcc import net_port, reference_node
    # the mode is the person's (`_mode_given` refused a simulated run without
    # one); DC currents are solved directly and need none
    mode = a.mode if (a.cc_sim or a.netlist or a.self_consistent) else "dc"
    why = f"--mode {mode}" if mode != "dc" else "DC --currents: the R grid is solved directly"
    result = w if w is not None else a.cc_sim
    # the reference is always a node on *this* net -- taking the first port in
    # the DSPF would ground a signal net at some other net's pin
    gnd = reference_node(d, net, (a.ground or a.feed) if single else None)
    # `--feed` moves the feed tap as well as the reference: the current that
    # closes KCL enters where the user says the net is fed. (It used to stay at
    # the DSPF port, so rms/peak on the port-to-feed path collapsed to DC.)
    feed = (reference_node(d, net, a.feed) if (single and a.feed)
            else net_port(d, net))

    if mode == "rcc" and a.cc_sim:
        from .rcc import rcc_check
        if a.self_consistent:
            raise EmirError("EMIR-E505",
                            "--self-consistent is a CC-mode correction; RCC "
                            "already simulates the R network, so there is "
                            "nothing to make self-consistent")
        r = rcc_check(d, net, proc, result, gnd, analysis=a.analysis,
                      period=a.period)
        r["mode_reason"] = why
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

    taps = weights = cv = None
    if a.self_consistent:
        from .selfconsist import summary
        # the loop ran for every net it closes (`_self_consistent_loop`)
        cv = a._sc_results[net]
        r, taps, weights = cv["result"], cv["taps"], cv["ingest"]["weights"]
        r["mode"], r["self_consistent"] = "cc-grid", True
        r["self_consistency"] = summary(cv)
        r["unsaved_pins"] = list(cv["ingest"].get("missing") or [])
        # the reported pass's result: the run's simulator verification and
        # its current source name it
        a.cc_sim = cv["raw"]
        # decaps the testbench simulated: their pins carry their current
        r["_loop_simulated_decaps"] = list(cv.get("simulated_decaps") or [])
        if getattr(a, "dynamic", False):
            from .bigblock import cc_dynamic
            # the reported pass's currents through the RC grid; its source
            # names that pass (`bigblock.dynamic_source`)
            r["dynamic"] = cc_dynamic(d, net, r, feed or gnd, taps,
                                      cv["ingest"].get("time"), proc, a)
        # how its branches were scored: on the R-only grid, or through the RC
        # grid (--dynamic's decap-aware EM); EMIR-W545 when the loop fed back
        # the RC grid and scored on the R-only one
        from .selfconsist import scoring, scoring_notice
        r["self_consistency"]["scoring"] = scoring(r, taps)
        scoring_notice(net, r["self_consistency"])
    elif a.cc_sim:
        from .ccsim import cc_taps
        # A net with a port is fed through it and the feed tap closes KCL; a
        # net without one is closed by its own device pins and needs no feed.
        ing = cc_taps(d, net, result, analysis=a.analysis, period=a.period,
                      feed=feed, waveforms=not a.scalars)
        taps, weights = ing["taps"], ing["weights"]
        inj = {}
        for t in taps:
            inj[t["node"]] = inj.get(t["node"], 0.0) + t["i_avg"]
        r = check_net(d, net, proc, inj, gnd, taps_list=taps,
                      tap_weights=weights, max_bytes=getattr(a, "max_memory", None),
                      superposition=getattr(a, "superposition", "auto"))
        r["mode"] = "cc-grid"
        # pins the result did not save: their current was taken as zero
        r["unsaved_pins"] = list(ing.get("missing") or [])
        if getattr(a, "dynamic", False):
            from .bigblock import cc_dynamic
            r["dynamic"] = cc_dynamic(d, net, r, gnd, taps, ing.get("time"), proc, a)
        _droop_check(r, a, net)
    else:
        from .jsonarg import load
        obj, _path = load(a.currents, "--currents", "EMIR-E401", schema="currents",
                          dspf=a.dspf, net=net)
        try:
            inj = {str(k): float(v) for k, v in obj.items()}
        except (ValueError, TypeError, AttributeError) as e:
            raise EmirError("EMIR-E401", f"--currents is not a JSON object of node: "
                            f"amps ({e})", dspf=a.dspf, net=net) from e
        r = check_net(d, net, proc, inj, a.ground or gnd)
        # one label for this route everywhere (summary, HTML, viewer); the
        # summary said "dc" while the HTML said "cc-grid"
        r["mode"] = "dc"

    if cv is not None:
        from .selfconsist import state_text
        state = state_text(cv)
        print(f"  self-consistent: {state} in {len(cv['passes'])} simulation(s) "
              f"({cv['simulator'] or 'simulator'}, droop waveforms; tolerance "
              f"max({cv['tol_rel']:g} x the droop, {cv['tol_mV']:g} mV))")
        for p in cv["passes"]:
            mv = ("open loop" if p["current_change"] is None
                  else f"{p['current_change']:.3%}")
            print(f"    pass {p['pass']}: residual {p['residual_mV']:9.4f} mV "
                  f"(tolerance {p['tolerance_mV']:.4g}), currents moved {mv:>9}, "
                  f"worst {_ratio4(p['worst_ratio'])}, IR {p['ir_drop_mV']:.4f} mV, "
                  f"fed by {p['fed_by']}")
    r["mode_reason"] = why
    if a.taps_out and taps:
        path = taps_path(a.taps_out, net, single)
        _write_taps(path, taps, tap_layers(d, proc, r["layer_map"]), net=net)
        print(f"  taps written   : {path}")
    _print_net(r, proc, a, d, net=net)
    if r.get("dynamic"):
        from .bigblock import print_dynamic
        print_dynamic(r["dynamic"], r["ir_drop"], r.get("self_consistency"))
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
                              command=getattr(a, "command", None) or "", what=what,
                              not_checked_extra=getattr(a, "not_checked_extra", None))
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
    label = f"R at {proc.temp:g} C"
    print(f"  {label:<15}: {scale_text(r['r_at_temp'], r['rscale'], r.get('layer_map'))}")
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


def tap_layers(dspf: Dspf, proc, lmap: dict) -> dict:
    """node -> PDK **conductor** layer, from whatever the tap connects to.

    `*|I` records often carry no `$lvl`, and a tap file (`--taps-out`) needs a
    layer to place the injection on. The structure supplies it: a device pin reaches the
    net through a contact, and a contact's `above` is the metal it lands on. So
    take the incident resistor's layer, and if that resolves to a via, follow it
    up to the conductor.
    """
    out = {}
    for r in dspf.res:
        hit = lmap.get(r["layer"])
        if not hit:
            continue
        kind, name = hit
        lay = proc.vias[name].get("above") if kind == "via" else name
        for n in (r["n1"], r["n2"]):
            out.setdefault(n, lay)
    return out


def taps_path(path: str, net: str, single: bool = True) -> str:
    """Where `--taps-out` writes one net's taps: `{net}` in the path is
    replaced by the net's name; otherwise a run over several nets writes
    `<stem>_<net><ext>` per net (one file held only the last net's taps
    before)."""
    import re
    safe = re.sub(r"[^A-Za-z0-9_.+-]", "_", net) or "net"
    if "{net}" in path:
        return path.replace("{net}", safe)
    if single:
        return path
    stem, ext = os.path.splitext(path)
    return f"{stem}_{safe}{ext or '.csv'}"


def _write_taps(path, taps, layers=None, net=None):
    """Ingested taps as a tap file (`taps.load_taps` reads it): name, x_um,
    y_um, layer (the PDK conductor the
    tap lands on, from the incident resistor; else the record's own `$lvl`),
    i_avg, i_rms, i_peak (A, 12 significant digits, so the sum still balances
    to KCL's tolerance), net. Waveforms do not survive the round trip -- the
    scalars do, and `superpose` treats rms/peak as in-phase upper bounds."""
    import csv
    import io

    from .output import write_text
    layers = layers or {}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["name", "x_um", "y_um", "layer", "i_avg", "i_rms", "i_peak", "net"])
    for t in taps:
        lay = layers.get(t["node"]) or t.get("layer") or ""
        w.writerow([t["name"], f"{t['xy'][0]:.4f}", f"{t['xy'][1]:.4f}", lay,
                    f"{t['i_avg']:.12g}", f"{t['i_rms']:.12g}", f"{t['i_peak']:.12g}",
                    net or ""])
    write_text(path, buf.getvalue())


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
    grid solve, superposition, and reading them straight off an RCC sim. Where
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
    # it (waveforms): a reversing current cancels in the signed average
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


def check_net(dspf: Dspf, net: str, proc, inj: dict, ground: str,
              taps_list=None, tap_weights=None, explicit_layers=None,
              network=None, log=print, max_bytes=None, superposition="auto") -> dict:
    """Full EM/IR on one extracted net, per metric.

    Returns what every report consumes: per-metric ratios, a governing metric, clusters (here one per resistor)
    and the IR spread. `inj` is {node: amps} for the average solve; pass
    `taps_list` (tap dicts carrying a `node`) for per-metric superposition,
    and `tap_weights` alongside waveform taps so the time reduction is a real
    time average rather than a mean over the simulator's uneven samples
    (`ccsim.cc_taps` returns both).

    Pass `network` to reuse a `Network` (and its LU factorisation) across
    calls -- what the self-consistency loop does, where the grid is fixed and
    only the injection changes pass to pass.

    `max_bytes` is the waveform superposition's memory budget (`--max-memory`)
    and `superposition` its path (`--superposition`): above the budget the
    dense transfer matrix is not built and the superposition streams in time
    chunks (`bigblock.superpose`; same answer).
    """
    from .engine import METRICS
    lmap = map_layers(dspf, proc, explicit_layers, log=log)
    unmapped = [t for t in dspf.layer_tokens() if t not in lmap]
    r_at_temp, rscale = net_scaling(dspf, proc, lmap)
    nw = network if network is not None else Network(dspf, net, rscale=rscale)
    if network is not None:
        rscale = nw.rscale

    def _branch(t, amps):
        _, Ib = nw.solve({t["node"]: amps,
                          ground: -amps if t["node"] != ground else 0.0}, ground)
        return Ib

    V, Ib = nw.solve(inj, ground)
    sup = None
    if taps_list:
        # All three metrics, always: the PDK's limits decide what gets a ratio,
        # not what gets computed. Back-filling an unlimited metric from the DC
        # solve would report the average current as if it were the rms.
        from .taps import has_waveforms, superpose
        if has_waveforms(taps_list):
            from .bigblock import superpose as superpose_exact
            Ie_m, note, sup = superpose_exact(nw, ground, _branch, taps_list, METRICS,
                                              weights=tap_weights, max_bytes=max_bytes,
                                              method=superposition, log=log)
        else:
            Ie_m, note = superpose(_branch, taps_list, len(nw.res), METRICS,
                                   log=log)
            # say which path ran, as the exact paths do: an agent reads the
            # rms and peak of this one as upper bounds
            sup = {"method": "scalar",
                   "reason": "per-tap scalars (no waveforms): avg exact, rms and peak the "
                             "in-phase upper bound over every alignment of the taps",
                   "taps": len(taps_list), "points": None, "branches": len(nw.res),
                   "nodes": len(nw.nodes), "dense_bytes": None, "budget_bytes": None,
                   "chunk": None, "chunks": None}
    else:
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
            "V": V, "Ib": Ib, "Ie": Ie_m,
            "network": nw, "layer_map": lmap, "unmapped": unmapped,
            "r_at_temp": r_at_temp, "rscale": rscale, "metric_note": note,
            "kcl_residual": nw.kcl_residual(Ib, inj, ground),
            "via_cuts": notes["via_cuts"], "no_width": notes["no_width"],
            "bidirectional": notes["bidirectional"], "via_crowding": notes["via_crowding"],
            "unsolved": unsolved, "superposition": sup}

if __name__ == "__main__":
    main()
