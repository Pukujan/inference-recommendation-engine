"""OTLP-JSON / SQLite-ledger row -> scrubbed canonical records.

content_hash = sha256(canonical JSON of the loader-compatible core fields). The core deliberately uses
the same value conversions as loader/loader.py, so a span parsed from a raw OTLP file and the same span
read back from the SQLite ledger hash identically (that is how parity is verified). Fields the loader
never stored (flags, trace_state, links, dropped counts, scope version/attributes, schema URLs, full
nanosecond event times) go to extras_json, which is kept but not hashed ("exporter-added" fields).
"""

import hashlib
import json

from . import common as C


def _h(o):
    return hashlib.sha256(C.canon(o).encode("utf-8")).hexdigest()


def _span_rec(canonical, extras, source, ref, prio, seq, stats=None):
    canonical = C.mask_emails(canonical, stats)
    extras = C.mask_emails(extras, stats) if extras else extras
    res = canonical["resource"]
    return {
        "key": f"{canonical['trace_id']}:{canonical['span_id']}",
        "trace_id": canonical["trace_id"],
        "span_id": canonical["span_id"],
        "parent_span_id": canonical["parent_span_id"],
        "name": canonical["name"],
        "service_name": res.get("service.name"),
        "start_unix_nano": canonical["start_unix_nano"],
        "end_unix_nano": canonical["end_unix_nano"],
        "day": C.ns_day(canonical["start_unix_nano"]),
        "content_hash": _h(canonical),
        "canonical_json": C.canon(canonical),
        "extras_json": C.canon(extras) if extras else None,
        "source": source,
        "source_ref": ref,
        "priority": prio,
        "seq": seq,
    }


def _log_rec(canonical, extras, source, ref, prio, seq, stats=None):
    canonical = C.mask_emails(canonical, stats)
    extras = C.mask_emails(extras, stats) if extras else extras
    keyobj = {
        k: canonical[k]
        for k in (
            "trace_id",
            "span_id",
            "time_unix_nano",
            "observed_time",
            "severity_number",
            "event_name",
            "body",
            "attributes",
        )
    }
    return {
        "key": _h(keyobj),
        "trace_id": canonical["trace_id"],
        "span_id": canonical["span_id"],
        "time_unix_nano": canonical["time_unix_nano"],
        "event_name": canonical["event_name"],
        "service_name": canonical["resource"].get("service.name"),
        "day": C.ns_day(canonical["time_unix_nano"]),
        "content_hash": _h(canonical),
        "canonical_json": C.canon(canonical),
        "extras_json": C.canon(extras) if extras else None,
        "source": source,
        "source_ref": ref,
        "priority": prio,
        "seq": seq,
    }


def _metric_rec(canonical, source, ref, prio, seq, stats=None):
    canonical = C.mask_emails(canonical, stats)
    keyobj = {
        k: canonical[k]
        for k in ("name", "type", "time_unix_nano", "start_time", "attributes", "resource")
    }
    return {
        "key": _h(keyobj),
        "name": canonical["name"],
        "time_unix_nano": canonical["time_unix_nano"],
        "service_name": canonical["resource"].get("service.name"),
        "day": C.ns_day(canonical["time_unix_nano"]),
        "content_hash": _h(canonical),
        "canonical_json": C.canon(canonical),
        "extras_json": None,
        "source": source,
        "source_ref": ref,
        "priority": prio,
        "seq": seq,
    }


def _nz(d):
    return {k: v for k, v in d.items() if v not in (None, "", 0, [], {})}


# ----------------------------------------------------------------------------- OTLP file lines
def otlp_traces(doc, ref, seq0, stats):
    seq = seq0
    for rs in doc.get("resourceSpans", []):
        resource = rs.get("resource", {}) or {}
        res = C.scrub(C.attrs(resource.get("attributes")), stats)
        for ss in rs.get("scopeSpans", []):
            scope = ss.get("scope") or {}
            for sp in ss.get("spans", []):
                a = C.scrub(C.attrs(sp.get("attributes")), stats)
                st, en = sp.get("startTimeUnixNano"), sp.get("endTimeUnixNano")
                status = sp.get("status") or {}
                evs = sp.get("events", []) or []
                events = [
                    {
                        "name": e.get("name"),
                        "time": C.ns_iso(e.get("timeUnixNano")),
                        "attributes": C.scrub(C.attrs(e.get("attributes")), stats),
                    }
                    for e in evs
                ]
                canonical = {
                    "trace_id": sp.get("traceId"),
                    "span_id": sp.get("spanId"),
                    "parent_span_id": sp.get("parentSpanId") or None,
                    "name": sp.get("name"),
                    "kind": sp.get("kind"),
                    "start_unix_nano": int(st) if st else None,
                    "end_unix_nano": int(en) if en else None,
                    "status_code": status.get("code", 0),
                    "status_message": status.get("message"),
                    "scope_name": scope.get("name"),
                    "attributes": a,
                    "resource": res,
                    "events": events if events else None,
                }
                extras = _nz(
                    {
                        "flags": sp.get("flags"),
                        "trace_state": sp.get("traceState"),
                        "links": [
                            dict(
                                _nz(
                                    {
                                        "trace_id": lk.get("traceId"),
                                        "span_id": lk.get("spanId"),
                                        "trace_state": lk.get("traceState"),
                                        "flags": lk.get("flags"),
                                    }
                                ),
                                attributes=C.scrub(C.attrs(lk.get("attributes")), stats),
                            )
                            for lk in (sp.get("links") or [])
                        ],
                        "dropped_attributes_count": sp.get("droppedAttributesCount"),
                        "dropped_events_count": sp.get("droppedEventsCount"),
                        "dropped_links_count": sp.get("droppedLinksCount"),
                        "event_time_unix_nano": [
                            int(e["timeUnixNano"]) for e in evs if e.get("timeUnixNano")
                        ],
                        "scope_version": scope.get("version"),
                        "scope_attributes": C.scrub(C.attrs(scope.get("attributes")), stats),
                        "resource_schema_url": rs.get("schemaUrl"),
                        "scope_schema_url": ss.get("schemaUrl"),
                    }
                )
                yield _span_rec(
                    canonical, extras, "otlp_file", f"{ref}#{seq - seq0}", 1, seq, stats
                )
                seq += 1


def otlp_logs(doc, ref, seq0, stats):
    seq = seq0
    for rl in doc.get("resourceLogs", []):
        res = C.scrub(C.attrs((rl.get("resource") or {}).get("attributes")), stats)
        for sl in rl.get("scopeLogs", []):
            scope = sl.get("scope") or {}
            for lr in sl.get("logRecords", []):
                a = C.scrub(C.attrs(lr.get("attributes")), stats)
                body = C.anyval(lr.get("body", {}))
                if isinstance(body, dict):
                    C.scrub(body, stats)
                t = lr.get("timeUnixNano") or lr.get("observedTimeUnixNano")
                canonical = {
                    "trace_id": lr.get("traceId") or None,
                    "span_id": lr.get("spanId") or None,
                    "time_unix_nano": int(t) if t else None,
                    "observed_time": C.ns_iso(lr.get("observedTimeUnixNano")),
                    "severity_number": lr.get("severityNumber"),
                    "severity_text": lr.get("severityText"),
                    "event_name": lr.get("eventName") or a.get("event.name"),
                    "body": body if isinstance(body, str) or body is None else C.J(body),
                    "scope_name": scope.get("name"),
                    "attributes": a,
                    "resource": res,
                }
                extras = _nz(
                    {
                        "flags": lr.get("flags"),
                        "dropped_attributes_count": lr.get("droppedAttributesCount"),
                        "observed_time_unix_nano": int(lr["observedTimeUnixNano"])
                        if lr.get("observedTimeUnixNano")
                        else None,
                        "scope_version": scope.get("version"),
                        "scope_attributes": C.scrub(C.attrs(scope.get("attributes")), stats),
                        "resource_schema_url": rl.get("schemaUrl"),
                        "scope_schema_url": sl.get("schemaUrl"),
                    }
                )
                yield _log_rec(canonical, extras, "otlp_file", f"{ref}#{seq - seq0}", 1, seq, stats)
                seq += 1


def otlp_metrics(doc, ref, seq0, stats):
    seq = seq0
    for rm in doc.get("resourceMetrics", []):
        res = C.scrub(C.attrs((rm.get("resource") or {}).get("attributes")), stats)
        for sm in rm.get("scopeMetrics", []):
            scope = (sm.get("scope") or {}).get("name")
            for m in sm.get("metrics", []):
                for mtype in ("sum", "gauge", "histogram", "exponentialHistogram", "summary"):
                    if mtype not in m:
                        continue
                    for dp in m[mtype].get("dataPoints", []):
                        a = C.scrub(C.attrs(dp.get("attributes")), stats)
                        if "asDouble" in dp:
                            val = dp["asDouble"]
                        elif "asInt" in dp:
                            val = float(dp["asInt"])
                        elif "sum" in dp:
                            val = dp.get("sum")
                        else:
                            val = None
                        point = {k: v for k, v in dp.items() if k != "attributes"}
                        t = dp.get("timeUnixNano")
                        canonical = {
                            "name": m.get("name"),
                            "unit": m.get("unit"),
                            "type": mtype,
                            "time_unix_nano": int(t) if t else None,
                            "start_time": C.ns_iso(dp.get("startTimeUnixNano")),
                            "value": val,
                            "point": point,
                            "scope_name": scope,
                            "attributes": a,
                            "resource": res,
                        }
                        yield _metric_rec(
                            canonical, "otlp_file", f"{ref}#{seq - seq0}", 1, seq, stats
                        )
                        seq += 1


OTLP = {"traces": otlp_traces, "logs": otlp_logs, "metrics": otlp_metrics}


# ----------------------------------------------------------------------------- SQLite ledger rows
def _j(s, default=None):
    if s is None:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def sqlite_span(r, stats, seq):
    res = C.scrub(_j(r["resource_attributes"], {}) or {}, stats)
    a = C.scrub(_j(r["attributes"], {}) or {}, stats)
    events = _j(r["events"])
    if events:
        for e in events:
            C.scrub(e.get("attributes") or {}, stats)
    canonical = {
        "trace_id": r["trace_id"],
        "span_id": r["span_id"],
        "parent_span_id": r["parent_span_id"] or None,
        "name": r["name"],
        "kind": r["kind"],
        "start_unix_nano": r["start_unix_nano"],
        "end_unix_nano": r["end_unix_nano"],
        "status_code": r["status_code"],
        "status_message": r["status_message"],
        "scope_name": r["scope_name"],
        "attributes": a,
        "resource": res,
        "events": events or None,
    }
    return _span_rec(canonical, None, "sqlite_ledger", f"sqlite:spans:{r['id']}", 2, seq, stats)


def sqlite_log(r, stats, seq):
    res = C.scrub(_j(r["resource_attributes"], {}) or {}, stats)
    a = C.scrub(_j(r["attributes"], {}) or {}, stats)
    body = r["body"]
    canonical = {
        "trace_id": r["trace_id"] or None,
        "span_id": r["span_id"] or None,
        "time_unix_nano": r["time_unix_nano"],
        "observed_time": r["observed_time"],
        "severity_number": r["severity_number"],
        "severity_text": r["severity_text"],
        "event_name": r["event_name"],
        "body": body,
        "scope_name": r["scope_name"],
        "attributes": a,
        "resource": res,
    }
    return _log_rec(canonical, None, "sqlite_ledger", f"sqlite:logs:{r['id']}", 2, seq, stats)


def sqlite_metric(r, stats, seq):
    canonical = {
        "name": r["name"],
        "unit": r["unit"],
        "type": r["type"],
        "time_unix_nano": r["time_unix_nano"],
        "start_time": r["start_time"],
        "value": r["value"],
        "point": _j(r["point"], {}),
        "scope_name": r["scope_name"],
        "attributes": C.scrub(_j(r["attributes"], {}) or {}, stats),
        "resource": C.scrub(_j(r["resource_attributes"], {}) or {}, stats),
    }
    return _metric_rec(canonical, "sqlite_ledger", f"sqlite:metrics:{r['id']}", 2, seq, stats)


SQLITE = {"spans": sqlite_span, "logs": sqlite_log, "metrics": sqlite_metric}
