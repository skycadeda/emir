#!/bin/sh
# EMIR over MCP, offline: a scripted MCP client starts `emir mcp` over stdio,
# follows the plan emir_next makes for the bundled sample, and shows the
# go-ahead rule; then the results are compared with expected/.
#
#   sh examples/10_mcp_agent/run.sh [OUT_DIR]      (default: ./out beside this file)
#
# Needs a Python with emir's dependencies and the MCP SDK, e.g. a clone with
# `pip install -e '.[mcp]'` in its venv ($EMIR_PYTHON picks the interpreter).
set -eu
here=$(cd "$(dirname "$0")" && pwd -P)
root=$(cd "$here/../.." && pwd -P)
py=${EMIR_PYTHON:-python3}
out=${1:-$here/out}
# the mcp extra, checked first: a clean message and exit 3 (a missing
# package), as emir itself says it, not a traceback
if ! "$py" -c 'import mcp, anyio' 2> /dev/null; then
    echo "example 10: needs the mcp extra: pip install -e '.[mcp]' from the clone root" >&2
    exit 3
fi
mkdir -p "$out"
PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH
"$py" "$here/client.py" --out "$out"
"$py" "$here/compare.py" "$out" "$here/expected"
