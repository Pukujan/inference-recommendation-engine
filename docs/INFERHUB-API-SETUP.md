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

## Run an authorized long task with Codex CLI

From a repository checkout, use the repository runner:

```powershell
.\run_codex_harness.ps1 -Model "<configured-model-id>" -Prompt "<authorized task>"
```

The runner explicitly sets `workspace-write`, `approval_policy=never`, and
workspace network access for tool commands. It streams Codex JSONL progress
and returns the CLI's exit code. The installed Codex configuration continues
to supply the provider and credential; the runner does not replace or print
them. Codex `exec` otherwise starts read-only unless the caller selects a
writable sandbox, so agents should use the runner instead of making their own
permission choice. See the official [Codex non-interactive mode
documentation](https://developers.openai.com/codex/non-interactive-mode) and
[sandbox settings](https://developers.openai.com/codex/sandboxing).

This mode authorizes changes and command execution inside the repository
workspace without additional routine permission prompts. It does not disable
the project's task, checkpoint, credential, or receipt requirements.
