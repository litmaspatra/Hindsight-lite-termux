#!/usr/bin/env bash
# Read-only Hermes Python discovery. No installs, activation, or changes to Hermes.
# Usage: source scripts/hermes-python.sh; resolve_hermes_python || exit 1
resolve_hermes_python() {
  local home agent candidate tool
  home="${HERMES_HOME:-$HOME/.hermes}"
  agent="${HERMES_AGENT_DIR:-$home/hermes-agent}"
  if [ -n "${HERMES_PYTHON:-}" ]; then
    candidate="$HERMES_PYTHON"
    if [ ! -x "$candidate" ]; then
      echo "ERROR: HERMES_PYTHON is not executable: $candidate" >&2
      return 1
    fi
    HINDSIGHT_HERMES_PYTHON="$candidate"; return 0
  fi
  if [ -n "${HERMES_VENV:-}" ]; then
    for candidate in "$HERMES_VENV/bin/python" "$HERMES_VENV/bin/python3"; do
      if [ -x "$candidate" ]; then HINDSIGHT_HERMES_PYTHON="$candidate"; return 0; fi
    done
    echo "ERROR: HERMES_VENV has no executable python: $HERMES_VENV" >&2
    return 1
  fi
  for candidate in "$agent/venv/bin/python" "$agent/.venv/bin/python"; do
    if [ -x "$candidate" ]; then HINDSIGHT_HERMES_PYTHON="$candidate"; return 0; fi
  done
  # Standalone uv-managed runtime: inspect the launcher, never pick an arbitrary Python.
  local launcher="$agent/.hermes/bin/hermes"
  if [ -f "$launcher" ]; then
    tool="$(sed -n '2s|^exec \([^ ]*/bin/python3\) -I .*|\1|p' "$launcher")"
    case "$tool" in
      "$home"/tools/*/bin/python3)
        if [ -x "$tool" ]; then HINDSIGHT_HERMES_PYTHON="$tool"; return 0; fi ;;
    esac
  fi
  echo "ERROR: Cannot identify Hermes Python; set HERMES_PYTHON explicitly" >&2
  return 1
}
