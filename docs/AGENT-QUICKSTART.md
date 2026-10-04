# IRE agent quickstart

For coding agents and scripts that need today's model pick. Humans: read [START-HERE.md](../START-HERE.md). `AGENTS.md` in this repo is for the maintainer's own agents; don't follow it.

## Contract

- Feed (GET, no auth): `https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v2/today.json`
- Schema (JSON Schema 2020-12): `.../data/ire-feed/feed/v2/schema.json`
- Day history: `.../data/ire-feed/feed/v2/days/YYYY-MM-DD.json`, listed in `.../feed/v2/index.json`
- `schema_version` is `ire-feed/v2`. Refuse any other major version. (`feed/v1/` still serves the same data in the old shape, deprecated.)
- Open-weight models only. Every entry has `open_weight: true` and `licence` (`name`, `url`, `weights_url`). No official-price or discount fields.
- The feed never contains a key. You need your own `INFERHUB_API_KEY`. Read it from the environment; never log it, never put it in a URL.
- One refresh per day, run by the GitHub Action `.github/workflows/daily-refresh.yml` at 11:10 UTC and published on the orphan branch `data/ire-feed`. Cache for hours, not seconds.

## Pick a route

```text
GET today.json
if now > stale_after: warn the user, continue
tier = "cheap" or "strongest_open"
pick = first e in tiers[tier].entries where e.recommended == true
model = pick.best_route              # e.g. "alicn/deepseek-v4.1-flash"
fallbacks = pick.routes minus model  # same model family on other rails
```

Entry fields you'll use:

| Field | Meaning |
| --- | --- |
| `rank` | Position within the tier, 1 is best |
| `recommended` | `false` means a gate failed; `gate_reasons` says which |
| `best_route` | Exact `model` string to send |
| `price_usd_per_mtok.input` / `.output` | Lowest listed ask per 1M tokens at the tier's `as_of`; served price can be higher |
| `health.status` | `healthy`, `insufficient_data`, `degraded`, `failing`, `unavailable`, or null; from the public platform-wide status page |
| `confidence` | `low` while capability is a carried-over prior |
| `system_prompt_handling`, `preferred_endpoint`, `caveats` | Route quirks; see below |
| `licence.name` / `.url` / `.weights_url` | The model's licence and where the weights are published |
| `routes` | All rails carrying this model family |

Provenance at the top level: `generated_at`, `day_et`, `stale_after`, `code_commit`, `source_repo`, `snapshot_sha256`, and `sources.*.sha256` for each source list. Each tier also has its own `as_of` and `snapshot_sha256`.

## Call it

OpenAI-compatible: `POST https://api.inferhub.dev/v1/chat/completions`, header `Authorization: Bearer $INFERHUB_API_KEY`, body `{"model": "<best_route>", "messages": [...]}`.

Anthropic-compatible (Claude Code): `ANTHROPIC_BASE_URL=https://api.inferhub.dev` (no `/v1`), `ANTHROPIC_API_KEY=$INFERHUB_API_KEY` (sent as `x-api-key`), `ANTHROPIC_MODEL=<best_route>`.

LiteLLM: `model: openai/<best_route>`, `api_base: https://api.inferhub.dev/v1`, `api_key: os.environ/INFERHUB_API_KEY`.

## Route prefixes

Text before the first `/` is the provider rail: `cx` OpenAI Codex, `cb` CodeBuddy, `cbcn` CodeBuddy CN, `cc` Claude Code, `ag` Antigravity, `ali` Qwencloud/Alibaba, `alicn` Qwencloud/Alibaba CN, `mm` MiniMax, `mmcn` MiniMax CN, `zai` Z.AI GLM Coding Plan, `ocg` OpenCode Go, `mimo` Xiaomi MiMo, `cp` ClinePass, `cmc` Command Code. Live list: `GET https://inferhub.dev/api/status` (`families[].prefix`).

## Prompt changes on some rails

`cb/` and `cbcn/` prepend a short system note unless prompt filtering is off for the key; `cc/` prepends a one-line client header.

## Self-check

`pnpm ire:doctor --json` from a checkout prints `{"schema_version": "ire-doctor/v1", "ok": ..., "results": [...], "next_steps": [...]}`. Exit 1 means a check failed. It makes two public GETs and never sends or prints the key.

## Limits

Rankings, not guarantees. No workload testing, no account-specific health, no privacy claims about any rail. Unknown model families may be unscored.
