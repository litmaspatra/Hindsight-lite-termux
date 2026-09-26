#!/data/data/com.termux/files/usr/bin/bash
set -u
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
VENV="${HERMES_VENV:-$HERMES_HOME/hermes-agent/venv}"
PY="$VENV/bin/python"
fail=0
ok(){ echo "PASS  $1"; }
bad(){ echo "FAIL  $1"; fail=1; }

[ -x "$PY" ] && ok "Hermes Python venv" || { bad "Hermes Python venv"; exit 1; }

VER="$("$PY" - <<'PY' 2>/dev/null
import hindsight_lite
print(hindsight_lite.__version__)
PY
)"
[ "$VER" = "0.6.0a1" ] && ok "working Hindsight Lite build 0.6.0a1" || bad "unexpected package version: ${VER:-missing}"

"$PY" - <<'PY' >/dev/null 2>&1
import sqlite3
c=sqlite3.connect(':memory:')
c.execute('CREATE VIRTUAL TABLE t USING fts5(x)')
PY
[ $? -eq 0 ] && ok "SQLite FTS5" || bad "SQLite FTS5"

"$PY" - <<'PY' >/dev/null 2>&1
from hindsight_lite.hermes import TOOL_SCHEMAS
names=set()
for x in TOOL_SCHEMAS:
    if not isinstance(x, dict):
        continue
    if isinstance(x.get('function'), dict):
        n=x['function'].get('name')
    else:
        n=x.get('name')
    if n:
        names.add(n)
required={'hmem_recall','hmem_remember','hmem_reflect','hmem_reindex_embeddings'}
assert required <= names
assert len(names) >= 10
PY
[ $? -eq 0 ] && ok "10 hmem tool schemas including recall/reflect/reindex" || bad "expected hmem tool schemas"

[ -f "$HERMES_HOME/plugins/hindsight-lite/__init__.py" ] && ok "Hermes plugin adapter" || bad "Hermes plugin adapter"
[ -f "$HERMES_HOME/plugins/hindsight-lite/plugin.yaml" ] && ok "Hermes plugin metadata" || bad "Hermes plugin metadata"
[ -f "$HERMES_HOME/hindsight-lite/config.json" ] && ok "provider config" || bad "provider config"

PROVIDER="$("$PY" - "$HERMES_HOME/config.yaml" <<'PY' 2>/dev/null
import sys,yaml
from pathlib import Path
p=Path(sys.argv[1])
if p.exists():
 d=yaml.safe_load(p.read_text()) or {}
 print((d.get('memory') or {}).get('provider') or '')
PY
)"
[ "$PROVIDER" = "hindsight-lite" ] && ok "memory.provider=hindsight-lite" || bad "memory.provider is not hindsight-lite"

echo
[ "$fail" -eq 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit "$fail"
