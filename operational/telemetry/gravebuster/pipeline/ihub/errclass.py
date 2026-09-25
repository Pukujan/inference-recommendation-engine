"""Request-outcome classification shared by the SQL marts and the tests (IRE #46 M4, #40 M0.6).

Single source of truth: ``sql_error_type()`` / ``sql_error_class()`` render the rules below into
portable SQL CASE expressions (substituted into ``sql/fact_request_outcome.sql`` by model.py), and
``error_type()`` / ``error_class()`` are the same rules in Python. CI evaluates the rendered SQL in
sqlite3 against the Python functions so the two cannot drift.

Why 400 is not always a client error: the M0.6 fuzz (#40) showed InferHub/CodeBuddy error 11133
(``model_param_invalid``, HTTP 400, ``inferhubRetry: cb4xx``) rejects about 10% of *identical*
payloads, spread across router nodes, with no failover. It is an intermittent upstream/seller
rejection, so it counts against the route. InferHub's request logs carry no error code, so:

1. an error code from telemetry wins (``inferhub_request_match.provider_error_code``, or an
   11133-coded incident in ``fact_incidents`` on the same route within 60 s);
2. otherwise a 400 on a CodeBuddy rail (``cb``/``cbcn``, where 11133 is the only 400 seen) is
   *presumed* 11133 -> upstream (conservative: it lowers the route's success rate rather than
   hiding a possible seller fault);
3. other 400/4xx stay client errors (excluded from the service success rate), 401/403/402 are
   account errors (excluded), 499 is a client cancel (excluded).
"""

from __future__ import annotations

UPSTREAM_REJECT_CODES = ("11133",)  # model_param_invalid, intermittent seller reject (#40 M0.6)
CLIENT_ERROR_CODES: tuple[str, ...] = ()  # no code confirmed as a genuine client error yet
REJECT_400_RAILS = ("cb", "cbcn")  # CodeBuddy rails: uncoded 400 presumed 11133
UPSTREAM_TYPES = (
    "upstream_reject",
    "upstream_unavailable",
    "timeout",
    "rate_limited",
    "server_error",
    "failed_http_200",
)
CLIENT_TYPES = ("client_request_error", "client_cancelled")
ACCOUNT_TYPES = ("auth", "payment_required")
ERROR_CLASSES = ("upstream", "client", "account", "unknown")
EXCLUDED_CLASSES = ("client", "account")  # not counted in service attempts
BASIS_CODE_MATCH = "error_code:telemetry_request_match"
BASIS_CODE_INCIDENT = "error_code:telemetry_incident_60s"
BASIS_RAIL_RULE = "rail_rule:cb_400_presumed_11133"
BASIS_HTTP = "http_status"


def error_type(is_error: bool, http_status: int | None, rail: str | None, code: str | None) -> str:
    if not is_error:
        return "ok"
    if code is not None and code in UPSTREAM_REJECT_CODES:
        return "upstream_reject"
    if code is not None and code in CLIENT_ERROR_CODES:
        return "client_request_error"
    h = http_status
    if h is None:
        return "unknown"
    if h in (502, 503):
        return "upstream_unavailable"
    if h in (408, 504):
        return "timeout"
    if h == 429:
        return "rate_limited"
    if 500 <= h <= 599:
        return "server_error"
    if h == 499:
        return "client_cancelled"
    if h in (401, 403):
        return "auth"
    if h == 402:
        return "payment_required"
    if h == 400 and rail in REJECT_400_RAILS:
        return "upstream_reject"
    if 400 <= h <= 499:
        return "client_request_error"
    if 200 <= h <= 299:
        return "failed_http_200"
    return "unknown"


def error_class(etype: str) -> str | None:
    if etype == "ok":
        return None
    if etype in UPSTREAM_TYPES:
        return "upstream"
    if etype in CLIENT_TYPES:
        return "client"
    if etype in ACCOUNT_TYPES:
        return "account"
    return "unknown"


def class_basis(
    is_error: bool,
    http_status: int | None,
    rail: str | None,
    code: str | None,
    code_source: str | None,
) -> str | None:
    if not is_error:
        return None
    if code is not None and code in UPSTREAM_REJECT_CODES + CLIENT_ERROR_CODES:
        return code_source or BASIS_CODE_MATCH
    if error_type(is_error, http_status, rail, code) == "upstream_reject":
        return BASIS_RAIL_RULE
    return BASIS_HTTP


def _lit(values: tuple[str, ...]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def sql_error_type(is_error: str = "is_error", http: str = "http_status", rail: str = "rail",
                   code: str = "error_code") -> str:  # fmt: skip
    """Portable SQL (DuckDB and sqlite) equivalent of ``error_type``."""
    w = [f"WHEN NOT {is_error} THEN 'ok'"]
    if UPSTREAM_REJECT_CODES:
        w.append(f"WHEN {code} IN ({_lit(UPSTREAM_REJECT_CODES)}) THEN 'upstream_reject'")
    if CLIENT_ERROR_CODES:
        w.append(f"WHEN {code} IN ({_lit(CLIENT_ERROR_CODES)}) THEN 'client_request_error'")
    w += [
        f"WHEN {http} IS NULL THEN 'unknown'",
        f"WHEN {http} IN (502, 503) THEN 'upstream_unavailable'",
        f"WHEN {http} IN (408, 504) THEN 'timeout'",
        f"WHEN {http} = 429 THEN 'rate_limited'",
        f"WHEN {http} BETWEEN 500 AND 599 THEN 'server_error'",
        f"WHEN {http} = 499 THEN 'client_cancelled'",
        f"WHEN {http} IN (401, 403) THEN 'auth'",
        f"WHEN {http} = 402 THEN 'payment_required'",
        f"WHEN {http} = 400 AND {rail} IN ({_lit(REJECT_400_RAILS)}) THEN 'upstream_reject'",
        f"WHEN {http} BETWEEN 400 AND 499 THEN 'client_request_error'",
        f"WHEN {http} BETWEEN 200 AND 299 THEN 'failed_http_200'",
    ]
    return "CASE " + " ".join(w) + " ELSE 'unknown' END"


def sql_error_class(etype: str = "error_type") -> str:
    return (
        f"CASE WHEN {etype} = 'ok' THEN NULL "
        f"WHEN {etype} IN ({_lit(UPSTREAM_TYPES)}) THEN 'upstream' "
        f"WHEN {etype} IN ({_lit(CLIENT_TYPES)}) THEN 'client' "
        f"WHEN {etype} IN ({_lit(ACCOUNT_TYPES)}) THEN 'account' ELSE 'unknown' END"
    )


def sql_class_basis(etype: str = "error_type", code: str = "error_code",
                    code_source: str = "error_code_source") -> str:  # fmt: skip
    codes = _lit(UPSTREAM_REJECT_CODES + CLIENT_ERROR_CODES)
    return (
        f"CASE WHEN {etype} = 'ok' THEN NULL "
        f"WHEN {code} IN ({codes}) THEN coalesce({code_source}, '{BASIS_CODE_MATCH}') "
        f"WHEN {etype} = 'upstream_reject' THEN '{BASIS_RAIL_RULE}' ELSE '{BASIS_HTTP}' END"
    )
