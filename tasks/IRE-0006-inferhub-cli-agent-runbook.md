# IRE-0006 — Durable BYOK coding CLI and long-running task runbook

GitHub issue: [#27](https://github.com/Pukujan/inference-recommendation-engine/issues/27),
a sub-issue of [#8](https://github.com/Pukujan/inference-recommendation-engine/issues/8).

## Status

Issue #24 was closed on 2026-09-24 after PRs #25 and #26 had merged with required CI. This task extends the InferHub setup guide and makes it mandatory reading before any BYOK coding CLI launch or spawn. Documentation-only; no model request and no credential access are part of this checkpoint.

## Goal

Preserve a usable, repo-owned setup and operations guide for using the configured InferHub API key from remote-inference coding CLIs, including nested CLI/subagent routing, full authorized tools, streaming, liveness, retries, resume, and durable handoff.

## Decisions

- Keep `docs/INFERHUB-API-SETUP.md` as the canonical guide linked from `AGENTS.md`.
- State the OpenAI-compatible URL as `https://api.inferhub.dev/v1`; Anthropic-compatible clients and Claude Code use `https://api.inferhub.dev`.
- Treat each nested CLI and each native subagent as an independently routed process/agent. Require explicit provider/model configuration and identity/usage verification at every hop.
- Distinguish direct native delegation from launching a second CLI which then spawns its own native children.
- Explain that 3–4 minutes of silence alone does not prove the process exited; use stream events, tool/process state, configured idle timeout, and exit status.
- Prefer bounded retries for confirmed transient failures, session resume after interruption, and reconciliation before replaying a turn that may have run tools.
- Keep credentials and raw/private receipts out of the public repository. No inference call is needed for this documentation task.

## Files in scope

- `AGENTS.md`
- `docs/INFERHUB-API-SETUP.md`
- `tasks/IRE-0005-codex-harness-permissions.md`
- `tasks/IRE-0006-inferhub-cli-agent-runbook.md`
- `checkpoints/CURRENT.md`
- `src/check-public.mjs`

## Acceptance criteria

- `AGENTS.md` directs agents to read the runbook before configuring, launching, or spawning any BYOK coding CLI.
- The runbook documents exact InferHub URL/auth mapping, secret-source handling, and separates remote inference from host-side tools.
- Codex CLI, Claude Code, Pi, and generic compatible CLI guidance states which parts are native, extension-specific, or separate processes.
- The Astra Codex root → separately launched InferHub Luna Codex CLI → Luna CLI native subagents pattern explains how to verify each route and distinct process/agent identity without mislabeling the topology.
- Streaming/liveness guidance explains long quiet periods, Codex idle/retry controls, output draining, bounded retries, resume, and no blind replay after ambiguous tool execution.
- Handoffs require durable GitHub checkpoint/status and IDs while private receipt details and credentials stay private.
- No model request is made; repository gates and required GitHub CI pass.

## Checkpoint log

- 2026-09-24: Created issue #27 and linked it under parent #8.
- 2026-09-24: Closed issue #24 after merged PRs #25 and #26 recorded the completed authorized Codex harness, local gates, and required CI.
- 2026-09-24: Extended the existing InferHub guide with per-CLI setup, nested process/subagent routing, stream/liveness handling, retry/resume, and handoff requirements. Added mandatory runbook reading to `AGENTS.md`.
- 2026-09-24: No CLI was launched and no inference or credential access occurred during this documentation checkpoint.

## Next action

Run the required local gates, deliver this explicit-file checkpoint via the checkpoint helper, and confirm the GitHub PR's required checks and merge before closing issue #27.
