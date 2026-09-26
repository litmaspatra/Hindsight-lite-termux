# Export the known-good working build safely

The public installer already contains the reviewed build. This document is only for maintainers who need to reproduce or audit the source export from a known-good device.

The working installation used for this release reports:

```text
0.6.0a1
```

Run:

```bash
bash scripts/export-installed-package.sh
```

The export helper copies only Python source files from the installed `hindsight_lite` package. It does not copy the Hindsight Lite data/config directory, memory database, environment files, caches, tokens, USER.md, or MEMORY.md.

Always manually review an export before publishing it. The public release was built only after source review and package/plugin smoke tests.
