#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
AGENT_DIR="${HERMES_AGENT_DIR:-$HERMES_HOME/hermes-agent}"
VENV="${HERMES_VENV:-$AGENT_DIR/venv}"
PY="$VENV/bin/python"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$HERMES_HOME/plugins/hindsight-lite"
DATA_DIR="$HERMES_HOME/hindsight-lite"
CONFIG_YAML="$HERMES_HOME/config.yaml"
WHEEL_NAME="hindsight_lite_termux-0.6.0a1-py3-none-any.whl"
EXPECTED_SHA="6556e2f01d9d2e7b357386bdf76a5867050d1690422b6f1f6a1becdd1f7cca1a"
TMP_DIR="$(mktemp -d)"
WHEEL="$TMP_DIR/$WHEEL_NAME"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "== Hindsight Lite for Hermes / Termux =="

if [ ! -x "$PY" ]; then
  echo "ERROR: Hermes Python venv not found: $VENV"
  echo "Set HERMES_VENV to the active Hermes venv and retry."
  exit 1
fi

PARTS=("$ROOT"/dist/hindsight_lite_termux-0.6.0a1.whl.b64.part*)
if [ "${#PARTS[@]}" -ne 7 ] || [ ! -f "${PARTS[0]}" ]; then
  echo "ERROR: release wheel chunks are missing. Re-clone the repository and retry."
  exit 1
fi

cat "${PARTS[@]}" | tr -d '\r\n' | base64 -d > "$WHEEL"
ACTUAL_SHA="$(sha256sum "$WHEEL" | awk '{print $1}')"
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
  echo "ERROR: wheel checksum mismatch."
  echo "Expected: $EXPECTED_SHA"
  echo "Actual:   $ACTUAL_SHA"
  exit 1
fi

echo "PASS  release checksum"
"$PY" -m pip install --upgrade "$WHEEL"

mkdir -p "$PLUGIN_DIR" "$DATA_DIR"
chmod 700 "$DATA_DIR"
cp "$ROOT/plugin/hindsight-lite/__init__.py" "$PLUGIN_DIR/__init__.py"
cp "$ROOT/plugin/hindsight-lite/plugin.yaml" "$PLUGIN_DIR/plugin.yaml"

if [ ! -f "$DATA_DIR/config.json" ]; then
  cp "$ROOT/config/config.example.json" "$DATA_DIR/config.json"
  chmod 600 "$DATA_DIR/config.json"
  echo "Created config: $DATA_DIR/config.json"
else
  echo "Keeping existing config: $DATA_DIR/config.json"
fi

if [ -f "$CONFIG_YAML" ]; then
  BACKUP="$CONFIG_YAML.backup-hindsight-lite-$(date +%Y%m%d-%H%M%S)"
  cp "$CONFIG_YAML" "$BACKUP"
  "$PY" "$ROOT/scripts/set_provider.py" "$CONFIG_YAML" hindsight-lite
  echo "Backed up Hermes config to: $BACKUP"
else
  echo "WARNING: $CONFIG_YAML not found."
  echo "Set this manually in the active Hermes config:"
  echo "memory:"
  echo "  provider: hindsight-lite"
fi

INSTALLED_VER="$("$PY" - <<'PY'
import hindsight_lite
print(hindsight_lite.__version__)
PY
)"
if [ "$INSTALLED_VER" != "0.6.0a1" ]; then
  echo "ERROR: installed version is '$INSTALLED_VER', expected 0.6.0a1."
  exit 1
fi

echo
echo "Installation complete: Hindsight Lite $INSTALLED_VER"
echo "Edit $DATA_DIR/config.json with your endpoint/model names."
echo "Keep real credentials in environment variables named by *_api_key_env."
echo "Restart Hermes or start a new session, then run:"
echo "  bash $ROOT/scripts/doctor.sh"
