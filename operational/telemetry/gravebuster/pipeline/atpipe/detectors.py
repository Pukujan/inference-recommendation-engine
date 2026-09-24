"""P4 incident detectors: modeled facts + receipts -> fact_incidents (IRE #41 P4, #40 detectors).

The detectors are pure functions over lists of row dicts, so they are unit-tested without DuckDB
(operational/tests/test_telemetry_detectors.py). ``run()`` is the pipeline hook: it loads the facts
of a freshly built snapshot with DuckDB, runs every detector in ``detectors/catalog.json`` and
rewrites ``fact_incidents.parquet`` in that snapshot directory.

Rules:
  * Every incident cites evidence (``evidence_refs``): ``run:``, ``span:<trace>/<span>``,
    ``receipt:<stamp>/<file>:L<line>``, ``gap:``, ``hop:``, ``tool_call:``.
  * ``incident_id`` and ``fingerprint`` are deterministic (same inputs -> same ids).
  * ``justified`` stays NULL: adjudication is a later step, detectors never judge intent.
  * Detectors read harness-neutral columns, so Kilo/OpenCode runs are covered once ingested.
  * Pipeline verification traces are excluded by the catalog's ``exclusions``.
"""

import datetime
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG = os.path.join(os.path.dirname(HERE), "detectors", "catalog.json")

INCIDENT_COLUMNS = (
    "incident_id",
    "run_id",
    "hop_id",
    "incident_type",
    "mast_mode",
    "fingerprint_v",
    "fingerprint",
    "severity",
    "justified",
    "detected_at_utc",
    "detector_version",
    "ledger_event_id",
    "evidence_refs",
    "snapshot_id",
    "catalog_version",
    "catalog_sha256",
    "root_run_id",
    "parent_run_id",
    "run_role",
    "harness",
    "route_id",
    "task_id",
    "receipt_stamp",
    "occurred_at_utc",
    "summary",
    "metrics_json",
    "proposed_fix_id",
    "proposed_fix_status",
)

OK_OUTCOMES = ("completed", "success", "ok")


# ---------------------------------------------------------------- helpers


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _ts(v):
    if v is None or isinstance(v, datetime.datetime):
        return v
    s = str(v).replace("Z", "")
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def _stamp_ts(stamp):
    try:
        return datetime.datetime.strptime(stamp, "%Y%m%dT%H%M%SZ")
    except (TypeError, ValueError):
        return None


def _iso(v):
    t = _ts(v)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def _num(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def receipt_ref(stamp, file="codex-events.jsonl", line_no=None, seq=None):
    if line_no is not None:
        return f"receipt:{stamp}/{file}:L{int(line_no)}"
    if seq is not None:
        return f"receipt:{stamp}/{file}#seq{int(seq)}"
    return f"receipt:{stamp}/{file}"


def span_ref(trace_id, span_id):
    if not trace_id:
        return None
    return f"span:{trace_id}/{span_id}" if span_id else f"trace:{trace_id}"


class Ctx:
    """Indexes over the inputs shared by all detectors."""

    def __init__(self, inputs, catalog):
        self.catalog = catalog
        ex = catalog.get("exclusions") or {}
        self.runs = {r["run_id"]: r for r in inputs.get("runs") or [] if r.get("run_id")}
        self.excluded = {
            rid for rid, r in self.runs.items() if self._is_excluded(r, ex)
        } | self._excluded_children(ex)
        self.model_requests = inputs.get("model_requests") or []
        self.idle_gaps = inputs.get("idle_gaps") or []
        self.hops = inputs.get("hops") or []
        self.tool_calls = inputs.get("tool_calls") or []
        self.root_errors = inputs.get("root_errors") or []
        self.receipt_runs = {
            r["stamp"]: r for r in inputs.get("receipt_runs") or [] if r.get("stamp")
        }
        self.receipt_events = {}
        for e in inputs.get("receipt_events") or []:
            self.receipt_events.setdefault(e.get("stamp"), []).append(e)
        for evs in self.receipt_events.values():
            evs.sort(key=lambda e: (e.get("seq") is None, e.get("seq") or 0))
        self.stamp_run = {}
        for rid, r in self.runs.items():
            if r.get("receipt_stamp") and r.get("role") in ("launcher", "agent", None):
                self.stamp_run.setdefault(r["receipt_stamp"], rid)
        for st, rr in self.receipt_runs.items():
            if st not in self.stamp_run:
                self.stamp_run[st] = rr.get("run_id") or f"astra-{st}"

    @staticmethod
    def _is_excluded(r, ex):
        tid = r.get("task_id") or ""
        if any(tid.startswith(p) for p in ex.get("task_id_prefixes") or []):
            return True
        if (r.get("harness") or "") in (ex.get("harnesses") or []):
            return True
        rid = r.get("run_id") or ""
        return any(s in rid for s in ex.get("run_id_substrings") or [])

    def _excluded_children(self, ex):
        out = set()
        for rid, r in self.runs.items():
            root = r.get("root_run_id") or r.get("parent_run_id")
            if root and root in self.runs and self._is_excluded(self.runs[root], ex):
                out.add(rid)
        return out

    def root_of(self, run_id):
        r = self.runs.get(run_id) or {}
        return r.get("root_run_id") or r.get("parent_run_id") or run_id

    def ok(self, run_id):
        return bool(run_id) and run_id not in self.excluded

    def stamp_ok(self, stamp):
        return self.ok(self.stamp_run.get(stamp))


def _cand(run_id, evidence, summary, metrics=None, fp=None, hop_id=None, occurred=None, stamp=None):
    return {
        "run_id": run_id,
        "hop_id": hop_id,
        "evidence_refs": [e for e in evidence if e],
        "summary": summary,
        "metrics": metrics or {},
        "fp": fp or {},
        "occurred_at_utc": occurred,
        "receipt_stamp": stamp,
    }


# ---------------------------------------------------------------- detectors


def d_unjustified_kill(ctx, p):
    live = float(p.get("live_threshold_s", 60))
    out = []
    last_gap = {}
    for g in ctx.idle_gaps:
        if g.get("ended_by") == "run_end":
            last_gap[g["run_id"]] = g
    hop_by_child = {h.get("child_run_id"): h for h in ctx.hops}
    seen_stamps = set()
    for rid, r in ctx.runs.items():
        if not ctx.ok(rid) or not (r.get("killed") or r.get("timed_out")):
            continue
        ev, why, m = [f"run:{rid}"], [], {"wall_s": _num(r.get("wall_s"))}
        g = last_gap.get(rid)
        if g is not None and _num(g.get("gap_s")) is not None and g["gap_s"] < live:
            why.append(f"last event {g['gap_s']:.1f}s before the end")
            m["last_event_to_end_s"] = round(g["gap_s"], 3)
            ev += [
                f"gap:{g.get('gap_id')}",
                span_ref(g.get("before_trace_id"), g.get("before_span_id")),
            ]
        h = hop_by_child.get(rid)
        if h is not None and _num(h.get("child_last_event_to_parent_end_s")) is not None:
            if h["child_last_event_to_parent_end_s"] < live:
                why.append(
                    f"child last event {h['child_last_event_to_parent_end_s']:.1f}s before parent end"
                )
                m["child_last_event_to_parent_end_s"] = round(
                    h["child_last_event_to_parent_end_s"], 3
                )
                ev.append(f"hop:{h.get('hop_id')}")
        st = r.get("receipt_stamp")
        rr = ctx.receipt_runs.get(st) if st else None
        if rr is not None:
            seen_stamps.add(st)
            why_r, ev_r, m_r = _receipt_liveness(ctx, rr)
            why += why_r
            ev += ev_r
            m.update(m_r)
        if why:
            out.append(
                _cand(
                    rid,
                    ev,
                    f"{rid} killed ({r.get('kill_signal') or 'signal unknown'}) while live: "
                    + "; ".join(why),
                    m,
                    {"harness": r.get("harness"), "kill_signal": r.get("kill_signal")},
                    occurred=_iso(r.get("ended_at_utc")),
                    stamp=st,
                )
            )
    for st, rr in ctx.receipt_runs.items():
        if st in seen_stamps or not rr.get("killed") or not ctx.stamp_ok(st):
            continue
        why, ev, m = _receipt_liveness(ctx, rr)
        if why:
            rid = ctx.stamp_run.get(st)
            out.append(
                _cand(
                    rid,
                    [f"run:{rid}"] + ev,
                    f"{rid} killed while live: " + "; ".join(why),
                    m,
                    {"harness": "astra-launcher", "kill_signal": rr.get("exit_code_raw")},
                    occurred=_iso(rr.get("ended_at_utc")),
                    stamp=st,
                )
            )
    return out


def _receipt_liveness(ctx, rr):
    st = rr["stamp"]
    why, ev, m = [], [], {}
    started = rr.get("n_commands_started") or 0
    done = rr.get("n_commands") or 0
    if started > done:
        why.append(f"{started - done} command(s) in flight at the kill")
        m["commands_in_flight"] = started - done
        evs = ctx.receipt_events.get(st) or []
        done_ids = {e.get("item_id") for e in evs if e.get("type") == "item.completed"}
        for e in evs:
            if e.get("type") == "item.started" and e.get("item_id") not in done_ids:
                ev.append(receipt_ref(st, line_no=e.get("line_no"), seq=e.get("seq")))
    ev.append(receipt_ref(st, "summary.txt"))
    return why, ev, m


def d_terminated_without_closeout(ctx, p):
    out = []
    for st, rr in ctx.receipt_runs.items():
        if not ctx.stamp_ok(st):
            continue
        if rr.get("outcome") != "no_terminal_event" or rr.get("killed") or rr.get("exit_code_raw"):
            continue
        evs = ctx.receipt_events.get(st) or []
        if not evs:
            continue
        rid = ctx.stamp_run.get(st)
        last = evs[-1]
        out.append(
            _cand(
                rid,
                [f"run:{rid}", receipt_ref(st, line_no=last.get("line_no"), seq=last.get("seq"))],
                f"{rid}: {len(evs)} events, last '{last.get('type')}', no terminal event, exit code or kill record",
                {
                    "n_events": len(evs),
                    "last_event_type": last.get("type"),
                    "duration_s": _num(rr.get("duration_s")),
                    "n_commands": rr.get("n_commands"),
                    "has_summary": rr.get("has_summary"),
                },
                {"last_event_type": last.get("type")},
                occurred=_iso(rr.get("ended_at_utc")),
                stamp=st,
            )
        )
    return out


def _is_param_rejection(status, code, p):
    s = _num(status)
    if code is None or s is None:
        return False
    return p.get("min_status", 400) <= s <= p.get("max_status", 499) and int(s) not in (
        p.get("ignore_statuses") or []
    )


def _collect_provider_errors(ctx, p):
    """-> {(root_run_id, code): {...}} merged from model requests, receipt events, root spans."""
    groups = {}

    def g(root, code):
        return groups.setdefault(
            (root, int(code)),
            {
                "evidence": [],
                "request_ids": set(),
                "statuses": set(),
                "types": set(),
                "shape": {},
                "stamps": set(),
                "runs": set(),
                "first": None,
                "harness": None,
                "route": None,
                "provider": None,
            },
        )

    for mr in ctx.model_requests:
        rid = mr.get("run_id")
        if not ctx.ok(rid) or not _is_param_rejection(
            mr.get("http_status"), mr.get("provider_error_code"), p
        ):
            continue
        root = ctx.root_of(rid)
        x = g(root, mr["provider_error_code"])
        x["runs"].add(rid)
        x["statuses"].add(int(mr["http_status"]))
        if mr.get("error_type"):
            x["types"].add(mr["error_type"])
        if mr.get("provider_request_id"):
            x["request_ids"].add(mr["provider_request_id"])
        if mr.get("receipt_stamp"):
            x["stamps"].add(mr["receipt_stamp"])
        x["evidence"].append(span_ref(mr.get("trace_id"), mr.get("span_id")))
        x["route"] = x["route"] or mr.get("route_id")
        x["provider"] = x["provider"] or mr.get("provider")
        for k_src, k in (
            ("n_messages", "msgs"),
            ("n_tools", "tools"),
            ("n_empty_assistant_text_with_tool_calls", "empty_content"),
            ("n_tool_schema_additional_properties", "additional_properties"),
            ("max_tool_description_bytes", "max_tool_desc_bytes"),
            ("max_message_bytes", "max_msg_bytes"),
        ):
            if mr.get(k_src) is not None:
                x["shape"][k] = mr[k_src]
        t = _ts(mr.get("started_at_utc"))
        if t and (x["first"] is None or t < x["first"]):
            x["first"] = t
    for st, evs in ctx.receipt_events.items():
        if not ctx.stamp_ok(st):
            continue
        rid = ctx.stamp_run.get(st)
        for e in evs:
            if not _is_param_rejection(e.get("http_status"), e.get("provider_error_code"), p):
                continue
            x = g(ctx.root_of(rid), e["provider_error_code"])
            x["runs"].add(rid)
            x["stamps"].add(st)
            x["statuses"].add(int(e["http_status"]))
            if e.get("error_type"):
                x["types"].add(e["error_type"])
            if e.get("request_id"):
                x["request_ids"].add(e["request_id"])
            x["evidence"].append(receipt_ref(st, line_no=e.get("line_no"), seq=e.get("seq")))
            for k in (
                "msgs",
                "tools",
                "empty_content",
                "additional_properties",
                "max_tool_desc_bytes",
                "max_msg_bytes",
            ):
                if e.get(k) is not None:
                    x["shape"][k] = e[k]
            t = _stamp_ts(st)
            if t and (x["first"] is None or t < x["first"]):
                x["first"] = t
    for re_ in ctx.root_errors:
        rid = re_.get("run_id")
        if not ctx.ok(rid) or not _is_param_rejection(re_.get("http_status"), re_.get("code"), p):
            continue
        x = g(ctx.root_of(rid), re_["code"])
        x["runs"].add(rid)
        x["statuses"].add(int(_num(re_["http_status"])))
        if re_.get("type"):
            x["types"].add(re_["type"])
        for q in re_.get("request_ids") or []:
            x["request_ids"].add(q)
        x["evidence"].append(span_ref(re_.get("trace_id"), re_.get("span_id")))
        if re_.get("request_shape"):
            x["shape"]["request_shape"] = re_["request_shape"]
    # 4xx model-request spans without a parsed code (e.g. the child's HTTP 400 log) join their root's group.
    for mr in ctx.model_requests:
        rid = mr.get("run_id")
        s = _num(mr.get("http_status"))
        if (
            not ctx.ok(rid)
            or mr.get("provider_error_code") is not None
            or s is None
            or not 400 <= s <= 499
        ):
            continue
        for (root, _code), x in groups.items():
            if root == ctx.root_of(rid):
                x["runs"].add(rid)
                x["statuses"].add(int(s))
                x["evidence"].append(span_ref(mr.get("trace_id"), mr.get("span_id")))
    for (root, _code), x in groups.items():
        r = ctx.runs.get(root) or {}
        x["harness"] = r.get("harness") or x["harness"]
        x["route"] = x["route"] or r.get("route_id")
    return groups


def d_provider_param_rejection(ctx, p):
    out = []
    for (root, code), x in sorted(groups_items(_collect_provider_errors(ctx, p))):
        statuses = sorted(x["statuses"])
        types = sorted(x["types"])
        stamp = sorted(x["stamps"])[0] if x["stamps"] else None
        out.append(
            _cand(
                root,
                [f"run:{root}"] + sorted(set(filter(None, x["evidence"]))),
                f"{root}: provider error {code} {'/'.join(types) or 'unknown'} (HTTP {'/'.join(map(str, statuses))})"
                + (f", request {sorted(x['request_ids'])[0][:12]}" if x["request_ids"] else ""),
                {
                    "provider_error_code": code,
                    "http_statuses": statuses,
                    "error_types": types,
                    "provider_request_ids": sorted(x["request_ids"]),
                    "request_shape": x["shape"],
                    "runs": sorted(x["runs"]),
                    "receipt_stamps": sorted(x["stamps"]),
                },
                {
                    "provider": x["provider"],
                    "route": x["route"],
                    "harness": x["harness"],
                    "code": code,
                    "http_status": statuses[0] if statuses else None,
                    "error_type": types[0] if types else None,
                },
                occurred=_iso(x["first"]),
                stamp=stamp,
            )
        )
    return out


def groups_items(groups):
    return [((k[0] or "", k[1]), v) for k, v in groups.items()]


def _chains(ctx):
    """Receipt runs grouped by session chain (session_id, linked through prior_session_id)."""
    parent = {}

    def find(a):
        while parent.get(a, a) != a:
            a = parent[a]
        return a

    for rr in ctx.receipt_runs.values():
        s, ps = rr.get("session_id"), rr.get("prior_session_id")
        if s:
            parent.setdefault(s, s)
        if s and ps:
            parent.setdefault(ps, ps)
            parent[find(s)] = find(ps)
    chains = {}
    for st, rr in ctx.receipt_runs.items():
        if not rr.get("session_id") or not ctx.stamp_ok(st):
            continue
        chains.setdefault(find(rr["session_id"]), []).append(rr)
    for v in chains.values():
        v.sort(key=lambda rr: rr["stamp"])
    return chains


def d_blind_resend(ctx, p):
    win = datetime.timedelta(minutes=float(p.get("window_min", 120)))
    out = []
    for chain in _chains(ctx).values():
        for i, first in enumerate(chain):
            if first.get("provider_error_code") is None:
                continue
            for nxt in chain[i + 1 :]:
                t0, t1 = _stamp_ts(first["stamp"]), _stamp_ts(nxt["stamp"])
                if t0 and t1 and t1 - t0 > win:
                    break
                if nxt.get("provider_error_code") != first.get("provider_error_code"):
                    continue
                rid = ctx.stamp_run.get(nxt["stamp"])
                same_shape = (
                    _shape_hash(ctx, first["stamp"]) == _shape_hash(ctx, nxt["stamp"])
                    and _shape_hash(ctx, nxt["stamp"]) is not None
                )
                out.append(
                    _cand(
                        rid,
                        [
                            f"run:{rid}",
                            f"run:{ctx.stamp_run.get(first['stamp'])}",
                            _err_ref(ctx, first["stamp"]),
                            _err_ref(ctx, nxt["stamp"]),
                        ],
                        f"{rid} ({nxt.get('action') or 'launch'}) hit provider error {nxt['provider_error_code']} again after "
                        f"{ctx.stamp_run.get(first['stamp'])} was rejected with the same code"
                        + (" and the same request shape" if same_shape else ""),
                        {
                            "prior_run": ctx.stamp_run.get(first["stamp"]),
                            "code": nxt["provider_error_code"],
                            "action": nxt.get("action"),
                            "same_request_shape": same_shape,
                            "minutes_after": round((t1 - t0).total_seconds() / 60, 1)
                            if t0 and t1
                            else None,
                        },
                        {"code": nxt["provider_error_code"], "action": nxt.get("action")},
                        occurred=_iso(_stamp_ts(nxt["stamp"])),
                        stamp=nxt["stamp"],
                    )
                )
                break
    return out


def _shape_hash(ctx, stamp):
    for e in ctx.receipt_events.get(stamp) or []:
        if e.get("request_shape_hash"):
            return e["request_shape_hash"]
    return None


def _err_ref(ctx, stamp):
    for e in ctx.receipt_events.get(stamp) or []:
        if e.get("provider_error_code") is not None and e.get("type") == "turn.failed":
            return receipt_ref(stamp, line_no=e.get("line_no"), seq=e.get("seq"))
    return receipt_ref(stamp, "summary.txt")


def d_resume_retry_churn(ctx, p):
    win = datetime.timedelta(minutes=float(p.get("window_min", 60)))
    nmin, fmin = int(p.get("min_runs", 3)), int(p.get("min_failures", 2))
    out = []
    for chain in _chains(ctx).values():
        best = None
        for i in range(len(chain)):
            t0 = _stamp_ts(chain[i]["stamp"])
            win_runs = [
                rr
                for rr in chain[i:]
                if t0 and _stamp_ts(rr["stamp"]) and _stamp_ts(rr["stamp"]) - t0 <= win
            ]
            fails = [rr for rr in win_runs if rr.get("outcome") not in OK_OUTCOMES]
            if (
                len(win_runs) >= nmin
                and len(fails) >= fmin
                and (best is None or len(win_runs) > len(best[0]))
            ):
                best = (win_runs, fails)
        if not best:
            continue
        runs, fails = best
        last = runs[-1]
        rid = ctx.stamp_run.get(last["stamp"])
        out.append(
            _cand(
                rid,
                [f"run:{ctx.stamp_run.get(rr['stamp'])}" for rr in runs]
                + [receipt_ref(rr["stamp"], "summary.txt") for rr in fails],
                f"session chain with {len(runs)} runs in {p.get('window_min', 60)} min, {len(fails)} not completed "
                f"({', '.join(rr.get('outcome') or '?' for rr in runs)})",
                {
                    "runs": [ctx.stamp_run.get(rr["stamp"]) for rr in runs],
                    "outcomes": [rr.get("outcome") for rr in runs],
                    "actions": [rr.get("action") for rr in runs],
                    "n_failures": len(fails),
                },
                {"n_runs": len(runs)},
                occurred=_iso(_stamp_ts(last["stamp"])),
                stamp=last["stamp"],
            )
        )
    return out


def d_launcher_error_or_early_exit(ctx, p):
    early = float(p.get("early_exit_s", 30))
    out, seen = [], set()
    for st, rr in ctx.receipt_runs.items():
        if not ctx.stamp_ok(st):
            continue
        rid = ctx.stamp_run.get(st)
        if rr.get("has_launcher_error") or rr.get("outcome") == "launcher_error":
            seen.add(rid)
            out.append(
                _cand(
                    rid,
                    [f"run:{rid}", receipt_ref(st, "launcher-error.txt")],
                    f"{rid}: launcher error recorded (launcher-error.txt); stderr starts '{_short(rr.get('stderr_first_line'))}'",
                    {
                        "stderr_first_line_sha256": _sha(rr["stderr_first_line"])[:16]
                        if rr.get("stderr_first_line")
                        else None,
                        "stderr_class": _stderr_class(rr.get("stderr_first_line")),
                        "duration_s": _num(rr.get("duration_s")),
                    },
                    {
                        "kind": "launcher_error",
                        "stderr_class": _stderr_class(rr.get("stderr_first_line")),
                    },
                    occurred=_iso(rr.get("ended_at_utc")),
                    stamp=st,
                )
            )
    root_err = {ctx.root_of(e.get("run_id")) for e in ctx.root_errors if e.get("run_id")}
    for rid, r in ctx.runs.items():
        if rid in seen or not ctx.ok(rid) or r.get("parent_run_id"):
            continue
        if r.get("provider_error_code") is not None or rid in root_err:
            continue  # explained by a provider error
        failed = (
            (r.get("exit_code") not in (None, 0))
            or r.get("killed")
            or r.get("outcome") in ("failed", "killed")
        )
        wall = _num(r.get("wall_s"))
        if not failed or wall is None or wall >= early:
            continue
        kids = [k for k in ctx.runs.values() if k.get("parent_run_id") == rid]
        ntools = (r.get("n_tool_calls") or 0) + sum(k.get("n_tool_calls") or 0 for k in kids)
        if ntools:
            continue
        ev = [f"run:{rid}", span_ref(r.get("root_trace_id"), None)]
        if r.get("receipt_stamp"):
            ev.append(receipt_ref(r["receipt_stamp"], "summary.txt"))
        ev += [f"hop:{h.get('hop_id')}" for h in ctx.hops if h.get("parent_run_id") == rid]
        out.append(
            _cand(
                rid,
                ev,
                f"{rid}: {r.get('outcome')} after {wall:.1f}s (exit {r.get('exit_code')}, signal {r.get('kill_signal')}) before any agent work",
                {
                    "wall_s": wall,
                    "exit_code": r.get("exit_code"),
                    "killed": r.get("killed"),
                    "stderr_class": _stderr_class(r.get("stderr_tail_text")),
                },
                {
                    "kind": "early_exit",
                    "harness": r.get("harness"),
                    "stderr_class": _stderr_class(r.get("stderr_tail_text")),
                },
                occurred=_iso(r.get("ended_at_utc")),
                stamp=r.get("receipt_stamp"),
            )
        )
    return out


def _short(s, n=80):
    s = (s or "").replace("\r", " ").replace("\n", " ")
    return s[:n] + ("..." if len(s) > n else "")


def _stderr_class(s):
    s = (s or "").lower()
    if not s:
        return None
    if "failed to refresh available models" in s:
        return "models_refresh_warning"
    if '"owned_by"' in s or '"object":"model"' in s.replace(" ", ""):
        return "model_list_dump"
    if "connection failed" in s or "error sending request" in s:
        return "connection_error"
    if "error" in s:
        return "error_text"
    return "other"


def d_unattributed_failure(ctx, p, early_exit_s=30):
    out = []
    root_err = {ctx.root_of(e.get("run_id")) for e in ctx.root_errors}
    for rid, r in ctx.runs.items():
        if not ctx.ok(rid) or r.get("parent_run_id"):
            continue
        if r.get("exit_code") in (None, 0) or r.get("killed") or r.get("timed_out"):
            continue
        if r.get("provider_error_code") is not None or rid in root_err:
            continue
        wall = _num(r.get("wall_s"))
        if wall is not None and wall < early_exit_s:
            continue
        st = r.get("receipt_stamp")
        rr = ctx.receipt_runs.get(st) if st else None
        if rr and (rr.get("has_launcher_error") or rr.get("provider_error_code") is not None):
            continue
        kids = [k for k in ctx.runs.values() if k.get("parent_run_id") == rid]
        out.append(
            _cand(
                rid,
                [f"run:{rid}", span_ref(r.get("root_trace_id"), None)]
                + [f"run:{k['run_id']}" for k in kids],
                f"{rid}: exit {r.get('exit_code')} after {wall or 0:.0f}s with no captured cause "
                f"(no provider error, launcher error or kill{', no receipt on the host' if not rr else ''})",
                {
                    "wall_s": wall,
                    "exit_code": r.get("exit_code"),
                    "has_receipt": bool(rr),
                    "child_tool_calls": sum(k.get("n_tool_calls") or 0 for k in kids),
                    "stderr_class": _stderr_class(r.get("stderr_tail_text")),
                },
                {"harness": r.get("harness"), "exit_code": r.get("exit_code")},
                occurred=_iso(r.get("ended_at_utc")),
                stamp=st,
            )
        )
    return out


def d_supervisor_polling_loop(ctx, p):
    nrep = int(p.get("min_repeats", 3))
    npoll, share = int(p.get("min_poll_commands", 6)), float(p.get("min_poll_share", 0.3))
    out = []
    for st, evs in ctx.receipt_events.items():
        if not ctx.stamp_ok(st):
            continue
        cmds = [e for e in evs if e.get("type") == "item.completed" and e.get("command_sha256")]
        if not cmds:
            continue
        by = {}
        for e in cmds:
            by.setdefault(e["command_sha256"], []).append(e)
        top_h, top = max(by.items(), key=lambda kv: (len(kv[1]), kv[0]))
        polls = [e for e in cmds if e.get("command_class") in ("sleep_wait", "status_poll")]
        rep = len(top) >= nrep
        poll = len(polls) >= npoll and len(polls) / len(cmds) >= share
        if not (rep or poll):
            continue
        rid = ctx.stamp_run.get(st)
        ev = [f"run:{rid}"] + [
            receipt_ref(st, line_no=e.get("line_no"), seq=e.get("seq"))
            for e in (top if rep else polls)[:10]
        ]
        why = []
        if rep:
            why.append(
                f"command {top_h} ran {len(top)}x (class {top[0].get('command_class') or 'unknown'})"
            )
        if poll:
            why.append(f"{len(polls)}/{len(cmds)} commands were wait/status polls")
        out.append(
            _cand(
                rid,
                ev,
                f"{rid}: " + "; ".join(why),
                {
                    "max_repeats": len(top),
                    "repeated_command_sha256": top_h,
                    "repeated_command_class": top[0].get("command_class"),
                    "n_commands": len(cmds),
                    "n_poll_commands": len(polls),
                    "distinct_commands": len(by),
                },
                {
                    "harness": (ctx.runs.get(rid) or {}).get("harness"),
                    "class": top[0].get("command_class") if rep else "poll",
                },
                occurred=_iso(_stamp_ts(st)),
                stamp=st,
            )
        )
    # Telemetry-only runs (no receipt): repeated tool arguments within one run.
    receipt_runs = {ctx.stamp_run.get(s) for s in ctx.receipt_events}
    by_run = {}
    for tc in ctx.tool_calls:
        if tc.get("source") == "receipt" or not tc.get("arguments_sha256"):
            continue
        rid = tc.get("run_id")
        if not ctx.ok(rid) or rid in receipt_runs or ctx.root_of(rid) in receipt_runs:
            continue
        by_run.setdefault(rid, {}).setdefault(tc["arguments_sha256"], []).append(tc)
    for rid, by in by_run.items():
        top_h, top = max(by.items(), key=lambda kv: (len(kv[1]), kv[0]))
        if len(top) < nrep:
            continue
        out.append(
            _cand(
                rid,
                [f"run:{rid}"] + [f"tool_call:{t.get('tool_call_id')}" for t in top[:10]],
                f"{rid}: tool call {top[0].get('tool_name')} with identical arguments ran {len(top)}x",
                {
                    "max_repeats": len(top),
                    "repeated_arguments_sha256": top_h,
                    "tool_name": top[0].get("tool_name"),
                },
                {
                    "harness": (ctx.runs.get(rid) or {}).get("harness"),
                    "tool": top[0].get("tool_name"),
                },
            )
        )
    return out


def d_long_idle_gap(ctx, p):
    thr = float(p.get("min_gap_s", 300))
    worst = {}
    for g in ctx.idle_gaps:
        rid = g.get("run_id")
        gs = _num(g.get("gap_s"))
        if not ctx.ok(rid) or gs is None or gs < thr or g.get("ended_by") == "run_end":
            continue
        if rid not in worst or gs > worst[rid]["gap_s"]:
            worst[rid] = g
    out = []
    for rid, g in worst.items():
        r = ctx.runs.get(rid) or {}
        out.append(
            _cand(
                rid,
                [
                    f"run:{rid}",
                    f"gap:{g.get('gap_id')}",
                    span_ref(g.get("before_trace_id"), g.get("before_span_id")),
                    span_ref(g.get("after_trace_id"), g.get("after_span_id")),
                ],
                f"{rid}: {g['gap_s']:.0f}s without events ({g.get('before_event')} -> {g.get('after_event')})",
                {
                    "gap_s": round(g["gap_s"], 3),
                    "before_event": g.get("before_event"),
                    "after_event": g.get("after_event"),
                    "process_cpu_active": g.get("process_cpu_active"),
                },
                {"harness": r.get("harness"), "before_event": g.get("before_event")},
                occurred=_iso(g.get("gap_start_utc")),
            )
        )
    return out


def d_permission_denied(ctx, p):
    by = {}
    for tc in ctx.tool_calls:
        if tc.get("permission_denied") and ctx.ok(tc.get("run_id")):
            by.setdefault(tc["run_id"], []).append(tc)
    out = []
    for rid, tcs in by.items():
        refs = [
            span_ref(t.get("trace_id"), t.get("span_id")) or f"tool_call:{t.get('tool_call_id')}"
            for t in tcs[:10]
        ]
        out.append(
            _cand(
                rid,
                [f"run:{rid}"] + refs,
                f"{rid}: {len(tcs)} tool call(s) denied ({', '.join(sorted({t.get('tool_name') or '?' for t in tcs}))})",
                {
                    "n_denied": len(tcs),
                    "tools": sorted({t.get("tool_name") or "?" for t in tcs}),
                    "decisions": sorted({t.get("decision") or "?" for t in tcs}),
                },
                {
                    "harness": (ctx.runs.get(rid) or {}).get("harness"),
                    "tools": sorted({t.get("tool_name") or "?" for t in tcs}),
                },
                occurred=_iso(
                    min(
                        (t.get("finished_at_utc") for t in tcs if t.get("finished_at_utc")),
                        default=None,
                    )
                ),
            )
        )
    return out


def d_unverified_hop(ctx, p):
    out = []
    for h in ctx.hops:
        rid = h.get("child_run_id")
        req, ver = h.get("requested_route"), h.get("verified_route")
        if not ctx.ok(rid) or not req or not ver or req == ver:
            continue
        out.append(
            _cand(
                rid,
                [
                    f"run:{rid}",
                    f"hop:{h.get('hop_id')}",
                    span_ref(h.get("parent_trace_id"), h.get("parent_span_id")),
                ],
                f"hop {h.get('parent_run_id')} -> {rid}: requested route {req}, observed {ver}",
                {"requested_route": req, "verified_route": ver},
                {"requested": req, "verified": ver},
                hop_id=h.get("hop_id"),
            )
        )
    return out


def d_one_shot_capture(ctx, p):
    mw = float(p.get("min_child_wall_s", 30))
    out = []
    for h in ctx.hops:
        rid = h.get("child_run_id")
        if not ctx.ok(rid) or h.get("streaming_observed") is not False:
            continue
        wall = _num((ctx.runs.get(rid) or {}).get("wall_s"))
        if wall is None or wall < mw:
            continue
        out.append(
            _cand(
                rid,
                [f"run:{rid}", f"hop:{h.get('hop_id')}"],
                f"hop {h.get('parent_run_id')} -> {rid}: {wall:.0f}s child with no streamed output",
                {"child_wall_s": wall},
                {"harness": (ctx.runs.get(rid) or {}).get("harness")},
                hop_id=h.get("hop_id"),
            )
        )
    return out


DETECTORS = {
    "unjustified_kill": d_unjustified_kill,
    "terminated_without_closeout": d_terminated_without_closeout,
    "provider_param_rejection": d_provider_param_rejection,
    "blind_resend": d_blind_resend,
    "resume_retry_churn": d_resume_retry_churn,
    "launcher_error_or_early_exit": d_launcher_error_or_early_exit,
    "unattributed_failure": d_unattributed_failure,
    "supervisor_polling_loop": d_supervisor_polling_loop,
    "long_idle_gap": d_long_idle_gap,
    "permission_denied": d_permission_denied,
    "unverified_hop": d_unverified_hop,
    "one_shot_capture": d_one_shot_capture,
}


# ---------------------------------------------------------------- framework


def load_catalog(path=DEFAULT_CATALOG):
    with open(path, "rb") as f:
        raw = f.read()
    cat = json.loads(raw.decode("utf-8"))
    cat["_sha256"] = hashlib.sha256(raw).hexdigest()
    return cat


def validate_catalog(cat):
    errs = []
    types = set()
    for s in cat.get("signatures") or []:
        t = s.get("incident_type")
        if t in types:
            errs.append(f"duplicate incident_type {t}")
        types.add(t)
        if t not in DETECTORS:
            errs.append(f"no detector implements {t}")
        if not str(s.get("detector_version", "")).startswith(f"{t}/"):
            errs.append(f"{t}: detector_version must be '{t}/<n>'")
        if (s.get("proposed_fix") or {}).get("status") not in (
            "proposed",
            "verified",
            "rejected",
            "superseded",
        ):
            errs.append(f"{t}: proposed_fix.status missing or unknown")
        if s.get("severity") not in ("low", "medium", "high"):
            errs.append(f"{t}: severity must be low/medium/high")
    for t in DETECTORS:
        if t not in types:
            errs.append(f"detector {t} has no catalog entry")
    return errs


def detect_all(inputs, catalog, snapshot_id=None, detected_at=None):
    errs = validate_catalog(catalog)
    if errs:
        raise ValueError("invalid incident catalog: " + "; ".join(errs))
    ctx = Ctx(inputs, catalog)
    detected_at = detected_at or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    fpv = catalog.get("fingerprint_version", "fp/v1")
    rows, seen = [], set()
    for sig in catalog["signatures"]:
        t = sig["incident_type"]
        for c in DETECTORS[t](ctx, sig.get("params") or {}):
            ev = sorted(set(c["evidence_refs"]))
            iid = "INC-" + _sha("|".join([t, c["run_id"] or "", c["hop_id"] or ""] + ev))[:20]
            if iid in seen:
                continue
            seen.add(iid)
            r = ctx.runs.get(c["run_id"]) or {}
            fp_parts = {"incident_type": t, **{k: v for k, v in c["fp"].items() if v is not None}}
            rows.append(
                {
                    "incident_id": iid,
                    "run_id": c["run_id"],
                    "hop_id": c["hop_id"],
                    "incident_type": t,
                    "mast_mode": sig.get("mast_mode"),
                    "fingerprint_v": fpv,
                    "fingerprint": _sha(
                        fpv + "|" + json.dumps(fp_parts, sort_keys=True, default=str)
                    )[:16],
                    "severity": sig.get("severity"),
                    "justified": None,
                    "detected_at_utc": _ts(detected_at),
                    "detector_version": sig["detector_version"],
                    "ledger_event_id": None,
                    "evidence_refs": ev,
                    "snapshot_id": snapshot_id,
                    "catalog_version": catalog.get("catalog_version"),
                    "catalog_sha256": catalog.get("_sha256"),
                    "root_run_id": ctx.root_of(c["run_id"]) if c["run_id"] else None,
                    "parent_run_id": r.get("parent_run_id"),
                    "run_role": r.get("role"),
                    "harness": r.get("harness"),
                    "route_id": r.get("route_id"),
                    "task_id": r.get("task_id"),
                    "receipt_stamp": c.get("receipt_stamp") or r.get("receipt_stamp"),
                    "occurred_at_utc": _ts(c.get("occurred_at_utc")),
                    "summary": c["summary"],
                    "metrics_json": json.dumps(c["metrics"], sort_keys=True, default=str),
                    "proposed_fix_id": (sig.get("proposed_fix") or {}).get("id"),
                    "proposed_fix_status": (sig.get("proposed_fix") or {}).get("status"),
                }
            )
    rows.sort(key=lambda x: (x["incident_type"], x["run_id"] or "", x["incident_id"]))
    return rows


# ---------------------------------------------------------------- pipeline hook (DuckDB/pyarrow)


def _rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _root_errors(con, clean_root, runs):
    pat = os.path.join(clean_root, "spans", "day=*", "*.parquet")
    import glob

    if not glob.glob(pat):
        return []
    rows = _rows(
        con,
        f"SELECT trace_id, span_id, CAST(attributes AS VARCHAR) AS a FROM read_parquet('{pat}', hive_partitioning=false) "
        "WHERE is_root AND json_extract(attributes, '$.\"provider.error.count\"') IS NOT NULL",
    )
    by_trace = {}
    for r in runs:
        if r.get("root_trace_id"):
            by_trace.setdefault(r["root_trace_id"], r["run_id"])
    for r in runs:
        if r.get("role") != "launcher":
            continue
        for t in r.get("trace_ids") or []:
            by_trace.setdefault(t, r["run_id"])
    out = []
    for x in rows:
        try:
            a = json.loads(x["a"])
        except (TypeError, ValueError):
            continue
        if not a.get("provider.error.count"):
            continue
        rids = a.get("provider.request_ids") or (
            [a["provider.request_id"]] if a.get("provider.request_id") else []
        )
        if isinstance(rids, str):
            rids = [s for s in rids.split(",") if s]
        out.append(
            {
                "run_id": by_trace.get(x["trace_id"]),
                "trace_id": x["trace_id"],
                "span_id": x["span_id"],
                "code": a.get("provider.error.code"),
                "http_status": a.get("provider.error.http_status"),
                "type": a.get("provider.error.type"),
                "request_ids": list(rids),
                "request_shape": a.get("provider.error.request_shape"),
            }
        )
    return out


def load_inputs(con, out_dir, clean_root):
    def t(name, base=out_dir):
        f = os.path.join(base, name + ".parquet")
        return _rows(con, f"SELECT * FROM read_parquet('{f}')") if os.path.exists(f) else []

    rc = os.path.join(clean_root, "receipts")
    inputs = {
        "runs": t("fact_runs"),
        "model_requests": t("fact_model_requests"),
        "idle_gaps": t("fact_idle_gaps"),
        "hops": t("fact_hops"),
        "tool_calls": t("fact_tool_calls"),
        "receipt_runs": t("receipt_runs", rc),
        "receipt_events": t("receipt_events", rc),
    }
    inputs["root_errors"] = _root_errors(con, clean_root, inputs["runs"])
    return inputs


def arrow_schema():
    import pyarrow as pa

    ts = pa.timestamp("us")
    types = {
        "justified": pa.bool_(),
        "detected_at_utc": ts,
        "occurred_at_utc": ts,
        "evidence_refs": pa.list_(pa.string()),
    }
    return pa.schema([(c, types.get(c, pa.string())) for c in INCIDENT_COLUMNS])


def write_incidents(rows, path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    tbl = pa.Table.from_pylist(
        [{c: r.get(c) for c in INCIDENT_COLUMNS} for r in rows], schema=arrow_schema()
    )
    pq.write_table(tbl, path + ".tmp", compression="zstd")
    os.replace(path + ".tmp", path)


def run(out_dir, snapshot_id, clean_root, catalog_path=DEFAULT_CATALOG, log=print):
    import duckdb

    cat = load_catalog(catalog_path)
    con = duckdb.connect()
    try:
        inputs = load_inputs(con, out_dir, clean_root)
    finally:
        con.close()
    rows = detect_all(inputs, cat, snapshot_id)
    write_incidents(rows, os.path.join(out_dir, "fact_incidents.parquet"))
    counts = {}
    for r in rows:
        counts[r["incident_type"]] = counts.get(r["incident_type"], 0) + 1
    log(
        f"[detectors] {cat['catalog_version']} {len(rows)} incidents {json.dumps(counts, sort_keys=True)}"
    )
    return {
        "catalog_version": cat["catalog_version"],
        "catalog_sha256": cat["_sha256"],
        "incidents": len(rows),
        "by_type": counts,
    }
