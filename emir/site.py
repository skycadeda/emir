"""Site configuration: where the external tools live on *this* host.

Nothing in the package hardcodes an install path. Every external binary and
site-specific default is resolved here, in this order:

1. an explicit argument (CLI flag or function parameter)
2. an environment variable (`SPECTRE_BIN`, `XYCE_BIN`, `NGSPICE_BIN`, ...)
3. the site file -- `$EMIR_SITE` (or a command's `--site FILE`), else
   `~/.emir/site.yaml`, else `<install>/emir_site.yaml`
4. `shutil.which()` on `$PATH`

A site file in the working directory is never picked up by itself: it names
programs emir runs (`emir info` asks magic its version), and the working
directory may hold files nobody vetted (a downloaded design, an agent's
workspace). Name it with `$EMIR_SITE` or `--site`.

The site file is plain YAML::

    spectre: /path/to/spectre           # --netlist runs; simulate/verify-sim --run
    hspice: /path/to/hspice             # simulate/verify-sim --sim hspice --run
    xyce: /path/to/Xyce                 # listed by `emir info`
    ngspice: /path/to/ngspice           # listed by `emir info`
    magic: /path/to/magic               # `emir extract --tool magic`
    virtuoso: /path/to/virtuoso         # listed by `emir info` (`emir next`
    pvs: /path/to/pvs                   #   reads them to plan a flow;
    quantus: /path/to/qrc               #   `emir extract` runs PVS and qrc)
    process: /path/to/pdk/emir.yaml     # default --process
    lib: MYLIB                          # default OA library for the viewer
    mark_layer: [M1, drawing]           # LPP the viewer paints on; any valid
                                        # LPP (default: the entry layer)
    work_dir: emir_out                  # default output root

`emir info` prints what was resolved and from where, which is the first
thing to check when a site says "it cannot find spectre".
"""
from __future__ import annotations

import os
import shutil

import yaml

from .errors import EmirError

_ENV = {"spectre": "SPECTRE_BIN", "hspice": "HSPICE_BIN", "xyce": "XYCE_BIN",
        "ngspice": "NGSPICE_BIN",
        "magic": "MAGIC_BIN", "virtuoso": "VIRTUOSO_BIN", "pvs": "PVS_BIN",
        "quantus": "QRC_BIN",
        "process": "EMIR_PROCESS", "lib": "EMIR_LIB", "work_dir": "EMIR_WORK"}

#: the external tools `emir info` looks for, and their executable names on
#: $PATH (Xyce installs as `Xyce`, Quantus as `qrc`): every tool a command
#: runs, so `emir info` -- the next step of every "not found" code -- shows
#: where each one was found
SIMULATORS = {"spectre": ("spectre",), "hspice": ("hspice",), "xyce": ("Xyce", "xyce"),
              "ngspice": ("ngspice",), "magic": ("magic",), "virtuoso": ("virtuoso",),
              "pvs": ("pvs",), "quantus": ("qrc",)}

#: the ones that need a licence: an agent asks the person before running them
LICENSED = frozenset({"spectre", "hspice", "virtuoso", "pvs", "quantus"})

#: the oldest magic EMIR's magic route was checked with. An older one
#: may not read a current PDK's technology file: magic 8.3.105 (2021) crashed
#: (SIGSEGV) on the sky130 technology file in a check during development, and
#: the run then read as "magic extracted nothing" (EMIR-E640)
MAGIC_CHECKED = (8, 3, 515)

_cache: dict | None = None


def site_file() -> str | None:
    """The site file: `$EMIR_SITE`, `~/.emir/site.yaml` or the install's
    `emir_site.yaml` -- never one in the working directory (see above)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.environ.get("EMIR_SITE"),
              os.path.expanduser("~/.emir/site.yaml"),
              os.path.join(here, "..", "emir_site.yaml")):
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    return None


def load(reload: bool = False) -> dict:
    global _cache
    if _cache is None or reload:
        p = site_file()
        _cache = {}
        if p:
            with open(p, encoding="utf-8") as f:
                _cache = yaml.safe_load(f) or {}
            _cache["_file"] = p
    return _cache


def get(key: str, explicit=None, default=None):
    """`(value, source)` for one setting."""
    if explicit not in (None, ""):
        return explicit, "argument"
    env = _ENV.get(key)
    if env and os.environ.get(env):
        return os.environ[env], f"${env}"
    s = load()
    if key in s:
        return s[key], s.get("_file", "site file")
    return default, "default"


def tool(name: str, explicit=None) -> tuple[str | None, str]:
    """Resolve an executable: `(path or None, source)`."""
    v, src = get(name, explicit)
    if v:
        v = os.path.expanduser(str(v))
        return (v if os.path.exists(v) else None), src
    for exe in SIMULATORS.get(name, (name,)):
        w = shutil.which(exe)
        if w:
            return w, "$PATH"
    return None, "$PATH"


def spectre_bin(explicit=None) -> str:
    p, src = tool("spectre", explicit)
    if not p:
        raise EmirError("EMIR-E601",
            "spectre not found -- pass --spectre, set $SPECTRE_BIN, put "
            "`spectre:` in the site file, or add it to $PATH (see `emir info`)")
    return p


def tool_version(name: str, path: str | None, timeout: float = 10.0) -> str | None:
    """The version a tool reports (`magic --version`), or None. Only magic is
    asked: the others need a licence, or print nothing short."""
    if name != "magic" or not path:
        return None
    import re
    import subprocess
    try:
        p = subprocess.run([path, "--version"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", (p.stdout or "") + (p.stderr or ""))
    return m.group(0) if m else None


def magic_too_old(version: str | None) -> bool:
    """Whether a magic version is older than `MAGIC_CHECKED`."""
    if not version:
        return False
    return tuple(int(x) for x in version.split(".")[:3]) < MAGIC_CHECKED


def magic_warning(path: str | None, version: str | None) -> str | None:
    """The EMIR-W640 text for an old magic, or None."""
    if not magic_too_old(version):
        return None
    checked = ".".join(map(str, MAGIC_CHECKED))
    return (f"magic {version} at {path} is older than {checked}, the oldest version "
            f"EMIR was checked with (magic 8.3.105 crashed on the sky130 technology "
            f"file during development). Use a newer one (--magic, $EMIR_MAGIC, `magic:` "
            f"in the site file or $MAGIC_BIN)")


def describe() -> list[tuple[str, str, str]]:
    """`[(key, value, source)]` for `emir info`."""
    out = []
    for k in SIMULATORS:
        p, src = tool(k)
        out.append((k, p or "NOT FOUND", src))
    for k in ("process", "lib", "mark_layer", "work_dir"):
        v, src = get(k)
        out.append((k, "" if v is None else str(v), src))
    out.append(("site file", site_file() or "(none)", ""))
    return out
