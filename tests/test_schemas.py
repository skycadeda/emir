"""The JSON Schemas (`emir/schemas.py`, copies in `agent/schemas/`): valid
draft 2020-12, the committed copies are current, and the process files the
repo ships validate against `process.schema.json`. (Every command's real
output is validated in `tests/test_output_contract.py`.)"""
import glob
import json
import os
import subprocess
import sys

import jsonschema
import pytest
import yaml
from _helpers import PROC, ROOT

from emir import schemas

V = jsonschema.Draft202012Validator

#: the schemas of inputs (no `schema` envelope): the process file, the
#: --currents JSON and the `<result>.names` sidecar
INPUTS = {"process", "currents", "names"}


@pytest.mark.parametrize("name", schemas.names())
def test_each_schema_is_valid_2020_12(name):
    s = schemas.get(name)
    V.check_schema(s)
    assert s["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert s["$id"] == f"urn:emir:schema:{name}:1" and s["title"] and s["description"]
    if name not in INPUTS:
        assert s["properties"]["schema"] == {"const": f"emir.{name}/1"}
        assert {"schema", "emir_version"} <= set(s["required"])


def test_every_document_the_cli_prints_has_a_schema():
    expected = {"info", "selftest", "validate", "nets", "pins", "save_lines", "check",
                "run", "import_ict", "gui_install", "explain", "schema_list", "next",
                "report", "error", "help", "extract", "simulate",
                # the on-site verification: what `emir verify-sim` prints, and
                # the records it keeps (verified.json)
                "verify_sim", "verified",
                # process data entered by hand: `emir process new` and `check`
                "process_new", "process_check",
                "mcp"} | INPUTS                         # `emir mcp --list`
    assert set(schemas.names()) == expected


def test_committed_schemas_are_current():
    for name in schemas.names():
        path = os.path.join(ROOT, "agent", "schemas", f"{name}.schema.json")
        assert open(path, encoding="utf-8").read() == schemas.text(name), \
            f"{path} is stale: run python scripts/gen_schemas.py"
    files = sorted(os.path.basename(p) for p in
                   glob.glob(os.path.join(ROOT, "agent", "schemas", "*.json")))
    assert files == sorted(f"{n}.schema.json" for n in schemas.names())
    p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "gen_schemas.py"),
                        "--check"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def _as_json(path):
    """A YAML process file as JSON sees it (integer keys become strings)."""
    with open(path, encoding="utf-8") as f:
        return json.loads(json.dumps(yaml.safe_load(f)))


@pytest.mark.parametrize("path", [PROC] + sorted(glob.glob(os.path.join(ROOT, "pdks", "*.yaml"))),
                         ids=lambda p: os.path.relpath(p, ROOT))
def test_shipped_process_files_validate(path):
    V(schemas.get("process")).validate(_as_json(path))


@pytest.mark.parametrize("edit,where", [
    (lambda d: d["layers"]["Metal1"].update(jmax_avgg=3.2), "jmax_avgg"),       # a typo
    (lambda d: d.pop("em_tref"), "em_tref"),
    (lambda d: d["layers"]["Metal1"].update(sheet_res=-1), "sheet_res"),
    (lambda d: d["layers"]["Metal1"].update(gds=[101]), "gds"),
    (lambda d: d["layers"]["Metal1"].update(jmax_rms=12.0), "not"),            # both rms forms
    (lambda d: d["vias"]["Cont"].pop("res"), "res"),
    (lambda d: d.update(warn_fraction=1.5), "warn_fraction"),
])
def test_process_schema_rejects_what_the_checker_rejects(edit, where):
    d = _as_json(PROC)
    edit(d)
    errs = list(V(schemas.get("process")).iter_errors(d))
    assert errs and any(where in (str(e.path) + e.message + str(e.schema_path))
                        for e in errs), [e.message for e in errs]
