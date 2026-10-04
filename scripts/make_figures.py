#!/usr/bin/env python3
"""Every figure of the docs, the README and the blog, made from runs of this repo.

    python scripts/make_figures.py                 # all figures (about a minute)
    python scripts/make_figures.py --only scaling,accuracy
    python scripts/make_figures.py --list          # the figures and their sources
    python scripts/make_figures.py --work DIR      # keep the example runs in DIR

No number in a figure is typed in by hand. Each comes from one of:

* a run this script does, from a clone, offline: example 06's mesh screened
  through the self-consistent loop with the invented limits of its
  process_screen.yaml (Xyce, in process, as that example's run.sh step 4 runs
  it: the banner, the covers and carousel pages 1 and 2), example 08's magic
  route (`emir extract --tool magic`, Xyce, `emir run --html --gds`; it needs a
  magic with the geoout patch, `PDK_ROOT` with sky130A and Xyce, and keeps the
  committed chart without them), examples/05_chip_level/run.sh, and the example
  02 flow (`emir next`, `emir simulate` with Xyce, `emir run`, `emir report`)
  for the agent transcript and the HTML report;
* the committed measurement tables of examples/06_big_block/results/ (made by
  that example's sweep.py, stream_check.py and selfconsist.py; Xyce,
  synthetic, serial);
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
import csv
import glob
import io
import json
import math
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
RESULTS06 = os.path.join(EX, "06_big_block", "results")
#: examples/06_big_block's screening picture: the mesh, its invented limits and
#: the command that example's run.sh step 4 runs (a test holds them to one)
SCREEN_N, SCREEN_K = 10, 32
SCREEN_PROCESS = os.path.join(EX, "06_big_block", "process_screen.yaml")


def screen_args() -> list:
    """`emir run`'s arguments for the screen, in the mesh's directory."""
    return ["grid_r.dspf", "--process", SCREEN_PROCESS, "--net", "VDD", "--mode", "cc-grid",
            "--self-consistent", "--sim", "xyce", "--netlist", "cc_tb.cir", "--vdd", "1.0",
            "--period", "1e-9"]

#: every size below is in pixels: a figure is W x H px at this dpi
DPI = 100
#: PNG size the optimiser aims under (the repository's large-file hook is 1 MB)
PNG_TARGET = 400 * 1024

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
XYCE_LABEL = "Xyce, synthetic, serial"


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


def optimise_png(path: str) -> None:
    """Smallest of: the RGB PNG optimised, and a 256-colour palette version.
    No text chunks are written, so the bytes do not depend on the build."""
    im = Image.open(path).convert("RGB")
    best = None
    for variant in ("rgb", "pal"):
        buf = io.BytesIO()
        if variant == "rgb":
            im.save(buf, "PNG", optimize=True)
        else:
            im.quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                        dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
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
        with open(os.path.join(EX, "08_open_layout", "tb_tran.cir.in"), encoding="utf-8") as f:
            tb = f.read().replace("@PDK_ROOT@", root).replace("@NETLIST@", "extract/sink.dspf")
        with open(os.path.join(out, "tb_rcc.cir"), "w", encoding="utf-8") as f:
            f.write(tb)
        sh(_emir_cmd() + ["simulate", "tb_rcc.cir", "--sim", "xyce", "--dspf", "extract/sink.dspf",
                          "--mode", "rcc", "--out", "sim/rcc.raw"], cwd=out)
        sh(_emir_cmd() + ["run", "extract/sink.dspf", "--process", "sky130.yaml", "--cc-sim",
                          "sim/rcc.raw", "--mode", "rcc", "--period", "4e-9", "--summary-out",
                          "rcc.json", "--html", "html", "--gds", "sink.gds", "--cell", "sink"],
           cwd=out)
        return {"html": os.path.join(out, "html")}

    # -- examples/05_chip_level/run.sh, as it is
    def _chip05(self):
        out = os.path.join(self.work, "05_chip_level")
        env_emir = " ".join(_emir_cmd())
        r = subprocess.run(["bash", os.path.join(EX, "05_chip_level", "run.sh"), out],
                           cwd=self.work, env={**_env(), "EMIR": env_emir,
                                               "PYTHON": sys.executable},
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write(r.stdout[-3000:] + r.stderr[-3000:])
            raise SystemExit("make_figures: examples/05_chip_level/run.sh failed")
        with open(os.path.join(out, "measured.json")) as f:
            measured = json.load(f)
        with open(os.path.join(out, "top.summary.json")) as f:
            top = json.load(f)
        return {"measured": measured, "top": top, "dir": out}

    # -- the example 02 flow, as an agent runs it: plan, simulate, run, report
    def _agent02(self):
        if not shutil.which("Xyce", path=_env()["PATH"]):
            return None
        out = os.path.join(self.work, "agent")
        if os.path.isdir(out):
            shutil.rmtree(out)
        os.makedirs(out)
        for f in ("loadrail.dspf", "cc.sp", "models.sp", "tb_cc.cir"):
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

        # the mode is the person's choice: without --mode, emir next stops at
        # "choose-mode" and plans no run
        plan_args = ["next", "--dspf", "loadrail.dspf", "--process", "process.yaml",
                     "--netlist", "tb_cc.cir", "--mode", "cc-grid", "--period", "1e-8"]
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
                     "emir_out/sim/result.raw", "--mode", "cc-grid", "--period", "1e-8",
                     "--summary-out",
                     "emir_out/html_run.json", "--html", "emir_out/html"]
        step(html_args)
        return {"dir": out, "plan_args": plan_args, "plan": plan, "plan_json": plan_json,
                "log": log, "report": report, "steps": runs,
                "html": os.path.join(out, "emir_out", "html")}

    # -- the committed measurements of examples/06_big_block
    def _bench06(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "emir_example06_mesh", os.path.join(EX, "06_big_block", "mesh.py"))
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        return {"sweep": read_csv(os.path.join(RESULTS06, "sweep.csv")),
                "stream": read_csv(os.path.join(RESULTS06, "stream_bench.csv")),
                "vdd": float(gen.VDD)}

    # -- examples/06_big_block's N = 10 mesh screened with the invented limits of
    # process_screen.yaml through the waveform self-consistent loop, as that
    # example's run.sh step 4 runs it; in process, so the figures can draw the
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
            "emir_example06_mesh", os.path.join(EX, "06_big_block", "mesh.py"))
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        out = os.path.join(self.work, "06_screen")
        shutil.rmtree(out, ignore_errors=True)
        info = gen.xyce_case(SCREEN_N, SCREEN_K, out)
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
        loop = r["self_consistency"]
        if loop.get("stopped") != "converged":
            sys.stderr.write(log.getvalue()[-3000:])
            raise SystemExit(f"make_figures: the screen's loop stopped: {loop.get('stopped')}")
        scene = run_scene(res.solved, res.dspf, res.proc)
        # each branch's average current (A), by its DSPF name: the hand check
        # at the feed (page 2) adds them up
        avg = {x["name"]: abs(float(r["Ie"]["avg"][i])) for i, x in enumerate(r["network"].res)}
        width = {x["name"]: x.get("w") for x in r["network"].res}
        last = loop["passes"][-1]
        return {"scene": scene, "items": scene["items"], "avg_A": avg, "w_um": width,
                "jmax_avg": res.proc.layers["Metal1"]["jmax"]["avg"],
                "supply_A": abs(float(last["kcl"]["source_avg_A"])),
                "sims": len(loop["passes"]), "residual_mV": float(last["residual_mV"]),
                "tolerance_mV": float(last["tolerance_mV"]), "ir_mV": float(r["ir_drop"]) * 1e3,
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


def read_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


def pm(v: float, digits: int = 2) -> str:
    """A signed number with a typographic minus: +1.20, \u22120.15."""
    return f"{v:+.{digits}f}".replace("-", "\u2212")


def num(row, key) -> float:
    v = row.get(key, "")
    return float(v) if v not in ("", None) else float("nan")


# ----------------------------------------------------------------- big block


def k32_rows(bench):
    rows = [r for r in bench["sweep"] if r["case"] == "k32"]
    return sorted(rows, key=lambda r: int(r["N"]))


def draw_scaling(bench, theme, w=1600, h=1000, compact=False):
    """RCC against CC + cc-grid on the synthetic mesh: time, result size, ratio."""
    t = T(theme)
    rows = k32_rows(bench)
    nodes = np.array([num(r, "grid_nodes") for r in rows])
    rcc = np.array([num(r, "rcc_sim_s") for r in rows])
    cc = np.array([num(r, "cc_sim_s") for r in rows])
    cc_route = cc + np.array([num(r, "cc_post_s") for r in rows])
    rcc_mb = np.array([num(r, "rcc_raw_MB") for r in rows])
    cc_mb = np.array([num(r, "cc_raw_MB") for r in rows])
    route = np.array([num(r, "speedup_route") for r in rows])
    sim_only = np.array([num(r, "speedup_sim_vs_cc_route") for r in rows])
    # the whole RCC route (the simulation and emir reading every branch), where
    # it was run to the end
    rcc_route = rcc + np.array([num(r, "rcc_post_s") for r in rows])

    fig, ax0 = canvas(w, h, theme)
    pad = 56
    y = chart_header(ax0, fig, theme, pad, 36, "Big blocks · CC + R-only extraction",
                     "RCC slows down with the grid; the CC route barely moves",
                     f"The same {int(num(rows[0], 'devices'))} inverters on an N x N metal mesh "
                     f"from {nodes[0]:,.0f} to "
                     f"{nodes[-1]:,.0f} nodes, simulated both ways from one description. "
                     f"RCC: the RC netlist with every branch current saved. CC route: the "
                     f"C-only simulation, then emir run --mode cc-grid on the R-only grid.",
                     w - 2 * pad)
    top = y + 64
    bottom = h - 170
    gap = 100
    widths = [0.40, 0.27, 0.33]
    avail = w - 2 * pad - 2 * gap - 60
    xs = [pad + 60]
    for k in range(1, 3):
        xs.append(xs[-1] + avail * widths[k - 1] + gap)
    ph = bottom - top

    def fmt_s(v):
        return f"{v:g} s" if v >= 1 else f"{v:g} s"

    # (a) wall time
    a = axes_px(fig, xs[0], top, avail * widths[0], ph)
    style_axes(a, theme, grid="both")
    a.loglog(nodes, rcc, "-o", color=t["neutral"], lw=2.4, ms=8, mec=t["surface"], mew=2,
             label="RCC simulation", zorder=4)
    rr = ~np.isnan(rcc_route)
    a.loglog(nodes[rr], rcc_route[rr], "--o", color=t["neutral"], lw=1.8, ms=6,
             mfc=t["surface"], mew=1.8, label="RCC route (simulation + rcc)", zorder=4)
    a.loglog(nodes, cc_route, "-D", color=t["blue"], lw=2.4, ms=7.5, mec=t["surface"], mew=2,
             label="CC route (simulation + cc-grid)", zorder=4)
    a.loglog(nodes, cc, "-s", color=t["blue_deep"], lw=2.0, ms=7, mec=t["surface"], mew=2,
             label="CC simulation alone", zorder=3)
    a.set_xlim(80, nodes[-1] * 2.6)
    a.set_ylim(0.1, 1500)
    a.set_xlabel("mesh nodes", fontsize=fs(19))
    a.set_title("Wall time (s)", loc="left", fontsize=fs(21), color=t["ink"], pad=12,
                fontweight="bold")
    from matplotlib.ticker import FuncFormatter, LogLocator
    a.xaxis.set_major_locator(LogLocator(base=10))
    a.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    a.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    for series, val, col, dy in ((rcc, rcc[-1], t["neutral"], 0),
                                 (cc_route, cc_route[-1], t["blue"], 0),
                                 (cc, cc[-1], t["blue_deep"], 0)):
        a.annotate(f"{val:.3g} s", (nodes[-1], val), xytext=(10, dy),
                   textcoords="offset points", va="center", fontsize=fs(18), color=t["ink"],
                   fontweight="bold")
    a.legend(loc="upper left", frameon=False, fontsize=fs(17), labelcolor=t["ink2"],
             handlelength=1.6)

    # (b) result size
    b = axes_px(fig, xs[1], top, avail * widths[1], ph)
    style_axes(b, theme, grid="both")
    b.loglog(nodes, rcc_mb, "-o", color=t["neutral"], lw=2.4, ms=8, mec=t["surface"], mew=2,
             label="RCC result: every branch current")
    b.loglog(nodes, cc_mb, "-s", color=t["blue"], lw=2.4, ms=7, mec=t["surface"], mew=2,
             label="CC result: device pin currents")
    b.set_xlim(80, nodes[-1] * 3.2)
    b.set_ylim(0.5, 5000)
    b.set_xlabel("mesh nodes", fontsize=fs(19))
    b.set_title("Result file (MiB)", loc="left", fontsize=fs(21), color=t["ink"], pad=12,
                fontweight="bold")
    b.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    b.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    b.annotate(f"{rcc_mb[-1]:,.0f} MiB", (nodes[-1], rcc_mb[-1]), xytext=(10, 0),
               textcoords="offset points", va="center", fontsize=fs(18), color=t["ink"],
               fontweight="bold")
    b.annotate(f"{cc_mb[-1]:.2f} MiB at every size", (nodes[1], cc_mb[1]), xytext=(0, 9),
               textcoords="offset points", va="bottom", ha="left", fontsize=fs(17),
               color=t["ink"], fontweight="bold")
    b.legend(loc="upper left", frameon=False, fontsize=fs(16), labelcolor=t["ink2"],
             handlelength=1.6)

    # (c) the ratio
    c = axes_px(fig, xs[2], top, avail * widths[2], ph)
    style_axes(c, theme, grid="y")
    full = ~np.isnan(route)
    ratio = np.where(full, route, sim_only)
    # a gap and a label between the two definitions: they are not one trend
    idx = np.arange(len(rows)) + np.where(full, 0.0, 0.9)
    c.bar(idx[full], ratio[full], width=0.62, color=t["blue"], zorder=3,
          label="RCC route / CC route (both with emir)")
    c.bar(idx[~full], ratio[~full], width=0.62, color=t["blue_soft"], zorder=3,
          label="RCC simulation alone / CC route\n(right of the dotted line)")
    for i, v in zip(idx, ratio):
        c.text(i, v + 1.2, f"{v:.1f}×", ha="center", va="bottom", fontsize=fs(17),
               color=t["ink"], fontweight="bold")
    if (~full).any():
        xb = (idx[full][-1] + idx[~full][0]) / 2
        c.axvline(xb, color=t["axis"], lw=1.6, ls=":", zorder=2)
    c.set_xticks(idx)
    c.set_xticklabels([f"{n / 1000:.0f}k" if n >= 9500 else f"{n / 1000:.1f}k" if n >= 1000
                       else f"{n:.0f}" for n in nodes], fontsize=fs(16))
    c.set_xlabel("mesh nodes", fontsize=fs(19))
    c.set_ylim(0, max(ratio) * 1.28)
    c.set_title("Speed-up of the CC route", loc="left", fontsize=fs(21),
                color=t["ink"], pad=12, fontweight="bold")
    c.legend(loc="upper left", frameon=False, fontsize=fs(16), labelcolor=t["ink2"],
             handlelength=1.2)
    c.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}×"))

    loads = [num(r, "load_1min") for r in rows]
    footer(ax0, fig, theme, pad, h - 84,
           f"{XYCE_LABEL}. examples/06_big_block/results/sweep.csv (rows k32): wall seconds "
           f"of single-threaded processes on a shared machine with 24 hardware threads (1-minute load "
           f"{min(loads):.0f} to {max(loads):.0f}); read the trends, not the absolute "
           f"times. Above {nodes[full][-1]:,.0f} nodes the RCC result was not read back, so "
           f"the ratio compares the RCC simulation alone. " + SCREENING + " " + RESEARCH_LINE,
           w - 2 * pad)
    return fig


def draw_streaming(bench, theme, w=1600, h=1000):
    """Dense against streamed superposition: matrix size, peak memory, time.
    Small multiples with the cases as shared rows, labelled once."""
    t = T(theme)
    rows = bench["stream"]
    mat = np.array([num(r, "dense_matrix_GiB") for r in rows]) * 1024     # MiB
    d_mem = np.array([num(r, "dense_peak_rss_MB") for r in rows])
    s_mem = np.array([num(r, "stream_peak_rss_MB") for r in rows])
    d_t = np.array([num(r, "dense_wall_s") for r in rows])
    s_t = np.array([num(r, "stream_wall_s") for r in rows])
    diff = max(max(num(r, k) for k in ("rel_diff_avg", "rel_diff_rms", "rel_diff_peak"))
               for r in rows)
    big = rows[-1]

    fig, ax0 = canvas(w, h, theme)
    pad = 56
    y = chart_header(ax0, fig, theme, pad, 36, "Big blocks · streamed superposition",
                     "Big grids stream in bounded memory",
                     f"Held as a dense transfer matrix (branches x taps), the exact "
                     f"superposition needs more memory than the default --max-memory (2 GiB) "
                     f"on the largest grid; EMIR then streams time chunks through the "
                     f"factored grid instead. Same answer: the largest per-branch difference, "
                     f"dense against streamed, is {diff:.1e} of the largest branch.", w - 2 * pad)
    top = y + 70
    label_w = 300
    gap = 64
    pw = (w - 2 * pad - label_w - 2 * gap) / 3
    xs = [pad + label_w + k * (pw + gap) for k in range(3)]
    ph = h - top - 200
    n = len(rows)
    idx = np.arange(n)
    from matplotlib.ticker import FixedLocator, FuncFormatter

    def row_y(i):                      # pixel centre of row i
        return top + ph * (i + 0.5) / n

    for i, r in enumerate(rows):
        yy = row_y(i)
        text(ax0, pad, yy - 22, f"{int(num(r, 'nodes')):,} nodes", 19, t["ink"], "bold",
             va="center")
        text(ax0, pad, yy + 4, f"{int(num(r, 'taps')):,} device taps x "
             f"{int(num(r, 'points')):,} samples", 16, t["ink2"], va="center")
        m = r["auto_method"]
        text(ax0, pad, yy + 28, f"auto picks: {m}", 15,
             t["blue"] if m == "stream" else t["muted"], "bold" if m == "stream" else "normal",
             va="center")

    def panel(k, title):
        a = axes_px(fig, xs[k], top, pw, ph)
        style_axes(a, theme, grid="x")
        a.set_ylim(n - 0.5, -0.5)
        a.set_yticks([])
        a.spines["left"].set_color(t["axis"])
        text(ax0, xs[k], top - 44, title, 21, t["ink"], "bold")
        return a

    a = panel(0, "Dense transfer matrix")
    a.barh(idx, mat, height=0.5, color=t["neutral"], zorder=3)
    a.set_xscale("log")
    a.set_xlim(1, 30000)
    a.xaxis.set_major_locator(FixedLocator([1, 100, 10240]))
    a.xaxis.set_major_formatter(FuncFormatter(
        lambda v, _: f"{v / 1024:g} GiB" if v >= 1024 else f"{v:g} MiB"))
    a.xaxis.set_minor_locator(FixedLocator([10, 1024]))
    a.xaxis.set_minor_formatter(FuncFormatter(lambda v, _: ""))
    a.grid(True, axis="x", which="minor", color=t["grid"], lw=1.0, zorder=0)
    a.axvline(2048, color=t["red"], lw=2.4, zorder=4)
    a.text(2048 / 1.15, -0.32, "2 GiB: the default\n--max-memory", color=t["ink"],
           fontsize=fs(15), fontweight="bold", va="top", ha="right", linespacing=1.15)
    inside = "#ffffff" if theme == "light" else "#0b0b0b"
    for i, v in zip(idx, mat):
        lab = (f"{v:.1f} MiB" if v < 10 else f"{v:,.0f} MiB") if v < 1024 \
            else f"{v / 1024:.2f} GiB"
        if 50 <= v < 1536:   # inside the bar end, clear of the 2 GiB rule
            a.text(v / 1.12, i, lab, va="center", ha="right", fontsize=fs(16), color=inside,
                   fontweight="bold", zorder=5)
        else:
            a.text(v * 1.15, i, lab, va="center", fontsize=fs(16), color=t["ink2"])

    def paired(k, title, dv, sv, unit, xmax):
        b = panel(k, title)
        b.barh(idx - 0.17, dv, height=0.3, color=t["neutral"], zorder=3, label="dense (forced)")
        b.barh(idx + 0.17, sv, height=0.3, color=t["blue"], zorder=3, label="streamed (forced)")
        b.set_xlim(0, xmax)
        for i in idx:
            b.text(dv[i] + xmax * 0.015, i - 0.17, f"{dv[i]:,.{0 if dv[i] >= 10 else 1}f}{unit}",
                   va="center", fontsize=fs(15), color=t["ink2"])
            b.text(sv[i] + xmax * 0.015, i + 0.17, f"{sv[i]:,.{0 if sv[i] >= 10 else 1}f}{unit}",
                   va="center", fontsize=fs(15), color=t["ink"], fontweight="bold")
        b.legend(loc="upper right", frameon=False, fontsize=fs(16), labelcolor=t["ink2"],
                 handlelength=1.2)
        return b

    b = paired(1, "Peak memory (MiB)", d_mem, s_mem, "", max(d_mem) * 1.3)
    b.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    paired(2, "Wall time (s)", d_t, s_t, " s", max(d_t) * 1.3)

    footer(ax0, fig, theme, pad, h - 104,
           f"Synthetic meshes and currents (examples/06_big_block/stream_check.py, "
           f"results/stream_bench.csv): emir run single-threaded, the faster of two runs, on a "
           f"shared machine with 24 hardware threads. Dense forced with --max-memory 64G, "
           f"streamed forced with --superposition stream; 'auto' is the default 2G budget. The "
           f"{int(num(big, 'nodes')):,}-node case, with the defaults: "
           f"{num(big, 'auto_wall_s'):.1f} s at {num(big, 'auto_peak_rss_MB'):,.0f} MiB peak. "
           f"Device taps exclude the net's feed, which the run counts as one more tap. "
           + SCREENING + " " + RESEARCH_LINE,
           w - 2 * pad)
    return fig


def accuracy_data(bench):
    rows = [r for r in k32_rows(bench) if int(r["N"]) <= 70]
    k256 = next(r for r in bench["sweep"] if r["case"] == "k256")
    out = {}
    for m in ("avg", "rms", "peak"):
        gov = [num(r, f"{m}_gov") * 100 for r in rows]
        allv = []
        for r in rows:
            for cls in ("major", "secondary", "minor"):
                for e in ("min", "max"):
                    v = num(r, f"{m}_{cls}_{e}")
                    if not np.isnan(v):
                        allv.append(v * 100)
        out[m] = {"gov": gov, "all": (min(allv), max(allv)), "k256": num(k256, f"{m}_gov") * 100}
    out["ir"] = {"gov": [num(r, "ir_err") * 100 for r in rows], "all": None,
                 "k256": num(k256, "ir_err") * 100}
    out["dyn"] = {"gov": [num(r, "dyn_err") * 100 for r in rows], "all": None,
                  "k256": num(k256, "dyn_err") * 100}
    out["_n"] = (int(rows[0]["N"]), int(rows[-1]["N"]))
    out["_nodes"] = (int(rows[0]["grid_nodes"]), int(rows[-1]["grid_nodes"]))
    out["_k256"] = {"N": int(k256["N"]), "devices": int(k256["devices"]),
                    "droop": num(k256, "rcc_ir_mV") * 1e-3 / bench["vdd"] * 100,
                    "devices_base": int(rows[0]["devices"])}
    return out


#: the marker colour of the high-droop case (not amber: amber means WARN)
HIGH_DROOP = {"light": "#7b5ea7", "dark": "#b39ddb"}


def draw_accuracy(bench, theme, w=1600, h=1000):
    """Two panels on their own scales: avg and IR on +-3 % with the +-2 % guard
    band shaded (the quantities the guard band is about), and rms, peak and
    dynamic IR on -25 % to +90 %."""
    t = T(theme)
    d = accuracy_data(bench)
    fig, ax0 = canvas(w, h, theme)
    pad = 56
    n0, n1 = d["_n"]
    purple = HIGH_DROOP[theme]
    y = chart_header(ax0, fig, theme, pad, 36, "Big blocks · accuracy",
                     "cc-grid against RCC, branch by branch",
                     f"cc-grid minus RCC over RCC on the synthetic mesh "
                     f"({d['_k256']['devices_base']} inverters, N = {n0} "
                     f"to {n1}, {d['_nodes'][0]:,} to {d['_nodes'][1]:,} nodes). Plus is "
                     f"conservative: cc-grid reads high. Averages and IR agree to a fraction of "
                     f"a percent; rms and peak read high on the branches that carry the most "
                     f"current, but not on every branch.", w - 2 * pad)
    top = y + 70
    ax_h = h - top - 260
    label_w = 150
    left_w = 470
    gap = 150
    right_x = pad + label_w + left_w + gap
    right_w = w - right_x - pad
    from matplotlib.ticker import FuncFormatter

    def panel(x, wd, keys, lo, hi, title, band=None, step=None):
        a = axes_px(fig, x, top, wd, ax_h)
        style_axes(a, theme, grid="x")
        a.spines["left"].set_visible(False)
        a.set_xlim(lo, hi)
        ybot = len(keys) - (0.0 if len(keys) == 2 else 0.4)
        a.set_ylim(ybot, -0.6)
        a.set_yticks([])
        if band:
            a.axvspan(-band, band, color=to_rgba(t["blue"], 0.10), zorder=1)
            a.text(0, -0.5, f"±{band:g} % guard band", fontsize=fs(16),
                   color=t["blue_deep"] if theme == "dark" else t["blue"], ha="center",
                   va="center", fontweight="bold", zorder=7,
                   bbox=dict(boxstyle="round,pad=0.25", fc=t["surface"], ec="none",
                             alpha=0.92))
        a.axvline(0, color=t["ink2"], lw=1.4, zorder=2)
        if step:
            a.set_xticks(np.arange(lo, hi + 1e-9, step))
        a.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{pm(v, 0)} %" if v else "0"))
        a.tick_params(axis="x", labelsize=fs(17))
        a.set_xlabel("cc-grid − RCC, % of RCC", fontsize=fs(18))
        ax0.text(x, top - 30, title, fontsize=fs(21), color=t["ink"], fontweight="bold")
        for i, (key, name) in enumerate(keys):
            r = d[key]
            yy = top + ax_h * (i + 0.6) / (ybot + 0.6)       # the row's own height
            ax0.text(x - 22, yy, name, fontsize=fs(21), color=t["ink"], fontweight="bold",
                     ha="right", va="center")
            if r["all"]:
                a.plot(r["all"], [i, i], color=t["neutral_soft"], lw=14,
                       solid_capstyle="round", zorder=3)
            g0, g1 = min(r["gov"]), max(r["gov"])
            a.plot([g0, g1], [i, i], color=t["blue"], lw=6, solid_capstyle="round", zorder=4)
            a.plot(r["gov"], [i] * len(r["gov"]), "o", color=t["blue"], ms=10,
                   mec=t["surface"], mew=2, zorder=5)
            a.plot([r["k256"]], [i], "D", color=purple, ms=12, mec=t["surface"], mew=2,
                   zorder=6)
            # the numbers, above the row, on a backing so no line crosses them
            dg = 2 if max(abs(g0), abs(g1)) < 10 else 1
            notes = [f"governing {pm(g0, dg)} to {pm(g1, dg)} %"]
            if r["all"]:
                dga = 2 if max(abs(r["all"][0]), abs(r["all"][1])) < 1 else 1
                notes.append(f"every branch {pm(r['all'][0], dga)} to "
                             f"{pm(r['all'][1], dga)} %")
            back = dict(boxstyle="round,pad=0.25", fc=t["surface"], ec="none", alpha=0.92)
            if len(keys) == 2:      # room for one note per line, under the row
                for k_, nt in enumerate(notes):
                    a.text(lo + (hi - lo) * 0.015, i + 0.42 + 0.16 * k_, nt, fontsize=fs(16),
                           color=t["ink2"], va="center", zorder=7, bbox=back)
            else:
                a.text(lo + (hi - lo) * 0.015, i - 0.3, "; ".join(notes), fontsize=fs(16),
                       color=t["ink2"], va="center", zorder=7, bbox=back)
        return a

    panel(pad + label_w, left_w, [("avg", "avg"), ("ir", "IR drop")], -3, 3,
          "Averages and IR: read them as ±2 %", band=2, step=1)
    panel(right_x, right_w, [("rms", "rms"), ("peak", "peak"), ("dyn", "dynamic IR")],
          -25, 90, "rms, peak and dynamic IR: high on the governing branch", step=25)

    # legend
    ly = top + ax_h + 92
    lx = pad
    items = [("line", t["blue"], f"governing branch, N = {n0} to {n1} (one dot per size)"),
             ("thick", t["neutral_soft"], "every branch carrying ≥ 1 % of the largest"),
             ("diamond", purple, f"{d['_k256']['devices']} inverters at N = "
                                 f"{d['_k256']['N']} ({d['_k256']['droop']:.1f} % static "
                                 f"droop)")]
    for kind, col, label in items:
        if kind == "line":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=6, solid_capstyle="round")
            ax0.plot([lx + 17], [ly], "o", color=col, ms=10, mec=t["page"], mew=2)
        elif kind == "thick":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=14, solid_capstyle="round")
        else:
            ax0.plot([lx + 17], [ly], "D", color=col, ms=12, mec=t["page"], mew=2)
        ax0.text(lx + 52, ly, label, fontsize=fs(18), color=t["ink2"], va="center")
        lx += 52 + text_width(fig, label, 18) + 44
        if lx > w - 620 and kind != items[-1][0]:
            lx, ly = pad, ly + 38
    footer(ax0, fig, theme, pad, h - 96,
           f"{XYCE_LABEL}: examples/06_big_block/results/sweep.csv. "
           "Guard band from these: read cc-grid avg and IR as ±2 %; confirm a fix decided "
           "by a secondary branch's rms or peak with an rcc run of that sub-block. "
           + SCREENING + " " + RESEARCH_LINE, w - 2 * pad, size=17)
    return fig


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

    cols = [("1", "Extraction", [("RC (R + C)", "small blocks: the direct route"),
                                 ("C-only", "each net collapses to one node"),
                                 ("R-only", "the grid cc-grid solves")],
             "emir extract plans the Quantus / PVS run;\nmagic (geoout patch) runs it for SKY130"),
            ("2", "Simulation", [("Spectre", "save lines from the DSPF"),
                                 ("HSPICE", ".probe lines, ASCII .tr0"),
                                 ("Xyce", "0 V probes + names sidecar"),
                                 ("ngspice", "the same, ASCII result")],
             "emir save-lines, simulate;\nemir verify-sim on your machine"),
            ("3", "emir", [("rcc", "every branch current, exact"),
                           ("cc-grid", "pin currents through the R grid"),
                           ("streaming", "big grids in bounded memory")],
             "EM avg · rms · peak per segment,\nIR drop, the fix in um or cuts"),
            ("4", "Report", [("JSON", "emir.run/1, for the agent"),
                             ("HTML report", "per net, self-contained"),
                             ("Virtuoso Explorer", "highlights in the layout"),
                             ("KLayout", "a marker database")],
             "emir report: the summary the\nagent relays to the person")]
    tools = ["emir extract", "save-lines · simulate", "check · run · abstract", "emir report"]
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
            hl = (k == 0 and name in ("C-only", "R-only")) or name in ("cc-grid", "streaming")
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

    # the two routes and the layout-only path
    ly = cy + ch + 34
    routes = [("Small block", t["neutral"],
               "RC extraction  →  simulate it, every branch current saved  →  rcc"),
              ("Big block: ADC top, PLL top", t["blue"],
               "C-only + R-only extraction  →  simulate C-only, pin currents saved  →  "
               "cc-grid, streamed when large"),
              ("SKY130, a GDS", t["neutral"],
               "GDS  →  emir extract --tool magic (magic's network, every resistor "
               "drawn)  →  rcc or cc-grid, the same reports")]
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


def draw_chip_level(chip, theme, w=1600, h=860):
    """examples/05_chip_level: abstracts, the top with black boxes, budgets,
    back-annotation; every number from that example's run."""
    t = T(theme)
    m, top = chip["measured"], chip["top"]
    hier = top["hierarchy"]
    blocks = {b["instance"]: b for b in hier["blocks"]}
    pins = {p["node"]: p for p in hier["nets"][0]["pins"]}
    pt = hier["nets"][0]["pass_through"][0]
    budget = hier["budget"]
    rows = {r["instance"]: r for r in budget["rows"]}
    black = "#111111" if theme == "light" else "#000000"

    fig, ax = canvas(w, h, theme)
    pad = 56
    text(ax, pad, 34, "CHIP LEVEL · HIERARCHICAL", 15, t["muted"], "bold")
    text(ax, pad, 60, "Each block characterised once; the top screened with the blocks "
         "black-boxed", 29, t["ink"], "bold")
    text(ax, pad, 106, "examples/05_chip_level: a synthetic chip with an ADC on one supply "
         "pin and a PLL fed from both sides. Offline, no simulator.", 18, t["ink2"])

    def step(x, n, title):
        text(ax, x, 170, n, 18, t["muted"], "bold")
        text(ax, x + 28, 167, title, 22, t["ink"], "bold")

    # 1. abstracts
    x1, cw1 = pad, 370
    cy = 220
    step(x1, "1", "emir abstract, per block")
    cards = [("XADC", "adc", "VDDA, 1 pin"), ("XPLL", "pll", "VDDP, 2 pins (west, east)")]
    yy = cy
    for inst, name, sub in cards:
        b = blocks[inst]
        box(ax, x1, yy, cw1, 246, t["card"], t["edge"], r=14)
        box(ax, x1 + 18, yy + 18, 54, 54, black, "none", r=8)
        text(ax, x1 + 45, yy + 45, name, 15, "#ffffff", "bold", ha="center", va="center")
        text(ax, x1 + 88, yy + 20, f"block {name}", 21, t["ink"], "bold")
        text(ax, x1 + 88, yy + 50, sub, 16, t["ink2"])
        lines = [("internal IR", f"{b['internal_ir_mV']:.2f} mV, pins held ideal")]
        if b["n_pins"] == 1:
            p = pins[f"{inst}:VDDA"]
            lines.append(("pin current", f"{-p['i_avg_A'] * 1e3:.2f} mA avg, "
                                         f"{p['i_peak_A'] * 1e3:.2f} mA peak"))
        else:
            lines.append(("pin to pin", f"{pt['R_ohm']:.1f} Ω (from y_ports)"))
        lines.append(("verdict", f"{b['internal_verdict']} inside the block"))
        for i, (k, v) in enumerate(lines):
            text(ax, x1 + 22, yy + 92 + 30 * i, k, 16, t["muted"])
            text(ax, x1 + 132, yy + 92 + 30 * i, v, fit(fig, v, 16.5, cw1 - 152), t["ink"])
        pill(ax, fig, x1 + cw1 - 18, yy + 246 - 50, f"→ {name}.abstract.json", 14,
             t["ink"], t["panel"], t["edge"], ha="right", pad=(10, 5))
        yy += 274

    # 2. the top: the topology of top.dspf, drawn as a schematic
    x2, w2 = x1 + cw1 + 50, 640
    step(x2, "2", "emir run top.dspf --abstract ...")
    box(ax, x2, cy, w2, 520, t["card"], t["edge"], r=14)
    ym = cy + 262
    N = {"pad": (x2 + 56, ym), "T1": (x2 + 190, ym), "ADC": (x2 + 190, cy + 130),
         "PLLW": (x2 + 350, ym), "PLLE": (x2 + 520, ym), "T2": (x2 + 190, ym + 120),
         "T3": (x2 + 520, ym + 120)}
    wires = [("pad", "T1", "Rt0", 0.5, "below"), ("T1", "ADC", "Rt1", 1.0, "right"),
             ("T1", "PLLW", "Rt2", 1.0, "below"), ("T1", "T2", "Rt3", 0.5, "left"),
             ("T2", "T3", "Rt4", 1.75, "below"), ("T3", "PLLE", "Rt5", 0.5, "right")]
    for a_, b_, name, ohm, where in wires:
        (xa, ya), (xb, yb) = N[a_], N[b_]
        ax.plot([xa, xb], [ya, yb], color=t["neutral"], lw=5, solid_capstyle="round", zorder=2)
        mx, my = (xa + xb) / 2, (ya + yb) / 2
        lab = f"{name} {ohm:g} Ω"
        if where == "below":
            text(ax, mx, my + 12, lab, 14.5, t["ink2"], ha="center")
        elif where == "right":
            text(ax, mx + 12, my, lab, 14.5, t["ink2"], va="center")
        else:
            text(ax, mx - 12, my, lab, 14.5, t["ink2"], ha="right", va="center")
    # black boxes
    xa_, ya_ = N["ADC"]
    box(ax, xa_ - 70, ya_ - 74, 140, 74, black, t["axis"], r=6, z=3)
    text(ax, xa_, ya_ - 37, "XADC black box", 15, "#ffffff", "bold", ha="center",
         va="center", z=4)
    xw, _ = N["PLLW"]
    xe, _ = N["PLLE"]
    box(ax, xw - 6, ym - 92, xe - xw + 12, 92, black, t["axis"], r=6, z=3)
    text(ax, (xw + xe) / 2, ym - 64, "XPLL black box", 15, "#ffffff", "bold", ha="center",
         va="center", z=4)
    text(ax, (xw + xe) / 2, ym - 34, f"{pt['I_avg_A'] * 1e3:.3f} mA west → east",
         13.5, "#d9d8d0", ha="center", va="center", z=4)
    xp, yp = N["pad"]
    box(ax, xp - 16, yp - 16, 32, 32, t["blue"], "none", r=4, z=4)
    text(ax, xp, yp - 26, "pad VDD", 15, t["ink"], "bold", ha="center", va="bottom")
    for key, node, dx, dy, ha in (("ADC", "XADC:VDDA", 14, 14, "left"),
                                  ("PLLW", "XPLL:VDDP", 14, 34, "left"),
                                  ("PLLE", "XPLL:VDDP#2", 14, 18, "left")):
        xx, yy2 = N[key]
        ax.plot([xx], [yy2], "o", color=t["amber"], ms=12, mec=t["card"], mew=2, zorder=5)
        v = pins[node]["V_avg_mV"]
        text(ax, xx + dx, yy2 + dy, f"{-v:.3f} mV", 16, t["ink"], "bold", ha=ha,
             va="center", z=6)
    ax.plot([x2 + 28], [cy + 446], "o", color=t["amber"], ms=12, mec=t["card"], mew=2)
    text(ax, x2 + 44, cy + 446, "pin: its voltage drop from the pad, in the top-level solve "
         "(avg, sync)", 15, t["ink2"], va="center")
    chk = (f"Hierarchical against a flat run of the same chip: every top branch, avg / rms / "
           f"peak, within {m['top_vs_flat_max_rel']:.1e} relative.")
    paragraph(ax, fig, x2 + 22, cy + 468, chk, 15, w2 - 44, t["ink2"])

    # 3. budgets and back-annotation
    x3 = x2 + w2 + 46
    w3 = w - pad - x3
    step(x3, "3", "Budgets, back-annotation")
    box(ax, x3, cy, w3, 236, t["card"], t["edge"], r=14)
    text(ax, x3 + 20, cy + 18, f"IR budget {budget['budget_mV']:.0f} mV per block: "
         f"{budget['verdict']}", 17, t["ink"], "bold")
    for j, (col, lab) in enumerate(((t["blue_deep"], "top-level"),
                                    (t["blue"], "inside the block"))):
        lx = x3 + 20 + j * 120
        ax.add_patch(Rectangle((lx, cy + 50), 14, 14, fc=col, ec="none", zorder=4))
        text(ax, lx + 20, cy + 57, lab, 14, t["ink2"], va="center")
    bw = w3 - 40
    scale = bw / (budget["budget_mV"] * 1.08)
    for i, inst in enumerate(("XADC", "XPLL")):
        r = rows[inst]
        by = cy + 84 + i * 82
        text(ax, x3 + 20, by, blocks[inst]["block"], 16, t["ink"], "bold")
        text(ax, x3 + 20 + budget["budget_mV"] * scale, by, f"{r['total_mV']:.2f} mV", 16,
             t["ink"], "bold", ha="right")
        yb = by + 24
        ax.add_patch(Rectangle((x3 + 20, yb), r["top_mV"] * scale, 18, fc=t["blue_deep"],
                               ec=t["card"], lw=2, zorder=4))
        ax.add_patch(Rectangle((x3 + 20 + r["top_mV"] * scale, yb), r["internal_mV"] * scale,
                               18, fc=t["blue"], ec=t["card"], lw=2, zorder=4))
        xb = x3 + 20 + budget["budget_mV"] * scale
        ax.plot([xb, xb], [yb - 6, yb + 24], color=t["ink2"], lw=2.0, zorder=5)
        flat = m["flat_worst_mV"][inst]
        note = (f"{r['top_mV']:.2f} + {r['internal_mV']:.2f}; flat run {flat:.2f}: "
                + ("exact, one pin" if r["n_pins"] == 1 else "a bound, two pins"))
        text(ax, x3 + 20, yb + 26, note, fit(fig, note, 14, bw), t["ink2"])
    ax.plot([x3 + w3 - 82, x3 + w3 - 82], [cy + 48, cy + 66], color=t["ink2"], lw=2.0)
    text(ax, x3 + w3 - 20, cy + 57, "budget", 14, t["ink2"], ha="right", va="center")

    by = cy + 256
    box(ax, x3, by, w3, 264, t["card"], t["edge"], r=14)
    rm = m["pll_Rm1"]
    text(ax, x3 + 20, by + 18, "PLL segment Rm1, re-checked", 17, t["ink"], "bold")
    text(ax, x3 + 20, by + 42, "with the top's pin voltages", 17, t["ink"], "bold")
    for i, (lab, cur, ratio, verdict) in enumerate((
            ("pins held ideal", rm["short_circuit_mA"], rm["ratio_short"], rm["verdict_short"]),
            ("back-annotated", rm["annotated_mA"], rm["ratio_annotated"],
             rm["verdict_annotated"]))):
        yy = by + 88 + i * 58
        text(ax, x3 + 20, yy, lab, 15.5, t["ink2"], va="center")
        text(ax, x3 + 20, yy + 25, f"{cur:.3f} mA, {ratio:.3f}× the limit", 15.5, t["ink"],
             "bold", va="center")
        kind = verdict.upper()
        col = t["amber"] if kind == "WARN" else t["red"]
        ink = t["amber_ink"] if kind == "WARN" else "#ffffff"
        pill(ax, fig, x3 + w3 - 16, yy - 14, kind, 13, ink, col, ha="right", family=SANS,
             weight="bold", pad=(9, 3))
    paragraph(ax, fig, x3 + 20, by + 198, f"Held ideal, the block read "
              f"{rm['under_read'] * 100:.1f} % low; back-annotated, it equals the flat run.",
              14.5, w3 - 40, t["ink2"])

    footer(ax, fig, theme, pad, h - 64,
           "Synthetic and offline: closed-form currents, every number checked against hand "
           "values and a flat run (examples/05_chip_level/measure.py, compare.py). "
           + SCREENING + " " + RESEARCH_LINE, w - 2 * pad, size=15)
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


def transcript_lines(agent, width_chars: int):
    """(text, style) lines, at most about 25: the plan, the run step's
    verdict, and the report's verdict, worst item and IR, the report's
    Markdown shown as a terminal renders it (no `**` or `|`). The script runs
    each planned step exactly as `emir next` printed it; long command lines
    are shortened here, marked with an ellipsis; nothing is reworded."""
    work = agent["dir"]
    out = []

    def add(s, style="out"):
        out.append((_short(_strip_paths(s, work), width_chars), style))

    add("$ emir " + " ".join(agent["plan_args"]), "prompt")
    for ln in agent["plan"].splitlines():
        if ln.startswith("mode:"):
            add(ln.split(" -- ")[0] + "  (the person's choice: device currents through the "
                "grid)", "head")
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
    add("  " + verdict.replace(" Screening, not sign-off.", ""), "result")
    out.append(("", "out"))
    add("$ emir report --from emir_out/summary.json", "prompt")
    rep = agent["report"].splitlines()
    for ln in rep:
        if ln.startswith("**Verdict"):
            add("  " + ln.replace("**", ""), "head")
        elif ln.startswith("**Screening"):
            add("  " + ln.replace("**", ""), "out")
    rows = [ln for ln in rep if ln.startswith("| VDD |") and ("x |" in ln) and "cut" in ln]
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
# its run.sh step 4 screens it (`Runs._screen06`), with EMIR's own chart panel.


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
    fix ("widen 2.000 -> 3.655 um", not "... (x1.83)")."""
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
    return (f"examples/06_big_block: a synthetic {scr['n']} x {scr['n']} mesh, "
            f"{scr['devices']} inverters, Xyce currents through the self-consistent loop "
            f"({scr['sims']} simulations); invented limits.")


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
    text(ax, mx, 34, f"examples/06_big_block  ·  cc-grid, self-consistent ({scr['sims']} "
         f"simulations)  ·  each element by its governing ratio", 16, t["muted"])
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
    note = "examples/06_big_block · invented limits · " + SCREENING
    text(ax, mx, ly + 12, note, fit(fig, note, 19, mw, floor=18), t["muted"])
    rx = mx + mw + 48

    def column(ax_, fig_, top):
        """The title column from `top`; returns its bottom."""
        text(ax_, rx, top, "EMIR", 64, t["ink"], "bold")
        y = paragraph(ax_, fig_, rx, top + 94, "EM / IR-drop screening your AI agent can run",
                      30, w - rx - 36, t["ink"], "bold", lead=1.22)
        y = paragraph(ax_, fig_, rx, y + 12, "Every segment of a power net checked, with the "
                      "fix in microns or cuts.", 19, w - rx - 36, t["ink2"])
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


def carousel_cover(scr, theme, total=10):
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


def carousel_checks(scr, theme, total=10):
    """Carousel page 2: the feed corner zoomed, its four failures numbered,
    and two checks a reader can do by hand: Kirchhoff at the feed, and the
    worst strap's ratio and fix."""
    t = T(theme)
    items = {it["rank"]: it for it in scr["items"]}
    fig, ax, y = page(2, total, theme, "What it checks",
                      "Every segment, against your limits, with the fix",
                      "EM ratio (avg, rms, peak) of each wire section and via cut, the IR drop, "
                      "and what to change. Here: where the supply enters the mesh.")
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
             f"{scr['supply_A'] * 1e3:.3f} mA", "the supply's average current",
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
    page_footer(ax, fig, theme, f"examples/06_big_block, invented round-number limits; Xyce "
                f"currents through the self-consistent loop ({scr['sims']} simulations).")
    return fig


def carousel_loop(theme, total=10):
    """Carousel page 9: the waveform self-consistent loop, drawn from the table
    its figure draws (examples/06_big_block/results/selfconsist_waveform.csv):
    the governing branch's peak pass by pass, what it leaves, what it costs."""
    from matplotlib.ticker import FuncFormatter
    t = T(theme)
    cases = waveform_data()
    k32 = sorted((c for c in cases.values() if c["case"].startswith("k32")),
                 key=lambda c: c["N"])
    hd = cases["hd16b"]
    purple = HIGH_DROOP[theme]
    from emir import style as S
    ramp = S.EM_RAMP[4::-2] if theme == "light" else S.EM_RAMP[1::2]
    colors = {c["case"]: to_rgba(ramp[i]) for i, c in enumerate(k32)}
    colors["hd16b"] = purple

    def pct(r, key):
        return num(r, key) * 100

    fig, ax, y = page(9, total, theme, "Big blocks: closing the loop",
                      "Feed each pin its own droop, then simulate again",
                      "--self-consistent re-simulates the CC testbench with every device pin "
                      "fed its own droop waveform, until the droop the currents produce is the "
                      "droop they were fed.")
    # the governing branch's peak against the pass (the figure's first panel)
    ch_ = 400
    a = axes_px(fig, CPAD + 110, y + 10, CW - 2 * CPAD - 130, ch_)
    style_axes(a, theme, grid="y")
    passes = sorted({int(r["pass"]) for c in k32 + [hd] for r in c["passes"]})
    a.axhline(0, color=t["ink2"], lw=1.6, zorder=2)
    for c in k32 + [hd]:
        px = [int(r["pass"]) for r in c["passes"]]
        a.plot(px, [pct(r, "peak_gov") for r in c["passes"]], "-o", color=colors[c["case"]],
               lw=3, ms=10, mec=t["surface"], mew=2.2, zorder=4 if c is hd else 3)
        k = int(c["reported"]["pass"])
        a.plot([k], [pct(c["reported"], "peak_gov")], "o", ms=22, mfc="none",
               mec=colors[c["case"]], mew=2.6, zorder=5)
    a.set_xticks(passes)
    a.set_xlim(passes[0] - 0.3, passes[-1] + 0.3)
    a.set_ylim(-8, 80)
    a.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{pm(v, 0)} %" if v else "0"))
    a.tick_params(labelsize=fs(22))
    a.set_xlabel("pass (0 = open loop; ringed: the pass reported)", fontsize=fs(22))
    a.set_ylabel("peak, % of RCC", fontsize=fs(22))
    back = dict(boxstyle="round,pad=0.25", fc=t["surface"], ec="none", alpha=0.92)
    q0, q1 = pct(hd["passes"][0], "peak_gov"), pct(hd["reported"], "peak_gov")
    a.text(passes[-1] + 0.15, 66, f"{hd['devices']} inverters, {hd['droop']:.1f} % droop:\n"
           f"{pm(q0, 1)} → {pm(q1, 2)} %, {len(hd['passes'])} simulations", fontsize=fs(24),
           color=purple, ha="right", va="top", fontweight="bold", zorder=7, bbox=back)
    lo = min(pct(c["passes"][0], "peak_gov") for c in k32)
    hi = max(pct(c["passes"][0], "peak_gov") for c in k32)
    end = max(abs(pct(c["reported"], "peak_gov")) for c in k32)
    sims = sorted({len(c["passes"]) for c in k32})
    # three short lines, clear of the high-droop line's fall from pass 1 to 2
    a.text(passes[-1] + 0.15, 40, f"{k32[0]['devices']} inverters, "
           f"{k32[0]['nodes']:,} to {k32[-1]['nodes']:,} nodes:\n"
           f"{pm(lo, 0)} to {pm(hi, 0)} % → within {end:.1f} %,\n"
           f"{' or '.join(map(str, sims))} simulations", fontsize=fs(24), color=t["ink"],
           ha="right", va="top", zorder=7, bbox=back)
    yy = y + 10 + ch_ + 92
    # what it leaves: the grid's capacitance (the figure's third panel, in words)
    first, last = k32[0], k32[-1]
    m0 = max(abs(pct(first["reported"], k)) for k in ("peak_minor_min", "peak_minor_max"))
    m1 = max(abs(pct(last["reported"], k)) for k in ("peak_minor_min", "peak_minor_max"))
    nc = max(abs(pct(c["reported"], k)) for c in k32
             for k in ("peak_minor_min_nc", "peak_minor_max_nc"))
    body = (f"Scored on the R-only grid, minor branches read within {m0:.2f} % of RCC on "
            f"{first['nodes']:,} nodes, up to {m1:.1f} % off on {last['nodes']:,}, and within "
            f"{nc:.1f} % of RCC without the grid's capacitors. Accurate, not conservative.")
    lines = wrap(fig, body, 24, CW - 2 * CPAD - 52)
    hh = 74 + 32 * len(lines)
    box(ax, CPAD, yy, CW - 2 * CPAD, hh, to_rgba(t["blue"], 0.10), t["blue"], r=14, lw=1.4)
    text(ax, CPAD + 26, yy + 20, "What it leaves: the grid's own capacitance", 28, t["ink"],
         "bold")
    for j, ln in enumerate(lines):
        text(ax, CPAD + 26, yy + 66 + 32 * j, ln, 24, t["ink2"])
    yy += hh + 24
    paragraph(ax, fig, CPAD, yy, f"On grids this small the loop costs more than RCC: "
              f"{num(hd['passes'][-1], 'total_s'):.1f} s against a "
              f"{num(hd['open'], 'rcc_sim_s'):.1f} s RCC simulation at {hd['droop']:.1f} % "
              f"droop. The pay-off on a big block is an extrapolation, not a measurement.", 23,
              CW - 2 * CPAD, t["ink2"])
    page_footer(ax, fig, theme, f"{XYCE_LABEL}: examples/06_big_block/results/"
                f"selfconsist_waveform.csv, the governing branch of meshes with 2 fF of grid "
                f"capacitance a node.")
    return fig


def carousel_pages(runs, theme="dark") -> dict:
    """{page number: figure}: the carousel's ten pages."""
    t = T(theme)
    bench, chip, pkg = runs.get("bench06"), runs.get("chip05"), runs.get("package")
    agent, scr = runs.get("agent02"), runs.get("screen06")
    total = 10
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
        lines = transcript_lines(agent, int((CW - 2 * CPAD - 48) / probe_w))
        # the plan's mode (the person's choice, shortened to the page), its
        # steps, the run's verdict, the report's verdict and fix
        keep = []
        for s_, st_ in lines:
            if s_.startswith("mode:"):
                keep.append((s_.split("  (")[0] + "  (the person's choice)", st_))
            elif not s_.startswith(("steps:", "# each step", "  Screening")):
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
                "(scripted; cc-grid as the person chose; Xyce currents, the bundled "
                "illustrative limits); long lines shortened (\u2026).")
    pages[3] = fig

    # 4 any flow
    fig, ax, y = page(4, total, theme, "How it fits",
                      "Your extraction and simulator in, one report out",
                      "The agent drives each stage with one emir command and relays the report.")
    stages = [("Extraction", "RC  ·  C-only  ·  R-only",
               "emir extract plans the Quantus / PVS run; magic runs it for SKY130"),
              ("Simulation", "Spectre  ·  HSPICE  ·  Xyce  ·  ngspice",
               "save lines, 0 V probes; each must pass emir verify-sim on your machine"),
              ("emir", "rcc  ·  cc-grid  ·  self-consistent  ·  streaming",
               "the mode is your choice; limits from a PDK file, or typed by hand"),
              ("Report", "JSON  ·  HTML  ·  chart  ·  Virtuoso  ·  KLayout",
               "emir report: what to relay, and where the limits came from")]
    rail_x = CPAD
    bx = CPAD + 150
    bw_ = CW - bx - CPAD
    sh_ = 150
    box(ax, rail_x, y, 110, len(stages) * (sh_ + 26) - 26, to_rgba(t["blue"], 0.16), t["blue"],
        r=16, lw=1.6)
    text(ax, rail_x + 55, y + (len(stages) * (sh_ + 26) - 26) / 2, "AI agent", 25, t["ink"],
         "bold", ha="center", va="center", rotation=90)
    for k, (name, chips, how) in enumerate(stages):
        yy = y + k * (sh_ + 26)
        hl = name == "emir"
        box(ax, bx, yy, bw_, sh_, to_rgba(t["blue"], 0.10) if hl else t["card"],
            t["blue"] if hl else t["edge"], r=16, lw=1.4)
        arrow(ax, rail_x + 112, yy + sh_ / 2, bx - 6, yy + sh_ / 2, t["blue"], lw=2.4, head=12)
        text(ax, bx + 26, yy + 20, f"{k + 1}  {name}", 30, t["ink"], "bold")
        text(ax, bx + 26, yy + 66, chips, fit(fig, chips, 27, bw_ - 50), t["ink"])
        text(ax, bx + 26, yy + 106, how, fit(fig, how, 21, bw_ - 50), t["ink2"])
    y += len(stages) * (sh_ + 26) + 10
    paragraph(ax, fig, CPAD, y, "Big blocks: C-only simulation + R-only grid (cc-grid).  "
              "Small blocks: RC simulation (rcc).  You choose: the agent shows the measured "
              "trade-offs and asks.", 23, CW - 2 * CPAD, t["ink2"])
    page_footer(ax, fig, theme, "Spectre, HSPICE, Quantus, PVS and Virtuoso need licences: none "
                "runs in this repository's tests, and each must be verified on site by your "
                "agent before you rely on it. Xyce, ngspice, KLayout and magic run in its "
                "examples.", screening=False)
    pages[4] = fig

    # 5 big blocks: time
    rows = k32_rows(bench)
    nodes = np.array([num(r, "grid_nodes") for r in rows])
    rcc = np.array([num(r, "rcc_sim_s") for r in rows])
    ccr = np.array([num(r, "cc_sim_s") + num(r, "cc_post_s") for r in rows])
    fig, ax, y = page(5, total, theme, "ADC top, PLL top: time",
                      "Big blocks: skip the RCC simulation",
                      f"Simulate C-only, push the device currents through the R-only grid. "
                      f"From {nodes[0]:,.0f} to {nodes[-1]:,.0f} mesh nodes the RCC simulation "
                      f"went from {rcc[0]:.2g} s to {rcc[-1]:,.0f} s; the whole CC route "
                      f"stayed under {math.ceil(max(ccr)):.0f} s.")
    ch_ = CH - y - 500
    a = axes_px(fig, CPAD + 90, y + 20, CW - 2 * CPAD - 110, ch_)
    style_axes(a, theme, grid="both")
    a.loglog(nodes, rcc, "-o", color=t["neutral"], lw=3, ms=11, mec=t["surface"], mew=2.4)
    a.loglog(nodes, ccr, "-D", color=t["blue"], lw=3, ms=10, mec=t["surface"], mew=2.4)
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    a.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    a.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g} s"))
    a.tick_params(labelsize=fs(22))
    a.set_xlabel("mesh nodes", fontsize=fs(24))
    a.set_ylim(0.2, 1200)
    a.set_xlim(90, nodes[-1] * 1.6)
    a.annotate(f"RCC simulation\n{rcc[-1]:,.0f} s", (nodes[-1], rcc[-1]), xytext=(-12, 30),
               textcoords="offset points", ha="right", fontsize=fs(24), color=t["ink"],
               fontweight="bold")
    a.annotate(f"CC route\n{ccr[-1]:.1f} s", (nodes[-1], ccr[-1]), xytext=(-12, 34),
               textcoords="offset points", ha="right", fontsize=fs(24), color=t["ink"],
               fontweight="bold")
    # the largest mesh in two numbers: the speed-up and the result file
    last = rows[-1]
    sy_ = y + 20 + ch_ + 120
    stat(ax, CPAD, sy_, f"{num(last, 'speedup_sim_vs_cc_route'):.0f}×",
         f"faster at {nodes[-1]:,.0f} nodes:\nRCC simulation against the CC route", theme)
    stat(ax, CPAD + 500, sy_,
         f"{num(last, 'rcc_raw_MB'):,.0f} → {num(last, 'cc_raw_MB'):.1f}",
         "MiB of simulation result,\nRCC against C-only", theme)
    page_footer(ax, fig, theme, f"{XYCE_LABEL}: examples/06_big_block/results/sweep.csv, the "
                f"same {int(num(rows[0], 'devices'))} inverters on a growing mesh; CC route = "
                "C-only simulation + emir "
                "cc-grid. Trends, not absolute times.")
    pages[5] = fig

    # 6 big grids in bounded memory
    sb = bench["stream"]
    big = sb[-1]
    sdiff = max(num(r, k) for r in sb for k in ("rel_diff_avg", "rel_diff_rms", "rel_diff_peak"))
    fig, ax, y = page(6, total, theme, "Big blocks: memory",
                      "Big grids stream in bounded memory",
                      f"A {int(num(big, 'nodes')):,}-node grid with "
                      f"{int(num(big, 'taps')):,} device taps would need a "
                      f"{num(big, 'dense_matrix_GiB'):.2f} GiB dense transfer matrix; EMIR "
                      f"streams it instead, with the default settings:")
    stat(ax, CPAD, y + 10, f"{num(big, 'auto_wall_s'):.1f} s", "wall time, defaults",
         theme)
    stat(ax, CPAD + 470, y + 10, f"{num(big, 'auto_peak_rss_MB'):,.0f} MiB",
         "peak memory, defaults", theme)
    y += 200
    # read in the feed at about 390 px wide: labels at 24 px and over
    a = axes_px(fig, CPAD + 370, y, CW - 2 * CPAD - 420, CH - y - 280)
    style_axes(a, theme, grid="x")
    idx = np.arange(len(sb))
    dm = np.array([num(r, "dense_peak_rss_MB") for r in sb])
    smm = np.array([num(r, "stream_peak_rss_MB") for r in sb])
    a.barh(idx - 0.18, dm, height=0.34, color=t["neutral"], label="dense (forced)")
    a.barh(idx + 0.18, smm, height=0.34, color=t["blue"], label="streamed (forced)")
    a.set_ylim(len(sb) - 0.4, -0.6)
    a.set_yticks(idx)
    a.set_yticklabels([f"{int(num(r, 'nodes')) / 1000:.1f}k nodes\n{int(num(r, 'taps')):,} taps"
                       f" \u00b7 {int(num(r, 'points')):,} samples" for r in sb],
                      fontsize=fs(24))
    a.tick_params(axis="x", labelsize=fs(24))
    a.xaxis.set_major_locator(MaxNLocator(3))
    a.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    a.set_xlabel("peak memory (MiB)", fontsize=fs(26))
    a.set_xlim(0, max(dm) * 1.35)
    for i in idx:
        a.text(dm[i] + max(dm) * 0.015, i - 0.18, f"{dm[i]:,.0f}", va="center",
               fontsize=fs(24), color=t["ink2"])
        a.text(smm[i] + max(dm) * 0.015, i + 0.18, f"{smm[i]:,.0f}", va="center",
               fontsize=fs(24), color=t["ink"], fontweight="bold")
    a.legend(loc="upper right", frameon=False, fontsize=fs(26), labelcolor=t["ink2"])
    page_footer(ax, fig, theme, f"Synthetic meshes, emir run single-threaded "
                f"(examples/06_big_block/results/stream_bench.csv). The bars force each path; "
                f"dense against streamed: the same currents to {sdiff:.0e}.")
    pages[6] = fig

    # 7 accuracy
    d = accuracy_data(bench)
    fig, ax, y = page(7, total, theme, "Big blocks: accuracy",
                      "How close is cc-grid to RCC?",
                      "cc-grid minus RCC, the branch that decides the verdict, over four mesh "
                      "sizes. Plus is conservative.")
    for key, name in (("avg", "avg"), ("rms", "rms"), ("peak", "peak"), ("ir", "IR drop"),
                      ("dyn", "dynamic IR")):
        g0, g1 = min(d[key]["gov"]), max(d[key]["gov"])
        box(ax, CPAD, y, CW - 2 * CPAD, 112, t["card"], t["edge"], r=14)
        text(ax, CPAD + 26, y + 38, name, 32, t["ink"], "bold", va="center")
        text(ax, CW - CPAD - 26, y + 38, f"{pm(g0)} to {pm(g1)} %", 36, t["ink"], "bold",
             ha="right", va="center")
        dg = 2 if d[key]["all"] and max(map(abs, d[key]["all"])) < 1 else 1
        sub = (f"every branch over 1 % of the largest: {pm(d[key]['all'][0], dg)} to "
               f"{pm(d[key]['all'][1], dg)} %" if d[key]["all"] else
               "the worst node of the net" if key == "ir" else
               "open loop, conservative on every mesh measured")
        text(ax, CPAD + 26, y + 82, sub, 22, t["ink2"], va="center")
        y += 124
    # the high-droop case of the sweep, open loop: where the error grows; page
    # 9 closes the loop on a smaller mesh at the same droop (the waveform
    # loop's table has no 256-inverter case)
    k = d["_k256"]
    box(ax, CPAD, y + 6, CW - 2 * CPAD, 154, to_rgba(t["blue"], 0.10), t["blue"], r=14, lw=1.4)
    text(ax, CPAD + 26, y + 26, f"Open loop at {k['droop']:.1f} % droop "
         f"({k['devices'] // k['devices_base']}x the devices)", 28, t["ink"], "bold")
    hi = (f"avg {pm(d['avg']['k256'])} %, rms {pm(d['rms']['k256'], 1)} %, peak "
          f"{pm(d['peak']['k256'], 1)} %: the error grows with the droop")
    text(ax, CPAD + 26, y + 72, hi, fit(fig, hi, 24, CW - 2 * CPAD - 52), t["ink"])
    hd = waveform_data()["hd16b"]
    text(ax, CPAD + 26, y + 112, f"--self-consistent on a {hd['devices']}-inverter mesh at "
         f"{hd['droop']:.1f} % droop: page 9.", 22, t["ink2"])
    y += 178
    paragraph(ax, fig, CPAD, y, "Read cc-grid avg and IR as ±2 %; confirm a fix that a "
              "secondary branch's rms or peak decides with an rcc run of that sub-block. "
              "Above about 2 % droop, report the open and closed loop side by side.", 23,
              CW - 2 * CPAD, t["ink2"])
    page_footer(ax, fig, theme, f"{XYCE_LABEL}: examples/06_big_block/results/sweep.csv "
                f"({k['devices_base']} inverters, N = {d['_n'][0]} to {d['_n'][1]}; "
                f"{k['devices']} at N = {k['N']}, the {k['droop']:.1f} % case). The guard band "
                f"is the docs' recommendation from these numbers.")
    pages[7] = fig

    # 8 chip level
    m = chip["measured"]
    hier = chip["top"]["hierarchy"]
    rows_b = {r["instance"]: r for r in hier["budget"]["rows"]}
    rm = m["pll_Rm1"]
    fig, ax, y = page(8, total, theme, "Chip level",
                      "Chip-level EM/IR, block by block",
                      "Characterise each block once, screen the top with the blocks black-boxed, "
                      "then re-check the blocks with the top's pin voltages.")
    cards = [("1  emir abstract", "Each block once: its pin currents, port conductance and "
                                  "internal IR drop, in one JSON file."),
             ("2  emir run --abstract", f"The top with the blocks black-boxed: equal to a flat run "
                                        f"of the same chip to {m['top_vs_flat_max_rel']:.0e}."),
             ("3  budgets", f"IR per block = top + inside: ADC {rows_b['XADC']['top_mV']:.2f} + "
                            f"{rows_b['XADC']['internal_mV']:.2f} = "
                            f"{rows_b['XADC']['total_mV']:.2f} mV, within "
                            f"{hier['budget']['budget_mV']:.0f} mV."),
             ("4  back-annotation", f"A PLL segment read {rm['ratio_short']:.3f}× ({rm['verdict_short']}) "
                                    f"with its pins held ideal, {rm['ratio_annotated']:.3f}× "
                                    f"({rm['verdict_annotated']}) with the top's voltages.")]
    for title, body in cards:
        lines = wrap(fig, body, 24, CW - 2 * CPAD - 52)
        hh = 74 + 32 * len(lines)
        box(ax, CPAD, y, CW - 2 * CPAD, hh, t["card"], t["edge"], r=14)
        text(ax, CPAD + 26, y + 20, title, 29, t["ink"], "bold")
        for j, ln in enumerate(lines):
            text(ax, CPAD + 26, y + 66 + 32 * j, ln, 24, t["ink2"])
        y += hh + 22
    y += 12
    text(ax, CPAD, y, f"IR budget per block, {hier['budget']['budget_mV']:.0f} mV "
         f"({hier['budget']['verdict']})", 25, t["ink"], "bold")
    y += 48
    bw_ = CW - 2 * CPAD - 170
    sc_ = bw_ / (hier["budget"]["budget_mV"] * 1.06)
    for inst, lab in (("XADC", "ADC"), ("XPLL", "PLL")):
        r = rows_b[inst]
        text(ax, CPAD, y + 16, lab, 24, t["ink"], "bold", va="center")
        x0 = CPAD + 80
        ax.add_patch(Rectangle((x0, y), r["top_mV"] * sc_, 32, fc=t["blue_deep"], ec=t["page"],
                               lw=2, zorder=3))
        ax.add_patch(Rectangle((x0 + r["top_mV"] * sc_, y), r["internal_mV"] * sc_, 32,
                               fc=t["blue"], ec=t["page"], lw=2, zorder=3))
        ax.plot([x0 + hier["budget"]["budget_mV"] * sc_] * 2, [y - 8, y + 40], color=t["ink2"],
                lw=2.4, zorder=4)
        text(ax, x0 + hier["budget"]["budget_mV"] * sc_ + 14, y + 16, f"{r['total_mV']:.2f} mV",
             22, t["ink"], "bold", va="center")
        y += 62
    for j, (col, lab) in enumerate(((t["blue_deep"], "top level"), (t["blue"], "inside the "
                                                                    "block"))):
        ax.add_patch(Rectangle((CPAD + 80 + j * 200, y + 2), 20, 20, fc=col, ec="none"))
        text(ax, CPAD + 110 + j * 200, y + 12, lab, 20, t["ink2"], va="center")
    ax.plot([CPAD + 500, CPAD + 500], [y, y + 24], color=t["ink2"], lw=2.4)
    text(ax, CPAD + 514, y + 12, "the budget", 20, t["ink2"], va="center")
    page_footer(ax, fig, theme, "examples/05_chip_level: a synthetic chip, offline, every "
                "number checked against hand calculation and a flat run.")
    pages[8] = fig

    # 9 the self-consistent loop
    pages[9] = carousel_loop(theme, total)

    # 10 call to action
    fig, ax, y = page(10, total, theme, "Try it",
                      "Point your agent at the repository",
                      "It reads AGENTS.md and starts by proving the install.")
    steps = [("emir selftest", "proves the install, offline, in seconds"),
             ("emir next --dspf ... --process ...", "the plan for what you have; you choose the mode"),
             ("emir run ... --mode ... --summary-out s.json", "EM and IR on every net"),
             ("emir report --from s.json", "what the agent tells you")]
    for k, (cmd, why) in enumerate(steps):
        box(ax, CPAD, y, CW - 2 * CPAD, 112, t["card"], t["edge"], r=14)
        text(ax, CPAD + 24, y + 56, f"{k + 1}", 40, t["blue_deep"], "bold", va="center")
        text(ax, CPAD + 90, y + 20, cmd, fit(fig, cmd, 27, CW - 2 * CPAD - 120, MONO), t["ink"],
             family=MONO)
        text(ax, CPAD + 90, y + 66, why, 23, t["ink2"])
        y += 128
    y += 14
    for lab, items_ in (("Agents", "Claude Code, Codex, any MCP client"),
                        ("Simulators", "Spectre, HSPICE, Xyce, ngspice (verify each first)"),
                        ("Limits", "pdks/ for open PDKs, or typed from your manual"),
                        ("Viewers", "chart, HTML, Virtuoso, KLayout")):
        text(ax, CPAD, y, lab, 25, t["ink"], "bold")
        text(ax, CPAD + 200, y, items_, fit(fig, items_, 25, CW - 2 * CPAD - 200), t["ink2"])
        y += 44
    y += 30
    box(ax, CPAD, y, CW - 2 * CPAD, 196, to_rgba(t["amber"], 0.10), t["amber"], r=14, lw=1.6)
    text(ax, CPAD + 26, y + 22, SCREENING, 30, t["ink"], "bold")
    paragraph(ax, fig, CPAD + 26, y + 70, "Foundry-qualified EM/IR remains the sign-off flow; "
              "EMIR says where to look first, and what it did not check. A research project, "
              "with no guarantee of accuracy: test and verify its results.", 23,
              CW - 2 * CPAD - 52, t["ink2"])
    page_footer(ax, fig, theme, "Every number in these pages comes from a run in the "
                "repository (scripts/make_figures.py).", screening=False)
    pages[10] = fig
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


@figure("scaling", needs=("bench06",), outputs=("docs/images/cc_vs_rcc_scaling.png",
                                                "docs/images/cc_vs_rcc_scaling_dark.png"),
        title="RCC against CC + cc-grid: wall time, result size, speed-up",
        source=f"examples/06_big_block/results/sweep.csv, rows k32 ({XYCE_LABEL})",
        alt="Three charts on a synthetic mesh: the RCC simulation time and its result file grow "
            "steeply with the mesh, the CC route and its result stay small, and the CC route's "
            "speed-up grows with the mesh.",
        caption=f"RCC against CC + cc-grid on a synthetic mesh ({XYCE_LABEL}).")
def fig_scaling(runs):
    bench = runs.get("bench06")
    save_variants(lambda th: draw_scaling(bench, th), "cc_vs_rcc_scaling")


@figure("streaming", needs=("bench06",), outputs=("docs/images/streaming_memory.png",
                                                  "docs/images/streaming_memory_dark.png"),
        title="Dense against streamed superposition: matrix size, peak memory, time",
        source="examples/06_big_block/results/stream_bench.csv (synthetic meshes, emir run "
               "single-threaded)",
        alt="Five synthetic grids: the dense transfer matrix of the largest exceeds the "
            "2 GiB default --max-memory; the streamed path keeps peak memory low and is "
            "faster on the large cases.",
        caption="Streamed superposition: the same currents as the dense matrix, in bounded "
                "memory (synthetic meshes).")
def fig_streaming(runs):
    bench = runs.get("bench06")
    save_variants(lambda th: draw_streaming(bench, th), "streaming_memory")


@figure("accuracy", needs=("bench06",), outputs=("docs/images/accuracy.png",
                                                 "docs/images/accuracy_dark.png"),
        title="cc-grid against RCC per metric (avg, rms, peak, IR, dynamic IR)",
        source=f"examples/06_big_block/results/sweep.csv ({XYCE_LABEL})",
        alt="Range plot of cc-grid error against RCC per metric: averages and IR within a "
            "fraction of a percent; rms and peak conservative on the governing branch but not "
            "on every branch.",
        caption=f"cc-grid against RCC, branch by branch ({XYCE_LABEL}).")
def fig_accuracy(runs):
    bench = runs.get("bench06")
    save_variants(lambda th: draw_accuracy(bench, th), "accuracy")


@figure("flow", outputs=("docs/images/flow.svg", "docs/images/flow.png",
                         "docs/images/flow_dark.svg", "docs/images/flow_dark.png"),
        title="The flow: extraction, simulation, emir, report, with the agent driving it",
        source="a diagram of the commands (no measured numbers)",
        alt="Flow diagram: an AI agent drives four stages, extraction (RC, C-only, R-only), "
            "simulation (Spectre, HSPICE, Xyce, ngspice), emir (rcc, cc-grid, streaming) and the "
            "report "
            "(JSON, HTML, Virtuoso Explorer, KLayout).",
        caption="How EMIR fits a flow, with the agent driving each stage.")
def fig_flow(runs):
    save_variants(draw_flow, "flow", svg=True)


@figure("chip_level", needs=("chip05",),
        outputs=("docs/images/chip_level.svg", "docs/images/chip_level.png",
                 "docs/images/chip_level_dark.svg", "docs/images/chip_level_dark.png"),
        title="Chip level: abstracts, the top with black boxes, budgets, back-annotation",
        source="examples/05_chip_level/run.sh (measured.json, top.summary.json)",
        alt="Chip-level diagram: two block abstracts, the top-level network with the ADC and "
            "PLL black-boxed and their pin voltages, the IR budget per block, and a PLL segment "
            "that turns from WARN to FAIL when back-annotated with the top's pin voltages.",
        caption="Chip-level EM/IR, hierarchically (examples/05_chip_level, synthetic).")
def fig_chip_level(runs):
    chip = runs.get("chip05")
    save_variants(lambda th: draw_chip_level(chip, th), "chip_level", svg=True)


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
                "SKY130 limits of pdks/sky130.yaml); the colour scale is docs/concepts.md's "
                "\"Reading the charts\".")
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
        alt="Screenshot of EMIR's per-net HTML report: verdict FAIL, the worst EM item just over "
            "its limit on a contact, the net drawn on black as a vector map coloured by each "
            "element's EM ratio with the violation outlined, its legend, coverage per metric "
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
               "--mode cc-grid --period 1e-8, then each planned step exactly as printed (simulate with Xyce, "
               "check, run), then emir report",
        alt="Terminal: emir next prints a seven-step plan, the first verification of Xyce on this machine among them; each step runs as printed; emir "
            "report prints a FAIL verdict with the fix, 1 -> 2 cuts.",
        caption="emir next's plan for examples/02_xyce, each step run exactly as printed "
                "(scripted by scripts/make_figures.py).")
def fig_transcript(runs):
    agent = runs.get("agent02")
    if agent is None:
        print("  skipped: Xyce is not on PATH; the committed transcript is kept")
        return
    with plt.rc_context(rc("dark")):
        save(draw_transcript(agent), os.path.join(DOCS_IMG, "agent_transcript.png"))


SCREEN_SOURCE = ("examples/06_big_block's N = 10 mesh (32 inverters) screened through the "
                 "waveform self-consistent loop with the invented limits of "
                 "process_screen.yaml, as its run.sh step 4 runs it (Xyce, in process; golden "
                 "values in examples/06_big_block/expected/screen_n10.json); the map is "
                 "EMIR's chart panel (emir/chart.py). Without Xyce the committed files are kept")


def _no_screen(runs, what) -> bool:
    if runs.get("screen06") is None:
        print(f"  kept as committed: {what} needs Xyce (the screened mesh of example 06)")
        return True
    return False


@figure("hero", needs=("screen06",),
        outputs=("docs/images/hero.png", "docs/images/hero_light.png"),
        title="The banner (1600 x 800): a real EM chart and the agent flow",
        source=SCREEN_SOURCE,
        alt="EMIR banner: an EM chart of a synthetic power mesh screened through cc-grid, "
            "four straps at the feed failing and the two that leave it labelled with their "
            "fixes, beside the flow agent, emir next, emir run, emir report.",
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


@figure("carousel", needs=("screen06", "bench06", "chip05", "package", "agent02"),
        outputs=tuple(f"blog/images/carousel_{k:02d}.png" for k in range(1, 11))
        + ("blog/carousel.pdf",),
        title="The LinkedIn carousel: ten 1080 x 1350 pages, and the same pages as a PDF",
        source="pages 1 and 2: " + SCREEN_SOURCE + "; the "
               "others: the runs above, the package's own counts and "
               "examples/06_big_block/results/",
        alt="Ten pages: what EMIR is, what it checks, the agent interface, the flow, big blocks "
            "(time, memory, accuracy), chip level, the self-consistent loop, how to start.",
        caption="")
def fig_carousel(runs):
    if _no_screen(runs, "the carousel (pages 1 and 2)"):
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
           "python scripts/make_figures.py --only accuracy,scaling", "```", "",
           "This file is generated by the same script (`--check` compares it).", "",
           f"Every figure is drawn with matplotlib {MATPLOTLIB} (`MATPLOTLIB` in the script;",
           "another version lays text out differently, so the script refuses it).", "",
           "Conventions: `<name>.png` is the light variant and `<name>_dark.png` the dark one",
           "(the hero banner is the exception: `hero.png` is dark, `hero_light.png` light). In",
           "Markdown that renders on GitHub, pick by the reader's theme:", "",
           "```html", "<picture>",
           '  <source media="(prefers-color-scheme: dark)" srcset="docs/images/accuracy_dark.png">',
           '  <img alt="..." src="docs/images/accuracy.png">', "</picture>", "```", "",
           f"Every figure that shows results says so: **{SCREENING}** Numbers from the Xyce sweep",
           f"carry the label \"{XYCE_LABEL}\".", ""]
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



# ----------------------------------------------------------------- self-consistency
# The cc-grid self-consistent loop against RCC, pass by pass, from the
# committed table of examples/06_big_block/selfconsist.py (Xyce, synthetic).

SELFCONSIST_CSV = os.path.join(RESULTS06, "selfconsist.csv")
#: the default --tol-mV of `emir run --self-consistent` (emir/selfconsist.py)
SC_TOL_MV = 0.1


def selfconsist_data(path=SELFCONSIST_CSV):
    """Per case and size: the split testbench's passes (pass 0 is its open
    loop) and the open loop of sweep.py's own testbench (cc.cir)."""
    rows = read_csv(path)
    cases = {}
    for r in rows:
        key = (r["case"], int(r["N"]))
        c = cases.setdefault(key, {"case": r["case"], "N": int(r["N"]),
                                   "droop": num(r, "droop_pct"), "devices": int(r["devices"]),
                                   "open": None, "passes": []})
        if r["deck"] == "cc.cir":
            c["open"] = r
        else:
            c["passes"].append(r)
    for c in cases.values():
        c["passes"].sort(key=lambda r: int(r["pass"]))
        conv = [int(r["pass"]) for r in c["passes"] if r["converged_at"]]
        c["converged_at"] = int(c["passes"][-1]["converged_at"]) if conv else None
        # the pass the loop reports: the first under the tolerance (the
        # table runs on past it, to show the loop stays put)
        c["result"] = next((r for r in c["passes"] if int(r["pass"]) == c["converged_at"]),
                           c["passes"][-1])
    return cases


def draw_selfconsist(theme, w=1600, h=1080, path=SELFCONSIST_CSV):
    """Three panels against the pass: the governing branch's average error
    (with the +-2 % guard band), its peak and rms error, and the droop change
    that stops the loop."""
    from matplotlib.ticker import FuncFormatter
    t = T(theme)
    cases = selfconsist_data(path)
    k32 = sorted((c for c in cases.values() if c["case"] == "k32"), key=lambda c: c["N"])
    k256 = next(c for c in cases.values() if c["case"] == "k256")
    shown = k32 + [k256]
    purple = HIGH_DROOP[theme]
    # k32 along EMIR's one blue ramp (emir/style.py, the charts' scale) with N,
    # towards the end that stands out on the page; k256 in the high-droop colour
    from emir import style as S
    ramp = S.EM_RAMP[4::-2] if theme == "light" else S.EM_RAMP[1::2]
    shades = [to_rgba(c) for c in ramp]
    colors = {}
    for i, c in enumerate(k32):
        f = i / max(len(k32) - 1, 1)
        a_, b_ = (shades[0], shades[1]) if f <= 0.5 else (shades[1], shades[2])
        g = f * 2 if f <= 0.5 else (f - 0.5) * 2
        colors[id(c)] = tuple(a_[j] + (b_[j] - a_[j]) * g for j in range(4))
    colors[id(k256)] = purple

    def series(c, key):
        return ([int(r["pass"]) for r in c["passes"]],
                [num(r, key) * 100 for r in c["passes"]])

    fig, ax0 = canvas(w, h, theme)
    pad = 56
    n0, n1 = k32[0]["N"], k32[-1]["N"]
    p256 = k256["passes"]
    last = k256["result"]
    y = chart_header(ax0, fig, theme, pad, 36, "Big blocks · self-consistency",
                     "Feeding the droop back: what the loop corrects, and what it does not",
                     f"emir run --mode cc-grid --self-consistent against RCC on the synthetic "
                     f"meshes ({k32[0]['devices']} inverters, N = {n0} to {n1}; "
                     f"{k256['devices']} inverters at N = {k256['N']}, {k256['droop']:.1f} % "
                     f"static droop). Each pass simulates the CC testbench again with every "
                     f"instance pin at the nominal supply plus its own average droop; pass 0 "
                     f"is the open loop. Governing branch, cc-grid minus RCC over RCC: plus is "
                     f"conservative.", w - 2 * pad)
    top = y + 76
    ph = h - top - 290
    gap = 120
    lab = 70
    avail = w - 2 * pad - 2 * gap - lab
    widths = [0.36, 0.36, 0.28]
    xs = [pad + lab]
    for k in range(1, 3):
        xs.append(xs[-1] + avail * widths[k - 1] + gap)
    pct = FuncFormatter(lambda v, _: f"{pm(v, 0)} %" if v else "0")
    passes = sorted({int(r["pass"]) for c in shown for r in c["passes"]})

    def frame(i, title, ylabel):
        a = axes_px(fig, xs[i], top, avail * widths[i], ph)
        style_axes(a, theme, grid="y")
        a.set_xticks(passes)
        a.set_xlim(passes[0] - 0.25, passes[-1] + 0.25)
        a.set_xlabel("pass (0 = open loop)", fontsize=fs(18))
        a.set_ylabel(ylabel, fontsize=fs(18))
        ax0.text(xs[i] - lab + 14, top - 30, title, fontsize=fs(21), color=t["ink"],
                 fontweight="bold")
        return a

    back = dict(boxstyle="round,pad=0.25", fc=t["surface"], ec="none", alpha=0.92)

    # (a) the average: what the feedback is about
    a = frame(0, "Average: the loop over-corrects", "avg, % of RCC")
    a.axhspan(-2, 2, color=to_rgba(t["blue"], 0.10), zorder=1)
    a.axhline(0, color=t["ink2"], lw=1.4, zorder=2)
    for c in shown:
        px, py = series(c, "avg_gov")
        a.plot(px, py, "-o", color=colors[id(c)], lw=2.4, ms=8, mec=t["surface"], mew=2,
               zorder=4 if c is k256 else 3)
    a.set_ylim(-3, 3)
    a.yaxis.set_major_formatter(pct)
    a.text(passes[-1] + 0.2, 2.0, "±2 % guard band", fontsize=fs(16), ha="right", va="bottom",
           color=t["blue_deep"] if theme == "dark" else t["blue"], fontweight="bold", zorder=7,
           bbox=back)
    v0, v1 = num(p256[0], "avg_gov") * 100, num(last, "avg_gov") * 100
    a.annotate(f"{k256['devices']} inverters: {pm(v0)} % → {pm(v1)} %",
               xy=(int(last["pass"]), v1), xytext=(passes[-1] + 0.2, -2.75), fontsize=fs(16),
               color=purple, ha="right", va="bottom", fontweight="bold", zorder=7, bbox=back)
    c70 = k32[-1]
    w0, w1 = num(c70["passes"][0], "avg_gov") * 100, num(c70["result"], "avg_gov") * 100
    a.text(passes[0] - 0.15, 0.55, f"{c70['devices']} inverters, N = {c70['N']}: "
           f"{pm(w0)} % → {pm(w1)} %", fontsize=fs(16), color=t["ink2"], ha="left",
           va="bottom", zorder=7, bbox=back)

    # (b) rms and peak: the dynamic part is not fed back
    b = frame(1, "rms and peak: most of the error stays", "% of RCC")
    b.axhline(0, color=t["ink2"], lw=1.4, zorder=2)
    for c in shown:
        for key, style in (("peak_gov", "-o"), ("rms_gov", "--s")):
            px, py = series(c, key)
            b.plot(px, py, style, color=colors[id(c)], lw=2.2, ms=7, mec=t["surface"], mew=2,
                   zorder=4 if c is k256 else 3)
    top_v = max(num(r, "peak_gov") for c in shown for r in c["passes"]) * 100
    b.set_ylim(0, math.ceil(top_v / 20) * 20 + 10)
    b.yaxis.set_major_formatter(pct)
    q0, q1 = num(p256[0], "peak_gov") * 100, num(last, "peak_gov") * 100
    r0, r1 = num(p256[0], "rms_gov") * 100, num(last, "rms_gov") * 100
    b.text(passes[-1] + 0.2, (q1 + r0) / 2, f"peak {pm(q0, 1)} → {pm(q1, 1)} %\n"
           f"rms {pm(r0, 1)} → {pm(r1, 1)} %", fontsize=fs(16), color=purple, ha="right",
           va="center", fontweight="bold", zorder=7, bbox=back)

    # (c) the droop change per pass: what stops the loop
    d = frame(2, "Droop change: what stops it", "droop change, mV")
    d.set_yscale("log")
    d.axhline(SC_TOL_MV, color=t["neutral"], lw=1.6, ls=(0, (6, 4)), zorder=2)
    d.text(passes[-1] + 0.2, SC_TOL_MV * 1.25, f"--tol-mV {SC_TOL_MV:g} (default)",
           fontsize=fs(16), color=t["ink2"], ha="right", va="bottom", zorder=7, bbox=back)
    for c in shown:
        px, py = series(c, "droop_change_mV")
        py = [v / 100 for v in py]                      # series() scaled by 100
        d.plot(px, py, "-o", color=colors[id(c)], lw=2.2, ms=7, mec=t["surface"], mew=2,
               zorder=4 if c is k256 else 3)
        if c["converged_at"] is not None:
            k = px.index(c["converged_at"])
            d.plot([px[k]], [py[k]], "o", ms=16, mfc="none", mec=colors[id(c)], mew=2.2,
                   zorder=5)
    d.set_ylim(1e-4, 200)

    # legend
    ly = top + ph + 92
    lx = pad
    items = [("line", colors[id(c)], f"N = {c['N']}") for c in k32]
    items.append(("line", purple, f"{k256['devices']} inverters, N = {k256['N']}"))
    items += [("solid", t["ink2"], "peak"), ("dashed", t["ink2"], "rms"),
              ("ring", t["ink2"], "converged (first pass under the tolerance)")]
    ax0.text(lx, ly, f"{k32[0]['devices']} inverters:", fontsize=fs(18), color=t["ink2"],
             va="center", fontweight="bold")
    lx += text_width(fig, f"{k32[0]['devices']} inverters:", 18, weight="bold") + 22
    for kind, col, label in items:
        if kind == "line":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=4, solid_capstyle="round")
            ax0.plot([lx + 17], [ly], "o", color=col, ms=9, mec=t["page"], mew=2)
        elif kind == "solid":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=2.4)
        elif kind == "dashed":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=2.4, ls="--")
        else:
            ax0.plot([lx + 17], [ly], "o", ms=16, mfc="none", mec=col, mew=2.2)
        ax0.text(lx + 46, ly, label, fontsize=fs(18), color=t["ink2"], va="center")
        lx += 46 + text_width(fig, label, 18) + 36
        if lx > w - 420 and (kind, col, label) != items[-1]:
            lx, ly = pad, ly + 38
    # pass 0 of the split testbench against sweep.py's own open loop (cc.cir)
    dev = max(abs(num(c["passes"][0], "avg_gov") - num(c["open"], "avg_gov")) * 100
              for c in cases.values() if c["open"] is not None)
    footer(ax0, fig, theme, pad, h - 104,
           f"{XYCE_LABEL}: examples/06_big_block/results/selfconsist.csv. The feedback is each "
           f"pin's average droop, by design: a resistive load lands on RCC when the block's "
           f"other rails are ideal (examples/12_self_consistent), but a switching inverter charges its output after the dip "
           f"has passed, so the average over-corrects and rms and peak keep the dynamic part. "
           f"Pass 0 is within {dev:.2f} % (avg) of the sweep's own open loop. " + SCREENING
           + " " + RESEARCH_LINE, w - 2 * pad, size=17)
    return fig


@figure("selfconsist", outputs=("docs/images/selfconsist.png",
                                "docs/images/selfconsist_dark.png"),
        title="The replaced average-droop loop against RCC, pass by pass (avg, peak, rms, "
              "droop change)",
        source=f"examples/06_big_block/results/selfconsist.csv ({XYCE_LABEL}), made by "
               f"an earlier version of examples/06_big_block/selfconsist.py, not in this "
               f"repository (the average-droop loop the waveform loop replaced, kept as its "
               f"record)",
        alt="Three charts against the pass: on the high-droop mesh the governing branch's "
            "average error crosses from conservative to optimistic, just past the 2 % guard "
            "band, and on the others it moves a few tenths of a percent down; peak and rms "
            "errors fall only a little; the droop change falls under the then-default 0.1 mV "
            "tolerance after one or two passes.",
        caption=f"What feeding the average droop back did on switching inverters "
                f"({XYCE_LABEL}): it settled in one or two passes, over-corrected the average "
                f"and left most of the rms and peak error. The waveform loop replaced it.")
def fig_selfconsist(runs):
    save_variants(draw_selfconsist, "selfconsist")


# ----------------------------------------------------------------- the waveform loop
# The waveform self-consistent loop against RCC, pass by pass, from the
# committed table of examples/06_big_block/selfconsist.py (Xyce, synthetic).

WAVEFORM_CSV = os.path.join(RESULTS06, "selfconsist_waveform.csv")
#: the cases the figure draws: the 32-inverter meshes by size, and the
#: high-droop mesh (with the companion, and with the plain update: the guard)
WAVEFORM_CASES = ("k32_n10", "k32_n20", "k32_n40", "hd16b", "hd16b_plain")


def waveform_data(path=WAVEFORM_CSV):
    """Per case (VDD): the open loop (`cc.cir`), the passes, the reported
    pass and how the loop ended."""
    out = {}
    for r in read_csv(path):
        if r["net"] != "VDD" or r["case"] not in WAVEFORM_CASES:
            continue
        c = out.setdefault(r["case"], {"case": r["case"], "N": int(r["N"]),
                                       "nodes": int(r["grid_nodes"]),
                                       "devices": int(r["devices"]),
                                       "droop": num(r, "droop_pct"), "open": None,
                                       "passes": []})
        if r["deck"] == "cc.cir":
            c["open"] = r
        else:
            c["passes"].append(r)
    for c in out.values():
        c["passes"].sort(key=lambda r: int(r["pass"]))
        last = c["passes"][-1]
        c["stopped"] = last["stopped"]
        c["reported"] = next(r for r in c["passes"] if r["pass"] == last["reported_pass"])
    return out


def draw_selfconsist_waveform(theme, w=1600, h=1080, path=WAVEFORM_CSV):
    """Three panels: the governing branch's rms and peak error against the pass,
    the residual that stops the loop (and the plain update the guard stops),
    and what remains at the reported pass: the minor branches' peak against RCC
    and against RCC without the grid's capacitors."""
    from matplotlib.ticker import FuncFormatter
    t = T(theme)
    cases = waveform_data(path)
    k32 = sorted((c for c in cases.values() if c["case"].startswith("k32")),
                 key=lambda c: c["N"])
    hd, plain = cases["hd16b"], cases["hd16b_plain"]
    purple = HIGH_DROOP[theme]
    from emir import style as S
    ramp = S.EM_RAMP[4::-2] if theme == "light" else S.EM_RAMP[1::2]
    colors = {c["case"]: to_rgba(ramp[i]) for i, c in enumerate(k32)}
    colors["hd16b"] = colors["hd16b_plain"] = purple
    shown = k32 + [hd]

    def series(c, key, scale=100):
        return ([int(r["pass"]) for r in c["passes"]],
                [num(r, key) * scale for r in c["passes"]])

    fig, ax0 = canvas(w, h, theme)
    pad = 56
    y = chart_header(ax0, fig, theme, pad, 36, "Big blocks · self-consistency",
                     "Feeding each pin its droop waveform: the device currents come right",
                     f"emir run --mode cc-grid --self-consistent against RCC on the synthetic "
                     f"meshes ({k32[0]['devices']} inverters on N = {k32[0]['N']} to "
                     f"{k32[-1]['N']} meshes; {hd['devices']} inverters at "
                     f"{hd['droop']:.1f} % droop). Pass 0 is the open loop; pass 1 feeds every "
                     f"pin the droop pass 0's currents produce (at most 15 % of the supply), "
                     f"later passes the companion solve. Error is cc-grid minus RCC over RCC: "
                     f"plus is conservative.",
                     w - 2 * pad)
    top = y + 76
    ph = h - top - 300
    gap = 118
    lab = 70
    avail = w - 2 * pad - 2 * gap - lab
    widths = [0.35, 0.33, 0.32]
    xs = [pad + lab]
    for k in range(1, 3):
        xs.append(xs[-1] + avail * widths[k - 1] + gap)
    pct = FuncFormatter(lambda v, _: f"{pm(v, 0)} %" if v else "0")
    back = dict(boxstyle="round,pad=0.25", fc=t["surface"], ec="none", alpha=0.92)

    def frame(i, title, ylabel, xlabel="pass (0 = open loop)"):
        a = axes_px(fig, xs[i], top, avail * widths[i], ph)
        style_axes(a, theme, grid="y" if i < 2 else "x")
        a.set_xlabel(xlabel, fontsize=fs(18))
        if ylabel:
            a.set_ylabel(ylabel, fontsize=fs(18))
        ax0.text(xs[i] - lab + 14, top - 30, title, fontsize=fs(21), color=t["ink"],
                 fontweight="bold")
        return a

    passes = sorted({int(r["pass"]) for c in shown for r in c["passes"]})

    # (a) rms and peak of the governing branch, pass by pass
    a = frame(0, "Governing branch: rms and peak", "% of RCC")
    a.axhline(0, color=t["ink2"], lw=1.4, zorder=2)
    for c in shown:
        for key, style in (("peak_gov", "-o"), ("rms_gov", "--s")):
            px, py = series(c, key)
            a.plot(px, py, style, color=colors[c["case"]], lw=2.2, ms=7, mec=t["surface"],
                   mew=2, zorder=4 if c is hd else 3)
    a.set_xticks(passes)
    a.set_xlim(passes[0] - 0.3, passes[-1] + 0.3)
    a.set_ylim(-10, 80)
    a.yaxis.set_major_formatter(pct)
    q0, q1 = num(hd["passes"][0], "peak_gov") * 100, num(hd["reported"], "peak_gov") * 100
    r0, r1 = num(hd["passes"][0], "rms_gov") * 100, num(hd["reported"], "rms_gov") * 100
    a.text(passes[-1] + 0.2, 40, f"{hd['devices']} inverters, {hd['droop']:.1f} % droop:\n"
           f"peak {pm(q0, 1)} → {pm(q1, 2)} %\nrms {pm(r0, 1)} → {pm(r1, 2)} %",
           fontsize=fs(16), color=purple, ha="right", va="center", fontweight="bold",
           zorder=7, bbox=back)
    c10 = k32[0]
    p0, p1 = num(c10["passes"][0], "peak_gov") * 100, num(c10["reported"], "peak_gov") * 100
    a.text(passes[-1] + 0.2, 12, f"N = {c10['N']}: peak {pm(p0, 1)} → {pm(p1, 2)} %",
           fontsize=fs(16), color=t["ink2"], ha="right", va="bottom", zorder=7, bbox=back)

    # (b) the residual that stops the loop
    b = frame(1, "Residual: what stops it", "residual, mV (log)")
    b.set_yscale("log")
    for c in shown + [plain]:
        px, py = series(c, "residual_mV", 1)
        dashed = c is plain
        b.plot(px, py, "--" if dashed else "-", color=colors[c["case"]], lw=2.2,
               marker="o", ms=7, mec=t["surface"], mew=2, zorder=4 if c is hd else 3,
               alpha=0.75 if dashed else 1.0)
        if c is not plain:
            tx, ty = series(c, "tolerance_mV", 1)
            b.plot(tx, ty, ":", color=colors[c["case"]], lw=1.6, zorder=2)
            k = int(c["reported"]["pass"])
            b.plot([k], [num(c["reported"], "residual_mV")], "o", ms=17, mfc="none",
                   mec=colors[c["case"]], mew=2.2, zorder=5)
    b.set_xticks(passes)
    b.set_xlim(passes[0] - 0.3, passes[-1] + 0.3)
    pv = [num(r, "residual_mV") for r in plain["passes"]]
    b.set_ylim(0.1, max(1000.0, 3 * max(pv)))
    b.text(passes[-1] + 0.2, 0.6 * b.get_ylim()[1],
           "plain update: " + ", ".join(f"{v:.0f}" for v in pv) + " mV;\n"
           "the guard reports pass 0", fontsize=fs(15), color=purple, ha="right",
           va="top", zorder=7, bbox=back)

    # (c) what remains: the minor branches' peak at the reported pass
    c3 = frame(2, "What remains: the grid's capacitance", None,
               xlabel="minor-branch peak at the reported pass, % of RCC")
    rows = sorted(shown, key=lambda c: c["nodes"])
    for i, c in enumerate(rows):
        r = c["reported"]
        y0 = len(rows) - 1 - i
        lo, hi = num(r, "peak_minor_min") * 100, num(r, "peak_minor_max") * 100
        nlo, nhi = num(r, "peak_minor_min_nc") * 100, num(r, "peak_minor_max_nc") * 100
        c3.plot([lo, hi], [y0 + 0.16, y0 + 0.16], color=colors[c["case"]], lw=9,
                solid_capstyle="round", zorder=3)
        c3.plot([nlo, nhi], [y0 - 0.16, y0 - 0.16], color=t["neutral"], lw=9,
                solid_capstyle="round", zorder=3)
        name = (f"{c['nodes']:,} nodes".replace(",", " ")
                + (f", {c['droop']:.1f} %" if c is hd else ""))
        c3.text(-12.5, y0 + 0.36, name, fontsize=fs(15), color=t["ink2"], ha="left",
                va="bottom")
    c3.axvline(0, color=t["ink2"], lw=1.4, zorder=2)
    c3.set_xlim(-12.5, 12.5)
    c3.set_ylim(-0.6, len(rows) - 0.1)
    c3.set_yticks([])
    c3.xaxis.set_major_formatter(pct)

    # legend
    ly = top + ph + 96
    lx = pad
    items = [("line", colors[c["case"]], f"{c['devices']} inverters, N = {c['N']}")
             for c in k32]
    items += [("line", purple, f"{hd['devices']} inverters, {hd['droop']:.1f} % droop"),
              ("solid", t["ink2"], "peak"), ("dashed", t["ink2"], "rms"),
              ("dotted", t["ink2"], "tolerance"),
              ("ring", t["ink2"], f"the pass reported (at {hd['droop']:.1f} %: {hd['stopped']})"),
              ("bar", t["neutral"], "against RCC without the grid's capacitors")]
    for kind, col, label in items:
        if kind == "line":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=4, solid_capstyle="round")
            ax0.plot([lx + 17], [ly], "o", color=col, ms=9, mec=t["page"], mew=2)
        elif kind == "solid":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=2.4)
        elif kind == "dashed":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=2.4, ls="--")
        elif kind == "dotted":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=2.0, ls=":")
        elif kind == "bar":
            ax0.plot([lx, lx + 34], [ly, ly], color=col, lw=9, solid_capstyle="round")
        else:
            ax0.plot([lx + 17], [ly], "o", ms=16, mfc="none", mec=col, mew=2.2)
        ax0.text(lx + 46, ly, label, fontsize=fs(18), color=t["ink2"], va="center")
        lx += 46 + text_width(fig, label, 18) + 34
        if lx > w - 470 and (kind, col, label) != items[-1]:
            lx, ly = pad, ly + 38
    footer(ax0, fig, theme, pad, h - 104,
           f"{XYCE_LABEL}: examples/06_big_block/results/selfconsist_waveform.csv. After pass "
           f"1 each pin's device is fitted as g + C d/dt from the last two passes and the grid "
           f"is solved with it (the companion update); where the loop gain is above 1 the plain "
           f"update diverges. What remains against RCC is the grid's own capacitance, which "
           f"these R-only DSPFs do not have: it grows with the grid. " + SCREENING + " "
           + RESEARCH_LINE, w - 2 * pad, size=17)
    return fig


@figure("selfconsist_waveform", outputs=("docs/images/selfconsist_waveform.png",
                                         "docs/images/selfconsist_waveform_dark.png"),
        title="The waveform self-consistent loop against RCC, pass by pass (rms, peak, "
              "residual, what remains)",
        source=f"examples/06_big_block/results/selfconsist_waveform.csv ({XYCE_LABEL}), made "
               f"by examples/06_big_block/selfconsist.py",
        alt="Three charts. Against the pass, the governing branch's peak and rms error fall "
            "from up to 70 % to within 1 % of RCC by pass 2 on the 32-inverter meshes, and at "
            "4.1 % droop to within 2 % from pass 3 on (0.78 % at the reported pass, 7); the "
            "residual falls under its "
            "tolerance after 4 or 5 simulations, and at 4.1 % droop levels off at about 1.3 "
            "times it, where the answer no longer moves, while the plain update grows and is "
            "stopped; at the reported pass, the minor branches' peak error widens with the "
            "grid against RCC and stays within about 1 % against RCC without the grid's "
            "capacitors.",
        caption=f"What feeding each pin its droop waveform does on switching inverters "
                f"({XYCE_LABEL}): rms and peak come to within about 1 % of RCC on the "
                f"governing branch; what remains is the grid's own capacitance.")
def fig_selfconsist_waveform(runs):
    save_variants(draw_selfconsist_waveform, "selfconsist_waveform")


if __name__ == "__main__":
    sys.exit(main())
