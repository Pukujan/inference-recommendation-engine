# IRE daily feed (data branch)

Machine-written by `operational/telemetry/gravebuster/pipeline/ihub/feed.py` on `main`.
No CI, no PRs: each run commits the day's files here.

Open-weight models only: each entry names its licence and where the weights are published.

- `feed/v2/today.json`: today's picks (schema `feed/v2/schema.json`), tiers `cheap` and `strongest_open`
- `feed/v2/days/YYYY-MM-DD.json`: one file per ET day
- `feed/v2/index.json`: the list of days
- `feed/v1/`: the same data in the old shape (deprecated)

Stable URL: https://raw.githubusercontent.com/Pukujan/inference-recommendation-engine/data/ire-feed/feed/v2/today.json
