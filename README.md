# InferHub daily price snapshots (data branch)

Orphan data branch of Pukujan/inference-recommendation-engine written once a day by the
GET-only collector on the telemetry host (IRE issue #46). Not merged into `main`.

- `inferhub/price-snapshots/<YYYY>/<YYYY-MM-DD>/pricing.csv`: order book per route from the last
  catalog fetch of that ET day. Same columns as the 2026-09-22 `data/pricing.csv` snapshot;
  `*_price_points_json` = `[[price_usd_per_1M, avail_count], ...]`, `*_discount_points_json` adds the
  discount % vs the official price.
- `providers.csv`: rails (cb, cx, ...) with active provider counts.
- `route-daily-summary.csv`: per route over the ET day (min ask range, capacity-weighted median,
  snapshots, % of snapshots under the $0.10/1M policy threshold).
- `manifest.json`: sha256 of each CSV and of the raw API responses they came from (raw stays on the
  telemetry host under data/inferhub/raw, append-only, zstd).
- `route-catalogue.json` / `.csv`, `route-reliability.csv` (from 2026-09-24): reliability-adjusted
  route catalogue (static Top 20 / daily shortlist joined with live price and rolling 1h/24h/7d
  request-log reliability; statuses are evidence-backed hypotheses) as of the publish time.
  Latest copy: `inferhub/route-catalogue/latest/`; JSON Schema: `inferhub/schemas/`.
- `index.csv`: one line per day.

Prices are listed asks, not guarantees. Code, schemas and docs live on `main` under
`operational/telemetry/gravebuster/pipeline/ihub/`.
