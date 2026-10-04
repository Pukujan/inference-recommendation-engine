# IRE daily feed: decision and usage (IRE #75)

## Decision

**A static, versioned JSON feed is the primary way to get today's picks.** It's published to the
orphan branch `data/ire-feed` and read with a plain GET:

```
https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v1/today.json
```

| Option | Verdict | Why |
| --- | --- | --- |
| Static JSON on a data branch | **Chosen** | Nothing to run or pay for. Cacheable. Works for curl, scripts, CI, other repos and a static web page. Same pattern as `data/inferhub-price-snapshots`. No PR noise on `main`. |
| Read-only HTTP API | Not now | Needs a running service, uptime and an owner, and it would add nothing a static file doesn't already give. |
| Serverless function | No | design-bakery is leaving Vercel for gravebuster (Caddy + Cloudflare Tunnel), so we shouldn't add anything that only runs on Vercel. |
| MCP server | Later, optional | A stdio `ire-mcp` (`npx ire-mcp`) can wrap the same file as `get_today` / `get_day` tools for agents that prefer MCP. It reads the static feed; it never becomes the source. |

## Files

| Path on `data/ire-feed` | What it is |
| --- | --- |
| `feed/v1/today.json` | Today's picks: `tiers.cheap` (the Top 20, gated rows included) and `tiers.frontier` (recommended picks only) |
| `feed/v1/days/YYYY-MM-DD.json` | The same document for each ET day. Append-only. |
| `feed/v1/index.json` | Every day with its URL and sha256 |
| `feed/v1/schema.json` | JSON Schema (source: `schemas/feed.v1.schema.json` here) |

Each entry has `rank`, `model_family`, `recommended`, `gate_reasons`, `best_route`,
`price_usd_per_mtok.input/output` (the best route's lowest listed ask), `health.status/reasons`,
`confidence`, `caveats` and every `routes` id, best first.

Top-level provenance: `schema_version`, `generated_at`, `day_et`, `stale_after` (oldest list
`as_of` + 36 h), `code_commit`, `snapshot_sha256`, `sources.*.sha256` (each input list),
`source_repo`. Each tier has its own `as_of`.

## Rules for readers

- If `now > stale_after`, treat the feed as stale and say so. Don't pretend it's live.
- Prices move during the day. Check the route before a long run.
- `recommended: false` rows are listed on purpose. Read `gate_reasons` before using one.
- `cx/` routes send your system prompt as a developer message (see each row's `caveats`).

## Guard

`feed.py check DIR` fails if any file looks like it holds a credential: `sk-` keys, bearer
tokens, `Authorization` or `x-api-key` headers, GitHub tokens, AWS keys, `*_API_KEY=` lines and
private key blocks. `write_feed` and `publish` run the same scan and refuse to write or push on a
hit. CI builds the feed from the committed lists and runs the guard on every push.

## Refresh

```bash
cd <ire checkout>/operational/telemetry/gravebuster/pipeline/ihub
python frontier.py --raw-dir "$RAW" --fetch --env-file "$ENV_FILE"   # one GET round, frontier list
python top20.py --raw-dir "$RAW"                                     # cheap Top 20 from the same bodies
python feed.py publish                                               # feed -> data/ire-feed
```

`publish` builds from the lists in the checkout it runs in. Commit the refreshed lists to `main`
through a PR as usual, or run `publish` right after the refresh to put that day's lists in the feed.
