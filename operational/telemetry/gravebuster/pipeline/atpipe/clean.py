"""clean layer: typed, normalized, scrubbed; noise FLAGGED (never deleted).

clean/spans/day=D/data.parquet and clean/logs/day=D/data.parquet are derived from raw Parquet and are
rebuilt per touched day (overwrite is fine here: clean is rebuildable from raw).
  * typed columns (timestamps UTC, durations, status, resource context, run_key, tool/model fields)
  * attributes kept as normalized JSON (identity keys already hashed in raw; see common.scrub)
  * large free-text payload keys (tool output/arguments, prompt) removed from attributes and replaced by
    *_bytes + *_sha256 columns; email-looking strings masked in text columns (email_masked flag)
  * UTF-16LE text that arrived mis-decoded (e.g. launcher run.stderr_tail) decoded into *_text columns
  * is_noise / noise_rule / counts_for_liveness from dbt/seeds/noise_rules.csv (Codex housekeeping)
"""

import glob
import os
import shutil

from . import common as C

SEED = os.path.join(C.PIPE, "dbt", "seeds", "noise_rules.csv")
EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"


def decode_mojibake_utf16(s):
    """Recover UTF-16LE bytes that were decoded as 8-bit text (NUL every other char)."""
    if s is None or "\x00" not in s:
        return s
    if s.count("\x00") < len(s) * 0.2:
        return s.replace("\x00", "")
    t = s.lstrip("\ufffd\ufeff\u00ff\u00fe")
    if t and t[0] == "\x00":
        t = t[1:]
    b = bytes((ord(ch) if ord(ch) < 256 else 0x3F) for ch in t)
    if len(b) % 2:
        b = b[:-1]
    out = b.decode("utf-16-le", errors="replace")
    return out.replace("\ufeff", "")


def register_udfs(con):
    try:
        con.remove_function("dec16")
    except Exception:
        pass
    con.create_function("dec16", decode_mojibake_utf16, ["VARCHAR"], "VARCHAR")


class Paths:
    """Collect JSON paths so each canonical_json document is parsed ONCE (multi-path extraction).
    Per-field json_extract_string calls re-parse the document each time and cost ~25x the input size
    in RAM; one list extraction keeps the clean step well under the 1.5 GB memory limit."""

    def __init__(self):
        self.paths = []

    def a(self, k, base="attributes"):
        p = f'$.{base}."{k}"'
        if p not in self.paths:
            self.paths.append(p)
        return f"v[{self.paths.index(p) + 1}]"

    def top(self, k):
        p = f"$.{k}"
        if p not in self.paths:
            self.paths.append(p)
        return f"v[{self.paths.index(p) + 1}]"

    def ctx(self, keys):
        parts = [self.a(k) for k in keys] + [self.a(k, "resource") for k in keys]
        return "coalesce(" + ", ".join(f"nullif({p}, '')" for p in parts) + ")"

    def wrap(self, body, extra_cols=""):
        lst = ", ".join("'" + p.replace("'", "''") + "'" for p in self.paths)
        return (
            f"SELECT {body} FROM (SELECT *, json_extract_string(canonical_json, [{lst}]) AS v, "
            f"json_extract(canonical_json, ['$.attributes', '$.resource', '$.events']) AS jv {extra_cols} FROM raw_day)"
        )


def _span_select():
    p = Paths()
    a, ctx, top = p.a, p.ctx, p.top
    body = f"""
  trace_id, span_id, parent_span_id, name,
  TRY_CAST({top("kind")} AS INTEGER) AS kind,
  make_timestamp(start_unix_nano // 1000) AS start_ts_utc,
  make_timestamp(end_unix_nano // 1000) AS end_ts_utc,
  start_unix_nano, end_unix_nano,
  (end_unix_nano - start_unix_nano) / 1e6 AS duration_ms,
  TRY_CAST({top("status_code")} AS INTEGER) AS status_code,
  {top("status_message")} AS status_message,
  {top("scope_name")} AS scope_name,
  service_name,
  {a("service.version", "resource")} AS service_version,
  {a("service.instance.id", "resource")} AS service_instance_id,
  {ctx(["harness", "agent.harness"])} AS harness,
  {ctx(["topology", "run.topology"])} AS topology,
  {ctx(["task.id", "task_id"])} AS task_id,
  {ctx(["correlation_id", "correlation.id"])} AS correlation_id,
  {ctx(["ire.issue"])} AS ire_issue,
  {a("llm.provider", "resource")} AS llm_provider,
  {a("llm.model_route", "resource")} AS llm_model_route,
  {a("llm.base_url_host", "resource")} AS llm_base_url_host,
  coalesce({a("deployment.environment", "resource")}, {a("deployment.environment.name", "resource")}) AS deployment_env,
  {a("host.name", "resource")} AS host_name,
  {a("opencode.run", "resource")} AS opencode_run,
  {a("model")} AS model,
  {a("wire_api")} AS wire_api,
  {a("api.path")} AS api_path,
  coalesce({a("conversation.id")}, CASE WHEN {a("thread.id")} LIKE '%-%-%' THEN {a("thread.id")} END) AS conversation_id,
  {a("turn_id")} AS turn_id,
  coalesce({a("call_id")}, {a("runtime_tool_call_id")}) AS call_id,
  {a("tool_name")} AS tool_name,
  TRY_CAST({a("aborted")} AS BOOLEAN) AS tool_aborted,
  {a("outcome")} AS outcome_attr,
  TRY_CAST({a("busy_ns")} AS BIGINT) AS busy_ns,
  TRY_CAST({a("idle_ns")} AS BIGINT) AS idle_ns,
  TRY_CAST({a("gen_ai.usage.input_tokens")} AS BIGINT) AS tokens_in,
  TRY_CAST({a("gen_ai.usage.output_tokens")} AS BIGINT) AS tokens_out,
  TRY_CAST({a("gen_ai.usage.cache_read.input_tokens")} AS BIGINT) AS tokens_cache_read,
  TRY_CAST({a("codex.usage.reasoning_output_tokens")} AS BIGINT) AS tokens_reasoning,
  {a("run.action")} AS run_action,
  {a("run.phase")} AS run_phase,
  TRY_CAST({a("process.exit_code")} AS INTEGER) AS run_exit_code,
  TRY_CAST({a("run.killed")} AS BOOLEAN) AS run_killed,
  TRY_CAST({a("run.timed_out")} AS BOOLEAN) AS run_timed_out,
  {a("run.signal")} AS run_signal,
  TRY_CAST({a("run.duration_ms")} AS DOUBLE) AS run_duration_ms,
  regexp_replace(dec16({a("run.stderr_tail")}), '{EMAIL_RE}', '<email-redacted>', 'g') AS run_stderr_tail_text,
  CASE WHEN contains({a("run.stderr_tail")}, chr(0)) THEN 'utf16le_recovered' END AS stderr_tail_decoding,
  jv[1] AS attributes,
  jv[2] AS resource,
  jv[3] AS events,
  coalesce(json_array_length(jv[3]), 0) AS n_events,
  parent_span_id IS NULL AS is_root,
  content_hash, source, source_ref, etl_run_id, day"""
    return p.wrap(body)


def _log_select():
    p = Paths()
    a, ctx, top = p.a, p.ctx, p.top
    body = f"""
  key AS log_key, trace_id, span_id,
  make_timestamp(time_unix_nano // 1000) AS time_ts_utc, time_unix_nano,
  {top("observed_time")} AS observed_time,
  TRY_CAST({top("severity_number")} AS INTEGER) AS severity_number,
  {top("severity_text")} AS severity_text,
  event_name AS event_name_raw,
  {a("event.name")} AS event,
  {a("event.kind")} AS event_kind,
  {top("body")} AS body,
  {top("scope_name")} AS scope_name,
  service_name,
  {a("service.version", "resource")} AS service_version,
  {a("service.instance.id", "resource")} AS service_instance_id,
  {ctx(["harness", "agent.harness"])} AS harness,
  {ctx(["topology", "run.topology"])} AS topology,
  {ctx(["task.id", "task_id"])} AS task_id,
  {ctx(["correlation_id", "correlation.id"])} AS correlation_id,
  {ctx(["ire.issue"])} AS ire_issue,
  {a("llm.provider", "resource")} AS llm_provider,
  {a("llm.model_route", "resource")} AS llm_model_route,
  {a("llm.base_url_host", "resource")} AS llm_base_url_host,
  {a("opencode.run", "resource")} AS opencode_run,
  {a("conversation.id")} AS conversation_id,
  {a("model")} AS model,
  {a("call_id")} AS call_id,
  {a("tool_name")} AS tool_name,
  {a("tool_namespace")} AS tool_namespace,
  {a("decision")} AS tool_decision,
  {a("source")} AS decision_source,
  TRY_CAST({a("duration_ms")} AS DOUBLE) AS duration_ms,
  TRY_CAST({a("success")} AS BOOLEAN) AS success,
  TRY_CAST({a("output_truncated")} AS BOOLEAN) AS output_truncated,
  {a("error.message")} AS error_message,
  {a("endpoint")} AS endpoint,
  TRY_CAST({a("http.response.status_code")} AS INTEGER) AS http_status,
  TRY_CAST({a("attempt")} AS INTEGER) AS attempt,
  TRY_CAST({a("input_token_count")} AS BIGINT) AS tokens_in,
  TRY_CAST({a("output_token_count")} AS BIGINT) AS tokens_out,
  TRY_CAST({a("cached_token_count")} AS BIGINT) AS tokens_cached,
  TRY_CAST({a("reasoning_token_count")} AS BIGINT) AS tokens_reasoning,
  TRY_CAST({a("tool_token_count")} AS BIGINT) AS tokens_tool,
  TRY_CAST({a("ttft_ms")} AS DOUBLE) AS ttft_ms,
  {a("user.email")} AS user_email_sha256,
  {a("user.account_id")} AS user_account_sha256,
  length({a("output")}) AS output_bytes,
  sha256({a("output")}) AS output_sha256,
  length({a("arguments")}) AS arguments_bytes,
  sha256({a("arguments")}) AS arguments_sha256,
  TRY_CAST({a("prompt_length")} AS BIGINT) AS prompt_length,
  json_merge_patch(jv[1], '{{"output":null,"arguments":null,"prompt":null}}') AS attributes,
  jv[2] AS resource,
  ({a("output")} IS NOT NULL OR {a("arguments")} IS NOT NULL OR {a("prompt")} IS NOT NULL) AS payload_text_removed,
  content_hash, source, source_ref, etl_run_id, day"""
    return p.wrap(body)


SPAN_SELECT = _span_select()
LOG_SELECT = _log_select()


def _write(con, table, select, days, noise_join, log):
    n = 0
    for d in days:
        src = os.path.join(C.RAW, table, f"day={d}")
        files = glob.glob(os.path.join(src, "*.parquet"))
        if not files:
            continue
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW raw_day AS SELECT * FROM read_parquet('{src}/*.parquet', hive_partitioning=false)"
        )
        out_dir = os.path.join(C.CLEAN, table, f"day={d}")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "data.parquet")
        # streamed straight into Parquet (no temp table); the ORDER BY sort spills to temp_directory
        con.execute(f"""COPY (SELECT c.*,
                                coalesce(r.is_noise, false) AS is_noise, r.rule_id AS noise_rule,
                                coalesce(r.counts_for_liveness, true) AS counts_for_liveness
                         FROM (SELECT *, coalesce(correlation_id, 'opencode:' || opencode_run,
                                     service_name || ':' || service_instance_id, 'trace:' || trace_id) AS run_key
                               FROM ({select})) c
                         LEFT JOIN noise_rules r ON {noise_join}
                         ORDER BY run_key, 1)
                        TO '{out}.tmp' (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL 9)""")
        os.replace(out + ".tmp", out)
        n += con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
    log(f"[clean] {table}: rebuilt {len(days)} day(s), {n} rows")
    return n


def all_days(table):
    return sorted(os.path.basename(p)[4:] for p in glob.glob(os.path.join(C.RAW, table, "day=*")))


def run(con, touched, rebuild_all=False, log=print):
    register_udfs(con)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE noise_rules AS
                    SELECT * FROM read_csv('{SEED}', header=true, all_varchar=false)""")
    out = {}
    for table, select, join in (
        (
            "spans",
            SPAN_SELECT,
            "r.signal='traces' AND (r.service_name IS NULL OR r.service_name=c.service_name) "
            "AND r.field='name' AND r.value=c.name",
        ),
        (
            "logs",
            LOG_SELECT,
            "r.signal='logs' AND (r.service_name IS NULL OR r.service_name=c.service_name) AND "
            "((r.field='event_kind' AND r.value=c.event_kind) OR (r.field='body' AND r.value=c.body) "
            "OR (r.field='event' AND r.value=c.event))",
        ),
    ):
        days = all_days(table) if rebuild_all else sorted(touched.get(table, []))
        if rebuild_all and os.path.isdir(os.path.join(C.CLEAN, table)):
            # drop partitions whose raw day vanished (should never happen; raw is append-only)
            for p in glob.glob(os.path.join(C.CLEAN, table, "day=*")):
                if os.path.basename(p)[4:] not in days:
                    shutil.rmtree(p)
        out[table] = _write(con, table, select, days, join, log)
    return out
