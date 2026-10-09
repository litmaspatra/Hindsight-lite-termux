#!/data/data/com.termux/files/usr/bin/bash
# Health check. Add --live to also test the configured LLM / embedding endpoints.
set -u
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
source "$(dirname "${BASH_SOURCE[0]}")/hermes-python.sh"
resolve_hermes_python || exit 1
PY="$HINDSIGHT_HERMES_PYTHON"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE=0; [ "${1:-}" = "--live" ] && LIVE=1
fail=0
ok(){ echo "PASS  $1"; }
bad(){ echo "FAIL  $1"; fail=1; }
warn(){ echo "WARN  $1"; }

[ -x "$PY" ] && ok "Hermes Python: $PY" || { bad "Hermes Python"; exit 1; }

WANT="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$ROOT/hindsight_lite/__init__.py" 2>/dev/null | head -n1)"
VER="$("$PY" -c 'import hindsight_lite; print(hindsight_lite.__version__)' 2>/dev/null)"
if [ -z "$VER" ]; then bad "hindsight_lite is not importable by the detected Hermes Python"
elif [ -n "$WANT" ] && [ "$VER" != "$WANT" ]; then bad "installed $VER but this checkout is $WANT -- run install-termux.sh"
else ok "Hindsight Lite package $VER"; fi

"$PY" -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)')" >/dev/null 2>&1 \
  && ok "SQLite FTS5" || bad "SQLite FTS5"

"$PY" - <<'PY' >/dev/null 2>&1
from hindsight_lite.hermes import TOOL_SCHEMAS
names = {(x.get('function') or x).get('name') for x in TOOL_SCHEMAS if isinstance(x, dict)}
assert {'hmem_recall','hmem_remember','hmem_reflect','hmem_purge','hmem_forget'} <= names and len(names) >= 11
PY
[ $? -eq 0 ] && ok "11 hmem tool schemas" || bad "hmem tool schemas incomplete"

# Real round-trip in a throwaway database: remember -> overwrite -> natural-language recall -> cleanup.
"$PY" - <<'PY'
import sys, tempfile
from hindsight_lite import MemoryStore, RetrievalEngine, RetentionEngine
from hindsight_lite.maintenance import MaintenancePolicy, run_maintenance
d = tempfile.mkdtemp()
s = MemoryStore(d + "/t.db")
r = RetentionEngine(s, None)
r.remember_direct("Peter lives in Jaipur")
hits = RetrievalEngine(s).lexical("hey, where does Peter live these days?")
assert hits and "Jaipur" in hits[0]["content"], "natural-language recall failed"
a = s.create_memory("old", memory_type="world"); b = s.create_memory("new", memory_type="world")
s.supersede_memory(a, b)
run_maintenance(s, MaintenancePolicy(superseded_ttl_days=0))
assert s.get_memory(a) is None and s.get_memory(b) is not None, "cleanup failed"
PY
[ $? -eq 0 ] && ok "self-test: remember, natural-language recall, overwrite clean-up" || bad "self-test failed"

[ -f "$HERMES_HOME/plugins/hindsight-lite/__init__.py" ] && ok "Hermes plugin adapter" || bad "Hermes plugin adapter"
[ -f "$HERMES_HOME/plugins/hindsight-lite/plugin.yaml" ] && ok "Hermes plugin metadata" || bad "Hermes plugin metadata"
[ -f "$HERMES_HOME/hindsight-lite/config.json" ] && ok "provider config" || bad "provider config"

PROVIDER="$("$PY" - "$HERMES_HOME/config.yaml" <<'PY' 2>/dev/null
import sys, yaml
from pathlib import Path
p = Path(sys.argv[1])
if p.exists():
    print(((yaml.safe_load(p.read_text()) or {}).get('memory') or {}).get('provider') or '')
PY
)"
[ "$PROVIDER" = "hindsight-lite" ] && ok "memory.provider=hindsight-lite" || bad "memory.provider is not hindsight-lite"

# Config sanity + optional live endpoint checks (never fails the run for optional features)
LIVE="$LIVE" HERMES_HOME="$HERMES_HOME" "$PY" - <<'PY'
import os
from pathlib import Path
from hindsight_lite.hermes import HermesLiteConfig
try:
    cfg = HermesLiteConfig.load(Path(os.environ["HERMES_HOME"]) / "hindsight-lite" / "config.json")
except Exception as exc:
    print(f"FAIL  config invalid: {exc}"); raise SystemExit(0)
print("PASS  config parses")
llm = bool(cfg.llm_base_url and cfg.llm_model)
emb = bool(cfg.embedding_base_url and cfg.embedding_model)
print("PASS  automatic memory + smart overwrite enabled" if llm else "WARN  llm_model not set: automatic retention and smart overwrite are OFF (hmem_remember still works)")
print("PASS  semantic search enabled" if emb else "WARN  embedding_* not set: semantic search is OFF (keyword/entity recall still works)")
if os.environ.get("LIVE") == "1":
    from hindsight_lite.embeddings import OpenAICompatibleEmbeddingBackend
    from hindsight_lite.reconcile import OpenAICompatibleJudge
    if emb:
        key = os.environ.get(cfg.embedding_api_key_env, "") if cfg.embedding_api_key_env else ""
        try:
            v = OpenAICompatibleEmbeddingBackend(base_url=cfg.embedding_base_url, model=cfg.embedding_model, api_key=key, provider="doctor", timeout=10).embed(["ping"])[0]
            print(f"PASS  embedding endpoint ({len(v)} dims)")
        except Exception as exc:
            print(f"FAIL  embedding endpoint: {type(exc).__name__}")
    if llm:
        key = os.environ.get(cfg.llm_api_key_env, "") if cfg.llm_api_key_env else ""
        try:
            j = OpenAICompatibleJudge(base_url=cfg.llm_base_url, model=cfg.llm_model, api_key=key, timeout=20).judge(
                "Likes lo-fi in the morning", [{"id": "m1", "content": "Likes jazz in the morning", "subtype": "preference"}])
            print(f"PASS  LLM endpoint returns valid judge JSON (verdict: {j.verdicts[0].relation if j.verdicts else 'none'})")
        except Exception as exc:
            print(f"FAIL  LLM endpoint: {type(exc).__name__}")
PY

echo
[ "$fail" -eq 0 ] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit "$fail"
