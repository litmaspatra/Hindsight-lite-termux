#!/data/data/com.termux/files/usr/bin/bash
set -u

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
AGENT_DIR="${HERMES_AGENT_DIR:-$HERMES_HOME/hermes-agent}"
VENV="${HERMES_VENV:-$AGENT_DIR/venv}"
PY="$VENV/bin/python"
fail=0

ok() { printf 'PASS  %s\n' "$1"; }
bad() { printf 'FAIL  %s\n' "$1"; fail=1; }
warn() { printf 'WARN  %s\n' "$1"; }

echo "Hindsight Lite doctor"
echo

if [ -x "$PY" ]; then ok "Hermes Python venv found"; else bad "Hermes Python venv missing"; exit 1; fi

VERSION="$("$PY" - <<'PY' 2>/dev/null
try:
    import hindsight_lite
    print(getattr(hindsight_lite, "__version__", "unknown"))
except Exception:
    pass
PY
)"

if [ "$VERSION" = "0.9.0a1" ]; then
  ok "hindsight_lite version 0.9.0a1"
elif [ -n "$VERSION" ]; then
  bad "wrong hindsight_lite version: $VERSION (expected 0.9.0a1)"
else
  bad "hindsight_lite not importable"
fi

if [ -f "$HERMES_HOME/plugins/hindsight-lite/__init__.py" ]; then
  ok "Hermes plugin shim present"
else
  bad "Hermes plugin shim missing"
fi

if [ -f "$HERMES_HOME/hindsight-lite/config.json" ]; then
  ok "Hindsight Lite config present"
else
  bad "Hindsight Lite config missing"
fi

"$PY" - <<'PY' >/dev/null 2>&1
import sqlite3
c = sqlite3.connect(":memory:")
c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
PY
if [ $? -eq 0 ]; then ok "SQLite FTS5 available"; else bad "SQLite FTS5 unavailable"; fi

PROVIDER="$("$PY" - "$HERMES_HOME/config.yaml" <<'PY' 2>/dev/null
import sys, yaml
from pathlib import Path
p = Path(sys.argv[1])
if p.exists():
    d = yaml.safe_load(p.read_text()) or {}
    print((d.get("memory") or {}).get("provider") or "")
PY
)"

if [ "$PROVIDER" = "hindsight-lite" ]; then
  ok "memory.provider is hindsight-lite"
else
  bad "memory.provider is not hindsight-lite"
fi

TOOLS="$("$PY" - <<'PY' 2>/dev/null
try:
    from hindsight_lite.hermes import TOOL_SCHEMAS
    names = []
    for item in TOOL_SCHEMAS:
        if isinstance(item, dict):
            if isinstance(item.get("function"), dict):
                n = item["function"].get("name")
            else:
                n = item.get("name")
            if n:
                names.append(n)
    print(",".join(sorted(names)))
except Exception:
    pass
PY
)"

if printf '%s' "$TOOLS" | grep -q 'hmem_recall'; then
  ok "hmem_recall tool schema available"
else
  bad "hmem_recall tool schema missing"
fi

if printf '%s' "$TOOLS" | grep -q 'hmem_remember'; then
  ok "hmem_remember tool schema available"
else
  bad "hmem_remember tool schema missing"
fi

if printf '%s' "$TOOLS" | grep -q 'hmem_reflect'; then
  ok "hmem_reflect tool schema available"
else
  warn "hmem_reflect not detected"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "RESULT: PASS"
else
  echo "RESULT: FAIL"
fi
exit "$fail"
