"""The pieces EMIR's HTML pages share: the stylesheet (light and dark, the
page following the OS setting unless its toggle overrides it), the headline
tiles, the ratio bars and the legend of the colour scale (`legend_html`).
`emir.netreport` builds the per-net pages of `--html` and their index from
them.

Tables are rendered server-side so every number the colour scale encodes is
also readable without JavaScript and without colour vision -- the required
twin of a continuous colour encoding.

`legend_html` is ported in part from Skyforge. Skyforge is Copyright (c) 2026 Skycadeda, licensed under the MIT License by its copyright
holder (THIRD_PARTY_NOTICES.md).
"""
from __future__ import annotations

import html

# `style` (the colour scale, the legend, the numbers) imports no plotting
# library: `netreport` imports this module's CSS and helpers, and `--html`
# must work without matplotlib. The PNG charts are drawn by `emir.chart`.

CSS = """
:root{color-scheme:light dark;
 --page:#f9f9f7;--surface:#fcfcfb;--panel:#f0efec;--ink:#0b0b0b;--ink2:#52514e;
 --muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--accent:#2a78d6;
 --ring:rgba(11,11,11,.10);--shadow:0 1px 2px rgba(11,11,11,.06);
 --good:#1f9a5a;--warn:#fab219;--fail:#d03b3b;--warn-ink:#241a00;
 --fail-tint:rgba(208,59,59,.10);--warn-tint:rgba(250,178,25,.14);}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --page:#0d0d0d;--surface:#1a1a19;--panel:#232320;--ink:#ffffff;--ink2:#c3c2b7;
 --muted:#898781;--grid:#2c2c2a;--axis:#383835;--accent:#3987e5;
 --ring:rgba(255,255,255,.10);--shadow:none;
 --fail-tint:rgba(208,59,59,.16);--warn-tint:rgba(250,178,25,.14);}}
:root[data-theme="dark"]{
 --page:#0d0d0d;--surface:#1a1a19;--panel:#232320;--ink:#ffffff;--ink2:#c3c2b7;
 --muted:#898781;--grid:#2c2c2a;--axis:#383835;--accent:#3987e5;
 --ring:rgba(255,255,255,.10);--shadow:none;
 --fail-tint:rgba(208,59,59,.16);--warn-tint:rgba(250,178,25,.14);}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
 font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;}
a{color:var(--accent)}
.wrap{max-width:1500px;margin:0 auto;padding:0 24px 56px}
header.top{position:sticky;top:0;z-index:20;background:var(--panel);
 border-bottom:1px solid var(--ring);}
.topin{max-width:1500px;margin:0 auto;padding:14px 24px;display:flex;
 align-items:center;gap:16px;flex-wrap:wrap}
.brand{font-size:11px;letter-spacing:.14em;font-weight:700;color:var(--muted)}
.cell{font-size:19px;font-weight:700;letter-spacing:-.01em}
.sub{color:var(--ink2);font-size:12.5px}
.spacer{flex:1}
.chip{display:inline-flex;align-items:center;gap:6px;border-radius:999px;
 padding:4px 12px;font-size:12px;font-weight:700;letter-spacing:.03em}
.chip.FAIL{background:var(--fail);color:#fff}
.chip.WARN{background:var(--warn);color:var(--warn-ink)}
.chip.CLEAN{background:var(--good);color:#fff}
.chip.ghost{background:transparent;color:var(--ink2);border:1px solid var(--axis);
 font-weight:600}
button.tgl{background:var(--surface);color:var(--ink2);border:1px solid var(--axis);
 border-radius:8px;padding:6px 12px;font:inherit;font-size:12.5px;cursor:pointer}
button.tgl:hover{border-color:var(--accent);color:var(--ink)}
.tiles{display:grid;gap:12px;margin:22px 0 8px;
 grid-template-columns:repeat(auto-fit,minmax(168px,1fr))}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:12px;
 padding:14px 16px;box-shadow:var(--shadow)}
.tile .k{font-size:11px;letter-spacing:.08em;color:var(--muted);font-weight:700;
 text-transform:uppercase}
.tile .v{font-size:27px;font-weight:700;letter-spacing:-.02em;margin-top:4px}
.tile .n{font-size:12px;color:var(--ink2);margin-top:2px}
.tile.f .v{color:var(--fail)} .tile.w .v{color:var(--warn)} .tile.g .v{color:var(--good)}
h2{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
 margin:34px 0 12px;font-weight:700}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:7px;
 vertical-align:middle}
.dot.FAIL{background:var(--fail)} .dot.WARN{background:var(--warn)}
.dot.CLEAN{background:var(--good);opacity:.45}
.tblwrap{overflow-x:auto;background:var(--surface);border:1px solid var(--ring);
 border-radius:12px;box-shadow:var(--shadow)}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;font-size:11px;letter-spacing:.07em;text-transform:uppercase;
 color:var(--muted);font-weight:700;padding:10px 14px;border-bottom:1px solid var(--grid);
 white-space:nowrap}
th .u{text-transform:none;letter-spacing:.02em}
td{padding:9px 14px;border-bottom:1px solid var(--grid);
 font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:0}
tbody tr.clickable{cursor:pointer}
tbody tr.clickable:hover{background:var(--panel)}
td.num,th.num{text-align:right}
.sev{display:inline-flex;align-items:center;gap:6px;font-weight:700;font-size:11.5px;
 letter-spacing:.04em;border-radius:6px;padding:2px 8px}
.sev.FAIL{background:var(--fail-tint);color:var(--fail)}
.sev.WARN{background:var(--warn-tint);color:#8a6000}
:root[data-theme="dark"] .sev.WARN{color:var(--warn)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .sev.WARN{color:var(--warn)}}
.bar{position:relative;height:6px;border-radius:3px;background:var(--grid);
 min-width:78px;display:inline-block;vertical-align:middle;margin-left:9px}
.bar i{position:absolute;inset:0 auto 0 0;border-radius:3px;display:block}
.bar i.FAIL{background:var(--fail)} .bar i.WARN{background:var(--warn)}
.bar.over::after{content:"\\203A";position:absolute;right:-11px;top:-8px;
 color:var(--fail);font-weight:700;font-size:14px;line-height:1}
.bar u{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--surface);
 box-shadow:0 0 0 1px var(--ink2)}
footer.foot{margin-top:40px;padding-top:18px;border-top:1px solid var(--grid);
 color:var(--muted);font-size:12px}
footer.foot li{margin-bottom:6px}
footer.foot code{background:var(--panel);padding:3px 8px;border-radius:5px;
 font-size:11.5px;color:var(--ink2);overflow-wrap:anywhere;
 font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.legend{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0 0;font-size:12px;
 color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:26px;height:9px;border-radius:2px;display:inline-block}
.emleg{margin-top:10px;font-size:12px;color:var(--ink2);max-width:640px}
.emleg[hidden]{display:none}
.ebar{height:12px;border-radius:2px;margin:6px 0 2px;border:1px solid var(--axis)}
.eticks{position:relative;height:16px;font-size:11.5px;color:var(--muted)}
.eticks span{position:absolute;top:0;white-space:nowrap}
.sw.ring{background:transparent;border:2px solid var(--ink);border-radius:50%;width:11px;
 height:11px}
"""


def _e(s) -> str:
    return html.escape(str(s), quote=True)


def _tile(k, v, note="", cls=""):
    return (f'<div class="tile {cls}"><div class="k">{_e(k)}</div>'
            f'<div class="v">{_e(v)}</div>'
            + (f'<div class="n">{_e(note)}</div>' if note else "") + "</div>")


#: the bar's full width is twice the limit — the band a designer actually
#: reads. Anything past it is off-scale and gets the overflow arrow.
BAR_FULL = 2.0


def _bar(ratio, sev):
    frac = min(max(ratio / BAR_FULL, 0.0), 1.0)
    over = ' over' if ratio > BAR_FULL else ''
    return (f'<span class="bar{over}" title="full width = {BAR_FULL:g}× the '
            f'limit; the tick is 1.00×">'
            f'<i class="{sev}" style="width:{frac * 100:.1f}%"></i>'
            f'<u style="left:{100 / BAR_FULL:.1f}%"></u></span>')


def legend_html(mode: str, warn: float, ir_top: float = 0.0, *, unchecked: bool = False,
                lost: bool = False, markers: bool = True, rings: bool = True,
                extra: str = "", model: bool = False) -> str:
    """A legend in HTML (`emir.style.legend`, the scale of every chart): the
    ramp as a CSS gradient with its ticks, and every other colour named."""
    from . import style as S
    lg = S.legend(mode, warn, ir_max=ir_top, unchecked=unchecked, lost=lost,
                  markers=markers, rings=rings, model=model)
    ticks = "".join(
        f'<span style="left:{100 * pos:.1f}%;transform:translateX('
        f'{0 if pos <= 0.001 else -100 if pos >= 0.999 else -50}%)">{_e(lab)}</span>'
        for pos, lab in lg["bar"]["ticks"])
    keys = []
    for kind, colour, text in lg["keys"]:
        sw = ('<i class="sw ring"></i>' if kind == "ring" else
              f'<i class="sw" style="background:transparent;outline:2px solid {colour};'
              f'outline-offset:-2px"></i>' if kind == "outline" else
              f'<i class="sw" style="background:transparent;outline:2px dotted {colour};'
              f'outline-offset:-2px"></i>' if kind == "dotted" else
              f'<i class="sw" style="background:{colour}"></i>')
        keys.append(f"<span>{sw} {_e(text)}</span>")
    return (f'<div class="emleg" data-for="{mode}"><div><b>{_e(lg["title"])}</b> '
            f'<span class="u">{_e(lg["sub"])}</span></div>'
            f'<div class="ebar" style="background:{S.css_gradient()}"></div>'
            f'<div class="eticks">{ticks}</div><div class="legend">{"".join(keys)}'
            f'{extra}</div></div>')
