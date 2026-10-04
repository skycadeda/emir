"""`emir import-ict`: the expression evaluator is a whitelist, curated via
rms/peak survive, the block-name pattern is configurable, and the generated
file names the ICT without its directory.

The ICT here is invented and written to a temporary file at run time (the
leak guard refuses `*.ict` files in the tree).
"""
import pytest
import yaml
from _helpers import emir, write

from emir.pdk_import import _eval, ict_name, merge, parse_ict

FAKE_ICT = """process "DEMO" {
    temp_reference 50.0
    em_tref 100.0
}
conductor "%(m1)s" {
    temp_tc1 0.003
    em_jmax_dc_avg EQU 1 * 2.0 * ( w - 0.01 ) jmax_factor 50 4.0 100 1.0 L > 20
    em_jmax_dc_peak EQU 10.0 * w
    em_jmax_ac_rms EQU sqrt( 9.0 * delta_T * w * ( w + 1.0 ) )
}
via "cont" {
    em_vcwidth 0.1
    em_jmax_dc_avg EQU 0.2
}
via "via1" {
    em_jmax_dc_avg EQU 0.4
}
"""

EMIR_YAML = {
    "pdk": "demo",
    "layers": {"Metal1": {"gds": [101, 0], "sheet_res": 0.1}},
    "vias": {
        # curated by hand: must survive the import
        "Cont": {"gds": [100, 0], "below": "Metal1", "above": "Metal1", "res": 10.0,
                 "imax_rms": 0.5, "imax_peak": 0.3},
        # an earlier import's estimate: removed (nothing is estimated now)
        "Via1": {"gds": [102, 0], "below": "Metal1", "above": "Metal1", "res": 5.0,
                 "imax_avg": 9.0, "imax_rms": 45.0, "imax_peak": 45.0,
                 "rms_peak_estimate": True},
    },
}


def _ict(tmp_path, m1="metal1_conn"):
    return write(tmp_path / "demo.ict", FAKE_ICT % {"m1": m1})


def test_eval_handles_the_ict_shapes():
    assert abs(_eval("1 * 2.0 * ( w - 0.01 )", 1e6) / 1e6 - 2.0) < 1e-6
    assert _eval("10.0 * w", 2.0) == 20.0
    assert abs(_eval("sqrt( 9.0 * delta_T * w * ( w + 1.0 ) )", 1.0, 4.0) - 6 * 2 ** 0.5) < 1e-12
    assert _eval("2^3", 0.0) == 8.0
    assert _eval("-w + +2", 1.0) == 1.0
    assert abs(_eval("sqrt( 9.0 * delta_T * (w-0.1)^2 * (w-0.1+2)/(w-0.1+3) )", 2.1, 5.0)
               - (45 * 4 * 4 / 5) ** 0.5) < 1e-12


@pytest.mark.parametrize("bad", [
    "__import__('os').system('true')", "().__class__", "w.real", "open('x')",
    "[1][0]", "lambda: 1", "x + 1", "'a' * 3", "True + 1",
    # only sqrt, with exactly one positional argument
    "max(1, 2)", "min(w, 2)", "exp(1)", "log(10)", "log10(10)", "abs(-1)", "pow(2, 3)",
    "sqrt(1, 2)", "sqrt()", "sqrt(x=1)", "sqrt(*[4])", "math.sqrt(4)",
    # not a finite number
    "sqrt(-1)", "1 / 0", "9.0 ** 9.0 ** 9.0", "1e308 * 10",
    # a fractional power of a negative number is complex, not a limit
    "( 0.5 - w ) ^ 0.5", "(-w) ** 0.5",
    "1 + " * 400 + "1",                       # longer than any ICT limit line
])
def test_eval_refuses_anything_else(bad):
    assert _eval(bad, 1.0) is None, bad


def test_sandbox_escape_attempt_has_no_side_effect(tmp_path):
    """The object-subclass walk ran os.system under the old eval(); the AST
    walk refuses it before anything is evaluated."""
    marker = tmp_path / "ESCAPED"
    attempt = ("[c for c in ().__class__.__base__.__subclasses__() "
               "if c.__name__ == 'catch_warnings'][0]()._module.__builtins__"
               f"['open']({str(marker)!r}, 'w').write('x')")
    assert _eval(attempt, 1.0) is None
    assert not marker.exists()
    ict = write(tmp_path / "evil.ict", FAKE_ICT.replace(
        "em_jmax_dc_avg EQU 0.4", "em_jmax_dc_avg EQU " + attempt) % {"m1": "metal1_conn"})
    data, log = merge(yaml.safe_load(yaml.safe_dump(EMIR_YAML)), parse_ict(ict))
    assert not marker.exists()
    assert data["vias"]["Via1"]["imax_avg"] == 9.0               # kept, not nulled
    assert any(line.startswith("[emir] WARNING EMIR-W") and "via1" in line for line in log)


def test_curated_via_rms_peak_are_kept(tmp_path):
    ict = parse_ict(_ict(tmp_path))
    data, log = merge(yaml.safe_load(yaml.safe_dump(EMIR_YAML)), ict)
    cont, via1 = data["vias"]["Cont"], data["vias"]["Via1"]
    assert cont["imax_avg"] == 0.2 and cont["vcwidth"] == 0.1
    assert (cont["imax_rms"], cont["imax_peak"]) == (0.5, 0.3)   # used to become 1.0/1.0
    assert not cont.get("rms_peak_estimate")
    # no limit is invented: the old 5x estimates are gone, rms/peak not checked
    assert via1["imax_avg"] == 0.4 and "imax_rms" not in via1 and "imax_peak" not in via1
    assert "rms_peak_estimate" not in via1
    assert any("EMIR-W313" in line and "NOT checked" in line for line in log)
    assert any("curated" in line and "Cont" in line for line in log)
    assert data["layers"]["Metal1"]["jmax_avg"] == 2.0


def test_conductor_block_name_is_configurable(tmp_path):
    ict = parse_ict(_ict(tmp_path, m1="M1_em"))
    assert ict_name("Metal1") == "metal1_conn"
    # not found under the default pattern: left as-is, with the way out named
    data, log = merge(yaml.safe_load(yaml.safe_dump(EMIR_YAML)), ict)
    assert "jmax_avg" not in data["layers"]["Metal1"]
    assert any("ict_name" in line for line in log)
    # explicit name on the layer
    y = yaml.safe_load(yaml.safe_dump(EMIR_YAML))
    y["layers"]["Metal1"]["ict_name"] = "M1_em"
    assert merge(y, ict)[0]["layers"]["Metal1"]["jmax_avg"] == 2.0
    # a pattern, from the file or the command line
    y = yaml.safe_load(yaml.safe_dump(EMIR_YAML))
    y["layers"] = {"M1": y["layers"]["Metal1"]}
    y["ict_conductor_pattern"] = "{Layer}_em"
    assert merge(y, ict)[0]["layers"]["M1"]["jmax_avg"] == 2.0


def test_cli_writes_only_the_ict_file_name(tmp_path):
    ict = _ict(tmp_path, m1="metal1_em")
    y = write(tmp_path / "emir.yaml", yaml.safe_dump(EMIR_YAML))
    out = tmp_path / "out.yaml"
    p = emir("import-ict", "--ict", ict, "--emir", y, "-o", out,
             "--conductor-pattern", "{layer}_em")
    assert p.returncode == 0, p.stdout + p.stderr
    text = out.read_text()
    assert "demo.ict" in text and str(tmp_path) not in text
    assert yaml.safe_load(text)["layers"]["Metal1"]["jmax_avg"] == 2.0


# ------------------------------------------------------------ nothing nulled


def test_refused_via_expression_keeps_the_existing_limit(tmp_path):
    """A refused expression used to overwrite imax_avg with null, silently,
    and `emir check` then said INPUTS OK with the via unchecked."""
    ict = parse_ict(write(tmp_path / "d.ict", FAKE_ICT.replace(
        "em_jmax_dc_avg EQU 0.4", "em_jmax_dc_avg EQU 0.3 * log10(10)") % {"m1": "metal1_conn"}))
    assert ict["vias"]["via1"]["imax_avg"] is None
    assert ict["refused"] == [("via1", "imax_avg", "0.3 * log10(10)")]
    y = yaml.safe_load(yaml.safe_dump(EMIR_YAML))
    y["vias"]["Via1"].update(imax_avg=1.0, rms_peak_estimate=False)
    data, log = merge(y, ict)
    assert data["vias"]["Via1"]["imax_avg"] == 1.0 and data["vias"]["Via1"]["estimate"]
    warn = [line for line in log if line.startswith("[emir] WARNING EMIR-W")]
    assert any("'via1'" in w and "refused" in w for w in warn), log
    assert any("Via1: kept imax_avg 1.0" in w for w in warn), log


def test_refused_conductor_expression_keeps_the_hand_value(tmp_path):
    ict = parse_ict(write(tmp_path / "d.ict", FAKE_ICT.replace(
        "em_jmax_dc_peak EQU 10.0 * w", "em_jmax_dc_peak EQU exp(w)") % {"m1": "metal1_conn"}))
    y = yaml.safe_load(yaml.safe_dump(EMIR_YAML))
    y["layers"]["Metal1"]["jmax_peak"] = 7.5
    data, log = merge(y, ict)
    assert data["layers"]["Metal1"]["jmax_peak"] == 7.5
    assert data["layers"]["Metal1"]["estimate"]
    assert any(line.startswith("[emir] WARNING EMIR-W") and "jmax_peak" in line for line in log)


def test_missing_ict_temperatures_keep_the_files(tmp_path):
    no_temps = FAKE_ICT.replace("    temp_reference 50.0\n    em_tref 100.0\n", "")
    ict = parse_ict(write(tmp_path / "d.ict", no_temps % {"m1": "metal1_conn"}))
    assert ict["em_tref"] is None and ict["temp_reference"] is None   # no hidden default
    y = yaml.safe_load(yaml.safe_dump(EMIR_YAML))
    y["em_tref"] = 85.0
    data, log = merge(y, ict)
    assert data["em_tref"] == 85.0 and "temp_reference" not in data
    assert "[emir] WARNING EMIR-W309: ICT has no em_tref: kept 85.0 from the file" in log
    assert any("no temp_reference and the file has none" in line for line in log)


def test_cli_warns_on_stderr_and_keeps_the_value(tmp_path):
    ict = write(tmp_path / "d.ict", FAKE_ICT.replace(
        "em_jmax_dc_avg EQU 0.4", "em_jmax_dc_avg EQU 0.3 * log10(10)") % {"m1": "metal1_conn"})
    y = yaml.safe_load(yaml.safe_dump(EMIR_YAML))
    y["vias"]["Via1"].update(imax_avg=1.0, rms_peak_estimate=False)
    src = write(tmp_path / "emir.yaml", yaml.safe_dump(y))
    out = tmp_path / "out.yaml"
    p = emir("import-ict", "--ict", ict, "--emir", src, "-o", out)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "WARNING EMIR-W310" in p.stderr and "refused" in p.stderr
    assert yaml.safe_load(out.read_text())["vias"]["Via1"]["imax_avg"] == 1.0


def test_cli_refuses_a_complex_valued_expression(tmp_path):
    """`( 0.5 - w ) ^ 0.5` evaluated at the wide-wire probe is complex: the
    import crashed with an internal TypeError (exit 1) instead of refusing
    the expression with a WARNING and keeping the file's value."""
    ict = write(tmp_path / "c.ict", FAKE_ICT.replace(
        "em_jmax_dc_avg EQU 0.4", "em_jmax_dc_avg EQU ( 0.5 - w ) ^ 0.5") % {"m1": "metal1_conn"})
    y = yaml.safe_load(yaml.safe_dump(EMIR_YAML))
    y["vias"]["Via1"].update(imax_avg=1.0, rms_peak_estimate=False)
    src = write(tmp_path / "emir.yaml", yaml.safe_dump(y))
    out = tmp_path / "out.yaml"
    p = emir("import-ict", "--ict", ict, "--emir", src, "-o", out)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "internal error" not in p.stderr and "refused" in p.stderr
    assert yaml.safe_load(out.read_text())["vias"]["Via1"]["imax_avg"] == 1.0
