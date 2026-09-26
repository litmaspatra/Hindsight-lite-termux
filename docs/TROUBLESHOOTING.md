# Troubleshooting

## Hermes still uses its old memory provider

The plugin being installed does not automatically mean Hermes is using it.

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

## `hindsight_lite.__version__` reports 0.6.0a1 or 0.8.0a1

That is the wrong build for this packaging.

The known-good fixed target is:

```text
0.9.0a1
```

Do not silently continue with an older package because tool schemas and fixes may differ.

## `KeyError: 'function'` while inspecting TOOL_SCHEMAS

Do not assume every tool schema is wrapped as:

```python
{"function": {...}}
```

Inspect both forms:

```python
if isinstance(item.get("function"), dict):
    name = item["function"].get("name")
else:
    name = item.get("name")
```

The included doctor uses this compatible logic.

## Full Hindsight fails on Termux

The full stack may pull native ML dependencies that are unavailable or impractical on Android/Termux, particularly Torch / MLX and NumPy-dependent local embedding stacks.

Hindsight Lite was selected specifically to avoid requiring those heavy local ML dependencies.

## Embedding response has no `index`

Some OpenAI-compatible routers return embedding objects without the optional `index` field.

The fixed build must not require `index` to be present.

## Embeddings time out

The fixed build uses a configurable embedding timeout. The known-good setup used 60 seconds.

## Semantic memories do not appear

Confirm:

1. the embedding endpoint is reachable;
2. the configured embedding model supports `/v1/embeddings`;
3. the API-key environment variable exists if the endpoint requires one;
4. existing rows have embeddings;
5. reindex embeddings using the fixed build's reindex operation.

## Do not expose secrets while debugging

Never paste:

- `.env`
- API keys
- OAuth credentials
- token JSON
- memory database contents
- real USER.md / MEMORY.md

Use redacted status output only.
