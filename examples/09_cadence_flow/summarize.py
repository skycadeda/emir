"""Digest of example 09's outputs: the machine-independent facts the test
compares with expected/summary.json (paths shortened to their names, tool
paths left out: they depend on the machine). Standard library only."""
import json
import os
import re
import sys


def load(out, name):
    with open(os.path.join(out, name), encoding="utf-8") as f:
        return json.load(f)


def plan_digest(d):
    ccl = d["ccl"] or ""
    body = [ln for ln in ccl.splitlines() if ln.strip() and not ln.startswith("#")]
    return {
        "schema": d["schema"], "dry_run": d["dry_run"], "kind": d["kind"],
        "dspf": os.path.basename(d["dspf"]),
        "missing_settings": sorted(m["what"] for m in d["missing"]
                                   if m["what"] not in ("pvs", "qrc")),
        "settings_from": {k: (os.path.basename(v["source"]) if v["source"].startswith("/")
                              else v["source"]) for k, v in d["settings"].items()},
        "temperature": d["settings"]["extract_temperature"]["value"],
        "steps": [s["id"] for s in d["steps"]],
        "licensed": sorted({s["licensed_tool"] for s in d["steps"] if s["licensed_tool"]}),
        "ccl_command_lines": len(body),
        "load_bearing_in_ccl": all(flag in ccl for flag in d["load_bearing"]),
        "ccl_type": (re.search(r'-type "([a-z_]+)"', ccl) or [None, None])[1],
    }


def main(out):
    s = load(out, "save_lines.json")
    with open(os.path.join(out, "tb", "tb_rcc.scs"), encoding="utf-8") as f:
        tb = f.read()
    digest = {
        "extract_unconfigured": plan_digest(load(out, "plan_unconfigured.json")),
        "extract_r_only": plan_digest(load(out, "plan_r_only.json")),
        "extract_c_only_coupled": plan_digest(load(out, "plan_c_only_coupled.json")),
        "save_lines": {"schema": s["schema"], "mode": s["mode"], "count": s["count"], "first_words": s["lines"][0].split()[:3],
                       "file_lines": len(open(s["out"], encoding="utf-8").read().splitlines())},
        "testbench": {"unfilled_fields": len(re.findall(r"\$\{[A-Z_]+\}", tb)),
                      "includes_saves": 'include "emir_save.scs"' in tb,
                      "instance": next(ln for ln in tb.splitlines() if ln.startswith("XDUT"))},
    }
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(digest, f, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
