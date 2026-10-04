"""Every test runs with its own EMIR state directory ($EMIR_STATE_DIR): the
on-site verification records (`emir verify-sim`, ~/.emir/verified.json) of
the machine running the tests never change what a run or a plan says, and a
test never writes there."""
import pytest


@pytest.fixture(autouse=True)
def _emir_state_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("EMIR_STATE_DIR", str(tmp_path_factory.mktemp("emir_state")))
