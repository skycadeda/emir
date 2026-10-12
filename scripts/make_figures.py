#!/usr/bin/env python3
"""Every figure of the docs, the README and the blog, made from runs of this repo.

    python scripts/make_figures.py                 # all figures (about a minute)
    python scripts/make_figures.py --only hero,cover
    python scripts/make_figures.py --list          # the figures and their sources
    python scripts/make_figures.py --work DIR      # keep the example runs in DIR

No number in a figure is typed in by hand. Each comes from one of:

* a run this script does, from a clone, offline: example 06's mesh screened
  by rcc with the invented limits of its process_screen.yaml (Xyce, then
  `emir run` in process, as that example's run.sh runs it: the banner, the
  covers and carousel pages 1 and 2), example 08's magic route (`emir extract
  --tool magic`, Xyce, `emir run --html --gds`: the layout chart, and carousel
  page 4 from the same run in process; it needs a magic with the geoout patch,
  `PDK_ROOT` with sky130A and Xyce, and keeps the committed images without
  them), and the example 02 flow (`emir next`, `emir simulate`
  with Xyce, `emir run`, `emir report`) for the agent transcript and the HTML
  report;
* the package itself (the number of commands, error codes and schemas).

Outputs: docs/images/*.png|svg (light by default, `_dark` variants; hero.png
is the dark banner, hero_light.png its light twin), docs/images/README.md (the
manifest: what each figure shows and where its numbers come from),
blog/images/cover_1200x627.png (the feed and link previews),
blog/images/cover_article_1920x1080.png (the article's 16:9 header),
blog/images/carousel_NN.png (1080 x 1350) and blog/carousel.pdf.

Every map is drawn by EMIR's own chart renderer (emir/chart.py) on its one
colour scale (emir/style.py); the layout under the results is KLayout's
drawing, in the PDK's layer colours when $PDK_ROOT holds it (default
/usr/local/share/pdk, as for the subprocesses), else a neutral grey per layer.

Needs the `dev` extra (matplotlib, klayout, Pillow via matplotlib). Optional:
Xyce (the screened mesh: without it the banner, the covers and the carousel
keep their committed files; the transcript and the HTML report). The HTML
screenshot uses the first headless browser that works: $EMIR_FIGURES_BROWSER,
firefox, chromium or Chrome from PATH, a Playwright chromium; without one the
committed screenshot is kept and the script says so. Screening, not sign-off.
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import to_rgba  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402
from PIL import Image  # noqa: E402

#: the matplotlib every committed figure is drawn with: another version lays
#: text out differently (a subtitle wraps elsewhere, an annotation moves), so a
#: figure redrawn with it differs from the rest in every pixel. Drawing refuses
#: another version unless --any-matplotlib is given (a draft, not to commit)
MATPLOTLIB = "3.11.2"

DOCS_IMG = os.path.join(ROOT, "docs", "images")
BLOG_IMG = os.path.join(ROOT, "blog", "images")
BLOG = os.path.join(ROOT, "blog")
EX = os.path.join(ROOT, "examples")
#: examples/06_supply_mesh's screening picture: the mesh, its invented limits and
#: the command that example's run.sh runs (a test holds them to one)
SCREEN_N, SCREEN_K = 10, 32
SCREEN_PROCESS = os.path.join(EX, "06_supply_mesh", "process_screen.yaml")


def screen_args() -> list:
    """`emir run`'s arguments for the screen, in the mesh's directory (after
    Xyce has simulated its rcc.cir into rcc.raw)."""
    return ["grid_rc.dspf", "--process", SCREEN_PROCESS, "--net", "VDD", "--cc-sim", "rcc.raw",
            "--period", "1e-9", "--sim", "xyce"]

#: every size below is in pixels: a figure is W x H px at this dpi
DPI = 100
#: PNG size the optimiser aims under (the repository's large-file hook is 1 MB)
PNG_TARGET = 400 * 1024
#: a 256-colour palette may move the shades of an antialiased edge, never a
#: colour that fills an area: a colour on PALETTE_AREA pixels or more must come
#: back within PALETTE_TOL per channel (a median cut once drew a legend's small
#: amber warn swatch red)
PALETTE_AREA = 64
PALETTE_TOL = 8

SANS = "DejaVu Sans"
MONO = "DejaVu Sans Mono"

# EMIR's own report colours (emir/style.py, emir/dashboard.py): one blue for
# "safe" and identity, amber for WARN, red for FAIL, and neutral greys.
THEMES = {
    "light": {
        "page": "#f9f9f7", "surface": "#fcfcfb", "panel": "#f0efec", "card": "#ffffff",
        "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#7a7973", "grid": "#e4e3dc",
        "axis": "#c3c2b7", "edge": "#d9d8d0",
        "blue": "#2a78d6", "blue_deep": "#18538f", "blue_soft": "#a9cbf2",
        "amber": "#fab219", "amber_ink": "#241a00", "red": "#e0362f",
        "neutral": "#5d5c57", "neutral_soft": "#c9c8c0",
        "term": "#1a1a19", "term_ink": "#ecebe4", "term_dim": "#9a9992",
    },
    "dark": {
        "page": "#0d0d0d", "surface": "#1a1a19", "panel": "#232320", "card": "#1d1d1b",
        "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#8f8e88", "grid": "#2c2c2a",
        "axis": "#4a4a46", "edge": "#3a3a37",
        "blue": "#3987e5", "blue_deep": "#8ec0f5", "blue_soft": "#244a78",
        "amber": "#fab219", "amber_ink": "#241a00", "red": "#e0362f",
        "neutral": "#c3c2b7", "neutral_soft": "#4f4e49",
        "term": "#141413", "term_ink": "#ecebe4", "term_dim": "#8f8e88",
    },
}

SCREENING = "Screening, not sign-off."
#: the research disclaimer in one line (emir.RESEARCH_LINE), under every chart
from emir import RESEARCH_LINE  # noqa: E402


def fs(pixels: float) -> float:
    """A font size given in pixels, in points (matplotlib's unit)."""
    return pixels * 72.0 / DPI


def T(theme: str) -> dict:
    return THEMES[theme]


def rc(theme: str) -> dict:
    t = T(theme)
    return {
        "font.family": "sans-serif", "font.sans-serif": [SANS],
        "font.size": fs(20), "text.color": t["ink"],
        "axes.facecolor": t["surface"], "axes.edgecolor": t["axis"],
        "axes.labelcolor": t["ink2"], "axes.linewidth": 1.0,
        "xtick.color": t["muted"], "ytick.color": t["muted"],
        "xtick.labelcolor": t["ink2"], "ytick.labelcolor": t["ink2"],
        "xtick.labelsize": fs(19), "ytick.labelsize": fs(19),
        "grid.color": t["grid"], "grid.linewidth": 1.0,
        "figure.facecolor": t["page"], "savefig.facecolor": t["page"],
        "svg.fonttype": "path", "svg.hashsalt": "emir-figures",
        "pdf.fonttype": 42, "axes.unicode_minus": True,
    }


# ----------------------------------------------------------------- drawing kit


def canvas(w: int, h: int, theme: str):
    """A W x H pixel figure with one full-size axes in pixel units (y down)."""
    fig = plt.figure(figsize=(w / DPI, h / DPI), dpi=DPI)
    fig.patch.set_facecolor(T(theme)["page"])
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    ax.set_zorder(0)
    return fig, ax


def axes_px(fig, x, y, w, h):
    """An axes placed by its pixel box (x, y from the top left)."""
    W, H = fig.get_size_inches() * DPI
    return fig.add_axes([x / W, 1 - (y + h) / H, w / W, h / H])


def box(ax, x, y, w, h, fc, ec="none", r=10, lw=1.2, z=2, alpha=None, hatch=None):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                       fc=fc, ec=ec, lw=lw, zorder=z, alpha=alpha, hatch=hatch)
    ax.add_patch(p)
    return p


def text(ax, x, y, s, size=20, color="#000", weight="normal", ha="left", va="top",
         family=SANS, z=5, **kw):
    return ax.text(x, y, s, fontsize=fs(size), color=color, fontweight=weight, ha=ha,
                   va=va, family=family, zorder=z, **kw)


def arrow(ax, x0, y0, x1, y1, color, lw=2.0, z=3, head=10, style="-|>"):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0), zorder=z,
                arrowprops=dict(arrowstyle=f"{style},head_length={head / 10:.2f},"
                                f"head_width={head / 20:.2f}" if style != "-" else "-",
                                color=color, lw=lw, shrinkA=0, shrinkB=0))


def text_width(fig, s, size, family=SANS, weight="normal"):
    """Rendered width of a string in pixels."""
    r = fig.canvas.get_renderer()
    t = fig.text(0, 0, s, fontsize=fs(size), family=family, fontweight=weight)
    w = t.get_window_extent(renderer=r).width
    t.remove()
    return w


def fit(fig, s, size, width, family=SANS, weight="normal", floor=10):
    """The largest font size <= size at which `s` fits `width` pixels."""
    while size > floor and text_width(fig, s, size, family, weight) > width:
        size -= 0.5
    return size


def wrap(fig, s, size, width, family=SANS, weight="normal"):
    """Greedy word wrap of `s` to `width` pixels; returns the lines."""
    lines = []
    for para in s.split("\n"):
        words, cur = para.split(" "), ""
        for wd in words:
            trial = (cur + " " + wd).strip()
            if cur and text_width(fig, trial, size, family, weight) > width:
                lines.append(cur)
                cur = wd
            else:
                cur = trial
        lines.append(cur)
    return lines


def paragraph(ax, fig, x, y, s, size, width, color, weight="normal", lead=1.32,
              family=SANS):
    """Wrapped text from (x, y) down; returns the y below the last line."""
    for line in wrap(fig, s, size, width, family, weight):
        text(ax, x, y, line, size, color, weight, family=family)
        y += size * lead
    return y


def pill(ax, fig, x, y, s, size, fg, bg, ec="none", family=MONO, pad=(14, 8),
         weight="normal", ha="left"):
    """Text in a rounded box; returns (x0, y0, w, h) of the box."""
    w = text_width(fig, s, size, family, weight) + 2 * pad[0]
    h = size * 1.15 + 2 * pad[1]
    x0 = x - w / 2 if ha == "center" else (x - w if ha == "right" else x)
    box(ax, x0, y, w, h, bg, ec, r=h / 2 if h < 60 else 14, z=4)
    text(ax, x0 + w / 2, y + h / 2, s, size, fg, weight, ha="center", va="center_baseline",
         family=family, z=5)
    return x0, y, w, h


def style_axes(ax, theme, grid="y"):
    t = T(theme)
    ax.set_facecolor(t["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t["axis"])
    if grid:
        ax.grid(True, axis=grid, color=t["grid"], lw=1.0, zorder=0)
        ax.set_axisbelow(True)
    ax.tick_params(colors=t["muted"], labelcolor=t["ink2"], length=4, width=1.0)


def chart_header(ax, fig, theme, x, y, kicker, title, subtitle, width):
    t = T(theme)
    text(ax, x, y, kicker.upper(), 15, t["muted"], "bold")
    text(ax, x, y + 26, title, 31, t["ink"], "bold")
    return paragraph(ax, fig, x, y + 72, subtitle, 19, width, t["ink2"])


def footer(ax, fig, theme, x, y, s, width, size=15):
    return paragraph(ax, fig, x, y, s, size, width, T(theme)["muted"])


# ----------------------------------------------------------------- saving


GENERATED: list[str] = []


def _rel(path):
    return os.path.relpath(path, ROOT)


def palette_keeps_colours(rgb: Image.Image, pal: Image.Image) -> bool:
    """Whether the palette image `pal` draws every colour of `rgb` that fills
    an area (PALETTE_AREA pixels or more) within PALETTE_TOL per channel."""
    a = np.asarray(rgb, dtype=np.int16)
    off = np.abs(a - np.asarray(pal.convert("RGB"), dtype=np.int16)).max(axis=2) > PALETTE_TOL
    if not off.any():
        return True
    key = (a[..., 0].astype(np.int32) << 16) | (a[..., 1].astype(np.int32) << 8) | a[..., 2]
    colours, counts = np.unique(key, return_counts=True)
    return not np.isin(key[off], colours[counts >= PALETTE_AREA]).any()


def optimise_png(path: str) -> None:
    """Smallest of: the RGB PNG optimised, and a 256-colour palette version
    that keeps every colour filling an area (`palette_keeps_colours`).
    No text chunks are written, so the bytes do not depend on the build."""
    im = Image.open(path).convert("RGB")
    best = None
    for variant in ("rgb", "pal"):
        buf = io.BytesIO()
        if variant == "rgb":
            im.save(buf, "PNG", optimize=True)
        else:
            pal = im.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                              dither=Image.Dither.NONE)
            if not palette_keeps_colours(im, pal):
                break                   # it would change a colour the reader reads
            pal.save(buf, "PNG", optimize=True)
        if best is None or buf.tell() < best.tell():
            best = buf
        if variant == "rgb" and buf.tell() <= PNG_TARGET // 2:
            break                       # small enough without a palette
    with open(path, "wb") as f:
        f.write(best.getvalue())


def save(fig, path: str, fmt: str = "png", scale: float = 1.0) -> str:
    """Write a figure; `scale` multiplies the pixel size (the layout is unchanged)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta = {"Software": None} if fmt == "png" else {"Date": None, "Creator": None}
    fig.savefig(path, dpi=DPI * scale, facecolor=fig.get_facecolor(), format=fmt,
                metadata=meta)
    plt.close(fig)
    if fmt == "png":
        optimise_png(path)
    GENERATED.append(path)
    print(f"  {_rel(path):48s} {os.path.getsize(path) / 1024:7.1f} KB")
    return path


def save_variants(draw, name: str, themes=("light", "dark"), svg=False, out=DOCS_IMG,
                  default="light") -> list[str]:
    """Draw a figure once per theme. The default theme keeps the plain name;
    the other gets `_<theme>`."""
    paths = []
    for theme in themes:
        sfx = "" if theme == default else f"_{theme}"
        for fmt in (("svg", "png") if svg else ("png",)):
            with plt.rc_context(rc(theme)):
                fig = draw(theme)
                paths.append(save(fig, os.path.join(out, f"{name}{sfx}.{fmt}"), fmt))
    return paths


# ----------------------------------------------------------------- the runs


def _emir_cmd() -> list[str]:
    exe = shutil.which("emir", path=os.path.dirname(sys.executable))
    return [exe] if exe else [sys.executable, "-m", "emir"]


def _env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    env.setdefault("PDK_ROOT", "/usr/local/share/pdk")
    return env


def sh(cmd, cwd, check=True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, env=_env(), capture_output=True, text=True, **kw)
    if check and r.returncode != 0:
        sys.stderr.write(r.stdout[-3000:] + r.stderr[-3000:])
        raise SystemExit(f"make_figures: {' '.join(map(str, cmd))} exited {r.returncode}")
    return r


class Runs:
    """The runs the figures need, each done once, on demand, in `work`."""

    def __init__(self, work: str):
        self.work = work
        self._done: dict = {}

    def get(self, name):
        if name not in self._done:
            print(f"[run] {name}")
            self._done[name] = getattr(self, "_" + name)()
        return self._done[name]

    # -- example 08's magic route: magic's network of a SKY130 NMOS array, rcc
    def _magic08(self):
        env = _env()
        root = env.get("PDK_ROOT", "")
        if not (shutil.which("Xyce", path=env["PATH"]) and os.path.isdir(
                os.path.join(root, "sky130A"))):
            return None
        out = os.path.join(self.work, "08_open_layout")
        os.makedirs(out, exist_ok=True)
        sh([sys.executable, os.path.join(EX, "08_open_layout", "make_layouts.py"), "."], cwd=out)
        shutil.copy(os.path.join(ROOT, "pdks", "sky130.yaml"), out)
        r = sh(_emir_cmd() + ["extract", "--tool", "magic", "--gds", "sink.gds", "--cell", "sink",
                              "--process", "sky130.yaml", "--out", "extract", "--format",
                              "json"], cwd=out, check=False)
        if r.returncode == 3:              # no magic that writes geometry (EMIR-E643)
            return None
        if r.returncode != 0:
            sys.stderr.write(r.stdout[-2000:] + r.stderr[-2000:])
            raise SystemExit("make_figures: emir extract --tool magic failed")
        extract = json.loads(r.stdout)
        with open(os.path.join(EX, "08_open_layout", "tb_tran.cir.in"), encoding="utf-8") as f:
            tb = f.read().replace("@PDK_ROOT@", root).replace("@NETLIST@", "extract/sink.dspf")
        with open(os.path.join(out, "tb_rcc.cir"), "w", encoding="utf-8") as f:
            f.write(tb)
        sh(_emir_cmd() + ["simulate", "tb_rcc.cir", "--sim", "xyce", "--dspf", "extract/sink.dspf",
                          "--out", "sim/rcc.raw"], cwd=out)
        sh(_emir_cmd() + ["run", "extract/sink.dspf", "--process", "sky130.yaml", "--cc-sim",
                          "sim/rcc.raw", "--period", "4e-9", "--summary-out",
                          "rcc.json", "--html", "html", "--gds", "sink.gds", "--cell", "sink"],
           cwd=out)
        return {"html": os.path.join(out, "html"), "dir": out, "extract": extract}

    # -- the same rcc run of example 08, in process, for carousel page 4: its
    # scene (magic's network over the layout) for EMIR's own chart panel
    def _sink08(self):
        lay = self.get("magic08")
        if lay is None:
            return None
        import contextlib

        from emir import dspf as D
        from emir.errors import collecting
        from emir.scene import run_scene
        out = lay["dir"]
        a = D.build_parser().parse_args(["extract/sink.dspf", "--process", "sky130.yaml",
                                         "--cc-sim", "sim/rcc.raw", "--period", "4e-9"])
        here, log = os.getcwd(), io.StringIO()
        os.chdir(out)
        try:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), \
                    collecting(propagate=False):
                res = D.analyse(a)
        except BaseException:
            sys.stderr.write(log.getvalue()[-3000:])
            raise
        finally:
            os.chdir(here)
        modes = {r.get("mode") for r in res.solved.values()}
        if modes != {"rcc"}:
            raise SystemExit(f"make_figures: example 08 ran {sorted(map(str, modes))}, not rcc")
        pdk = (getattr(res.proc, "data", None) or {}).get("pdk")
        scene = run_scene(res.solved, res.dspf, res.proc, gds=os.path.join(out, "sink.gds"),
                          cell="sink", pdk=pdk)
        return {"scene": scene, "items": scene["items"],
                "ir_mV": {n["net"]: float(n["ir"]) * 1e3 for n in scene["nets"]},
                "resistors": int(lay["extract"]["resistors"])}

    # -- the example 02 flow, as an agent runs it: plan, simulate, run, report
    def _agent02(self):
        if not shutil.which("Xyce", path=_env()["PATH"]):
            return None
        out = os.path.join(self.work, "agent")
        if os.path.isdir(out):
            shutil.rmtree(out)
        os.makedirs(out)
        for f in ("loadrail.dspf", "models.sp", "tb_rcc.cir"):
            shutil.copy(os.path.join(EX, "02_xyce", f), out)
        shutil.copy(os.path.join(ROOT, "emir", "data", "sample", "process.yaml"), out)
        emir = _emir_cmd()
        log = []

        def step(args, check=True):
            r = sh(emir + args, cwd=out, check=False)
            if check and r.returncode not in (0,):
                sys.stderr.write(r.stdout[-2000:] + r.stderr[-2000:])
                raise SystemExit(f"make_figures: emir {' '.join(args)} exited {r.returncode}")
            log.append({"args": args, "stdout": r.stdout, "stderr": r.stderr,
                        "code": r.returncode})
            return r

        # the testbench simulates the RC netlist (the DSPF): emir next plans rcc
        plan_args = ["next", "--dspf", "loadrail.dspf", "--process", "process.yaml",
                     "--netlist", "tb_rcc.cir", "--period", "1e-8"]
        plan = step(plan_args).stdout
        plan_json = json.loads(step(plan_args + ["--format", "json"]).stdout)
        # the planned steps, exactly as `emir next` printed them (selftest and
        # nets are cheap; simulate, check, run and report make the result)
        runs = {}
        for s in plan_json["steps"]:
            if s["status"] != "todo":
                raise SystemExit(f"make_figures: emir next planned a waiting step: {s}")
            argv = _split(s["command"])
            # the JSON plan keeps the caller's --format; the person-facing run
            # is the text plan's command, the same without it
            if argv[-2:] == ["--format", "json"]:
                argv = argv[:-2]
            if argv[0] != "emir" or " ".join(argv) not in plan:
                raise SystemExit(f"make_figures: planned command not in the text plan: "
                                 f"{s['command']}")
            runs[s["id"]] = step(argv[1:])
        report = log[-1]["stdout"]
        # the same run with the HTML report and the example's period, for the
        # screenshot (examples/02_xyce/run.sh runs it so)
        html_args = ["run", "loadrail.dspf", "--process", "process.yaml", "--cc-sim",
                     "emir_out/sim/result.raw", "--period", "1e-8", "--summary-out",
                     "emir_out/html_run.json", "--html", "emir_out/html"]
        step(html_args)
        return {"dir": out, "plan_args": plan_args, "plan": plan, "plan_json": plan_json,
                "log": log, "report": report, "steps": runs,
                "html": os.path.join(out, "emir_out", "html")}

    # -- examples/06_supply_mesh's N = 10 mesh screened by rcc with the invented
    # limits of process_screen.yaml, as that example's run.sh runs it (Xyce on
    # rcc.cir, then emir run); emir in process, so the figures can draw the
    # solved net with EMIR's own chart panel
    def _screen06(self):
        if not shutil.which("Xyce", path=_env()["PATH"]):
            return None
        import contextlib
        import importlib.util

        from emir import dspf as D
        from emir.errors import collecting
        from emir.scene import run_scene
        spec = importlib.util.spec_from_file_location(
            "emir_example06_mesh", os.path.join(EX, "06_supply_mesh", "mesh.py"))
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        out = os.path.join(self.work, "06_screen")
        shutil.rmtree(out, ignore_errors=True)
        info = gen.xyce_case(SCREEN_N, SCREEN_K, out)
        with open(os.path.join(out, "xyce.log"), "w", encoding="utf-8") as f:
            sim = subprocess.run([shutil.which("Xyce", path=_env()["PATH"]), "-a", "rcc.cir"],
                                 cwd=out, stdout=f, stderr=subprocess.STDOUT, timeout=60)
        if sim.returncode != 0:
            raise SystemExit(f"make_figures: Xyce failed on the screen's mesh; see {out}/xyce.log")
        a = D.build_parser().parse_args(screen_args())
        here, log = os.getcwd(), io.StringIO()
        os.chdir(out)
        try:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), \
                    collecting(propagate=False):
                res = D.analyse(a)
        except BaseException:
            sys.stderr.write(log.getvalue()[-3000:])
            raise
        finally:
            os.chdir(here)
        r = res.solved["VDD"]
        if r.get("mode") != "rcc":
            raise SystemExit(f"make_figures: the screen ran {r.get('mode')}, not rcc")
        scene = run_scene(res.solved, res.dspf, res.proc)
        # each branch's average current (A), by its DSPF name: the hand check
        # at the feed (page 2) adds them up
        avg = {x["name"]: abs(float(r["Ie"]["avg"][i])) for i, x in enumerate(r["network"].res)}
        width = {x["name"]: x.get("w") for x in r["network"].res}
        # the devices' average current, each through its own contact (Rc<d>):
        # what the feed's straps carry, which page 2 adds up
        supply = sum(v for k, v in avg.items() if re.fullmatch(r"Rc\d+", k))
        return {"scene": scene, "items": scene["items"], "avg_A": avg, "w_um": width,
                "jmax_avg": res.proc.layers["Metal1"]["jmax"]["avg"], "supply_A": supply,
                "ir_mV": float(r["ir_drop"]) * 1e3,
                "devices": info["devices"], "n": info["n"], "dir": out}

    # -- the package's own counts
    def _package(self):
        from emir import __version__, cli, errors, schemas
        return {"commands": len(cli.COMMANDS), "codes": len(errors.REGISTRY),
                "retired": len(getattr(errors, "RETIRED", {})),
                "schemas": len(schemas.names()), "version": __version__}


def _split(command: str) -> list[str]:
    import shlex
    return shlex.split(command)


# ----------------------------------------------------------------- diagrams


def draw_flow(theme, w=1600, h=900):
    """Extraction -> simulation -> emir -> report, with the agent driving it."""
    t = T(theme)
    fig, ax = canvas(w, h, theme)
    pad = 56
    text(ax, pad, 34, "HOW EMIR FITS A FLOW", 15, t["muted"], "bold")
    text(ax, pad, 60, "From an extraction to a report a person can act on", 31, t["ink"], "bold")

    # the agent band
    ay, ah = 124, 92
    box(ax, pad, ay, w - 2 * pad, ah, to_rgba(t["blue"], 0.10), t["blue"], r=16, lw=1.6)
    text(ax, pad + 26, ay + 22, "AI agent", 24, t["ink"], "bold")
    text(ax, pad + 26, ay + 56, "Claude Code, Codex or any MCP client", 16, t["ink2"])
    msg = ("reads AGENTS.md  ·  runs emir next for the plan, then each step  ·  branches on "
           "error codes, never on text  ·  relays emir report")
    text(ax, pad + 420, ay + ah / 2, msg, fit(fig, msg, 18, w - 2 * pad - 450), t["ink"],
         va="center")

    cols = [("1", "Extraction", [("RC (R + C)", "the netlist simulated and screened"),
                                 ("R-only", "the grid, for DC currents"),
                                 ("magic, SKY130", "every resistor's geometry")],
             "emir extract plans the Quantus / PVS run;\nmagic (geoout patch) runs it for SKY130"),
            ("2", "Simulation", [("Spectre", "save lines from the DSPF"),
                                 ("HSPICE", ".probe lines, ASCII .tr0"),
                                 ("Xyce", ".print lines + names sidecar"),
                                 ("ngspice", ".save lines, ASCII result")],
             "every branch current saved;\nemir verify-sim on your machine"),
            ("3", "emir", [("rcc", "every branch current: every metric"),
                           ("DC screen", "--currents: avg = rms = peak"),
                           ("loop residual", "a check of the currents read")],
             "EM per segment: avg, rms, peak;\nIR drop; the fix in um or cuts"),
            ("4", "Report", [("JSON", "emir.run/1, for the agent"),
                             ("HTML report", "per net, self-contained"),
                             ("Virtuoso Explorer", "highlights in the layout"),
                             ("KLayout", "a marker database")],
             "emir report: the summary the\nagent relays to the person")]
    tools = ["emir extract", "save-lines · simulate", "check · run", "emir report"]
    gap = 54
    cw = (w - 2 * pad - 3 * gap) / 4
    cy, ch = 290, 410
    for k, (n, title, chips, note) in enumerate(cols):
        x = pad + k * (cw + gap)
        # the agent drives every stage
        arrow(ax, x + cw / 2, ay + ah, x + cw / 2, cy - 4, t["blue"], lw=2.0, head=12)
        tw = text_width(fig, tools[k], 15, MONO) + 20
        box(ax, x + cw / 2 - tw / 2, ay + ah + 20, tw, 28, t["page"], "none", r=6, z=3)
        text(ax, x + cw / 2, ay + ah + 34, tools[k], 15, t["blue"] if theme == "light"
             else t["blue_deep"], ha="center", va="center", family=MONO, z=4)
        box(ax, x, cy, cw, ch, t["card"], t["edge"], r=16, lw=1.2)
        text(ax, x + 24, cy + 22, n, 18, t["muted"], "bold")
        text(ax, x + 54, cy + 18, title, 25, t["ink"], "bold")
        yy = cy + 74
        chip_h = 74 if len(chips) == 3 else 66
        for name, desc in chips:
            hl = (k == 0 and name == "RC (R + C)") or name == "rcc"
            box(ax, x + 18, yy, cw - 36, chip_h - 12,
                to_rgba(t["blue"], 0.12) if hl else t["panel"],
                t["blue"] if hl else "none", r=10, lw=1.2)
            text(ax, x + 34, yy + 10, name, 19, t["ink"], "bold")
            text(ax, x + 34, yy + 34, desc, fit(fig, desc, 15.5, cw - 70), t["ink2"])
            yy += chip_h
        for i, line in enumerate(note.split("\n")):
            text(ax, x + 22, cy + ch - 62 + 24 * i, line, fit(fig, line, 15, cw - 40),
                 t["muted"])
        if k < 3:
            arrow(ax, x + cw + 6, cy + ch / 2, x + cw + gap - 6, cy + ch / 2, t["ink2"],
                  lw=2.4, head=14)

    # the routes: a simulation, DC currents, a SKY130 layout
    ly = cy + ch + 34
    routes = [("Simulated currents", t["blue"],
               "RC extraction  →  simulate it, every branch current saved  →  rcc: every "
               "metric, exact"),
              ("DC currents", t["neutral"],
               "an extraction and the currents you give (--currents)  →  the DC screen: "
               "avg = rms = peak"),
              ("SKY130, a GDS", t["neutral"],
               "GDS  →  emir extract --tool magic (magic's network, every resistor "
               "drawn)  →  rcc, the same reports")]
    for i, (name, col, seq) in enumerate(routes):
        yy = ly + i * 38
        ax.add_patch(Rectangle((pad, yy - 2), 6, 28, fc=col, ec="none"))
        text(ax, pad + 20, yy, name, 18, t["ink"], "bold")
        text(ax, pad + 330, yy + 1, seq, fit(fig, seq, 17, w - 2 * pad - 330), t["ink2"])
    footer(ax, fig, theme, pad, h - 44,
           "Spectre, HSPICE, Quantus, PVS and Virtuoso need licences: none runs in this "
           "repository's tests, and each must be verified on site by your agent; Xyce, "
           "ngspice, KLayout and magic run in its examples. " + SCREENING + " " + RESEARCH_LINE,
           w - 2 * pad, size=14)
    return fig


# ----------------------------------------------------------------- layouts


def copy_render(src: str, dst: str) -> str:
    """One of emir's own renders, copied as it is (and optimised)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    optimise_png(dst)
    GENERATED.append(dst)
    print(f"  {_rel(dst):48s} {os.path.getsize(dst) / 1024:7.1f} KB")
    return dst


# ----------------------------------------------------------------- the HTML report


def browsers():
    """Headless-capable browsers to try, in order: $EMIR_FIGURES_BROWSER,
    firefox, chromium or Chrome on PATH, a Playwright chromium."""
    cand = os.environ.get("EMIR_FIGURES_BROWSER")
    if cand:
        yield ("firefox" if "firefox" in os.path.basename(cand) else "chrome", cand)
    p = shutil.which("firefox")
    if p:
        yield ("firefox", p)
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome",
                 "chrome-headless-shell"):
        p = shutil.which(name)
        if p:
            yield ("chrome", p)
    pw = os.path.expanduser(os.path.join("~", ".cache", "ms-playwright"))
    for pat in ("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
                "chromium-*/chrome-linux64/chrome"):
        hits = sorted(glob.glob(os.path.join(pw, pat)))
        if hits:
            yield ("chrome", hits[-1])


def screenshot(browser, url: str, out: str, width: int, height: int, dark: bool) -> bool:
    kind, exe = browser
    if kind == "firefox":
        prof = tempfile.mkdtemp(prefix="emir-ff-")
        cmd = [exe, "--headless", "--no-remote", "--profile", prof,
               f"--window-size={width},{height}", "--screenshot", out, url]
        env = {**os.environ, **({"MOZ_DISABLE_CONTENT_SANDBOX": "1"})}
        if dark:
            with open(os.path.join(prof, "user.js"), "w") as f:
                f.write('user_pref("ui.systemUsesDarkTheme", 1);\n'
                        'user_pref("layout.css.prefers-color-scheme.content-override", 0);\n')
    else:
        cmd = [exe, "--headless", "--no-sandbox", "--hide-scrollbars",
               "--disable-gpu", f"--window-size={width},{height}", f"--screenshot={out}"]
        if dark:
            cmd += ["--force-dark-mode", "--blink-settings=preferredColorScheme=0"]
        cmd.append(url)
        env = os.environ
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return os.path.isfile(out) and os.path.getsize(out) > 0


def autocrop_bottom(path: str, margin: int = 24) -> None:
    """Cut the empty page below the content."""
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(int)
    bg = a[-1, -1]
    rows = np.where(np.abs(a - bg).sum(axis=2).max(axis=1) > 24)[0]
    if len(rows):
        im.crop((0, 0, im.width, min(im.height, rows[-1] + margin))).save(path)


# ----------------------------------------------------------------- the agent transcript


def _strip_paths(s: str, work: str) -> str:
    return s.replace(work + os.sep, "").replace(work, ".")


def _short(cmd: str, width: int) -> str:
    """A command line cut to `width` characters at a word, with an ellipsis
    (the figure says the long lines are shortened)."""
    if len(cmd) <= width:
        return cmd
    k = cmd.rfind(" ", 0, width - 2)
    return cmd[:k if k > 0 else width - 2] + " \u2026"


def transcript_lines(agent, width_chars: int, wrap_run: bool = True):
    """(text, style) lines, at most about 25: the plan, the run step's
    verdict, and the report's verdict, worst item and IR, the report's
    Markdown shown as a terminal renders it (no `**` or `|`). The script runs
    each planned step exactly as `emir next` printed it; long command lines
    are shortened here, marked with an ellipsis; nothing is reworded."""
    work = agent["dir"]
    out = []

    def add(s, style="out"):
        out.append((_short(_strip_paths(s, work), width_chars), style))

    def add_wrapped(s, style, max_lines=2):
        """A result line wrapped at words (a hanging indent) rather than cut,
        so its verdict stays on the page; past `max_lines` it is shortened."""
        import textwrap
        s = _strip_paths(s, work)
        lines = textwrap.wrap(s, width_chars, initial_indent="  ", subsequent_indent="    ",
                              break_long_words=False, break_on_hyphens=False)
        for k, ln in enumerate(lines[:max_lines]):
            last = k == max_lines - 1 and len(lines) > max_lines
            out.append((_short(ln + " " + lines[k + 1].strip(), width_chars) if last else ln,
                        style))

    add("$ emir " + " ".join(agent["plan_args"]), "prompt")
    for ln in agent["plan"].splitlines():
        if ln.startswith("mode:"):
            add(ln, "head")             # as printed, shortened with an ellipsis
        elif ln.startswith("steps:"):
            add(ln, "head")
        elif re.match(r"^\s+\d+\.\s", ln):
            add(ln, "cmd")
    out.append(("", "out"))
    add("# each step run exactly as printed; the run step's verdict:", "dim")
    run = next(e for e in agent["log"] if e["args"][0] == "run" and "--html" not in e["args"])
    add("$ emir " + " ".join(run["args"]), "prompt")
    verdict = next((x for x in run["stdout"].splitlines() if x.startswith("overall verdict")),
                   "")
    verdict = verdict.replace(" Screening, not sign-off.", "")
    if wrap_run:
        add_wrapped(verdict, "result")
    else:
        add("  " + verdict, "result")
    out.append(("", "out"))
    add("$ emir report --from emir_out/summary.json", "prompt")
    rep = agent["report"].splitlines()
    for ln in rep:
        if ln.startswith("**Verdict"):
            # its first sentence: the verdict and the counts
            first = ln.replace("**", "").split(". ")[0]
            add_wrapped(first if first.endswith(".") else first + ".", "head")
        elif ln.startswith("**Screening"):
            add("  " + ln.replace("**", ""), "out")
    # the first row of the report's worst-items table (not its nets table)
    worst = rep[next((i for i, ln in enumerate(rep) if ln.startswith("## Worst items")),
                     len(rep)):]
    rows = [ln for ln in worst if ln.startswith("| VDD |") and ("x |" in ln)]
    if rows:
        cells = [c.strip() for c in rows[0].strip("|").split("|")]
        # net | segment | at | layer | kind | metric | ratio | fix
        # the fix on a line of its own, so a narrow page never cuts it off
        add(f"  worst: {cells[1]} ({cells[3]} {cells[4]}) {cells[5]} {cells[6]}", "result")
        add(f"  fix: {cells[7]}", "result")
    ir = next((ln for ln in rep if ln.startswith("- VDD:")), None)
    if ir:
        add("  IR drop " + ir[2:], "out")
    return out


def draw_transcript(agent, theme="dark", w=1600):
    t = T(theme)
    size, lead = 22, 33
    probe = plt.figure(figsize=(1, 1), dpi=DPI)
    cw = text_width(probe, "M" * 100, size, MONO) / 100
    plt.close(probe)
    inner = w - 2 * 56 - 2 * 36
    nchar = int(inner / cw)
    lines = transcript_lines(agent, nchar)
    head = 160
    h = int(head + 40 + len(lines) * lead + 36 + 90)
    fig, ax = canvas(w, h, theme)
    pad = 56
    text(ax, pad, 30, "THE PLAN, RUN AS PRINTED (SCRIPTED)", 17, t["muted"], "bold")
    text(ax, pad, 58, "emir next plans; each step runs as printed; emir report is what an agent "
         "relays", 28, t["ink"], "bold")
    text(ax, pad, 104, "examples/02_xyce in a scratch directory, run by scripts/make_figures.py "
         "(not a model). Xyce supplies the currents.", 19, t["ink2"])
    cy = head
    box(ax, pad, cy, w - 2 * pad, h - cy - 90, t["term"], t["edge"], r=14)
    colors = {"prompt": t["term_ink"], "cmd": "#cfe3fb", "head": "#ffffff",
              "out": t["term_ink"], "dim": t["term_dim"], "result": "#b9d7f7"}
    y = cy + 30
    for s_, style in lines:
        x = pad + 36
        if style == "prompt" and s_.startswith("$ "):
            text(ax, x, y, "$", size, "#8ec0f5", "bold", family=MONO, va="top")
            text(ax, x + 2 * cw, y, s_[2:], size, colors["prompt"], "bold", family=MONO,
                 va="top")
        else:
            # verdict words keep EMIR's severity colours
            pos = 0
            for m_ in re.finditer(r"\bFAIL\b|\bWARN\b|Screening, not sign-off\.", s_):
                if m_.start() > pos:
                    text(ax, x + pos * cw, y, s_[pos:m_.start()], size, colors[style],
                         "bold" if style == "head" else "normal", family=MONO, va="top")
                tok = m_.group(0)
                col = (t["red"] if tok == "FAIL" else t["amber"] if tok == "WARN"
                       else "#ffffff")
                text(ax, x + m_.start() * cw, y, tok, size, col, "bold", family=MONO, va="top")
                pos = m_.end()
            if pos < len(s_):
                text(ax, x + pos * cw, y, s_[pos:], size, colors[style],
                     "bold" if style == "head" else "normal", family=MONO, va="top")
        y += lead
    footer(ax, fig, theme, pad, h - 70,
           "Each step ran as emir next printed it; long command lines are shortened (\u2026) "
           "and the report shows its verdict, worst item and IR. The process limits are the "
           "bundled illustrative ones, not a PDK's. " + SCREENING + " " + RESEARCH_LINE,
           w - 2 * pad, size=17)
    return fig


# ----------------------------------------------------------------- hero, cover, carousel
# The banner, the covers and carousel pages 1 and 2 draw example 06's mesh as
# its run.sh screens it (`Runs._screen06`), with EMIR's own chart panel.


#: the legend's words in a few characters, for a cover read at link-preview size
SHORT_KEYS = {"ok": "ok", "warn": "warn", "fail": "FAIL", "violation marker": "violation"}


def em_legend(ax, fig, x, y, theme, size=15, width=None, short=False):
    """Every colour of the EM map named (emir.style.legend), in rows no wider
    than `width` px: never colour alone. `short`: one word per colour (the
    covers, read small). Returns the y below the last row."""
    from emir import style as S
    t = T(theme)
    keys = S.legend("em", S.WARN_DEFAULT, markers=True)["keys"]
    x0, width = x, width or 10_000
    for kind, colour, lab in keys:
        lab = lab.replace(" (shade by ratio)", ", blue by ratio").replace(": ", " ")
        if short:
            word = lab.split(" ")[0] if not lab.startswith("violation") else "violation marker"
            lab = SHORT_KEYS.get(word, word)
            lab = {"ok": f"ok < {S.WARN_DEFAULT:g}×", "warn": f"warn {S.WARN_DEFAULT:g}–1×",
                   "FAIL": "FAIL ≥ 1×"}.get(lab, lab)
        need = size * 1.8 + text_width(fig, lab, size)
        if x > x0 and x + need > x0 + width:
            x, y = x0, y + size * 1.7
        if kind == "outline":
            ax.add_patch(Rectangle((x + 1, y - size * 0.42), size * 1.2, size * 0.84,
                                   fc="none", ec=colour, lw=2.0, zorder=5))
        else:
            ax.add_patch(Rectangle((x, y - size * 0.45), size * 1.3, size * 0.9,
                                   fc=colour, ec="none", zorder=5))
        text(ax, x + size * 1.8, y, lab, size, t["ink2"], va="center")
        x += need + size * (1.0 if short else 1.4)
    return y + size * 0.9


def flow_pills(ax, fig, x, y, theme, size=20, gap=46, vertical=False, descs=None):
    """agent -> emir next -> emir run -> emir report."""
    t = T(theme)
    steps = ["agent", "emir next", "emir run", "emir report"]
    pos = []
    for i, s_ in enumerate(steps):
        first = i == 0
        fg = "#ffffff" if first else t["ink"]
        bg = t["blue"] if first else t["panel"]
        bx, by, bw, bh = pill(ax, fig, x, y, s_, size, fg, bg,
                              "none" if first else t["edge"], family=SANS if first else MONO,
                              weight="bold" if first else "normal", pad=(size * 0.8, size * 0.45))
        pos.append((bx, by, bw, bh))
        if descs:
            text(ax, bx + bw + 22, by + bh / 2, descs[i], size * 0.9, t["ink2"], va="center")
        if i < len(steps) - 1:
            if vertical:
                arrow(ax, bx + 30, by + bh + 4, bx + 30, by + bh + gap - 4, t["ink2"], lw=2,
                      head=10)
                y = by + bh + gap
            else:
                arrow(ax, bx + bw + 8, by + bh / 2, bx + bw + gap - 8, by + bh / 2, t["ink2"],
                      lw=2, head=10)
                x = bx + bw + gap
    return pos


def screen_map(fig, x, y, w, scr, theme, callouts=None, size=15, window=None, fix=True,
               scalebar=None, badges=0, frame=True, short=False):
    """Example 06's screened mesh as EMIR's chart panel draws it
    (`emir.chart.panel`: the canvas black, every element by its governing
    ratio on the one colour scale, violations outlined red), in a pixel box
    `w` wide (the height follows the window); returns the height.
    `callouts` maps an item's rank to (x, y, ha), x and y in um: where its
    label goes (the ratio and, with `fix`, the fix); without them `badges`
    numbers that many of the worst items. `window` (x0, x1, y0, y1, um) zooms
    in; `scalebar` (um) draws a scale bar; `short` leaves the factor off a
    fix ("widen 2.000 -> 3.660 um", not "... (x1.83)")."""
    from emir import chart as C
    scene = dict(scr["scene"])
    if not fix:
        scene["items"] = [{**it, "fix": ""} for it in scene["items"]]
    elif short:
        scene["items"] = [{**it, "fix": re.sub(r" \(x[0-9.]+\)$", "", it["fix"])}
                          for it in scene["items"]]
    x0, x1, y0, y1 = window if window is not None else C.scene_window(scene)
    h = w * (y1 - y0) / (x1 - x0)
    W, H = (int(round(v)) for v in fig.get_size_inches() * DPI)
    page = C.Page(W, H, theme, fig=fig, ax=fig.axes[0])
    info = C.panel(page, (x, y, w, h), scene, "em", callouts=callouts or None,
                   badges=0 if callouts else badges, window=[x0, x1, y0, y1],
                   scalebar=scalebar, ticks=False, px=size)
    for sp in info["ax"].spines.values():
        sp.set_visible(frame)
    return h


def screen_words(scr) -> str:
    """What the screened mesh is, in one line."""
    return (f"examples/06_supply_mesh: a synthetic {scr['n']} x {scr['n']} mesh, "
            f"{scr['devices']} inverters, Xyce currents, rcc (every metric); invented "
            f"limits.")


#: where the labels of the two worst items go (um), at the sizes each picture
#: is read at: clear of each other, of the feed corner's failures and of the
#: mesh's edge
HERO_CALLOUTS = {1: (9.0, 31.0, "left"), 2: (24.0, 13.0, "left")}
COVER_CALLOUTS = {1: (4.0, 40.0, "left"), 2: (22.0, 15.0, "left")}
PAGE1_CALLOUTS = {1: (4.0, 46.0, "left"), 2: (21.0, 14.0, "left")}
#: page 2's zoom on the feed corner (um), and its four labels: the vertical
#: straps' in the first cells, the horizontal ones' below the mesh's edge
ZOOM = (-3.0, 62.0, -9.0, 21.0)
ZOOM_CALLOUTS = {1: (4.0, 6.0, "left"), 3: (4.0, 15.0, "left"), 2: (12.0, -5.5, "left"),
                 4: (34.0, -5.5, "left")}


def hero_notes() -> list:
    """The two lines under the banner's chart: what it shows, and the
    research disclaimer."""
    return ["A synthetic mesh, invented limits: the straps that leave the feed fail. "
            + SCREENING, RESEARCH_LINE]


def hero_note_y(legend_bottom: float, k: int) -> float:
    """The top of the banner's k-th note line, below the legend's last row
    (`em_legend` returns its bottom): never on it."""
    return legend_bottom + 14 + 24 * k


def draw_hero(scr, theme, w=1600, h=800):
    t = T(theme)
    fig, ax = canvas(w, h, theme)
    mx, my, mw = 48, 74, 560
    text(ax, mx, 34, "examples/06_supply_mesh  ·  rcc: every metric  ·  each element by "
         "its governing ratio", 16, t["muted"])
    mh = screen_map(fig, mx, my, mw, scr, theme, callouts=HERO_CALLOUTS, size=16)
    ly = em_legend(ax, fig, mx, my + mh + 26, theme, size=16, width=mw + 40)
    # the two notes go under the legend's last row (it wraps), inside the page
    for k, line in enumerate(hero_notes()):
        text(ax, mx, hero_note_y(ly, k), line, fit(fig, line, 15, w - 2 * mx), t["muted"])
    rx = mx + mw + 90
    text(ax, rx, 66, "EMIR", 84, t["ink"], "bold")
    y = paragraph(ax, fig, rx, 186, "Electromigration and IR-drop screening for analog layout",
                  31, w - rx - 48, t["ink"], "bold", lead=1.25)
    y = paragraph(ax, fig, rx, y + 14, "Built to be driven by an AI agent; a person reads the "
                  "report and makes the fix.", 21, w - rx - 48, t["ink2"])
    pos = flow_pills(ax, fig, rx, y + 34, theme, size=20, gap=26, vertical=True,
                     descs=["reads AGENTS.md", "plans the commands", "EM + IR on every segment",
                            "the summary to relay"])
    # above the notes, which run the page's width
    tail = "DSPF in  ·  Spectre, HSPICE, Xyce, ngspice currents  ·  HTML, Virtuoso, KLayout out"
    text(ax, rx, pos[-1][1] + pos[-1][3] + 40, tail, fit(fig, tail, 15, w - rx - 48),
         t["muted"])
    return fig


def draw_cover(scr, theme="dark", w=1200, h=627):
    t = T(theme)
    fig, ax = canvas(w, h, theme)
    mx, mw = 36, 470
    from emir import chart as C
    x0, x1, y0, y1 = C.scene_window(scr["scene"])
    mh = mw * (y1 - y0) / (x1 - x0)
    # under the map: the colour key and the notes (about 120 px)
    my = max(22, (h - mh - 126) / 2)
    # read at link-preview size (552 px wide): the labels large
    screen_map(fig, mx, my, mw, scr, theme, callouts=COVER_CALLOUTS, size=20, short=True)
    ly = em_legend(ax, fig, mx, my + mh + 24, theme, size=19, width=mw, short=True)
    # the notes are read at link-preview size too: 18 px and over
    note = "examples/06_supply_mesh · invented limits · " + SCREENING
    text(ax, mx, ly + 12, note, fit(fig, note, 19, mw, floor=18), t["muted"])
    rx = mx + mw + 48

    def column(ax_, fig_, top):
        """The title column from `top`; returns its bottom."""
        text(ax_, rx, top, "EMIR", 64, t["ink"], "bold")
        y = paragraph(ax_, fig_, rx, top + 94, "EM / IR-drop screening your AI agent can run",
                      30, w - rx - 36, t["ink"], "bold", lead=1.22)
        y = paragraph(ax_, fig_, rx, y + 12, "Every segment of a power net checked (avg, "
                      "rms and peak EM, and IR), with the fix in microns or cuts.", 19,
                      w - rx - 36, t["ink2"])
        pos = flow_pills(ax_, fig_, rx, y + 26, theme, size=17, gap=18, vertical=True)
        y = pos[-1][1] + pos[-1][3] + 22
        for line in wrap(fig_, RESEARCH_LINE, 18, w - rx - 36):
            text(ax_, rx, y, line, 18, t["muted"])
            y += 24
        return y

    # measured on a scratch canvas, then centred on the map's middle
    probe, pax = canvas(w, h, theme)
    height = column(pax, probe, 0)
    plt.close(probe)
    column(ax, fig, max(24, min(my + mh / 2 - height / 2, h - 24 - height)))
    return fig


# -- the carousel: 1080 x 1350 pages

CW, CH = 1080, 1350
CPAD = 64


def page(n, total, theme, kicker, headline, sub=None):
    t = T(theme)
    fig, ax = canvas(CW, CH, theme)
    text(ax, CPAD, 56, kicker.upper(), 21, t["blue_deep"] if theme == "dark" else t["blue"],
         "bold")
    text(ax, CW - CPAD, 56, f"{n}/{total}", 21, t["muted"], "bold", ha="right")
    y = paragraph(ax, fig, CPAD, 100, headline, 52, CW - 2 * CPAD, t["ink"], "bold", lead=1.18)
    if sub:
        y = paragraph(ax, fig, CPAD, y + 14, sub, 28, CW - 2 * CPAD, t["ink2"], lead=1.32)
    return fig, ax, y + 30


def page_footer(ax, fig, theme, s, screening=True):
    t = T(theme)
    line = s + ((" " + SCREENING) if screening else "")
    lines = wrap(fig, line, 19, CW - 2 * CPAD)
    y = CH - 48 - 26 * (len(lines) - 1)
    ax.plot([CPAD, CW - CPAD], [y - 22, y - 22], color=t["grid"], lw=1.2)
    for ln in lines:
        text(ax, CPAD, y, ln, 19, t["muted"])
        y += 26


def stat(ax, x, y, big, small, theme, size=64, width=300):
    t = T(theme)
    text(ax, x, y, big, size, t["ink"], "bold")
    for k, ln in enumerate(small.split("\n")):
        text(ax, x, y + size * 1.25 + 32 * k, ln, 24, t["ink2"])


def carousel_cover(scr, theme, total=5):
    """Carousel page 1: example 06's mesh screened, the two worst items and
    their fixes, and the flow."""
    fig, ax, y = page(1, total, theme, "EMIR · open-source EM/IR screening",
                      "EM/IR screening your AI agent can run",
                      "Electromigration and IR drop on extracted analog layout: every segment "
                      "checked, the fix in microns or cuts, a report a person can act on.")
    # read in the feed at about 390 px wide: the map's words at 28 px
    mw = 590
    mh = screen_map(fig, (CW - mw) / 2, y + 6, mw, scr, theme, callouts=PAGE1_CALLOUTS,
                    size=28, short=True)
    ly = em_legend(ax, fig, CPAD, y + mh + 36, theme, size=26, width=CW - 2 * CPAD)
    pos = flow_pills(ax, fig, CPAD, ly + 24, theme, size=24, gap=40)
    # the page the feed shows says it at a size the feed can read
    text(ax, CPAD, max(p[1] + p[3] for p in pos) + 30, SCREENING, 28, T(theme)["ink2"],
         "bold")
    page_footer(ax, fig, theme, f"{screen_words(scr)} Swipe for how it works.")
    return fig


def carousel_checks(scr, theme, total=5):
    """Carousel page 2: the feed corner zoomed, its four failures numbered,
    and two checks a reader can do by hand: Kirchhoff at the feed, and the
    worst strap's ratio and fix."""
    t = T(theme)
    items = {it["rank"]: it for it in scr["items"]}
    fig, ax, y = page(2, total, theme, "Exact for the simulated record",
                      "Every segment, against your limits, with the fix",
                      "rcc reads every resistor's current from your RC simulation: each "
                      "segment's avg, rms and peak, the IR drop, the fix. Here: where the "
                      "supply enters the mesh.")
    mh = screen_map(fig, CPAD, y + 4, CW - 2 * CPAD, scr, theme, callouts=ZOOM_CALLOUTS,
                    size=26, window=ZOOM, fix=False, scalebar=10.0)
    yy = em_legend(ax, fig, CPAD, y + 4 + mh + 28, theme, size=24, width=CW - 2 * CPAD) + 26
    mA = {k: v * 1e3 for k, v in scr["avg_A"].items()}
    i1, i2, i0 = mA[items[1]["name"]], mA[items[2]["name"]], mA["Rc0"]
    w1, j = scr["w_um"][items[1]["name"]], scr["jmax_avg"] * 1e3
    text(ax, CPAD, yy, "by hand", 21, t["muted"], "bold")
    text(ax, CPAD + 560, yy, "the run", 21, t["muted"], "bold")
    yy += 36
    rows = [("Kirchhoff at the feed",
             f"{i1:.3f} + {i2:.3f} + {i0:.3f} = {i1 + i2 + i0:.3f} mA",
             "#1 up, #2 right, the corner inverter",
             f"{scr['supply_A'] * 1e3:.3f} mA", "the devices' average current",
             t["ink"]),
            ("#1, the strap up from the feed",
             f"{i1:.3f} mA / {w1:.1f} µm / {j:.1f} mA/µm = {items[1]['ratio']:.2f}×",
             "average current over width, over the limit",
             f"{items[1]['ratio']:.2f}× FAIL",
             "fix: " + re.sub(r" \(x[0-9.]+\)$", "", items[1]["fix"].split(": ", 1)[-1]),
             t["red"])]
    for name, hand, how, got, sub, col in rows:
        box(ax, CPAD, yy, CW - 2 * CPAD, 116, t["card"], t["edge"], r=14)
        text(ax, CPAD + 22, yy + 12, name, 24, t["ink"], "bold")
        text(ax, CPAD + 22, yy + 48, hand, fit(fig, hand, 22, 510), t["ink2"])
        text(ax, CPAD + 22, yy + 82, how, 17, t["muted"])
        text(ax, CPAD + 560, yy + 12, got, 25, col, "bold")
        text(ax, CPAD + 560, yy + 48, sub, fit(fig, sub, 22, CW - 2 * CPAD - 580), t["ink2"])
        yy += 130
    page_footer(ax, fig, theme, "examples/06_supply_mesh, invented round-number limits; Xyce "
                "currents, rcc: every branch current read, every metric checked.")
    return fig


#: page 4's labels (um) of example 08's two failures: the via2 of out and of
#: vss, left of the layout; the labels above and below the pads
SKY130_CALLOUTS = {1: (-2.9, 5.2, "left"), 2: (-2.9, -2.1, "left")}


def carousel_sky130(lay, theme, total=5):
    """Carousel page 4: example 08's SKY130 cell on the magic route, magic's
    network over the layout as EMIR's chart panel draws it, its two failures
    with their fixes, and the run's own counts."""
    t = T(theme)
    fig, ax, y = page(4, total, theme, "SKY130: from a GDS",
                      "Open PDK, open tools: magic extracts, rcc checks",
                      "magic, with the public geoout patch, writes every resistor's layer, "
                      "width and place; Xyce simulates that RC netlist; EMIR reads every "
                      "branch current.")
    if lay is None:
        # a draft without example 08's run (fig_carousel keeps the committed
        # carousel then): the page says what it lacks
        paragraph(ax, fig, CPAD, y + 20, "Drawn without example 08's run: it needs a magic "
                  "with the geoout patch, PDK_ROOT with sky130A and Xyce.", 26,
                  CW - 2 * CPAD, t["muted"])
        page_footer(ax, fig, theme, "examples/08_open_layout.")
        return fig
    items = {it["rank"]: it for it in lay["items"]}
    mh = screen_map(fig, CPAD, y + 4, CW - 2 * CPAD, lay, theme, callouts=SKY130_CALLOUTS,
                    size=26, short=True)
    yy = em_legend(ax, fig, CPAD, y + 4 + mh + 28, theme, size=24, width=CW - 2 * CPAD) + 30
    ir = lay["ir_mV"]
    tiles = [(f"{lay['resistors']}", "resistors in magic's\nnetwork, every one read"),
             (f"{items[1]['ratio']:.2f}×", f"{items[1]['layer']} {items[1]['metric']}: one cut\n"
              f"where two are needed"),
             (f"{ir['out']:.2f} mV", "IR drop on out\n(" + f"{ir['vss']:.2f} mV on vss)")]
    tw = (CW - 2 * CPAD - 2 * 24) / 3
    for k, (big, small) in enumerate(tiles):
        x = CPAD + k * (tw + 24)
        box(ax, x, yy, tw, 150, t["card"], t["edge"], r=14)
        text(ax, x + 22, yy + 14, big, fit(fig, big, 50, tw - 44, weight="bold"),
             t["red"] if k == 1 else t["ink"], "bold")
        for j, ln in enumerate(small.split("\n")):
            text(ax, x + 22, yy + 88 + 28 * j, ln, fit(fig, ln, 21, tw - 44), t["ink2"])
    page_footer(ax, fig, theme, "examples/08_open_layout: a synthetic SKY130 cell (four NMOS), "
                "magic with the geoout patch, Xyce, rcc; the limits of pdks/sky130.yaml; "
                "diffusion, poly and wells grey: no limit, not checked.")
    return fig


def carousel_site(theme, total=5):
    """Carousel page 5: the person's limits and tools, each verified where it
    runs, and the four commands to start."""
    t = T(theme)
    fig, ax, y = page(5, total, theme, "Try it",
                      "Your limits and tools, verified where they run",
                      "Point your agent at the repository: it reads AGENTS.md and proves "
                      "the install first.")
    cards = [("Limits", "pdks/ for SKY130, GF180MCU and IHP SG13G2, every value cited; or "
                        "typed by hand from your design manual (emir process new, emir "
                        "process check)"),
             ("Cadence", "emir extract plans PVS LVS and Quantus; Spectre save lines; a "
                         "Virtuoso Explorer highlights the worst segments, read-only"),
             ("Simulators", "Spectre, HSPICE, Xyce, ngspice: each passes emir verify-sim "
                            "on your machine before EMIR relies on it")]
    lx = CPAD + 230
    for name, body in cards:
        lines = wrap(fig, body, 25, CW - lx - CPAD - 24)
        h = max(100, 34 + 34 * len(lines))
        box(ax, CPAD, y, CW - 2 * CPAD, h, t["card"], t["edge"], r=14)
        text(ax, CPAD + 24, y + 19, name, 28, t["ink"], "bold")
        yy = y + 19
        for ln in lines:
            text(ax, lx, yy + 2, ln, 25, t["ink2"])
            yy += 34
        y += h + 18
    y += 16
    text(ax, CPAD, y, "Start", 26, t["ink"], "bold")
    steps = ["emir selftest", "emir next", "emir run", "emir report"]
    x, size = CPAD + 140, 22
    for k, s_ in enumerate(steps):
        bx, by, bw, bh = pill(ax, fig, x, y - 8, s_, size, t["ink"], t["panel"], t["edge"],
                              pad=(14, 9))
        if k < len(steps) - 1:
            arrow(ax, bx + bw + 7, by + bh / 2, bx + bw + 27, by + bh / 2, t["ink2"], lw=2,
                  head=9)
        x = bx + bw + 34
    y += 66
    from emir.repo import REPO_URL
    for lab, what in (("Agents", "Claude Code, Codex, any MCP client (emir mcp)"),
                      ("Code", REPO_URL.split("://", 1)[-1] + "  ·  MIT licence")):
        text(ax, CPAD, y, lab, 26, t["ink"], "bold")
        text(ax, CPAD + 140, y, what, fit(fig, what, 25, CW - 2 * CPAD - 140), t["ink2"])
        y += 48
    y += 26
    box(ax, CPAD, y, CW - 2 * CPAD, 176, to_rgba(t["amber"], 0.10), t["amber"], r=14, lw=1.6)
    text(ax, CPAD + 26, y + 20, SCREENING, 30, t["ink"], "bold")
    paragraph(ax, fig, CPAD + 26, y + 68, "Foundry-qualified EM/IR remains the sign-off flow; "
              "EMIR says where to look first, and what it did not check. A research project, "
              "with no guarantee of accuracy: test and verify its results.", 24,
              CW - 2 * CPAD - 52, t["ink2"])
    page_footer(ax, fig, theme, "Spectre, HSPICE, Quantus, PVS and Virtuoso need licences: none "
                "runs in this repository's tests, and each is UNVERIFIED until checked on "
                "site. Every number in these pages comes from a run in the repository "
                "(scripts/make_figures.py).", screening=False)
    return fig


def carousel_pages(runs, theme="dark") -> dict:
    """{page number: figure}: the carousel's five pages."""
    t = T(theme)
    pkg = runs.get("package")
    agent, scr = runs.get("agent02"), runs.get("screen06")
    total = 5
    pages = {1: carousel_cover(scr, theme, total), 2: carousel_checks(scr, theme, total)}

    # 3 built for agents
    fig, ax, y = page(3, total, theme, "Agent first",
                      "Built to be driven by an AI agent",
                      "Every command answers in JSON, every failure carries a code, and the "
                      "tool itself says what to run next.")
    tiles = [(f"{pkg['commands']}", "commands, each with\n--format json"),
             (f"{pkg['codes']}", "stable codes: branch on\nthe code, not the text"),
             (f"{pkg['schemas']}", "JSON schemas\n(emir schema)")]
    tw = (CW - 2 * CPAD - 2 * 24) / 3
    for k, (big, small) in enumerate(tiles):
        x = CPAD + k * (tw + 24)
        box(ax, x, y, tw, 156, t["card"], t["edge"], r=14)
        text(ax, x + 22, y + 14, big, 60, t["ink"], "bold")
        for j, ln in enumerate(small.split("\n")):
            text(ax, x + 22, y + 94 + 28 * j, ln, 21, t["ink2"])
    y += 180
    if agent is not None:
        size, lead = 28, 37
        probe_w = text_width(fig, "M" * 50, size, MONO) / 50
        lines = transcript_lines(agent, int((CW - 2 * CPAD - 48) / probe_w), wrap_run=False)
        # the plan's mode (shortened to the page), its steps, the run's
        # verdict, the report's verdict and fix; one blank line, between the
        # plan and the run
        keep = []
        blank = 0
        for s_, st_ in lines:
            if not s_:
                blank += 1
                if blank > 1:
                    continue
            if not s_.startswith(("steps:", "# each step", "  Screening", "  worst:",
                                  "  fix:")):
                keep.append((s_, st_))
        bh = len(keep) * lead + 40
        box(ax, CPAD, y, CW - 2 * CPAD, bh, t["term"], t["edge"], r=14)
        yy = y + 22
        for s_, st_ in keep:
            col = {"prompt": t["term_ink"], "cmd": "#cfe3fb", "head": "#ffffff",
                   "dim": t["term_dim"], "result": "#b9d7f7"}.get(st_, t["term_ink"])
            pos = 0
            for m_ in re.finditer(r"\bFAIL\b|\bWARN\b", s_):
                text(ax, CPAD + 24 + pos * probe_w, yy, s_[pos:m_.start()], size, col,
                     family=MONO, weight="bold" if st_ == "prompt" else "normal")
                text(ax, CPAD + 24 + m_.start() * probe_w, yy, m_.group(0), size,
                     t["red"] if m_.group(0) == "FAIL" else t["amber"], "bold", family=MONO)
                pos = m_.end()
            text(ax, CPAD + 24 + pos * probe_w, yy, s_[pos:], size, col, family=MONO,
                 weight="bold" if st_ == "prompt" else "normal")
            yy += lead
    page_footer(ax, fig, theme, "The plan and its steps on examples/02_xyce, run as printed "
                "(scripted; rcc; Xyce currents, the bundled illustrative limits); long lines "
                "shortened (\u2026).")
    pages[3] = fig

    pages[4] = carousel_sky130(runs.get("sink08"), theme, total)
    pages[5] = carousel_site(theme, total)
    return dict(sorted(pages.items()))


# ----------------------------------------------------------------- the registry


FIGURES: dict = {}
MANIFEST = os.path.join(DOCS_IMG, "README.md")


def figure(name, needs=(), outputs=(), title="", source="", alt="", caption=""):
    """Register a figure: what it draws, from what, and how to caption it."""
    def deco(fn):
        FIGURES[name] = {"fn": fn, "needs": needs, "outputs": outputs, "title": title,
                         "source": source, "alt": alt, "caption": caption}
        return fn
    return deco


@figure("flow", outputs=("docs/images/flow.svg", "docs/images/flow.png",
                         "docs/images/flow_dark.svg", "docs/images/flow_dark.png"),
        title="The flow: extraction, simulation, emir, report, with the agent driving it",
        source="a diagram of the commands (no measured numbers)",
        alt="Flow diagram: an AI agent drives four stages, extraction (RC, R-only, magic for "
            "SKY130), simulation (Spectre, HSPICE, Xyce, ngspice), emir (rcc, the DC screen) "
            "and the report (JSON, HTML, Virtuoso Explorer, KLayout).",
        caption="How EMIR fits a flow, with the agent driving each stage.")
def fig_flow(runs):
    save_variants(draw_flow, "flow", svg=True)


@figure("heatmaps", needs=("magic08",),
        outputs=("docs/images/example08_sink.png", "docs/images/example08_sink_dark.png"),
        title="Layout chart: example 08's rcc run on magic's network",
        source="emir run --html --gds on the DSPF `emir extract --tool magic` writes for "
               "examples/08_open_layout's layout, rcc with Xyce, as its run.sh runs it (the "
               "image is emir's own output, copied: emir_chart.png); the layout under the "
               "results drawn by KLayout, in SKY130's layer colours from $PDK_ROOT. Without a "
               "magic that writes geometry, sky130A or Xyce the committed images are kept",
        alt="EM and IR charts of a small SKY130 layout on its black canvas, every element of "
            "magic's network coloured by its ratio or its drop, the worst items numbered "
            "beside each: an NMOS array whose two ports fail on their single via2 cut; the "
            "diffusion, poly and well drawn grey, not checked.",
        caption="EM/IR charts over the GDS (a synthetic layout extracted by magic, with the "
                "SKY130 limits of pdks/sky130.yaml); the colour scale is the one docs/method.md "
                "describes.")
def fig_heatmaps(runs):
    lay = runs.get("magic08")
    if lay is None:
        print("  kept as committed: needs a magic with the geoout patch, PDK_ROOT with sky130A "
              "and Xyce")
        return
    copy_render(os.path.join(lay["html"], "emir_chart_light.png"),
                os.path.join(DOCS_IMG, "example08_sink.png"))
    copy_render(os.path.join(lay["html"], "emir_chart.png"),
                os.path.join(DOCS_IMG, "example08_sink_dark.png"))


@figure("html_report", needs=("agent02",),
        outputs=("docs/images/html_report.png", "docs/images/html_report_dark.png"),
        title="The per-net HTML report of a real run, as a browser shows it",
        source="emir run --html on examples/02_xyce with Xyce currents (as its run.sh runs it), "
               "screenshot by a headless browser",
        alt="Screenshot of EMIR's per-net HTML report of an rcc run: verdict FAIL, the "
            "worst EM item a contact at 1.042x of its peak limit, the net drawn on black as a "
            "vector map coloured by each element's EM ratio, its legend, coverage per metric "
            "and the violations table.",
        caption="The HTML report of examples/02_xyce (Xyce currents, illustrative limits).")
def fig_html_report(runs):
    agent = runs.get("agent02")
    if agent is None:
        print("  skipped: Xyce is not on PATH; the committed screenshot is kept")
        return
    page_ = os.path.join(agent["html"], "loadrail_VDD.html")
    for dark, name in ((False, "html_report.png"), (True, "html_report_dark.png")):
        out = os.path.join(DOCS_IMG, name)
        tmp = out + ".tmp.png"
        used = next((b for b in browsers()
                     if screenshot(b, "file://" + page_, tmp, 1400, 1600, dark)), None)
        if used is None:
            print("  skipped: no browser wrote a screenshot; the committed one is kept")
            return
        autocrop_bottom(tmp)
        os.replace(tmp, out)
        optimise_png(out)
        GENERATED.append(out)
        print(f"  {_rel(out):48s} {os.path.getsize(out) / 1024:7.1f} KB")


@figure("transcript", needs=("agent02",), outputs=("docs/images/agent_transcript.png",),
        title="The plan, run as printed (scripted): emir next, its steps, emir report",
        source="the example 02 flow in a scratch directory, run by this script: emir next "
               "--netlist tb_rcc.cir --period 1e-8, then each planned step exactly as printed "
               "(simulate with Xyce, check, run), then emir report",
        alt="Terminal: emir next prints a seven-step plan, the first verification of Xyce "
            "on this machine among them; each step runs as printed; emir report prints a FAIL "
            "verdict, the worst item a contact at 1.042x on its peak with its fix, and the IR "
            "drop.",
        caption="emir next's plan for examples/02_xyce, each step run exactly as printed "
                "(scripted by scripts/make_figures.py).")
def fig_transcript(runs):
    agent = runs.get("agent02")
    if agent is None:
        print("  skipped: Xyce is not on PATH; the committed transcript is kept")
        return
    with plt.rc_context(rc("dark")):
        save(draw_transcript(agent), os.path.join(DOCS_IMG, "agent_transcript.png"))


SCREEN_SOURCE = ("examples/06_supply_mesh's N = 10 mesh (32 inverters) screened by rcc "
                 "(every metric) with the invented limits of process_screen.yaml, as its "
                 "run.sh runs it (Xyce, then emir run in process; golden values in "
                 "examples/06_supply_mesh/expected/screen_n10.json); the map is EMIR's chart "
                 "panel (emir/chart.py). Without Xyce the committed files are kept")


def _no_screen(runs, what) -> bool:
    if runs.get("screen06") is None:
        print(f"  kept as committed: {what} needs Xyce (the screened mesh of example 06)")
        return True
    return False


@figure("hero", needs=("screen06",),
        outputs=("docs/images/hero.png", "docs/images/hero_light.png"),
        title="The banner (1600 x 800): a real EM chart and the agent flow",
        source=SCREEN_SOURCE,
        alt="EMIR banner: an EM chart of a synthetic power mesh screened with rcc, four "
            "straps at the feed failing and the two that leave it labelled with their fixes, "
            "beside the flow agent, emir next, emir run, emir report.",
        caption="")
def fig_hero(runs):
    if _no_screen(runs, "the banner"):
        return
    scr = runs.get("screen06")
    save_variants(lambda th: draw_hero(scr, th), "hero", default="dark")


@figure("cover", needs=("screen06",),
        outputs=("blog/images/cover_1200x627.png", "blog/images/cover_article_1920x1080.png"),
        title="The LinkedIn covers: 1200 x 627 (feed, link previews), 1920 x 1080 (the "
              "article's 16:9 header)",
        source=SCREEN_SOURCE,
        alt="EMIR: EM / IR-drop screening your AI agent can run, beside EMIR's chart of a "
            "synthetic power mesh: four straps at the feed fail, and the two that leave it "
            "carry their fixes.",
        caption="")
def fig_cover(runs):
    if _no_screen(runs, "the cover"):
        return
    scr = runs.get("screen06")
    with plt.rc_context(rc("dark")):
        save(draw_cover(scr), os.path.join(BLOG_IMG, "cover_1200x627.png"))
        # LinkedIn crops an article's header to 16:9, which would clip the 1.91:1
        # cover's sides: the same layout on a 16:9 page, at 1.6 x the pixels
        save(draw_cover(scr, h=675), os.path.join(BLOG_IMG, "cover_article_1920x1080.png"),
             scale=1.6)


@figure("carousel", needs=("screen06", "package", "agent02", "sink08"),
        outputs=tuple(f"blog/images/carousel_{k:02d}.png" for k in range(1, 6))
        + ("blog/carousel.pdf",),
        title="The LinkedIn carousel: five 1080 x 1350 pages, and the same pages as a PDF",
        source="pages 1 and 2: " + SCREEN_SOURCE + "; page 3: the example 02 flow and the "
               "package's own counts; page 4: examples/08_open_layout's rcc run on magic's "
               "network, as the heatmaps figure runs it, drawn in process by EMIR's chart "
               "panel (without a magic that writes geometry, sky130A or Xyce the committed "
               "carousel is kept)",
        alt="Five pages: what EMIR is, how exact its check is, the agent interface, a SKY130 "
            "layout on the magic route, your limits and tools and how to start.",
        caption="")
def fig_carousel(runs):
    if _no_screen(runs, "the carousel (pages 1 and 2)"):
        return
    if runs.get("sink08") is None:
        print("  kept as committed: page 4 needs example 08's run (a magic with the geoout "
              "patch, PDK_ROOT with sky130A and Xyce)")
        return
    if runs.get("agent02") is None:
        print("  note: Xyce is not on PATH; page 3 is drawn without the transcript")
    from matplotlib.backends.backend_pdf import PdfPages
    with plt.rc_context(rc("dark")):
        figs = carousel_pages(runs, "dark")
        pdf_path = os.path.join(BLOG, "carousel.pdf")
        os.makedirs(BLOG_IMG, exist_ok=True)
        with PdfPages(pdf_path, metadata={"Title": "EMIR: EM/IR screening your AI agent can run",
                                          "CreationDate": None, "ModDate": None,
                                          "Producer": None, "Creator": None}) as pdf:
            for k, fig in figs.items():
                pdf.savefig(fig, facecolor=fig.get_facecolor())
                save(fig, os.path.join(BLOG_IMG, f"carousel_{k:02d}.png"))
        GENERATED.append(pdf_path)
        print(f"  {_rel(pdf_path):48s} {os.path.getsize(pdf_path) / 1024:7.1f} KB")


# ----------------------------------------------------------------- the manifest


def manifest_text() -> str:
    """docs/images/README.md: every figure, its files, its source, its caption."""
    out = ["# Figures", "",
           "Every image here and in `blog/images/` is made by one script from runs of this",
           "repository; none is drawn by hand. Regenerate them all with",
           "", "```sh", "python scripts/make_figures.py            # about a minute",
           "python scripts/make_figures.py --only hero,cover", "```", "",
           "This file is generated by the same script (`--check` compares it).", "",
           f"Every figure is drawn with matplotlib {MATPLOTLIB} (`MATPLOTLIB` in the script;",
           "another version lays text out differently, so the script refuses it).", "",
           "Conventions: `<name>.png` is the light variant and `<name>_dark.png` the dark one",
           "(the hero banner is the exception: `hero.png` is dark, `hero_light.png` light). In",
           "Markdown that renders on GitHub, pick by the reader's theme:", "",
           "```html", "<picture>",
           '  <source media="(prefers-color-scheme: dark)" srcset="docs/images/flow_dark.png">',
           '  <img alt="..." src="docs/images/flow.png">', "</picture>", "```", "",
           f"Every figure that shows results says so: **{SCREENING}**", ""]
    for name, f in FIGURES.items():
        out += [f"## {f['title']}", "",
                "Files: " + ", ".join(f"`{o}`" for o in f["outputs"]), "",
                f"Source: {f['source']}.", "",
                f"Alt text: {f['alt']}", ""]
        if f["caption"]:
            out += [f"Caption: {f['caption']}", ""]
        out += [f"Regenerate: `python scripts/make_figures.py --only {name}`", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def clean_svg(path: str, title: str = "") -> None:
    """An accessible title, no trailing spaces, one final newline (the
    repository's text hooks)."""
    from xml.sax.saxutils import escape
    with open(path, encoding="utf-8") as f:
        svg = f.read()
    if title and "<title>" not in svg:
        svg = re.sub(r"(<svg\b[^>]*>)", lambda m_: m_.group(1) + f"\n <title>{escape(title)}"
                     f"</title>", svg, count=1)
    lines = [ln.rstrip() for ln in svg.splitlines()]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    ap.add_argument("--only", default="", help="comma-separated figure names (see --list)")
    ap.add_argument("--work", default="", help="keep the example runs in this directory")
    ap.add_argument("--list", action="store_true", help="list the figures and exit")
    ap.add_argument("--check", action="store_true",
                    help="only check that docs/images/README.md is current (exit 1 if not)")
    ap.add_argument("--any-matplotlib", action="store_true",
                    help=f"draw with the installed matplotlib even when it is not "
                         f"{MATPLOTLIB}, the one the committed figures are drawn with (a "
                         f"draft: its text lays out differently, do not commit it)")
    a = ap.parse_args(argv)
    if a.list:
        for name, f in FIGURES.items():
            print(f"{name:14s} {f['title']}\n{'':14s} source: {f['source']}")
        return 0
    if a.check:
        want = manifest_text()
        have = open(MANIFEST, encoding="utf-8").read() if os.path.isfile(MANIFEST) else ""
        if have != want:
            print(f"make_figures: {_rel(MANIFEST)} is stale; run scripts/make_figures.py",
                  file=sys.stderr)
            return 1
        missing = [o for f in FIGURES.values() for o in f["outputs"]
                   if not os.path.isfile(os.path.join(ROOT, o))]
        if missing:
            print("make_figures: missing " + ", ".join(missing), file=sys.stderr)
            return 1
        print(f"make_figures: {_rel(MANIFEST)} and {sum(len(f['outputs']) for f in FIGURES.values())}"
              f" figure files are current")
        return 0
    if matplotlib.__version__ != MATPLOTLIB and not a.any_matplotlib:
        print(f"make_figures: matplotlib {matplotlib.__version__} is installed, but every "
              f"committed figure is drawn with {MATPLOTLIB}: another version lays text out "
              f"differently, so a figure redrawn with it differs from the rest. Install "
              f"matplotlib=={MATPLOTLIB} (or pass --any-matplotlib for a draft not to "
              f"commit)", file=sys.stderr)
        return 2
    names = [n for n in a.only.split(",") if n] or list(FIGURES)
    unknown = [n for n in names if n not in FIGURES]
    if unknown:
        ap.error(f"unknown figure(s): {', '.join(unknown)} (see --list)")
    work = a.work or tempfile.mkdtemp(prefix="emir-figures-")
    os.makedirs(work, exist_ok=True)
    # a state directory of the runs' own: the example flow runs `emir
    # verify-sim` as planned (a first run on a machine), and nothing here
    # reads or writes the person's ~/.emir
    # anything drawn in process finds the PDK's KLayout layer colours for a
    # chart's layout underlay as the subprocesses do (_env)
    os.environ.setdefault("PDK_ROOT", _env()["PDK_ROOT"])
    state = os.path.join(os.path.abspath(work), "emir_state")
    shutil.rmtree(state, ignore_errors=True)
    os.environ["EMIR_STATE_DIR"] = state
    runs = Runs(os.path.abspath(work))
    for n in names:
        print(f"[figure] {n}")
        FIGURES[n]["fn"](runs)
    alts = {os.path.join(ROOT, o): f["alt"] for f in FIGURES.values() for o in f["outputs"]}
    for p in GENERATED:
        if p.endswith(".svg"):
            clean_svg(p, alts.get(p, ""))
    os.makedirs(DOCS_IMG, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write(manifest_text())
    print(f"  {_rel(MANIFEST)}")
    big = [p for p in GENERATED if p.endswith(".png") and os.path.getsize(p) > PNG_TARGET]
    for p in big:
        print(f"  note: {_rel(p)} is {os.path.getsize(p) / 1024:.0f} KB (target "
              f"{PNG_TARGET // 1024} KB)")
    if not a.work:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
