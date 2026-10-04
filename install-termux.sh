#!/data/data/com.termux/files/usr/bin/bash
# Installs Hindsight Lite from this checkout into the Hermes venv.
# Safe by construction: stage -> import-check -> swap in -> re-check -> roll back on any failure.
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
AGENT_DIR="${HERMES_AGENT_DIR:-$HERMES_HOME/hermes-agent}"
VENV="${HERMES_VENV:-$AGENT_DIR/venv}"
PY="$VENV/bin/python"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$ROOT/hindsight_lite"
PLUGIN_DIR="$HERMES_HOME/plugins/hindsight-lite"
DATA_DIR="$HERMES_HOME/hindsight-lite"
CONFIG_YAML="$HERMES_HOME/config.yaml"
STAMP="$(date +%Y%m%d-%H%M%S)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "== Hindsight Lite for Hermes / Termux =="

[ -x "$PY" ] || { echo "ERROR: Hermes Python venv not found: $VENV"; echo "Set HERMES_VENV to the active Hermes venv and retry."; exit 1; }
[ -f "$SRC/__init__.py" ] && [ -f "$SRC/hermes.py" ] || { echo "ERROR: $SRC is incomplete. Re-clone the repository."; exit 1; }

WANT_VER="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$SRC/__init__.py" | head -n1)"
[ -n "$WANT_VER" ] || { echo "ERROR: cannot read version from $SRC/__init__.py"; exit 1; }
echo "PASS  source tree (version $WANT_VER)"

SITE_PACKAGES="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
[ -d "$SITE_PACKAGES" ] || { echo "ERROR: could not locate Hermes site-packages."; exit 1; }

# 1) stage a clean copy (no __pycache__) and prove it imports before touching the live install
mkdir -p "$TMP_DIR/stage"
cp -a "$SRC" "$TMP_DIR/stage/hindsight_lite"
find "$TMP_DIR/stage" -name '__pycache__' -type d -prune -exec rm -rf {} +
PYTHONPATH="$TMP_DIR/stage" PYTHONDONTWRITEBYTECODE=1 "$PY" - <<PY || { echo "ERROR: staged package failed to import; nothing was changed."; exit 1; }
import hindsight_lite, hindsight_lite.hermes
assert hindsight_lite.__version__ == "$WANT_VER", hindsight_lite.__version__
PY
echo "PASS  staged package imports"

# 2) swap in, keeping the previous install as a backup
TARGET="$SITE_PACKAGES/hindsight_lite"
BACKUP=""
if [ -e "$TARGET" ]; then
  BACKUP="$SITE_PACKAGES/hindsight_lite.bak-$STAMP"
  mv "$TARGET" "$BACKUP"
fi
cp -a "$TMP_DIR/stage/hindsight_lite" "$TARGET"

rollback() {
  echo "ERROR: $1 -- rolling back."
  rm -rf "$TARGET"
  [ -n "$BACKUP" ] && mv "$BACKUP" "$TARGET" && echo "Restored previous install."
  exit 1
}
INSTALLED_VER="$("$PY" -c 'import hindsight_lite; print(hindsight_lite.__version__)' 2>/dev/null)" || rollback "installed package does not import"
[ "$INSTALLED_VER" = "$WANT_VER" ] || rollback "installed version is '$INSTALLED_VER', expected $WANT_VER"
echo "PASS  package version $INSTALLED_VER"
# keep only the newest backup
{ ls -1d "$SITE_PACKAGES"/hindsight_lite.bak-* 2>/dev/null || true; } | sort | head -n -1 | xargs -r rm -rf

# 3) plugin adapter + config
mkdir -p "$PLUGIN_DIR" "$DATA_DIR"
chmod 700 "$DATA_DIR"
cp "$ROOT/plugin/hindsight-lite/__init__.py" "$PLUGIN_DIR/__init__.py"
cp "$ROOT/plugin/hindsight-lite/plugin.yaml" "$PLUGIN_DIR/plugin.yaml"

if [ ! -f "$DATA_DIR/config.json" ]; then
  cp "$ROOT/config/config.example.json" "$DATA_DIR/config.json"
  chmod 600 "$DATA_DIR/config.json"
  echo "Created config: $DATA_DIR/config.json  (set llm_model / embedding_* to enable them)"
else
  echo "Keeping existing config: $DATA_DIR/config.json  (new options: see config/config.example.json)"
fi

# 4) activate as the Hermes memory provider (comments in config.yaml are preserved)
if [ -f "$CONFIG_YAML" ]; then
  CFG_BACKUP="$CONFIG_YAML.backup-hindsight-lite-$STAMP"
  cp "$CONFIG_YAML" "$CFG_BACKUP"
  "$PY" "$ROOT/scripts/set_provider.py" "$CONFIG_YAML" hindsight-lite
  echo "Backed up Hermes config to: $CFG_BACKUP"
else
  echo "WARNING: $CONFIG_YAML not found. Set this manually in the active Hermes config:"
  echo "memory:"
  echo "  provider: hindsight-lite"
fi

echo
echo "Installation complete: Hindsight Lite $INSTALLED_VER"
echo "Existing memory data and config were preserved (the database upgrades itself on first start)."
echo "Restart Hermes or start a new session, then run:  bash $ROOT/scripts/doctor.sh"
