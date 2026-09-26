#!/usr/bin/env python3
import sys
from pathlib import Path

try:
    import yaml
except Exception:
    print("ERROR: PyYAML is required in the Hermes venv.", file=sys.stderr)
    raise

if len(sys.argv) != 3:
    raise SystemExit("usage: set_provider.py CONFIG_YAML PROVIDER")

path = Path(sys.argv[1])
provider = sys.argv[2]

data = yaml.safe_load(path.read_text()) or {}
memory = data.get("memory")
if not isinstance(memory, dict):
    memory = {}
data["memory"] = memory
memory["provider"] = provider

path.write_text(yaml.safe_dump(data, sort_keys=False))
print(f"Set memory.provider={provider}")
