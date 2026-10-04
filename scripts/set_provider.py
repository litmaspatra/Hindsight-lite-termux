#!/usr/bin/env python3
"""Set memory.provider in a Hermes config.yaml without destroying comments or formatting."""
import re
import sys
from pathlib import Path

try:
    import yaml
except Exception:
    print("ERROR: PyYAML is required in the Hermes venv.", file=sys.stderr)
    raise

_MEMORY_KEY = re.compile(r"^memory\s*:\s*(#.*)?$")
_TOP_LEVEL = re.compile(r"^\S")
_PROVIDER = re.compile(r"^(\s+)provider\s*:[^\n]*?(\s+#.*)?(\r?\n?)$")


def _text_edit(text: str, provider: str):
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if _MEMORY_KEY.match(l)), None)
    if start is None:
        if re.search(r"(?m)^memory\s*:", text):
            return None  # inline form: memory: {...} / null
        sep = "" if not text or text.endswith("\n") else "\n"
        return f"{text}{sep}memory:\n  provider: {provider}\n"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].lstrip().startswith("#") and _TOP_LEVEL.match(lines[i]):
            end = i
            break
    for i in range(start + 1, end):
        m = _PROVIDER.match(lines[i])
        if m:
            comment = m.group(2) or ""
            lines[i] = f"{m.group(1)}provider: {provider}{comment}{m.group(3) or chr(10)}"
            return "".join(lines)
    indent = "  "
    for i in range(start + 1, end):
        m = re.match(r"^(\s+)\S", lines[i])
        if m and not lines[i].lstrip().startswith("#"):
            indent = m.group(1)
            break
    lines.insert(start + 1, f"{indent}provider: {provider}\n")
    return "".join(lines)


def set_provider(text: str, provider: str) -> str:
    edited = _text_edit(text, provider)
    if edited is not None and _provider_of(edited) == provider:
        return edited
    # Fallback for unusual layouts: structured rewrite (loses comments, but is correct).
    data = yaml.safe_load(text) or {}
    memory = data.get("memory")
    if not isinstance(memory, dict):
        memory = {}
    data["memory"] = memory
    memory["provider"] = provider
    return yaml.safe_dump(data, sort_keys=False)


def _provider_of(text: str):
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return None
    memory = data.get("memory") if isinstance(data, dict) else None
    return memory.get("provider") if isinstance(memory, dict) else None


def main(argv) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: set_provider.py CONFIG_YAML PROVIDER")
    path, provider = Path(argv[1]), argv[2]
    new = set_provider(path.read_text() if path.exists() else "", provider)
    if _provider_of(new) != provider:
        raise SystemExit("ERROR: could not safely set memory.provider; config left untouched")
    path.write_text(new)
    print(f"Set memory.provider={provider}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
