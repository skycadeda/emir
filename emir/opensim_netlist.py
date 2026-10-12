"""SPICE netlist trees for the SPICE simulators (HSPICE, Xyce, ngspice).

`emir simulate` reads a testbench and every file it `.include`s, finds the
subcircuits, their instances and the elements, and writes a rewritten copy of
the tree:

* with the print / save lines a run needs, before the top file's `.end`
  (before its first `.alter`, when it has `.alter` cases: the main case and
  every altered one then save them);
* with relative `.include` / `.lib` paths rebased onto the copy's directory,
  quoted as they were (HSPICE documents `.INCLUDE 'file'`);
* without the DSPF `$KEY=value` annotations, which Spectre, HSPICE and
  ngspice read as comments and Xyce rejects as unknown parameters.

What is understood (enough to rewrite, not to simulate): element lines and
their terminal nodes, `+` continuations, `*` comments, inline comments (`;`,
`//`, a `$` token), `.subckt` / `.ends` (nested), `.include` / `.inc`
(followed), `.lib FILE SECTION` (path rebased, not followed), `.control` /
`.endc` blocks (skipped), `.tran` and `.end`. The first line of the top file is
its title, as every SPICE reads it. Names compare without regard to case, as
SPICE does; what is written keeps the spelling it was read with.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .errors import EmirError

#: terminal count of the element letters whose node list is fixed; X and N
#: (subcircuit and OSDI instances) list their nodes up to the subcircuit /
#: model name, Q takes 3 or 4 (see `_nodes`)
NODES = {"R": 2, "C": 2, "L": 2, "D": 2, "V": 2, "I": 2, "M": 4, "J": 3, "Z": 3,
         "E": 4, "G": 4, "F": 2, "H": 2, "B": 2, "S": 4, "W": 2, "T": 4, "Q": 3}

def tokens(text: str) -> list:
    """The words of a statement: inline comments cut (`;`, `//`, a word that
    starts with `$` -- an HSPICE comment or a DSPF annotation), `a = b`
    joined to `a=b`, and `{...}` / quoted strings kept whole."""
    out, cur, depth, quote = [], [], 0, None
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            cur.append(c)
            if c == quote:
                quote = None
        elif c in "'\"" and depth == 0:
            quote = c
            cur.append(c)
        elif c == "{":
            depth += 1
            cur.append(c)
        elif c == "}":
            depth = max(0, depth - 1)
            cur.append(c)
        elif depth == 0 and (c == ";" or text.startswith("//", i)):
            break
        elif depth == 0 and c == "$" and not cur:
            break
        elif depth == 0 and c.isspace():
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(c)
        i += 1
    if cur:
        out.append("".join(cur))
    # `W = 1u` and `W =1u` -> `W=1u`
    joined: list = []
    for t in out:
        if joined and (t.startswith("=") or joined[-1].endswith("=")):
            joined[-1] += t
        else:
            joined.append(t)
    return joined


def has_annotation(text: str) -> bool:
    """A word of the statement starts with `$` (a DSPF annotation)."""
    return bool(re.search(r"(^|\s)\$", text))


def _unquote(s: str) -> str:
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"" else s


def _quote_of(s: str) -> str | None:
    """The quote character a word is written in, or None."""
    return s[0] if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"" else None


def _is_param(w: str) -> bool:
    return "=" in w


def _is_number(w: str) -> bool:
    return re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?[a-zA-Z]*", w) is not None


# ------------------------------------------------------------------ the model


@dataclass
class Chunk:
    """One statement (its physical lines joined) or one comment / blank line."""
    kind: str                 # title comment blank element dot control
    lines: list               # the physical lines, as read
    lineno: int               # the first physical line's number (1-based)
    words: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(ln.lstrip("+").strip() if i else ln.strip()
                        for i, ln in enumerate(self.lines))


@dataclass
class Element:
    """A device or source line."""
    name: str
    kind: str                 # its letter, upper case
    nodes: list               # terminal nodes, as written
    ref: str | None           # the subcircuit (X) or model name
    scope: str | None         # enclosing `.subckt` (upper case), None at top level
    file: str
    chunk: int                # index of its chunk in the file
    words: list


@dataclass
class Subckt:
    name: str                 # as written
    ports: list
    file: str
    elements: list = field(default_factory=list)


@dataclass
class Include:
    chunk: int
    target: str               # absolute path
    kind: str                 # include | lib
    section: str | None = None
    keyword: str = ".include"
    absolute: bool = False    # written as an absolute (or ~) path: stays so in copies
    quote: str | None = None  # the quote the path was written in (' or "), kept


class SpiceFile:
    """One file of the tree, as chunks."""

    def __init__(self, path: str, top: bool):
        self.path = os.path.abspath(path)
        self.top = top
        self.chunks: list = []
        self.includes: list = []
        self.end: int | None = None           # chunk index of the top `.end`
        self.alter: int | None = None         # chunk index of the first top `.alter`
        self.annotated = False                 # an element line carries `$...`


class Tree:
    """A testbench and the files it includes.

    `title=False` reads a netlist that is not a deck (an extractor's
    `.subckt` file): its first line is a statement like any other, not the
    title every SPICE skips in a deck."""

    def __init__(self, top: str, title: bool = True):
        self.title = title
        self.files: dict = {}
        self.order: list = []
        self.subckts: dict = {}                # NAME -> Subckt
        self.top_elements: list = []
        self.analyses: list = []               # `.tran ...` statements
        self.has_control = False
        self.top = self._read(top, top=True, stack=())

    # ---------------------------------------------------------- reading

    def _read(self, path: str, top: bool, stack: tuple) -> SpiceFile:
        path = os.path.abspath(path)
        if path in self.files:
            return self.files[path]
        try:
            with open(path, "rb") as f:
                head = f.read(8192)
        except FileNotFoundError as e:
            where = f" (included from {stack[-1]})" if stack else ""
            raise EmirError("EMIR-E101", f"{path}: not found{where}", path=path) from e
        except OSError as e:
            raise EmirError("EMIR-E107", f"{path}: {e.strerror or e}", path=path) from e
        if b"\0" in head:
            raise EmirError("EMIR-E260", f"{path} is a binary file, not a SPICE netlist",
                            path=path)
        sf = SpiceFile(path, top)
        self.files[path] = sf
        self.order.append(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read().splitlines()
        self._chunks(sf, raw, title=self.title)
        self._structure(sf, stack + (path,))
        return sf

    @staticmethod
    def _chunks(sf: SpiceFile, raw: list, title: bool = True):
        last_stmt = None
        for n, line in enumerate(raw, 1):
            s = line.strip()
            if sf.top and title and n == 1:
                sf.chunks.append(Chunk("title", [line], n))
                continue
            if s.startswith("+") and last_stmt is not None:
                last_stmt.lines.append(line)
                continue
            if not s:
                sf.chunks.append(Chunk("blank", [line], n))
                continue
            if s.startswith("*"):
                sf.chunks.append(Chunk("comment", [line], n))
                continue
            kind = "dot" if s.startswith(".") else "element"
            ch = Chunk(kind, [line], n)
            sf.chunks.append(ch)
            last_stmt = ch
        for ch in sf.chunks:
            if ch.kind in ("dot", "element"):
                ch.words = tokens(ch.text)

    def _structure(self, sf: SpiceFile, stack: tuple):
        scopes: list = []
        in_control = False
        for i, ch in enumerate(sf.chunks):
            if ch.kind not in ("dot", "element") or not ch.words:
                continue
            w0 = ch.words[0].lower()
            if in_control:
                ch.kind = "control"
                if w0 == ".endc":
                    in_control = False
                continue
            if w0 == ".control":
                ch.kind = "control"
                in_control = True
                self.has_control = True
                continue
            if ch.kind == "dot":
                if w0 == ".subckt" and len(ch.words) > 1:
                    ports = []
                    for w in ch.words[2:]:
                        if _is_param(w) or w.lower() in ("params:", "param:"):
                            break
                        ports.append(w)
                    sc = Subckt(ch.words[1], ports, sf.path)
                    self.subckts.setdefault(ch.words[1].upper(), sc)
                    scopes.append(sc)
                elif w0 == ".ends":
                    if scopes:
                        scopes.pop()
                elif w0 in (".include", ".inc", ".incl") and len(ch.words) > 1:
                    raw = _unquote(ch.words[1])
                    target = _resolve(sf.path, raw)
                    sf.includes.append(Include(i, target, "include", keyword=ch.words[0],
                                               absolute=_is_abs(raw),
                                               quote=_quote_of(ch.words[1])))
                    self._read(target, top=False, stack=stack)
                elif w0 == ".lib" and len(ch.words) > 2:
                    raw = _unquote(ch.words[1])
                    target = _resolve(sf.path, raw)
                    sf.includes.append(Include(i, target, "lib", section=ch.words[2],
                                               keyword=ch.words[0], absolute=_is_abs(raw),
                                               quote=_quote_of(ch.words[1])))
                elif w0 == ".alter" and sf.top and not scopes and sf.alter is None:
                    sf.alter = i
                elif w0 == ".tran" and not scopes:
                    self.analyses.append(ch.text)
                elif w0 == ".end" and sf.top and sf.end is None:
                    sf.end = i
                    break
                continue
            # an element
            name = ch.words[0]
            kind = name[0].upper()
            if has_annotation(ch.text):
                sf.annotated = True
            nodes, ref = _nodes(kind, ch.words)
            el = Element(name, kind, nodes, ref, scopes[-1].name.upper() if scopes else None,
                         sf.path, i, ch.words)
            if scopes:
                scopes[-1].elements.append(el)
            else:
                self.top_elements.append(el)

    # ---------------------------------------------------------- queries

    def elements(self, scope: str | None) -> list:
        """The elements of a subcircuit (`scope`, any case), or of the top
        level (None)."""
        if scope is None:
            return list(self.top_elements)
        sc = self.subckts.get(scope.upper())
        return list(sc.elements) if sc else []

    def element_names(self, scope: str | None) -> set:
        return {e.name.upper() for e in self.elements(scope)}

    def instance_paths(self, scope: str, limit: int = 50) -> list:
        """Every instance path from the top level down to subcircuit
        `scope`: `[["XI0"], ["XI1", "XA"]]` (names as written)."""
        target = scope.upper()
        out: list = []

        def walk(sc_elements, path, seen):
            for e in sc_elements:
                if e.kind not in ("X",) or not e.ref:
                    continue
                r = e.ref.upper()
                if r == target:
                    out.append(path + [e.name])
                    if len(out) >= limit:
                        return
                elif r in self.subckts and r not in seen:
                    walk(self.subckts[r].elements, path + [e.name], seen | {r})

        walk(self.top_elements, [], frozenset())
        return out


def _is_abs(path: str) -> bool:
    return os.path.isabs(os.path.expanduser(path))


def _resolve(including: str, path: str) -> str:
    """An include path as every SPICE here resolves it: relative to the
    directory of the file that includes it (measured with Xyce and ngspice,
    run from another directory and with nested includes)."""
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(os.path.dirname(including), path))


def _nodes(kind: str, words: list) -> tuple:
    """`(nodes, ref)` of an element line."""
    plain = []
    for w in words[1:]:
        if _is_param(w) or w.lower() in ("params:", "param:"):
            break
        plain.append(w)
    if kind in ("X", "N"):
        return (plain[:-1], plain[-1]) if len(plain) >= 2 else (plain, None)
    if kind == "Q":
        # c b e [s] model [area]: four nodes when a fifth plain word follows
        # that is not a number (the model), three otherwise
        k = 4 if len(plain) >= 5 and not _is_number(plain[4]) else 3
        return plain[:k], (plain[k] if len(plain) > k else None)
    k = NODES.get(kind)
    if k is None:
        return [], None
    return plain[:k], (plain[k] if len(plain) > k and kind in "MJZDQ" else None)


class Writer:
    """Write a rewritten copy of a tree into one directory.

    `edits[(file, chunk)]` replaces a chunk by lines; `inject` goes before the
    top file's first `.alter`, else its `.end`; `strip` drops the DSPF annotations of every element
    line (Xyce). A file is copied when it is edited, when stripping changes
    it, or when it includes a copied file; every other file is referenced
    where it is, by a path relative to the copy that includes it.
    """

    def __init__(self, tree: Tree, out_top: str, tag: str, edits=None, inject=(),
                 strip=False, command="simulate"):
        self.tree = tree
        self.command = command
        self.out_top = os.path.abspath(out_top)
        self.out_dir = os.path.dirname(self.out_top)
        self.tag = tag
        self.edits = edits or {}
        self.inject = list(inject)
        self.strip = strip
        self.copies: dict = {}

    def _needs_copy(self) -> set:
        edited = {f for f, _ in self.edits}
        need = set(edited)
        if self.strip:
            need |= {p for p, sf in self.tree.files.items() if sf.annotated}
        # a file that includes a copied file is copied too (its include line
        # changes): iterate to a fixed point
        changed = True
        while changed:
            changed = False
            for p, sf in self.tree.files.items():
                if p not in need and any(inc.target in need for inc in sf.includes):
                    need.add(p)
                    changed = True
        need.add(self.tree.top.path)
        return need

    def write(self, check=None) -> dict:
        """Write the copies; returns `{original: copy}`. `check(paths)`, when
        given, sees every path about to be written before any is (it may
        refuse by raising)."""
        from .output import make_dir, write_text
        need = self._needs_copy()
        make_dir(self.out_dir)
        used = {os.path.basename(self.out_top).lower()}
        for p in self.tree.order:
            if p not in need:
                continue
            if p == self.tree.top.path:
                self.copies[p] = self.out_top
                continue
            stem, ext = os.path.splitext(os.path.basename(p))
            name, k = f"{stem}.{self.tag}{ext or '.sp'}", 1
            while name.lower() in used:
                k += 1
                name = f"{stem}.{self.tag}{k}{ext or '.sp'}"
            used.add(name.lower())
            self.copies[p] = os.path.join(self.out_dir, name)
        if check is not None:
            check(list(self.copies.values()))
        for p, dest in self.copies.items():
            if os.path.abspath(p) == dest:
                raise EmirError("EMIR-E513", f"the output {dest} would overwrite its own "
                                f"input; write it to another file", command=self.command)
            write_text(dest, self.render(self.tree.files[p], dest))
        return dict(self.copies)

    def _ref(self, inc: Include, dest: str) -> str:
        """How the copy at `dest` names an included file: a copy by its path
        relative to `dest`; an untouched file as the original named it if
        that was absolute (a PDK's model file), else relative to `dest`."""
        t = self.copies.get(inc.target)
        if t is None and inc.absolute:
            ref = inc.target
        else:
            ref = os.path.relpath(t or inc.target, os.path.dirname(dest))
        if inc.quote:
            return f"{inc.quote}{ref}{inc.quote}"
        return f'"{ref}"' if re.search(r"\s", ref) else ref

    def render(self, sf: SpiceFile, dest: str) -> str:
        incs = {inc.chunk: inc for inc in sf.includes}
        out = []
        # the lines go before the first .alter (they then hold for the main
        # case and every altered one), else before .end
        at = sf.alter if sf.alter is not None else sf.end
        for i, ch in enumerate(sf.chunks):
            if sf.top and i == at and self.inject:
                out += self.inject
            rep = self.edits.get((sf.path, i))
            if rep is not None:
                out += rep
                continue
            if i in incs:
                inc = incs[i]
                line = f"{inc.keyword} {self._ref(inc, dest)}"
                if inc.kind == "lib":
                    line += f" {inc.section}"
                out.append(line)
                continue
            if self.strip and ch.kind == "element" and has_annotation(ch.text):
                out.append(" ".join(ch.words))
                continue
            out += ch.lines
        if sf.top and at is None and self.inject:
            out += self.inject
        return "\n".join(out) + "\n"
