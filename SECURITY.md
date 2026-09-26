# Security and privacy rules

This repository is public.

## Never commit

- API keys
- OAuth client secrets
- refresh/access tokens
- `.env`
- `google_token.json`
- private Hermes gateway credentials
- memory databases
- USER.md / MEMORY.md containing real user information
- phone numbers, contacts, messages, email contents
- account identifiers
- private filesystem dumps
- backups from a real device

## Configuration

Use environment-variable names in committed configuration, never the secret value itself.

Good:

```json
{"api_key_env": "HINDSIGHT_LITE_API_KEY"}
```

Bad:

```json
{"api_key": "real-secret-value"}
```

## Before every public release

Run a secret scanner and manually inspect the final archive/tree.
