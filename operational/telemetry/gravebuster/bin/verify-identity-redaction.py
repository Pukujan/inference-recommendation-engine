#!/usr/bin/env python3
"""Send one inferhub-tagged span+log (task.id=telemetry-verify-redaction) carrying user.email, user.account_id and
Tailscale identity headers on resource/span/event/log attributes; print the trace id. Check with at-query / jq."""

import json
import secrets
import time
import urllib.request


def kv(d):
    return [{"key": k, "value": {"stringValue": v}} for k, v in d.items()]


IDENT = {
    "user.email": "verify.redaction@example.com",
    "user.account_id": "acct-verify-0000",
    "http.request.header.tailscale-user-login": "verify@example.com",
    "http.request.header.tailscale-user-name": "Verify User",
    "http.request.header.tailscale-user-profile-pic": "https://example.com/p.png",
}
res = dict(
    {
        "service.name": "telemetry-verify-redaction",
        "harness": "verify",
        "task.id": "telemetry-verify-redaction",
        "correlation_id": "redaction-verify-20260924",
        "llm.provider": "inferhub",
        "llm.base_url_host": "api.inferhub.dev",
    },
    **IDENT,
)
t, s, now = secrets.token_hex(16), secrets.token_hex(8), time.time_ns()
attrs = dict({"task.id": "telemetry-verify-redaction", "verify.keep": "yes"}, **IDENT)
span = {
    "traceId": t,
    "spanId": s,
    "name": "redaction.verify",
    "kind": 1,
    "startTimeUnixNano": str(now - 100_000_000),
    "endTimeUnixNano": str(now),
    "attributes": kv(attrs),
    "status": {"code": 1},
    "events": [
        {"timeUnixNano": str(now - 50_000_000), "name": "verify.event", "attributes": kv(IDENT)}
    ],
}
body = {
    "resourceSpans": [
        {
            "resource": {"attributes": kv(res)},
            "scopeSpans": [{"scope": {"name": "verify"}, "spans": [span]}],
        }
    ]
}


def post(path, b):
    return urllib.request.urlopen(
        urllib.request.Request(
            "http://localhost:4318" + path,
            json.dumps(b).encode(),
            {"Content-Type": "application/json"},
        )
    ).status


r1 = post("/v1/traces", body)
log = {
    "resourceLogs": [
        {
            "resource": {"attributes": kv(res)},
            "scopeLogs": [
                {
                    "scope": {"name": "verify"},
                    "logRecords": [
                        {
                            "timeUnixNano": str(now),
                            "severityText": "INFO",
                            "body": {"stringValue": "redaction verify"},
                            "traceId": t,
                            "spanId": s,
                            "attributes": kv(attrs),
                        }
                    ],
                }
            ],
        }
    ]
}
r2 = post("/v1/logs", log)
print(json.dumps({"trace_id": t, "traces_http": r1, "logs_http": r2}))
