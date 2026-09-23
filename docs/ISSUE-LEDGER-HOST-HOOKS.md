# Host hook integration boundary

This document records the current, verified host surfaces for automatically
submitting bounded failure observations. It does not claim that hooks are
installed globally or that a hook can prove a model-quality claim.

## Verified capability snapshot

The local host versions checked for the current adapter templates were:

- OpenCode `1.18.31`;
- Claude Code `2.1.263`.

OpenCode’s official plugin documentation exposes project plugins under
`.opencode/plugins/` and lists `session.error` among the session events. See
[OpenCode plugins](https://dev.opencode.ai/docs/plugins/).

Claude Code’s official hook reference documents project `.claude/settings.json`
command hooks and the `StopFailure` event. `StopFailure` runs when a turn ends
because of an API error and provides a bounded error type suitable for a
report-only event. See [Claude Code hooks](https://code.claude.com/docs/en/hooks).

## Templates

- [OpenCode plugin](../integrations/opencode-issue-ledger.js)
- [Claude hook](../integrations/claude-code-issue-ledger-hook.py)
- [Claude settings fragment](../integrations/claude-code-issue-ledger.settings.json)
- [OpenCode environment example](../integrations/opencode-issue-ledger.env.example)

Both templates call the versioned `issue-ledger/report/v1` sidecar. They append
`report_submitted` only, so every observation remains `reported_only` until the
normal receipt/reproduction/verifier gates pass.

The local contract suite exercises both templates with synthetic events: Claude
`StopFailure` input and an OpenCode `session.error` input under a mocked Bun
spawn. These tests verify bounded normalized arguments and do not install or
invoke either host in a live project.

## Deliberate data boundary

The templates do not read prompts, transcripts, assistant messages, tool
arguments, headers, credentials, or response bodies. They report only session
identity, configured route/provider labels, stream mode, and a bounded failure
classification. OpenCode and Claude do not guarantee the same model metadata
on every failure event, so absent model data remains `null`/unknown rather
than being inferred from stale configuration.

The hooks are non-blocking observers. A reporting failure must not change the
agent’s provider/session behavior, and no hook installs itself or changes a
user’s global configuration.

## Installation boundary

Installation is explicit:

1. Set `IRE_ISSUE_LEDGER_DB` and `IRE_ISSUE_LEDGER_AGENT_SCRIPT` in the target
   runtime environment. Point the sidecar at this checkout's
   `operational/scripts/issue_ledger_agent.py`.
2. Copy the OpenCode template into `.opencode/plugins/`, or add it through the
   configured plugin path.
3. Merge the Claude settings fragment into the target project’s
   `.claude/settings.json`.
4. Send a synthetic hook payload and inspect the local ledger before enabling
   it for live sessions.

The repository does not silently modify those host settings. Codex, ChatGPT
Web, Hermes, and other hosts remain sidecar-only until their actual hook
surface is separately verified.
