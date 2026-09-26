# Export the known-good fixed build safely

This step is intentionally separate from the public installer.

The goal is to export only the installed Python package code from a known-good device, with **no memory database, config, tokens, environment files, or user data**.

## 1. Locate the installed package

Run inside the Hermes Python venv:

```bash
"$HOME/.hermes/hermes-agent/venv/bin/python" - <<'PY'
import pathlib, hindsight_lite
print("version:", hindsight_lite.__version__)
print("package:", pathlib.Path(hindsight_lite.__file__).resolve().parent)
PY
```

Continue only if the version is:

```text
0.9.0a1
```

## 2. Create a clean source export

From Termux:

```bash
bash scripts/export-installed-package.sh
```

The script:

- copies only the `hindsight_lite` Python package;
- rejects the export unless the installed version is `0.9.0a1`;
- excludes `__pycache__`, `.pyc`, databases and config files;
- scans text for common secret patterns;
- never copies `~/.hermes/hindsight-lite/`, `.env`, USER.md or MEMORY.md.

Review the produced directory manually before publishing it.

## 3. Build a wheel

After the package's packaging metadata has been reconstructed/verified, build a wheel in an isolated environment and place it under:

```text
dist/
```

The public installer deliberately refuses to install an older version.
