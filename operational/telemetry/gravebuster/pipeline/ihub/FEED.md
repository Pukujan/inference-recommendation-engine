# IRE daily feed: decision and usage (IRE #75)

## Decision

**A static, versioned JSON feed is the primary way to get today's picks.** It's published to the
orphan branch `data/ire-feed` and read with a plain GET:

```
https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v2/today.json
```

The feed only lists **open-weight models**: families whose weights are published for download
under a licence we've checked. See "Open-weight only" below.

| Option | Verdict | Why |
| --- | --- | --- |
| Static JSON on a data branch | **Chosen** | Nothing to run or pay for. Cacheable. Works for curl, scripts, CI, other repos and a static web page. Same pattern as `data/inferhub-price-snapshots`. No PR noise on `main`. |
| Read-only HTTP API | Not now | Needs a running service, uptime and an owner, and it would add nothing a static file doesn't already give. |
| Serverless function | No | design-bakery is leaving Vercel for gravebuster (Caddy + Cloudflare Tunnel), so we shouldn't add anything that only runs on Vercel. |
| MCP server | Later, optional | A stdio `ire-mcp` (`npx ire-mcp`) can wrap the same file as `get_today` / `get_day` tools for agents that prefer MCP. It reads the static feed; it never becomes the source. |

## Files

| Path on `data/ire-feed` | What it is |
| --- | --- |
| `feed/v2/today.json` | Today's picks: `tiers.cheap` (the open-weight rows of the Top 20, gated rows included, ranked 1..n) and `tiers.strongest_open` (the strongest recommended open-weight models) |
| `feed/v2/days/YYYY-MM-DD.json` | The same document for each ET day. Append-only. |
| `feed/v2/index.json` | Every day with its URL and sha256 |
| `feed/v2/schema.json` | JSON Schema (source: `schemas/feed.v2.schema.json` here) |
| `feed/v1/...` | Deprecated. The same open-weight data in the old shape, with `strongest_open` under the old `frontier` key, plus `deprecated` and `superseded_by`. Readers should move to v2. |

Each entry has `rank`, `model_family`, `open_weight` (always `true`), `licence` (`name`, `url`,
`weights_url`), `recommended`, `gate_reasons`, `best_route`,
`price_usd_per_mtok.input/output` (the best route's lowest listed ask), `health.status/reasons`,
`confidence`, `caveats` and every `routes` id, best first.

Top-level provenance: `schema_version`, `generated_at`, `day_et`, `stale_after` (oldest list
`as_of` + 36 h), `code_commit`, `snapshot_sha256`, `sources.*.sha256` (each input list),
`source_repo`, `open_weight_only` and `licences_url`. Each tier has its own `as_of`.

The feed has no official-price or discount fields. Prices are the listed ask and nothing else.

## Rules for readers

- If `now > stale_after`, treat the feed as stale and say so. Don't pretend it's live.
- Prices move during the day. Check the route before a long run.
- `recommended: false` rows are listed on purpose. Read `gate_reasons` before using one.
- Read the `licence` before you build on a model. Some open-weight licences (MiniMax, for one)
  restrict commercial use.

## Open-weight only

`model_licences.v1.json` (next to `feed.py`) maps each model family to its vendor, licence,
licence URL and weights URL, checked against the vendor's Hugging Face repository. A family goes
into the feed only when it's listed there with `open_weight: true`. Closed, API-only families
(GPT, Claude, Gemini, Grok, Muse Spark) are listed with `open_weight: false` or not at all, and
families we couldn't match to a published checkpoint are listed with `open_weight: null`. Both
stay out. To add a family, check its weights and licence, add the record, and open a PR.

This applies to the public feed only. The lists in `lists/` and `frontier.py` output are
unchanged.

## Guard

`feed.py check DIR` fails if a `today.json` or `days/*.json` names a closed model family or
vendor in a model, vendor or route field, if an entry isn't marked `open_weight` or has no
verified licence record, or if any key mentions an official price or discount. It also fails if
any file looks like it holds a credential: `sk-` keys, bearer
tokens, `Authorization` or `x-api-key` headers, GitHub tokens, AWS keys, `*_API_KEY=` lines and
private key blocks. `write_feed` and `publish` run the same scan and refuse to write or push on a
hit. CI builds the feed from the committed lists and runs the guard on every push, and
`operational/tests/test_telemetry_ihub_feed.py` covers the open-weight rules.

## Refresh

The refresh runs as a GitHub Action, [`.github/workflows/daily-refresh.yml`](../../../../../.github/workflows/daily-refresh.yml)
(IRE #84). It runs every day at 11:10 UTC (7:10 AM EDT, 6:10 AM EST) and can be started by hand
from the Actions tab (`workflow_dispatch`). Each run:

1. runs `frontier.py --fetch` with the `INFERHUB_API_KEY` repo secret (three GETs: `/api/catalog`
   with the key, `/api/status` and `/api/market` public), then `top20.py` on the same bodies;
2. builds the feed and runs `feed.py check`, so a closed model or a credential fails the run before
   anything is pushed;
3. runs `feed.py publish`, which pushes to `data/ire-feed` only when the feed changed;
4. opens or updates a PR from `auto/daily-lists` with the `lists/` changes and turns on squash
   auto-merge, so they reach `main` once `test` passes. It never commits to `main`.

CI runs started by a bot PR wait for approval, so the refresh approves its own PR's `ci.yml` run
and `test` gates the merge as usual. This needs the repo setting "Allow GitHub Actions to create
and approve pull requests" (Settings > Actions > General). A failed step fails the run, and GitHub emails
the repo owner. The key is only passed as an environment variable and is never printed.

To run the same steps by hand:

```bash
cd <ire checkout>/operational/telemetry/gravebuster/pipeline/ihub
python frontier.py --raw-dir "$RAW" --fetch --env-file "$ENV_FILE"   # one GET round, frontier list
python top20.py --raw-dir "$RAW"                                     # cheap Top 20 from the same bodies
python feed.py publish                                               # feed -> data/ire-feed
```

`publish` builds from the lists in the checkout it runs in, so run it right after the refresh to put
that day's lists in the feed. The refreshed lists still go to `main` through a PR.
