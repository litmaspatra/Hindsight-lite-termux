# Hindsight Lite for Hermes on Termux

A lightweight long-term-memory provider for Hermes Agent on Android/Termux, packaged from the known-good working installation.

## What is included

- Exact reviewed working `hindsight_lite` build (`0.6.0a1`)
- SQLite + FTS5 memory storage
- Semantic embeddings through an OpenAI-compatible endpoint
- Compatibility with embedding responses that omit the optional `index` field
- Configurable embedding timeout (60 seconds by default)
- Embedding backfill/reindex through `hmem_reindex_embeddings`
- 10 flat `hmem_*` tools
- Hermes `MemoryProvider` adapter
- Termux installer and doctor script
- SHA-256 verification of the packaged release before installation

The build intentionally avoids Torch, MLX, and local NumPy-dependent embedding stacks so it stays practical on Termux.

## Install

Hermes Agent must already be installed.

```bash
git clone https://github.com/litmaspatra/Hindsight-lite-termux.git
cd Hindsight-lite-termux
bash install-termux.sh
```

The installer reconstructs the reviewed wheel from the repository's release chunks, verifies its SHA-256 checksum, installs it into the active Hermes Python venv, installs the Hermes provider adapter, preserves an existing Hindsight Lite config/database, backs up `config.yaml`, and sets:

```yaml
memory:
  provider: hindsight-lite
```

Then edit:

```text
~/.hermes/hindsight-lite/config.json
```

Set your endpoint/model names. Keep real credentials in environment variables, never in the JSON or repository.

Restart Hermes or start a new session, then verify:

```bash
bash scripts/doctor.sh
```

## Configuration example

```json
{
  "llm_base_url": "http://127.0.0.1:20128/v1",
  "llm_model": "YOUR_MODEL",
  "llm_api_key_env": "YOUR_LLM_API_KEY_ENV",
  "embedding_base_url": "http://127.0.0.1:20128/v1",
  "embedding_model": "YOUR_EMBEDDING_MODEL",
  "embedding_api_key_env": "YOUR_EMBEDDING_API_KEY_ENV",
  "embedding_timeout": 60,
  "obsidian_vault": "",
  "prefetch_limit": 6,
  "prefetch_chars": 6000
}
```

## Tools

- `hmem_recall`
- `hmem_remember`
- `hmem_reflect`
- `hmem_forget`
- `hmem_update`
- `hmem_status`
- `hmem_reindex_embeddings`
- `hmem_trace`
- `hmem_graph`
- `hmem_sync_obsidian`

## Why the version is 0.6.0a1

The known-good phone installation reports `0.6.0a1`. The release here is built from that exact working source rather than renaming it to an assumed later version. Functionality and compatibility fixes were verified from the exported source and by local smoke tests.

## Privacy

No personal memories, memory database, API keys, OAuth tokens, account details, contacts, private messages, phone data, or private configuration are included.

See `SECURITY.md`.
