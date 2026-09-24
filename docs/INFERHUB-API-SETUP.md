# InferHub API setup

This guide records the API base URLs for connecting SDKs and coding agents to
InferHub. Model inference is served by InferHub; a coding agent's harness and
any tools it runs execute in the environment where that agent is launched.
This setup guide does not enable or authorize paid inference.

## Base URLs

| Client | Base URL | Why |
| --- | --- | --- |
| OpenAI-compatible SDK | `https://api.inferhub.dev/v1` | The SDK uses the OpenAI `/v1` API path. |
| Anthropic-compatible SDK or Claude Code | `https://api.inferhub.dev` | These clients append `/v1` themselves. Adding `/v1` here would produce a duplicated `/v1/v1` path. |

InferHub's [API documentation](https://inferhub.dev/docs) shows both SDK
configurations. Use the OpenAI API key field or `Authorization: Bearer` for
OpenAI-compatible clients; use the Anthropic API key field or `x-api-key` for
Anthropic-compatible clients.

## API key handling

Read the key from an environment variable or a secret store. Never put a real
key in source files, task notes, command history, logs, or benchmark artifacts.
For automation, store it as a GitHub Actions secret and expose it only to a
trusted job that needs InferHub access. Do not expose it to pull-request code.

## Codex CLI

InferHub documents `npx @inferhub/helper` as its setup path for Codex. The
helper validates a key through `GET /v1/models` and can write the Codex
configuration. Codex CLI can therefore act as the agent harness while model
requests go to InferHub; the CLI's workspace and tool commands still run on its
host, such as a GitHub Actions runner. No local model runtime is required.

Model IDs and provider availability can change. Check InferHub's live
[model catalog and pricing](https://inferhub.dev/pricing) before selecting a
model or setting a spend limit. Do not assume that adding an API key or this
configuration means a paid run has been approved.
