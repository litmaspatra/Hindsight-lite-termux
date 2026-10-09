#!/usr/bin/env bash
# Tests run entirely in a disposable directory.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/hermes-python.sh"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export HERMES_HOME="$T/.hermes"
unset HERMES_PYTHON HERMES_VENV HERMES_AGENT_DIR || :
mkdir -p "$HERMES_HOME/hermes-agent/.hermes/bin" "$HERMES_HOME/tools/python-test/bin"
printf '#!/bin/sh\nexec %s/tools/python-test/bin/python3 -I -c test\n' "$HERMES_HOME" > "$HERMES_HOME/hermes-agent/.hermes/bin/hermes"
printf '#!/bin/sh\nexit 0\n' > "$HERMES_HOME/tools/python-test/bin/python3"
chmod +x "$HERMES_HOME/tools/python-test/bin/python3"
resolve_hermes_python
test "$HINDSIGHT_HERMES_PYTHON" = "$HERMES_HOME/tools/python-test/bin/python3"
echo "PASS standalone runtime"
mkdir -p "$HERMES_HOME/hermes-agent/venv/bin"
printf '#!/bin/sh\nexit 0\n' > "$HERMES_HOME/hermes-agent/venv/bin/python"
chmod +x "$HERMES_HOME/hermes-agent/venv/bin/python"
resolve_hermes_python
test "$HINDSIGHT_HERMES_PYTHON" = "$HERMES_HOME/hermes-agent/venv/bin/python"
echo "PASS legacy venv"
HERMES_PYTHON="$T/absent"
export HERMES_PYTHON
if resolve_hermes_python 2>/dev/null; then echo "FAIL invalid override"; exit 1; fi
echo "PASS invalid override fails closed"
