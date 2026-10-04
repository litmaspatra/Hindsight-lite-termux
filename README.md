# Hindsight Lite for Hermes on Termux

A lightweight, self-cleaning long-term-memory provider for Hermes Agent on Android/Termux. SQLite + FTS5, optional embeddings through any OpenAI-compatible endpoint, no Torch/NumPy.

## What's new in 0.7.0

**Facts that change no longer pile up.** When you say "I like lo-fi in the morning" after earlier saying jazz, the new fact replaces the old one:

1. The new fact is compared with the few stored facts it could affect (same entity, similar wording, shared keywords).
2. A small LLM call decides, per candidate, whether the new fact is `same`, `replaces`, `extends` or `unrelated`. Only `replaces` retires the old fact; `extends` keeps both (e.g. "likes jazz" + "drinks chai with it").
3. When something is replaced, the new memory can carry the change itself: *"Likes lo-fi in the morning (previously jazz)"* (`keep_change_history`, on by default).
4. The old fact is marked superseded, then **deleted automatically after a short grace period** (`superseded_ttl_days`, default 3; `0` = immediately). Dangling entities and relationships are removed with it.
5. Memories that are low-importance, never recalled in `expire_unused_days` (default 120) are expired too. Directives and anything that was ever recalled are kept.

Clean-up runs in the background (at start and after retention, at most every 6 h). `hmem_purge` runs it on demand and defaults to a dry run.

If the LLM is unreachable nothing is ever deleted on a guess: the new fact is stored beside the old one. Without an LLM judge, only strong evidence replaces a fact: a single-valued relationship changed (`lives_in`, `current_*`, `default_*`...), or a near-identical statement about the same entity.

**Retrieval fixes**
- Natural questions now work: keywords are extracted (English + common Hinglish stopwords), OR-matched with prefix matching, and entity/graph arms match entities named inside a sentence.
- Auto-recall is precision-first: a memory needs a keyword overlap, a named entity, or a semantic score >= `prefetch_min_semantic`. Irrelevant turns inject nothing.
- Auto-recall can no longer stall a chat: 3 s embedding timeout, plus a circuit breaker that skips a dead endpoint for 60 s after two failures. The long timeout is kept for background work.
- Vector scan is cached between turns (compact float32).

**Other**
- `hmem_forget` is a real delete. Overwritten and deleted facts no longer appear in the Obsidian mirror.
- Obsidian sync writes only changed notes and is debounced (`obsidian_sync_interval`).
- Works without an LLM: `hmem_remember` stores text directly and search works; only automatic retention/overwrite is off. A bad vault path no longer disables the provider.
- Retention skips acknowledgements ("ok", "thanks", "haan"), sends the user's words plus a trimmed assistant reply (assistant text is context, not a source of facts), truncates instead of rejecting long turns, and reports dropped turns in `hmem_status`.
- Wider secret filter (GitHub/AWS/Google/Slack tokens, JWTs, Bearer headers); `hmem_recall` accepts `since`/`until`.
- Source is plain files with a test suite and CI. The installer stages, import-checks, swaps and rolls back on failure; the old base64 payload and checksum are gone (the checksum protected against corruption, not against a malicious repo - review the code you install).
- `set_provider.py` edits `config.yaml` in place and keeps your comments.

## Install

Hermes Agent must already be installed.

```bash
git clone https://github.com/litmaspatra/Hindsight-lite-termux.git
cd Hindsight-lite-termux
bash install-termux.sh
```

Upgrading from 0.6.0a1 is in place: the same command, and the database migrates itself on first start.

The installer installs the package into the active Hermes venv, installs the adapter, keeps an existing config/database, backs up `config.yaml`, and sets `memory.provider: hindsight-lite`.

Then edit `~/.hermes/hindsight-lite/config.json`. Real credentials go in environment variables named by `*_api_key_env`, never in the JSON.

```bash
bash scripts/doctor.sh          # add --live to test your LLM/embedding endpoints
```

## Configuration

```json
{
  "llm_base_url": "http://127.0.0.1:20128/v1",
  "llm_model": "",
  "llm_api_key_env": "",
  "embedding_base_url": "",
  "embedding_model": "",
  "embedding_api_key_env": "",
  "embedding_timeout": 60,
  "prefetch_embedding_timeout": 3,
  "prefetch_min_semantic": 0.35,
  "judge_timeout": 30,
  "keep_change_history": true,
  "superseded_ttl_days": 3,
  "expire_unused_days": 120,
  "obsidian_vault": "",
  "obsidian_sync_interval": 30,
  "prefetch_limit": 6,
  "prefetch_chars": 6000
}
```

- `llm_model` empty = no automatic retention / smart overwrite / reflect. Set it to enable them. The same model does extraction, overwrite judging and reflection.
- `embedding_*` empty = no semantic search (keyword + entity recall still work). Values starting with `YOUR_` count as unset.
- `prefetch_min_semantic` is a cosine floor; 0.35 suits most embedding models. Raise it if unrelated memories appear, lower it if relevant ones are missed.

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

`hmem_recall` (optional `since`/`until`), `hmem_remember`, `hmem_reflect`, `hmem_forget` (permanent), `hmem_update`, `hmem_status`, `hmem_reindex_embeddings`, `hmem_trace`, `hmem_graph`, `hmem_purge` (dry run by default), `hmem_sync_obsidian`.

## Development

```bash
pip install pytest pyyaml
python -m pytest
```

Tests use fakes only (no network, no real LLM). Not covered by automated tests: the Hermes plugin adapter against a real Hermes, real LLM judgement quality, and on-device performance.

## Privacy

No personal memories, memory database, API keys, OAuth tokens, account details, contacts, private messages, phone data, or private configuration are included.

See `SECURITY.md`.
