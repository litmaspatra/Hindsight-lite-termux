#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
AGENT_DIR="${HERMES_AGENT_DIR:-$HERMES_HOME/hermes-agent}"
VENV="${HERMES_VENV:-$AGENT_DIR/venv}"
PLUGIN_DIR="$HERMES_HOME/plugins/hindsight-lite"
CONFIG_DIR="$HERMES_HOME/hindsight-lite"
CONFIG_FILE="$CONFIG_DIR/config.json"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "== Hindsight Lite / Hermes Termux installer =="

if [ ! -x "$VENV/bin/python" ]; then
  echo "ERROR: Hermes Python virtualenv not found at:"
  echo "  $VENV"
  echo "Set HERMES_VENV to the correct Hermes venv and retry."
  exit 1
fi

WHEEL="$(find "$REPO_DIR/dist" -maxdepth 1 -type f -name 'hindsight_lite-0.9.0a1-*.whl' 2>/dev/null | head -n1 || true)"
if [ -z "$WHEEL" ]; then
  echo "ERROR: verified hindsight_lite 0.9.0a1 wheel is not present in dist/."
  echo "Do not substitute an older build."
  echo "See docs/EXPORT_FIXED_BUILD.md."
  exit 1
fi

mkdir -p "$PLUGIN_DIR" "$CONFIG_DIR"

"$VENV/bin/python" -m pip install --upgrade "$WHEEL"

cat > "$PLUGIN_DIR/__init__.py" <<'PY'
from hindsight_lite.hermes import HermesLiteConfig, HermesMemoryBridge, TOOL_SCHEMAS

try:
    from agent.memory_provider import MemoryProvider
except ImportError:
    from hermes_agent.agent.memory_provider import MemoryProvider


class HindsightLiteProvider(MemoryProvider):
    @property
    def name(self):
        return "hindsight-lite"

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._bridge = None

    async def initialize(self, config=None, **kwargs):
        cfg = HermesLiteConfig.load_default()
        self._bridge = HermesMemoryBridge(cfg)
        maybe = self._bridge.initialize()
        if hasattr(maybe, "__await__"):
            await maybe

    def system_prompt_block(self):
        return (
            "# Hindsight Lite Memory\n"
            "Use hmem_recall for explicit lookup, hmem_reflect for cross-memory synthesis, "
            "and hmem_remember/hmem_update for durable memory changes."
        )

    def get_tool_schemas(self):
        return TOOL_SCHEMAS

    async def handle_tool_call(self, name, arguments):
        result = self._bridge.handle_tool_call(name, arguments)
        if hasattr(result, "__await__"):
            result = await result
        return result

    async def shutdown(self):
        if self._bridge is not None:
            maybe = self._bridge.shutdown()
            if hasattr(maybe, "__await__"):
                await maybe
PY

if [ ! -f "$CONFIG_FILE" ]; then
  cp "$REPO_DIR/config/config.example.json" "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
  echo "Created example config: $CONFIG_FILE"
else
  echo "Keeping existing config: $CONFIG_FILE"
fi

CONFIG_YAML="$HERMES_HOME/config.yaml"
if [ -f "$CONFIG_YAML" ]; then
  BACKUP="$CONFIG_YAML.backup-hindsight-lite-$(date +%Y%m%d-%H%M%S)"
  cp "$CONFIG_YAML" "$BACKUP"

  "$VENV/bin/python" "$REPO_DIR/scripts/set_provider.py" "$CONFIG_YAML" hindsight-lite
  echo "Hermes config backed up to: $BACKUP"
else
  echo "WARNING: $CONFIG_YAML not found."
  echo "Add this manually:"
  echo "memory:"
  echo "  provider: hindsight-lite"
fi

echo
echo "Installation complete."
echo "1. Edit $CONFIG_FILE and set only non-secret endpoint/model values."
echo "2. Put API keys in environment variables, not in config.json."
echo "3. Restart Hermes / start a new Hermes session."
echo "4. Run: bash scripts/doctor.sh"
