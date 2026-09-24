#!/usr/bin/env python3
"""Send one inferhub-tagged and one untagged span+log to the local collector, print trace ids."""

import json
import secrets
import time
import urllib.request


def kv(d):
    return [{"key": k, "value": {"stringValue": v}} for k, v in d.items()]


out = {}
for tag in ("inferhub", "untagged"):
    res = {
        "service.name": "telemetry-verify-filter",
        "harness": "verify",
        "correlation_id": f"filter-verify-{tag}-20260924",
    }
    if tag == "inferhub":
        res.update(
            {
                "llm.provider": "inferhub",
                "llm.base_url_host": "api.inferhub.dev",
                "llm.model_route": "cb/gpt-6-astra",
            }
        )
    t, s, now = secrets.token_hex(16), secrets.token_hex(8), time.time_ns()
    span = {
        "traceId": t,
        "spanId": s,
        "name": f"filter.verify.{tag}",
        "kind": 1,
        "startTimeUnixNano": str(now - 100_000_000),
        "endTimeUnixNano": str(now),
        "attributes": kv({"verify.tag": tag}),
        "status": {"code": 1},
    }
    body = {
        "resourceSpans": [
            {
                "resource": {"attributes": kv(res)},
                "scopeSpans": [{"scope": {"name": "verify"}, "spans": [span]}],
            }
        ]
    }
    r1 = urllib.request.urlopen(
        urllib.request.Request(
            "http://localhost:4318/v1/traces",
            json.dumps(body).encode(),
            {"Content-Type": "application/json"},
        )
    ).status
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
                                "body": {"stringValue": f"filter verify {tag}"},
                                "traceId": t,
                                "spanId": s,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    r2 = urllib.request.urlopen(
        urllib.request.Request(
            "http://localhost:4318/v1/logs",
            json.dumps(log).encode(),
            {"Content-Type": "application/json"},
        )
    ).status
    out[tag] = {"trace_id": t, "traces_http": r1, "logs_http": r2}
print(json.dumps(out))
