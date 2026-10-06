# Start here

You have an InferHub key and want to know which model to point your tools at today. IRE answers that once a day. It reads InferHub's live price ladder and route health, applies a written price and supply policy, and publishes two short lists of open-weight models: a cheap tier and the strongest open-weight models it can find a healthy route for. Each pick names the exact route to call, what it costs right now, and why it was or wasn't recommended.

IRE doesn't proxy anything or hold your key. You call InferHub yourself, with your own key, using the route name IRE picked.

## Check your setup

```bash
pnpm install
pnpm ire:doctor
```

The doctor checks that `INFERHUB_API_KEY` is set (it never prints the value), makes one public GET to InferHub's status page, fetches today's picks, tells you how old they are, and prints the next commands to run.

## Today's picks

https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v2/today.json

Open it in a browser. `tiers.cheap.entries` and `tiers.strongest_open.entries` are ranked lists. Take the first entry with `"recommended": true` and copy its `best_route`, for example `alicn/deepseek-v4.1-flash`. Prices are the lowest listed ask in USD per million tokens when the list was built, and you can be billed more than that. Every pick in those two tiers is an open-weight model, and each entry links its licence and the published weights. The feed may also carry a `tiers.utility` list of image and multimodal models; those entries state the open-weight verdict per row and are never recommended unless a licence and weights were verified. Past days live under `feed/v2/days/` on the same branch. A scheduled GitHub Action ([daily-refresh](.github/workflows/daily-refresh.yml)) rebuilds the picks every day at 11:10 UTC (7:10 AM EDT). If `stale_after` is in the past, the daily run was missed; the picks still work, but check the price on InferHub first.

## Use a pick with your own key

Set the key once in your shell:

```bash
export INFERHUB_API_KEY=...            # PowerShell: $env:INFERHUB_API_KEY = "..."
```

Claude Code talks to InferHub's Anthropic-compatible endpoint. The base URL has no `/v1`, and Claude Code sends the key as `x-api-key`:

```bash
ANTHROPIC_BASE_URL=https://api.inferhub.dev \
ANTHROPIC_API_KEY=$INFERHUB_API_KEY \
ANTHROPIC_MODEL=alicn/deepseek-v4.1-flash \
claude
```

LiteLLM uses the OpenAI-compatible endpoint. Put `openai/` in front of the route so LiteLLM knows which client to use:

```yaml
model_list:
  - model_name: ire-cheap
    litellm_params:
      model: openai/alicn/deepseek-v4.1-flash
      api_base: https://api.inferhub.dev/v1
      api_key: os.environ/INFERHUB_API_KEY
```

curl, same endpoint, key as a Bearer token:

```bash
curl https://api.inferhub.dev/v1/chat/completions \
  -H "Authorization: Bearer $INFERHUB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"alicn/deepseek-v4.1-flash","messages":[{"role":"user","content":"hi"}]}'
```

Swap in whatever route today's feed names. Routes change from day to day as prices move.

## What the route prefix means

The part before the slash is the provider rail InferHub sends your request through. The same model can sit on several rails at different prices, which is why IRE names a route and not just a model.

| Prefix | Rail |
| --- | --- |
| `cx/` | OpenAI Codex |
| `cb/`, `cbcn/` | CodeBuddy, CodeBuddy CN |
| `cc/` | Claude Code |
| `ag/` | Antigravity |
| `ali/`, `alicn/` | Qwencloud/Alibaba, and its CN rail |
| `mm/`, `mmcn/` | MiniMax, MiniMax CN |
| `zai/` | Z.AI GLM Coding Plan |
| `ocg/` | OpenCode Go |
| `mimo/` | Xiaomi MiMo |
| `cp/` | ClinePass |
| `cmc/` | Command Code |

Some rails change your prompt on the way through. `cb/` adds a short system note ahead of yours, which you can switch off per key under Dashboard → API keys → Prompt filtering. `cc/` adds a one-line client header before your system prompt.

## Why only open-weight models

The feed only lists models whose weights are published, so you can run them elsewhere if a rail disappears. `operational/telemetry/gravebuster/pipeline/ihub/model_licences.v1.json` has the licence and weights link for every family; anything we couldn't check stays out. Open-weight doesn't always mean free for commercial use. MiniMax's licences, for example, attach conditions, so read the licence before you ship on one.

## What IRE doesn't promise

The picks are a ranking, not a guarantee. Health comes from InferHub's public, platform-wide status page, so it says nothing about your account, and a route that was healthy at build time can fail an hour later. The price you're billed is whatever InferHub charges when you call. Capability scores are a carried-over prior with low confidence until fresh benchmark inputs exist, so a model new to InferHub may show up unscored. IRE doesn't test your workload, and it says nothing about privacy or data handling on any rail. Read each provider's terms for that.
