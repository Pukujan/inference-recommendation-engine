# BYOK coding CLI setup and long-running agent runbook

This is the repository's canonical guide for coding CLIs that use InferHub or
another Bring Your Own Key (BYOK) OpenAI/Anthropic-compatible inference
endpoint. Read it before configuring, launching, or spawning such a CLI.
Inference is remote through the configured API; the coding CLI, shell,
repository tools, and nested processes run on the host where they are launched.
This guide does not authorize a paid model request: follow the task's existing
approval and receipt requirements.

## InferHub endpoint and credential setup

| Client protocol | Base URL | Authentication |
| --- | --- | --- |
| OpenAI-compatible SDK/CLI | `https://api.inferhub.dev/v1` | API key field or `Authorization: Bearer <key>` |
| Anthropic-compatible SDK/Claude Code | `https://api.inferhub.dev` | API key field or `x-api-key: <key>` |

Anthropic clients append `/v1` themselves; putting `/v1` in the configured
base URL duplicates the path. InferHub documents both protocols and its
interactive CLI setup helper at [InferHub Docs](https://inferhub.dev/docs).
Run `npx @inferhub/helper` in an interactive trusted shell to configure its
supported coding CLIs and select a model. It validates access through the
model catalog endpoint. Check the live catalog and current pricing before
choosing a route because model IDs, availability, and price can change.

### Project model and price preference

For this project, treat a model or route with a current estimated cost below
**$0.10 USDC per 1 million tokens** as *effectively free* for recommendation
and long-running experiment selection. This is a practical price threshold,
not a claim that inference is literally free. Prefer currently
recommendation-eligible entries in the project's Top 20 or Top 20+ lists when
they meet the threshold. Check the current recommendation snapshot, live route
availability, route-level price, and tool/stream support before launch; record
which snapshot and route informed the choice. If no eligible route meets the
threshold, say so and identify the nearest eligible option instead of
silently substituting a research-only or more expensive route.

Read the key from the authorized secret store or process environment. Never
put real keys in repository files, prompts, command arguments, task notes,
shell history, receipts, or logs. A process receives only environment
variables its parent passes; explicitly reload or pass the key to each nested
CLI from the approved credential source. Never print the key to verify it.
Use endpoint/model identity, successful response metadata, and provider usage
records instead.

For GitHub Actions, use a repository or environment secret, expose it only to
the trusted job step that needs inference, and do not make it available to
untrusted pull-request code. This is optional for remote runners; it is not
needed when the authorized task runs on another host.

## Full authorized tool access and repository execution

Inference routing and tool execution are separate. Selecting InferHub changes
where model requests go; it does not move shell or file tools to InferHub.
Give the CLI the task-authorized repository workspace and tools using the
repository's established runner/policy. Do not silently downgrade an
authorized coding task to read-only or add routine approval prompts after
authorization. Keep execution scoped to the authorized repository and retain
its issue, checkpoint, credential, and receipt rules.

### Codex CLI

Use `npx @inferhub/helper` for provider setup, then the repository runner for
an authorized long-running task:

```powershell
.\run_codex_harness.ps1 -Model "<configured-provider/model-id>" -Prompt "<authorized task>"
```

The runner passes workspace-write, no routine approval prompts, network access
for workspace tool execution, and JSONL progress output. It returns Codex's
process exit code. Provider and key configuration remain in the CLI's approved
configuration/secret source. On Windows, launch child commands through the
available PowerShell 7 `pwsh` executable when Windows PowerShell fails to
start the CLI; capture child exit status and diagnostic output in the private
receipt.

Codex custom providers use a provider ID, API base URL, environment key name,
and wire protocol (`responses` for a provider that supports Responses API).
Prefer the InferHub helper over hand-written provider setup. Codex supports
`stream_idle_timeout_ms`, `stream_max_retries`, and `request_max_retries`.
`agents.default_subagent_model` can set a default for native child agents, but
verify the model/provider route actually used for each child. See the [Codex
configuration reference](https://developers.openai.com/codex/config-reference)
and [non-interactive mode](https://developers.openai.com/codex/non-interactive-mode).

### Claude Code

Use InferHub's helper where available. A direct process setup uses Claude
Code's Anthropic-compatible settings; load the key from the approved secret
source in the launching process:

```powershell
$env:ANTHROPIC_BASE_URL = 'https://api.inferhub.dev'
$env:ANTHROPIC_API_KEY = '<load from approved secret source>'
$env:ANTHROPIC_MODEL = '<configured-provider/model-id>'
claude --permission-mode bypassPermissions --output-format stream-json --verbose --include-partial-messages -p '<authorized task>'
```

`ANTHROPIC_API_KEY` is sent as `x-api-key`. This command uses Claude Code's
no-prompt permission mode for authorized tool execution; the CLI flag itself
does not sandbox commands. For nested agents, set and
verify the child model route explicitly; do not infer it from the root model.
Claude Code supports streamed JSON output and session continuation with
`--resume`. Check the current [CLI reference](https://code.claude.com/docs/en/cli-usage),
[subagent guide](https://code.claude.com/docs/en/sub-agents), and [environment
variable reference](https://code.claude.com/docs/en/env-vars) before adopting
flags or variables across CLI versions.

### Pi coding agent

Pi can use a compatible OpenAI Chat Completions endpoint through `models.json`;
do not assume an InferHub route supports every API mode or tool-calling
behavior. Confirm the endpoint format and model route first. Configuration
shape:

```json
{
  "providers": {
    "inferhub": {
      "baseUrl": "https://api.inferhub.dev/v1",
      "api": "openai-completions",
      "apiKey": "$INFERHUB_API_KEY",
      "models": [{ "id": "<configured-provider/model-id>" }]
    }
  }
}
```

`INFERHUB_API_KEY` must exist in the process that starts Pi. Pi's `--mode json`
emits structured event lines, including partial messages and tool lifecycle
events; keep draining stdout. Resume with `pi --continue` or `pi --resume`.
Pi subagents are extension/package-specific: verify which extension starts
children, whether they are separate Pi processes, what model each selects,
and how events are surfaced. See Pi's [compatible endpoint guide](https://pi.dev/docs/latest/models),
[JSON event stream](https://pi.dev/docs/latest/json), and [session guide](https://pi.dev/docs/latest/sessions).

Other coding CLIs follow the same pattern: use the protocol they implement,
corresponding base URL and auth header, a live supported model ID, the CLI's
authorized full tool mode, and its structured streaming and resume interface.
An “OpenAI compatible” label alone does not prove that the specific route
supports tool calls or streaming; verify both.

## Nested CLI and subagent routing

For long tasks that need another coding agent/model's own native subagents, use
an explicit process chain:

```text
authorized Astra Codex CLI root
  -> launches a second Codex CLI process routed to InferHub Luna
       -> that Luna CLI creates its own native Luna subagents
```

The child CLI is a new process, not a native child agent of the Astra root.
Launch it through the supported shell/PowerShell executable and capture
stdout, stderr, and exit code. The nested CLI must independently load the
InferHub provider/key setup, request its intended root model, and have the
authorized workspace/tool settings. Its native subagents need an explicit
supported child model/provider route and sufficient child-thread capacity. A
parent model choice alone does not guarantee the child route. Apply the same
per-process/per-child rule to Claude Code, Pi extensions, and other CLIs.

Verify delegation at every level:

1. Record root CLI identity and model/provider route.
2. Record that the nested process started, its distinct session/thread ID,
   selected route, and exit status.
3. Record each native subagent's distinct ID and route, plus a completion
   event/final result and concise output proving its assigned work ran.
4. Match root, nested, and child requests to provider usage records/model IDs.
   A successful parent response alone does not prove children used BYOK.
5. Save only required non-secret evidence in the approved private receipt
   store. Do not publish raw logs that could contain prompts, source, or keys.

If a root's direct native spawn path produces no child requests, diagnose it
separately. Do not label a sibling session or second top-level process as
native subagents. Success of the nested-process pattern does not prove the
direct native route works.

## Streaming, long silence, and liveness

Treat model token delivery, tool execution, and process liveness as separate
signals:

- A stream is active when structured events, tokens, tool status, or
  subprocess output arrive.
- The model can be silent while the CLI waits on a tool, long model
  computation, or provider/network operation. Silence for 3–4 minutes alone
  does not mean the process was killed.
- A stream is stalled when there is no stream/tool progress beyond the
  configured idle limit and process/provider state shows no active work.
- A process has stopped only when its exit status or supervisor confirms
  termination. Preserve its exit code and final stderr/event lines.

Codex's documented defaults include a 300,000 ms (5 minute)
`stream_idle_timeout_ms`, up to 5 `stream_max_retries`, and 4
`request_max_retries`. These are separate controls; CLI versions and provider
behavior can change, so verify the installed version's reference. Do not kill
a live tool or blindly restart a long stream just because token text is quiet.
Increase an idle timeout only when observed behavior needs it, while keeping
finite retry and supervisor limits.

Keep draining stdout and stderr while a child runs; an undrained pipe can block
the process. Prefer JSONL / stream-json / JSON event modes so a supervisor can
distinguish tool starts/results, partial text, retry events, final completion,
and process exit. A supervisor heartbeat can show that the wrapper is alive;
it is not evidence of model output.

## Retry, resume, and handoff

Use bounded retry for clear transient failures before a request was accepted,
such as connection setup failure or a documented retryable status. Preserve
per-CLI retry limits and record attempt count and outcome. Once a model may
have started a turn that can run tools, an interrupted stream has an ambiguous
result: writes or external effects may already have happened. Before retrying,
inspect process state, event/output log, working tree, private receipt, and
provider usage records.

Prefer continuing the existing session: Codex `exec resume`, Claude Code
`--resume`, or Pi `--continue`/`--resume`. If no session can resume, reconcile
completed tool actions and restart from the last durable checkpoint with a
prompt stating what is done. Do not blindly replay the original tool-writing
turn or duplicate child runs.

At handoff, record on GitHub the task/issue, branch and commit, completed
checkpoint, next action, active/stopped process state, relevant session/thread
IDs, and push/PR/CI/merge status. Keep private logs and credentials in the
approved ignored receipt store; GitHub records the public plan and durable
code/checkpoint state. Confirm the child CLI and every subagent have completed
or are explicitly handed off before marking the task done.

## References

- [InferHub API and coding CLI setup](https://inferhub.dev/docs)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Claude Code CLI usage](https://code.claude.com/docs/en/cli-usage)
- [Pi models and compatible endpoints](https://pi.dev/docs/latest/models)
- [Pi JSON event stream](https://pi.dev/docs/latest/json)
