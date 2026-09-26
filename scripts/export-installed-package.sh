#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
VENV="${HERMES_VENV:-$HERMES_HOME/hermes-agent/venv}"
PY="$VENV/bin/python"
OUT="${1:-$PWD/exported-fixed-package}"

if [ ! -x "$PY" ]; then
  echo "ERROR: Hermes venv not found: $VENV"
  exit 1
fi

readarray -t META < <("$PY" - <<'PY'
import pathlib, hindsight_lite
print(getattr(hindsight_lite, "__version__", ""))
print(pathlib.Path(hindsight_lite.__file__).resolve().parent)
PY
)

VERSION="${META[0]:-}"
SRC="${META[1]:-}"

if [ "$VERSION" != "0.6.0a1" ]; then
  echo "ERROR: refusing export. Installed version is '$VERSION'; expected reviewed build 0.6.0a1."
  exit 1
fi

case "$SRC" in
  "$VENV"/*) ;;
  *) echo "ERROR: package path is outside Hermes venv; refusing."; exit 1 ;;
esac

rm -rf "$OUT"
mkdir -p "$OUT/hindsight_lite"

find "$SRC" -type f -name '*.py' -print0 | while IFS= read -r -d '' f; do
  rel="${f#$SRC/}"
  mkdir -p "$OUT/hindsight_lite/$(dirname "$rel")"
  cp "$f" "$OUT/hindsight_lite/$rel"
done

echo "Export created at: $OUT"
echo "Only Python source files were copied."
echo "No memory database, config directory, environment file, cache, or token file was copied."
echo "IMPORTANT: manually inspect every exported file before public release."
