# ihub: GET-only InferHub market + billing collector (IRE #46 M2/M3)

Runs on the telemetry host (gravebuster) as short oneshot systemd jobs. It never holds memory
between runs, never calls a paid endpoint (the client only implements `GET`), and never logs the
key. Raw responses stay local; GitHub gets code, schemas, docs and a small daily snapshot CSV.

## Sources (all GET; OpenAPI https://inferhub.dev/api/openapi.json)
| endpoint | auth | cadence | used for |
|---|---|---|---|
| `https://inferhub.dev/api/catalog` | key | 5 min | **order book**: per rail `activeProviders`, `enabled`; per model `officialIn/Out` and `pricePointsIn`/`pricePointsOut` = `[[price_per_1M, avail_count], ...]` (the tiers with "avail." counts shown on dashboard/models). The live field names are undocumented; the OpenAPI schema calls them `asksIn`/`asksOut` (one entry per provider). Both shapes are parsed. No HTML scraping needed. |
| `https://inferhub.dev/api/market` | public | 5 min | min/max ask and `lastRate` (blended $/1M of the last successful trade) per route |
| `https://inferhub.dev/api/status` | public | 5 min | per-rail state, availability now/24h/7d, p50/p95 duration, TTFT, TPS, 2-hour history |
| `https://api.inferhub.dev/v1/me/usage?window=day&tz=America/New_York` | key | 5 min | balance, today's (ET) spend and ok-request count |
| `https://inferhub.dev/api/usage/logs` | key | 15 min | one row per request attempt (see fields below) |
| `/v1/models`, `/budgets`, `/budgets/default`, `/budgets/aliases`, `/usage/breakdown?range=24h`, `/pricing/cache`, `/pricing/config` | key/public | hourly | raw-only config/aggregate snapshots (ids incl. aliases for the daily CSV, caps, per-rail breakdown, cache stats) |

Request-log fields: `id` (request_logs UUID), `ts` (UTC), `status`, `http_status`, `prompt_tokens`,
`completion_tokens`, `cached_tokens`, `cache_write_tokens`, `cost_consumer_usdc`,
`ask_input_per_mtok` / `ask_output_per_mtok` (price actually served; null on failed rows), `region`
(empty so far), `model`, `upstream_label` (rail), `ttft_ms`, `duration_ms`, `routing_ms`.
**No seller/provider id is exposed**; the served ask is the best seller proxy, and
`fact_request_billing` maps it to the order-book tier (rank + avail count) valid at request time.
Unknown new keys are kept verbatim in `extras_json`.

Rate limits: management API + MCP share 30 req/min per account. Management calls are paced at
>= 2.5 s (<= 24/min); 429 honours `Retry-After` (3 retries, then the endpoint is skipped until the
next run). A normal 5-min run makes 1 management call (+6 hourly), a 15-min log run 1-3.

## Layout (`/srv/agent-telemetry/data/inferhub/`)
```
raw/<endpoint>/<YYYY-MM-DD>/<HHMMSSmmm>Z-<sha8>.json.zst   verbatim body, zstd 12, verified round trip
raw/manifest.jsonl          every fetch: endpoint, url (no key), status, fetched_at, sha256, bytes, zst sha256,
                            path (null + dup_of_previous when identical to the previous body of that endpoint)
parquet/<table>/day=D/part-<run>.parquet   deduped append-only parts (_key per row)
  rails, routes (every fetch), tiers (change-data-capture: a route's full book is written only when a
  tier price/count, official price or enabled flag changes), catalog_fetches, market, status,
  status_history (only changed windows), balance, billing (+ billing_revisions: same id, new content)
modeled/snap-<run>/*.parquet, modeled/current -> newest (last 3 kept), _build.json (row counts)
exports/<ET day>/pricing.csv, providers.csv, route-daily-summary.csv, manifest.json
state/ (watermarks, last book hash per route, lock)   meta/runs.jsonl (one line per run: counts, RSS, errors)
```
Key file: `/srv/agent-telemetry/secrets/inferhub.env` (0600, dir 0700, owner yoav) with
`INFERHUB_API_KEY` (+ optional `INFERHUB_API_URL`, `INFERHUB_MANAGEMENT_URL`). Not in the repo; the
deploy script never touches it.

## Modeled tables (`modeled/current/`)
| table | grain |
|---|---|
| `fact_price_snapshot` | route x side (in/out) x tier: `tier_price`, `avail_count`, `cum_avail`, `official_price`, `discount_pct`, `ts`/`valid_from`, `valid_to`, `last_seen_at`, `is_current`, `under_policy_threshold` |
| `fact_route_price` | route x catalog fetch (5 min): min ask in/out + avail at the min tier, capacity-weighted median ask, tiers, total listings |
| `fact_rail_snapshot` | rail x fetch: `active_providers`, enabled/status |
| `fact_market_quote` | route x market snapshot: min/max ask, lastRate |
| `fact_route_status`, `fact_route_status_history` | rail x status computation; rail x 2h window |
| `fact_balance` | fetch: balance, today's spend (ET), ok requests |
| `fact_request_billing` | request_logs id: ts, day_et, route, rail, status/http, tokens in/out/cached/cache-write, billed cost, served ask in/out, served tier rank + avail, min ask at ts, ttft/duration/routing ms, extras_json |
| `inferhub_dim_route` | route: latest attributes, first/last seen |
| `fact_route_reliability` | route x window (1h/24h/7d, ending at the last log fetch): requests, ok, client vs service errors, error-type counts, success rates, ttft/duration p50/p95, served ask + tier, live price, platform rail state |
| `fact_request_outcome` | request: error_code (telemetry, if any), error_type, error_class (upstream/client/account/unknown), class_basis; rules in `errclass.py` (11133 and uncoded cb/cbcn 400s = upstream, #40 M0.6) |
| `fact_route_error_breakdown` | route x window x (status, http_status, error_code, error_type, error_class, class_basis): counts |
| `inferhub_request_match`, `inferhub_match_summary` | lake `fact_model_requests` (primary, same route) joined to billing: `exact_tokens` = same route, tokens in and out equal, within 300 s, one-to-one closest; `time_error` = failed request without tokens -> failed billing row within 30 s |

The tables are built with plain DuckDB SQL (`sql/*.sql`), not the shared dbt project, so a 5-minute
price fetch never triggers a rebuild of the agent-run marts. They read (never write)
`data/lake/modeled/current/fact_model_requests.parquet`.

## Operations
```
systemctl list-timers 'inferhub-*'
journalctl -u inferhub-collect-fast -n 20       # also inferhub-collect-logs, inferhub-snapshot-publish
cd /srv/agent-telemetry/pipeline && venv/bin/python -m ihub fast|logs|model
venv/bin/python -m ihub backfill --since 2026-09-24T04:00:00Z     # request logs back to midnight ET
venv/bin/python -m ihub export [--day 2026-09-24]                 # CSV + manifest only
venv/bin/python -m ihub publish [--day 2026-09-24]                # export + push to the data branch
bin/ih-query.py prices astra | book cb/gpt-6-astra | drift | balance | spend | match | status | runs
bin/ih-query.py catalogue [--route R] [--status S] [--models] [--full|--csv]   # agent JSON (M4)
bin/ih-query.py reliability [route ...] [--window 24h] [--errors]              # rollups as JSON
venv/bin/python -m ihub catalogue | compact
```
Route catalogue (IRE #46 M4): `catalogue.py` joins the static lists in `lists/` (verbatim copies,
sha256-checked, never edited) with live price and `fact_route_reliability` into
`catalogue/route-catalogue.{json,csv}` every logs run; schema `schemas/route-catalogue.v1.schema.json`.
The logs run then compacts finished UTC days (`lake.compact_finished`).
First install (units are installed by `deploy.sh`, but new timers must be enabled once):
`sudo systemctl enable --now inferhub-collect-fast.timer inferhub-collect-logs.timer inferhub-snapshot-publish.timer`.

## Daily snapshot on GitHub
`inferhub-snapshot-publish.timer` (23:50 America/New_York) exports the ET day's last catalog fetch
and pushes it to the orphan branch `data/inferhub-price-snapshots` (no CI, no protection, no PRs)
under `inferhub/price-snapshots/<YYYY>/<D>/`, plus `index.csv`. `pricing.csv` keeps the exact
columns of the 2026-09-22 snapshot `data/pricing.csv`; `manifest.json` has the sha256 of each CSV
and of the raw catalog/models bodies on the host. Push auth is the host's existing `gh` login
(credential helper `gh auth git-credential`); nothing is copied.

## Known gaps
- No seller id in any GET endpoint; per-seller error rates (M4) can only use the served tier.
- The telemetry join needs matching token counts; requests that failed before a usage block are
  matched by time only, and telemetry without tokens that succeeded stays `unmatched`.
- Marts are rebuilt in full each run with new rows (seconds today); switch to incremental builds
  once history grows. Finished days are compacted to one file per table/day by the logs run.
- `/usage/logs` offers only `range=24h|7d|30d|90d|all`; a gap longer than 90 days cannot be backfilled.
