# Troubleshooting

## Hermes still uses its old memory provider

Check:

```bash
grep -A3 '^memory:' ~/.hermes/config.yaml
```

Expected:

```yaml
memory:
  provider: hindsight-lite
```

Restart Hermes and begin a new session after changing providers.

## Which package version is expected?

`doctor.sh` compares the installed package with the version in this checkout and tells you to re-run `install-termux.sh` if they differ.

## An old fact was not replaced

Smart overwrite needs `llm_model` set (check `hmem_status`: `overwrite_decisions` should say `llm-judge`). Run `hmem_trace` after a turn, and `bash scripts/doctor.sh --live` to confirm the model returns valid JSON. Small models sometimes answer `extends` when you expected `replaces`; a stronger model helps. Without an LLM, only single-valued relationships and near-identical statements are replaced.

## Overwritten facts are still in the database

They are kept for `superseded_ttl_days` (default 3) as an undo window. Run `hmem_purge` with `dry_run=false` to delete them now, or set `superseded_ttl_days` to `0`.

## Auto-recall injects too much / too little

Tune `prefetch_min_semantic` (higher = stricter). `hmem_status` shows `semantic_circuit_open` when the embedding endpoint was skipped after repeated failures; it retries after 60 s.

## Installer says the staged package failed to import

Nothing was changed. Run `git pull`/re-clone and retry; if it persists, the error printed above it names the broken file.

## Full Hindsight fails on Termux

The full stack may pull native ML dependencies that are unavailable or impractical on Android/Termux, particularly Torch / MLX and NumPy-dependent local embedding stacks. Hindsight Lite avoids requiring those heavy local ML dependencies.

## Embedding response has no `index`

Some OpenAI-compatible endpoints return embedding objects without the optional `index` field. This build supports consistently indexed responses and consistently unindexed responses; mixed batches are rejected rather than silently reordered.

## Embeddings time out

The embedding timeout is configurable. The default in this package is 60 seconds.

## Semantic memories do not appear

Confirm:

1. the embedding endpoint is reachable;
2. the configured embedding model supports `/v1/embeddings`;
3. the environment variable named by `embedding_api_key_env` exists if needed;
4. existing memories have embeddings;
5. run `hmem_reindex_embeddings` to backfill/rebuild embeddings when appropriate.

## Do not expose secrets while debugging

Never paste `.env`, API keys, OAuth credentials, token JSON, memory database contents, or real USER.md / MEMORY.md files. Use redacted status output only.
