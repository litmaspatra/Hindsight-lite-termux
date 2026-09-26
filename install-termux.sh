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
EXPECTED_SHA="d7ed61f6bedc1b395ff3a3e6eace3f9137185a025e913ef31e1fcdec3103daa9"
TMP_DIR="$(mktemp -d)"
ARCHIVE="$TMP_DIR/hindsight_lite_termux-0.6.0a1-src.tar.gz"
SRC_DIR="$TMP_DIR/src"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "== Hindsight Lite for Hermes / Termux =="

if [ ! -x "$PY" ]; then
  echo "ERROR: Hermes Python venv not found: $VENV"
  echo "Set HERMES_VENV to the active Hermes venv and retry."
  exit 1
fi

PARTS=("$ROOT"/dist/hindsight_lite_termux-0.6.0a1-src.b64.part*)
if [ "${#PARTS[@]}" -ne 5 ] || [ ! -f "${PARTS[0]}" ]; then
  echo "ERROR: verified source payload is incomplete."
  echo "Run git pull or re-clone the repository and retry."
  exit 1
fi

cat "${PARTS[@]}" | tr -d '\r\n' | base64 -d > "$ARCHIVE"
ACTUAL_SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
  echo "ERROR: source archive checksum mismatch."
  echo "Expected: $EXPECTED_SHA"
  echo "Actual:   $ACTUAL_SHA"
  exit 1
fi

echo "PASS  source checksum"

if ! tar -tzf "$ARCHIVE" >/dev/null 2>&1; then
  echo "ERROR: verified source payload is not a valid tar.gz archive."
  exit 1
fi

echo "PASS  source archive"
mkdir -p "$SRC_DIR"
tar -xzf "$ARCHIVE" -C "$SRC_DIR"

EXPECTED_FILES=(
  __init__.py
  db.py
  embeddings.py
  hermes.py
  obsidian.py
  reflection.py
  retention.py
  retrieval.py
  schema.py
)
for f in "${EXPECTED_FILES[@]}"; do
  if [ ! -f "$SRC_DIR/hindsight_lite/$f" ]; then
    echo "ERROR: source payload is missing hindsight_lite/$f"
    exit 1
  fi
done

echo "PASS  source files"

SITE_PACKAGES="$("$PY" - <<'PY'
import sysconfig
print(sysconfig.get_paths()["purelib"])
PY
)"
if [ -z "$SITE_PACKAGES" ] || [ ! -d "$SITE_PACKAGES" ]; then
  echo "ERROR: could not locate Hermes site-packages."
  exit 1
fi

TARGET="$SITE_PACKAGES/hindsight_lite"
rm -rf "$TARGET"
cp -a "$SRC_DIR/hindsight_lite" "$TARGET"

echo "PASS  package installed from reviewed source"

INSTALLED_VER="$("$PY" - <<'PY'
import hindsight_lite
print(hindsight_lite.__version__)
PY
)"
if [ "$INSTALLED_VER" != "0.6.0a1" ]; then
  echo "ERROR: installed version is '$INSTALLED_VER', expected 0.6.0a1."
  exit 1
fi

echo "PASS  package version $INSTALLED_VER"

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

echo
echo "Installation complete: Hindsight Lite $INSTALLED_VER"
echo "Existing memory data/config were preserved."
echo "Restart Hermes or start a new session, then run:"
echo "  bash $ROOT/scripts/doctor.sh"
