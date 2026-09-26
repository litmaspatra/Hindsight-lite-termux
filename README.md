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
- Obsidian sync for memory/entity notes and graph visualization
- Termux installer and doctor script
- SHA-256 verification of the packaged source before installation

The build intentionally avoids Torch, MLX, and local NumPy-dependent embedding stacks so it stays practical on Termux.

## Install

Hermes Agent must already be installed.

```bash
git clone https://github.com/litmaspatra/Hindsight-lite-termux.git
cd Hindsight-lite-termux
bash install-termux.sh
```

The installer reconstructs the reviewed source archive from the repository payload, verifies its SHA-256 checksum, validates the archive and expected source files, installs the exact package source into the active Hermes Python environment, installs the Hermes provider adapter, preserves an existing Hindsight Lite config/database, backs up `config.yaml`, and sets:

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

## Obsidian semantic graph / 3D Galaxy View

Hindsight Lite can export memories and extracted entities into an Obsidian vault so the relationships can be explored visually.

The semantic search itself is still handled by Hindsight Lite using embeddings and SQLite. Obsidian is the visualization layer: linked memory/entity notes become a knowledge graph that can be viewed with Obsidian's normal Graph View or with a 3D graph plugin such as **Galaxy View**.

### 1. Set the Obsidian vault path

Edit:

```text
~/.hermes/hindsight-lite/config.json
```

and set `obsidian_vault` to the folder where Hindsight Lite should create/sync its notes. For example:

```json
{
  "obsidian_vault": "/storage/emulated/0/YourObsidianVaultName"
}
```

Use a path that belongs to your own Obsidian vault. The repository intentionally does not hard-code a private device path.

### 2. Sync Hindsight memories to Obsidian

From Hermes, run the memory tool:

```text
hmem_sync_obsidian
```

This exports the Hindsight memory/entity structure as linked Markdown notes suitable for Obsidian graph visualization.

You can also use:

```text
hmem_graph
```

to inspect relationships from Hermes itself.

### 3. View the graph in Obsidian

Open the configured vault in Obsidian and use the normal **Graph View** to inspect linked memories and entities.

The graph is created from note links/relationships. It is not a neural network model running inside Obsidian; it is a visual knowledge graph produced from Hindsight Lite's semantic memory data.

### 4. Optional: Galaxy View for a 3D graph

For a more neural-network-like 3D visualization, install and enable the **Galaxy View** community plugin in Obsidian.

Typical flow:

1. Open Obsidian.
2. Open **Settings → Community plugins**.
3. Enable community plugins if needed.
4. Browse/search for **Galaxy View** and install it.
5. Enable Galaxy View.
6. Open the Hindsight-backed vault/folder.
7. Launch Galaxy View to explore the linked memories/entities as a 3D graph.

After new memories or relationships are added, run `hmem_sync_obsidian` again so Obsidian/Galaxy View can display the updated graph.

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
