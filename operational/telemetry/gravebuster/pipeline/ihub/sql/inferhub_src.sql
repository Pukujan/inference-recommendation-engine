-- Source views over the deduped Parquet parts (created by model.py; {PQ} and {LAKE_MODELED} are
-- substituted). Timestamps are ISO strings in Parquet; cast here to TIMESTAMP (UTC, naive) so they
-- compare with fact_model_requests.started_at_utc.
CREATE OR REPLACE VIEW src_tiers AS
  SELECT * REPLACE (observed_at::TIMESTAMPTZ::TIMESTAMP AS observed_at)
  FROM read_parquet('{PQ}/tiers/day=*/*.parquet', union_by_name=true);
CREATE OR REPLACE VIEW src_routes AS
  SELECT * REPLACE (fetched_at::TIMESTAMPTZ::TIMESTAMP AS fetched_at)
  FROM read_parquet('{PQ}/routes/day=*/*.parquet', union_by_name=true);
CREATE OR REPLACE VIEW src_rails AS
  SELECT * REPLACE (fetched_at::TIMESTAMPTZ::TIMESTAMP AS fetched_at)
  FROM read_parquet('{PQ}/rails/day=*/*.parquet', union_by_name=true);
CREATE OR REPLACE VIEW src_market AS
  SELECT * REPLACE (market_ts::TIMESTAMPTZ::TIMESTAMP AS market_ts,
                    fetched_at::TIMESTAMPTZ::TIMESTAMP AS fetched_at)
  FROM read_parquet('{PQ}/market/day=*/*.parquet', union_by_name=true);
CREATE OR REPLACE VIEW src_status AS
  SELECT * REPLACE (status_updated_at::TIMESTAMPTZ::TIMESTAMP AS status_updated_at,
                    fetched_at::TIMESTAMPTZ::TIMESTAMP AS fetched_at)
  FROM read_parquet('{PQ}/status/day=*/*.parquet', union_by_name=true);
CREATE OR REPLACE VIEW src_status_history AS
  SELECT * REPLACE (window_start::TIMESTAMPTZ::TIMESTAMP AS window_start,
                    status_updated_at::TIMESTAMPTZ::TIMESTAMP AS status_updated_at)
  FROM read_parquet('{PQ}/status_history/day=*/*.parquet', union_by_name=true);
CREATE OR REPLACE VIEW src_balance AS
  SELECT * REPLACE (fetched_at::TIMESTAMPTZ::TIMESTAMP AS fetched_at)
  FROM read_parquet('{PQ}/balance/day=*/*.parquet', union_by_name=true);
CREATE OR REPLACE VIEW src_billing_all AS
  SELECT *, false AS is_revision FROM read_parquet('{PQ}/billing/day=*/*.parquet', union_by_name=true)
  {BILLING_REVISIONS};
