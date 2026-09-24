"""GET-only InferHub market + billing collector for the agent-telemetry lake (IRE #46 M2/M3).

Modules:
  api        GET-only HTTP client (key from a 0600 env file, never logged; 429 backoff; pacing)
  rawstore   append-only raw store: data/inferhub/raw/<endpoint>/<day>/*.json.zst + manifest.jsonl
  transform  pure functions: API JSON -> flat rows (no third-party imports; unit-tested in CI)
  lake       deduped, append-only Parquet parts (DuckDB)
  model      modeled tables (DuckDB SQL files in sql/inferhub_*.sql) -> data/inferhub/modeled/
  export     curated daily snapshot CSV (Sep 22 pricing.csv format) + sha256 manifest, git publish
  __main__   CLI used by the systemd units
"""

SCHEMA_VERSION = "ihub/v1"
