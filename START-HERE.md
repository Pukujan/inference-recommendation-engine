# Start here

You have an InferHub key and want to know which model to point your tools at today. IRE answers that once a day. It reads InferHub's live price ladder and route health, applies a written price and supply policy, and publishes two short lists: a cheap tier (up to 20 models) and a frontier tier. Each pick names the exact route to call, what it costs right now, and why it was or wasn't recommended.

IRE doesn't proxy anything or hold your key. You call InferHub yourself, with your own key, using the route name IRE picked.

## Check your setup

```bash
pnpm install
pnpm ire:doctor
```

The doctor checks that `INFERHUB_API_KEY` is set (it never prints the value), makes one public GET to InferHub's status page, fetches today's picks, tells you how old they are, and prints the next commands to run.

## Today's picks

https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v1/today.json

Open it in a browser. `tiers.cheap.entries` and `tiers.frontier.entries` are ranked lists. Take the first entry with `"recommended": true` and copy its `best_route`, for example `alicn/deepseek-v4.1-flash`. Prices are the lowest listed ask in USD per million tokens when the list was built, and you can be billed more than that. Past days live under `feed/v1/days/` on the same branch. If `stale_after` is in the past, the daily run was missed; the picks still work, but check the price on InferHub first.

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

## The cx/ caveat

On `cx/` routes, your system prompt is sent upstream as a developer message, and the upstream's required instructions field is filled with "You are a helpful assistant.". Most chat use won't notice. If your tool relies on a strict system prompt, call the native `/v1/responses` endpoint instead of `/v1/chat/completions`; that path keeps your instructions as sent. The feed repeats this in each `cx/` entry's `caveats`.

## What IRE doesn't promise

The picks are a ranking, not a guarantee. Health comes from InferHub's public, platform-wide status page, so it says nothing about your account, and a route that was healthy at build time can fail an hour later. The price you're billed is whatever InferHub charges when you call. Capability scores are a carried-over prior with low confidence until fresh benchmark inputs exist, so a model new to InferHub may show up unscored. IRE doesn't test your workload, and it says nothing about privacy or data handling on any rail. Read each provider's terms for that.
