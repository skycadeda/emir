#!/bin/sh
# Run the MCP tests (tests/test_mcp.py) against several releases of the MCP
# Python SDK, each in a throwaway virtual environment. This is how the
# supported range (`mcp>=1.19`, both the 1.x FastMCP and 2.x MCPServer lines)
# was established; run it again before moving the floor.
#
#   sh scripts/mcp_sdk_matrix.sh                  # the default versions
#   sh scripts/mcp_sdk_matrix.sh 1.19.0 2.2.0     # these
#
# Needs network access (pip) and python3 >= 3.10. Exit 1 if any version fails.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd -P)
versions=${*:-"1.19.0 1.30.0 2.0.0 2.2.0"}
work=$(mktemp -d "${TMPDIR:-/tmp}/emir-mcp-matrix.XXXXXX")
trap 'rm -rf "$work"' EXIT
status=0
for v in $versions; do
    python3 -m venv "$work/$v"
    "$work/$v/bin/pip" install -q --disable-pip-version-check "mcp==$v" numpy scipy pyyaml \
        jsonschema pytest
    if PYTHONPATH="$root" "$work/$v/bin/python" -m pytest -q -p no:cacheprovider \
            "$root/tests/test_mcp.py" > "$work/$v.log" 2>&1; then
        echo "mcp $v: $(tail -n 1 "$work/$v.log")"
    else
        echo "mcp $v: FAILED"; tail -n 20 "$work/$v.log"; status=1
    fi
done
exit $status
