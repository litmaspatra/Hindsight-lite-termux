# Hindsight Lite for Hermes on Termux

A Termux-friendly packaging and setup guide for the lightweight Hindsight memory provider used with Hermes Agent.

This repository is intentionally designed for Android/Termux environments where the full Hindsight stack may fail because of unavailable or impractical native dependencies such as Torch, MLX, or NumPy-heavy components.

## What this setup uses

- Hermes external memory provider: `hindsight-lite`
- Tested fixed package target: `hindsight_lite 0.9.0a1`
- Local SQLite / FTS5 storage
- External OpenAI-compatible LLM / embedding endpoint
- No bundled API keys or credentials
- No user memories are included
- No device-specific paths are hard-coded

## Why this exists

The full Hindsight installation was not a good fit for Android/Termux. The working setup used a lightweight provider instead, with several compatibility fixes:

- Avoid heavy Torch / MLX dependencies.
- Avoid relying on a Termux NumPy build for local embedding models.
- Use an external OpenAI-compatible endpoint for embeddings / LLM work.
- Handle embedding responses that omit the optional `index` field.
- Use a configurable embedding timeout (60 seconds in the fixed build).
- Include an embedding reindex operation in the fixed build.
- Ensure Hermes is actually configured with `memory.provider: hindsight-lite`.
- Verify the active provider after configuration rather than assuming activation succeeded.

## Repository status

The installer/configuration framework is complete.

The actual fixed `hindsight_lite 0.9.0a1` Python package must be exported from a known-good installation before it is published here. This repository deliberately does **not** recreate or guess that package source.

See `docs/EXPORT_FIXED_BUILD.md`.

## Safe install flow

Once the verified package is present under `dist/`:

```bash
git clone https://github.com/litmaspatra/Hindsight-lite-termux.git
cd Hindsight-lite-termux
bash install-termux.sh
```

Then edit the generated config:

```text
~/.hermes/hindsight-lite/config.json
```

and restart Hermes.

## Hermes configuration

The required provider setting is:

```yaml
memory:
  provider: hindsight-lite
```

The installer backs up the Hermes config before editing it.

## Example backend

The configuration example uses placeholders only:

```json
{
  "base_url": "http://127.0.0.1:PORT/v1",
  "model": "YOUR_MODEL",
  "embedding_model": "YOUR_EMBEDDING_MODEL",
  "api_key_env": "HINDSIGHT_LITE_API_KEY",
  "embedding_api_key_env": "HINDSIGHT_LITE_EMBEDDING_API_KEY",
  "embedding_timeout_seconds": 60
}
```

Never commit real API keys.

## Verification

After installation:

```bash
bash scripts/doctor.sh
```

The doctor checks package version, plugin discovery, config presence, SQLite FTS5 support, and the configured Hermes provider without printing secrets.

## Privacy

This repository must never contain:

- API keys or OAuth tokens
- `.env` files
- actual USER.md or MEMORY.md contents
- Google / Telegram / WhatsApp credentials
- phone numbers, contacts, private messages, or account IDs
- device backups or databases
- absolute paths copied from a private device

See `SECURITY.md`.
