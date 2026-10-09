#!/usr/bin/env bash
# Stage Hindsight Lite outside Hermes; no installation or activation.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/hermes-python.sh"
resolve_hermes_python
PY="$HINDSIGHT_HERMES_PYTHON"
OUT="${HINDSIGHT_STAGE_DIR:-$ROOT/.compat-stage}"
case "$OUT/" in "$HOME/.hermes/"*|*/hermes-agent/*) echo "ERROR: stage must be outside Hermes" >&2; exit 1;; esac
if [ -e "$OUT" ]; then echo "ERROR: stage already exists: $OUT (refusing overwrite)" >&2; exit 1; fi
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/hindsight_lite" "$TMP/plugin/hindsight-lite"
cp -a "$ROOT/hindsight_lite/." "$TMP/hindsight_lite/"
cp "$ROOT/plugin/hindsight-lite/__init__.py" "$ROOT/plugin/hindsight-lite/plugin.yaml" "$TMP/plugin/hindsight-lite/"
find "$TMP" -type d -name __pycache__ -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 "$PY" -I - "$TMP" "${HERMES_AGENT_DIR:-${HERMES_HOME:-$HOME/.hermes}/hermes-agent}" <<'PY'
import sys, importlib.util
from pathlib import Path
root, agent = map(Path, sys.argv[1:3])
sys.path.insert(0, str(agent))
sys.path.insert(0, str(root))
from agent.memory_provider import MemoryProvider
import hindsight_lite, hindsight_lite.hermes
spec = importlib.util.spec_from_file_location("hindsight_compat_adapter", root / "plugin/hindsight-lite/__init__.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
assert isinstance(mod.HindsightLiteProvider(), MemoryProvider)
print("PASS isolated staged adapter:", hindsight_lite.__version__)
PY
if [ -e "$OUT" ]; then echo "ERROR: stage path became occupied" >&2; exit 1; fi
mv "$TMP" "$OUT"
trap - EXIT
echo "PASS staged outside Hermes: $OUT"
echo "NOT activated; no Hermes files changed."
