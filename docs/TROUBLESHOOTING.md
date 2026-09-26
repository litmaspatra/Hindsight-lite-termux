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

The reviewed known-good build reports:

```text
0.6.0a1
```

That version string comes from the actual working installation that was exported and tested. Do not replace it with an assumed `0.9.0a1` build.

## `KeyError: 'function'` while inspecting TOOL_SCHEMAS

Compatible tooling should support both flat schemas and schemas wrapped in a `function` object. The included doctor handles both forms.

## Installer says checksum mismatch

Do not bypass the check. Delete the clone and fetch a clean copy:

```bash
cd ~
rm -rf Hindsight-lite-termux
git clone https://github.com/litmaspatra/Hindsight-lite-termux.git
cd Hindsight-lite-termux
bash install-termux.sh
```

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
