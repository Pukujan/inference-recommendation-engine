"""Kilo Code session history -> immutable per-session archives -> clean Parquet (issue #41).

Kilo Code 7.x (VS Code extension and the ``kilo`` CLI, both OpenCode-based) keeps every session in one
SQLite file on the PC: ``%USERPROFILE%\\.local\\share\\kilo\\kilo.db`` (tables ``session``, ``message``,
``part``; JSON in ``data``). The legacy ``globalStorage/kilocode.kilo-code/tasks/<taskId>`` layout is
read the same way once converted, but is not present on the current PC.

Two steps, both idempotent:

``export`` (manual, per copied DB snapshot)
    Opens a read-only snapshot of kilo.db, classifies every session by the provider endpoint its model
    requests used, and writes ONLY in-scope sessions (InferHub, ``api.inferhub.dev``) to
    ``data/kilo/<sessionId>/session-<sha16>.json.zst`` plus an append-only ``SHA256SUMS`` manifest.
    Archives are content-addressed and chmod 444, so a re-export of an unchanged session is a no-op and a
    grown session gets a new file next to the old one. Every session (in scope or not) is listed with its
    classification, but no content, in ``data/kilo/_inventory/<snapshot>.json``.
    Content passes the pipeline scrub conventions first: ``common.mask_emails`` and the collector's
    secret masks (``sk-``, ``Bearer``, GitHub tokens, ...).

``build`` (every pipeline run, called from ``atpipe.run``)
    Reads the newest archive of each session, verifies its sha256, and rewrites
    ``clean/kilo/kilo_{runs,hops,model_requests,tool_calls,events}.parquet`` when the archive set changed.
    Rows are keyed on (task_id, message index[, part index]) and deduplicated on that key, so reruns and
    overlapping snapshots never double count. Evidence ids are ``<taskId>#m<msgIdx>[.p<partIdx>]``.

The dbt models ``kilo_fact_*`` (``dbt/models/kilo/``) turn these into star-schema marts with
``harness = 'kilo'``.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sqlite3
import sys
from collections.abc import Iterable
from typing import Any

from . import common as C

KILO_DIR = os.environ.get("AT_KILO_DIR", os.path.join(C.ROOT, "data", "kilo"))
INVENTORY_DIR = os.path.join(KILO_DIR, "_inventory")
OUT = os.path.join(C.CLEAN, "kilo")
STATE_FILE = os.path.join(C.STATE, "kilo_build.json")

INFERHUB_HOST = "api.inferhub.dev"
BYOK_HOST = INFERHUB_HOST  # neutral alias used by the public-surface-checked tests
# Built-in endpoints of Kilo/OpenCode provider ids when kilo.jsonc does not override baseURL.
KNOWN_PROVIDER_HOSTS = {
    "inferhub": INFERHUB_HOST,
    "xai": "api.x.ai",
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
    "google": "generativelanguage.googleapis.com",
    "openrouter": "openrouter.ai",
    "kilo": "api.kilo.ai",
    "opencode": "opencode.ai",
    "litellm": "localhost:4000",
}

SECRET_RES = [
    re.compile(r"\bsk-(?:lf-)?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bpk-lf-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=\-]{12,}"),
    re.compile(r"\bxai-[A-Za-z0-9]{20,}"),
    re.compile(r"\bih[_-][A-Za-z0-9]{20,}"),
]
SECRET_KEY_RE = re.compile(
    r"(?i)^(authorization|x-api-key|api[._-]?key|apikey|secret|password|passwd|cookie|credential"
    r"|token|access_token|refresh_token|id_token)$"
)
CODE_RES = [
    re.compile(r'"code"\s*:\s*"?(\d{3,6})\b'),
    re.compile(r"\bcode[=: ]+(\d{4,6})\b", re.I),
    re.compile(r"\b(11133)\b"),
]
DENY_USER_RE = re.compile(r"(?i)user (?:has )?rejected permission|permission denied by user")
DENY_RULE_RE = re.compile(
    r"(?i)rule which prevents you from using|denied by (?:rule|policy|config)|auto[- ]?deny"
)


# ----------------------------------------------------------------------------------------------
# pure helpers (unit-tested in operational/tests/test_kilo_ingest.py)
# ----------------------------------------------------------------------------------------------
def host_of(url: str | None) -> str | None:
    if not url:
        return None
    m = re.match(r"^[a-z]+://([^/]+)", url.strip(), re.I)
    return (m.group(1) if m else url.strip()).lower()


def provider_map_from_config(cfg: dict[str, Any] | None) -> dict[str, str]:
    """provider id -> endpoint host from a kilo.jsonc ``provider`` block (baseURL overrides)."""
    out: dict[str, str] = {}
    for pid, p in ((cfg or {}).get("provider") or {}).items():
        opts = (p or {}).get("options") or {}
        h = host_of(opts.get("baseURL") or opts.get("baseUrl") or (p or {}).get("api"))
        if h:
            out[pid] = h
    return out


def provider_host(
    provider_id: str | None, provider_map: dict[str, str] | None = None
) -> str | None:
    if not provider_id:
        return None
    pm = provider_map or {}
    if provider_id in pm:
        return pm[provider_id]
    return KNOWN_PROVIDER_HOSTS.get(provider_id)


def classify(
    request_providers: Iterable[str | None],
    selected_providers: Iterable[str | None] = (),
    provider_map: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Return (scope, reason); scope is 'inferhub' | 'excluded' | 'unknown'.

    request_providers: providerID of every assistant message (one per model request turn).
    selected_providers: providerID of the model selected on user messages (a request may not exist).
    A session is in scope only if every provider it used resolves to api.inferhub.dev.
    """
    req = [p for p in request_providers]
    used = sorted({p for p in list(req) + list(selected_providers) if p})
    if not used:
        return "unknown", "no provider recorded on any message"
    hosts = {p: provider_host(p, provider_map) for p in used}
    unresolved = sorted(p for p, h in hosts.items() if h is None)
    others = sorted(f"{p}->{h}" for p, h in hosts.items() if h and h != INFERHUB_HOST)
    ih = sorted(p for p, h in hosts.items() if h == INFERHUB_HOST)
    if others:
        why = "non-InferHub provider " + ", ".join(others)
        if ih:
            why = "mixed providers: " + why
        return "excluded", why
    if unresolved:
        return "unknown", "provider endpoint not determinable: " + ", ".join(unresolved)
    if not any(req):
        return "unknown", "no model request recorded (only a selected model)"
    return "inferhub", "all requests via " + ", ".join(ih) + " -> " + INFERHUB_HOST


def scrub_text(s: str, stats: Any = None) -> str:
    s = C.mask_emails(s, stats)
    for rx in SECRET_RES:
        s = rx.sub("***REDACTED***", s)
    return s


def scrub_obj(o: Any, stats: Any = None) -> Any:
    if isinstance(o, str):
        return scrub_text(o, stats)
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if isinstance(k, str) and SECRET_KEY_RE.match(k) and isinstance(v, str):
                out[k] = "***REDACTED***"
            else:
                out[k] = scrub_obj(v, stats)
        return out
    if isinstance(o, list):
        return [scrub_obj(v, stats) for v in o]
    return o


def provider_error_code(*texts: Any) -> int | None:
    for t in texts:
        if t is None:
            continue
        s = t if isinstance(t, str) else json.dumps(t, default=str)
        for rx in CODE_RES:
            m = rx.search(s)
            if m:
                return int(m.group(1))
    return None


def ms_iso(ms: Any) -> str | None:
    try:
        v = int(ms)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return datetime.datetime.fromtimestamp(v / 1000, datetime.timezone.utc).isoformat(
        timespec="milliseconds"
    )


def _j(s: Any) -> dict[str, Any]:
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _sha(o: Any) -> str | None:
    if o is None:
        return None
    s = o if isinstance(o, str) else C.canon(o)
    return C.sha256_hex(s)


def _nbytes(o: Any) -> int | None:
    if o is None:
        return None
    s = o if isinstance(o, str) else C.canon(o)
    return len(s.encode("utf-8"))


def tool_outcome(state: dict[str, Any]) -> tuple[str, str | None]:
    """(outcome, denial) for a Kilo tool part state."""
    status = state.get("status")
    err = str(state.get("error") or "")
    if status == "completed":
        return "success", None
    if status == "error":
        if DENY_RULE_RE.search(err):
            return "denied", "auto_deny"
        if DENY_USER_RE.search(err):
            return "denied", "user_rejected"
        if re.search(r"(?i)aborted|interrupted|cancel", err):
            return "aborted", None
        return "error", None
    return "incomplete", None  # pending / running when the session stopped


def session_rows(doc: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """One archived session document -> clean rows. Pure (no I/O)."""
    s = doc["session"]
    sid = s["id"]
    run_id = "kilo:" + sid
    msgs = sorted(doc.get("messages", []), key=lambda m: (m["time_created"], m["id"]))
    parts_by_msg: dict[str, list[dict[str, Any]]] = {}
    for p in sorted(doc.get("parts", []), key=lambda p: (p["time_created"], p["id"])):
        parts_by_msg.setdefault(p["message_id"], []).append(p)

    reqs: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    children: list[dict[str, Any]] = []
    last_assistant: dict[str, Any] | None = None
    last_ms = s.get("time_created")

    def ev(t_ms: Any, kind: str, evid: str) -> None:
        nonlocal last_ms
        if t_ms:
            events.append(
                {
                    "run_id": run_id,
                    "task_id": sid,
                    "t_ms": int(t_ms),
                    "event": kind,
                    "evidence_id": evid,
                }
            )
            last_ms = max(last_ms or 0, int(t_ms))

    for mi, m in enumerate(msgs):
        d = _j(m["data"])
        role = d.get("role")
        mt = d.get("time") or {}
        mev = f"{sid}#m{mi}"
        ev(mt.get("created") or m["time_created"], f"message.{role}.created", mev)
        if mt.get("completed"):
            ev(mt["completed"], f"message.{role}.completed", mev)
        parts = parts_by_msg.get(m["id"], [])
        if role == "assistant":
            last_assistant = {"d": d, "mi": mi}
        step_i = -1
        open_step: dict[str, Any] | None = None
        for pi, p in enumerate(parts):
            pd = _j(p["data"])
            pt = pd.get("type")
            pev = f"{mev}.p{pi}"
            if pt == "step-start":
                step_i += 1
                open_step = {"start_ms": p["time_created"], "pi": pi}
                ev(p["time_created"], "step.start", pev)
            elif pt == "step-finish":
                if open_step is None:
                    step_i += 1
                    open_step = {"start_ms": mt.get("created") or m["time_created"], "pi": pi}
                tok = pd.get("tokens") or {}
                cache = tok.get("cache") or {}
                reqs.append(
                    {
                        "request_id": f"{sid}#m{mi}.s{step_i}",
                        "run_id": run_id,
                        "task_id": sid,
                        "msg_index": mi,
                        "step_index": step_i,
                        "message_id": m["id"],
                        "provider_id": d.get("providerID"),
                        "model_id": d.get("modelID"),
                        "agent": d.get("agent") or d.get("mode"),
                        "started_ms": open_step["start_ms"],
                        "ended_ms": p["time_created"],
                        "finish_reason": pd.get("reason"),
                        "tokens_in": tok.get("input"),
                        "tokens_out": tok.get("output"),
                        "tokens_reasoning": tok.get("reasoning"),
                        "tokens_cache_read": cache.get("read"),
                        "tokens_cache_write": cache.get("write"),
                        "cost": pd.get("cost"),
                        "is_error": False,
                        "error_name": None,
                        "http_status": None,
                        "provider_error_code": None,
                        "error_message": None,
                        "is_retryable": None,
                        "evidence_id": pev,
                    }
                )
                ev(p["time_created"], "step.finish", pev)
                open_step = None
            elif pt == "tool":
                st = pd.get("state") or {}
                stt = st.get("time") or {}
                outcome, denial = tool_outcome(st)
                meta = st.get("metadata") or {}
                child = meta.get("sessionId") if isinstance(meta, dict) else None
                inp = st.get("input")
                if pd.get("tool") == "task" and child:
                    children.append(
                        {
                            "child_task_id": child,
                            "launch_evidence_id": pev,
                            "launch_ms": stt.get("start") or p["time_created"],
                            "subagent_type": (inp or {}).get("subagent_type")
                            if isinstance(inp, dict)
                            else None,
                            "background": (inp or {}).get("background")
                            if isinstance(inp, dict)
                            else None,
                        }
                    )
                start = stt.get("start")
                end = stt.get("end")
                err = st.get("error")
                tools.append(
                    {
                        "tool_call_id": pev,
                        "run_id": run_id,
                        "task_id": sid,
                        "msg_index": mi,
                        "part_index": pi,
                        "call_id": pd.get("callID"),
                        "tool_name": pd.get("tool"),
                        "status": st.get("status"),
                        "outcome": outcome,
                        "permission_denied": denial is not None,
                        "denial_kind": denial,
                        "started_ms": start,
                        "ended_ms": end,
                        "duration_ms": (end - start)
                        if isinstance(start, int) and isinstance(end, int)
                        else None,
                        "error_message": scrub_text(str(err))[:400] if err else None,
                        "arguments_bytes": _nbytes(inp),
                        "arguments_sha256": _sha(inp),
                        "output_bytes": _nbytes(st.get("output")),
                        "output_sha256": _sha(st.get("output")),
                        "child_task_id": child,
                        "evidence_id": pev,
                    }
                )
                ev(start or p["time_created"], "tool.start", pev)
                if end:
                    ev(end, "tool.end", pev)
            else:
                ev(p["time_created"], f"part.{pt}", pev)
        err = d.get("error") if role == "assistant" else None
        if err:
            ed = err.get("data") or {}
            msg = ed.get("message")
            code = provider_error_code(ed.get("responseBody"), msg)
            row = {
                "request_id": f"{sid}#m{mi}.e",
                "run_id": run_id,
                "task_id": sid,
                "msg_index": mi,
                "step_index": step_i + 1 if open_step is None else step_i,
                "message_id": m["id"],
                "provider_id": d.get("providerID"),
                "model_id": d.get("modelID"),
                "agent": d.get("agent") or d.get("mode"),
                "started_ms": open_step["start_ms"] if open_step else mt.get("created"),
                "ended_ms": mt.get("completed") or m["time_updated"],
                "finish_reason": d.get("finish"),
                "tokens_in": None,
                "tokens_out": None,
                "tokens_reasoning": None,
                "tokens_cache_read": None,
                "tokens_cache_write": None,
                "cost": None,
                "is_error": err.get("name") != "MessageAbortedError",
                "error_name": err.get("name"),
                "http_status": ed.get("statusCode"),
                "provider_error_code": code,
                "error_message": scrub_text(str(msg))[:400] if msg else None,
                "is_retryable": ed.get("isRetryable"),
                "evidence_id": mev,
            }
            reqs.append(row)

    # outcome of the run from the final assistant turn
    outcome, status_message = "unknown", None
    if last_assistant:
        d = last_assistant["d"]
        err = d.get("error")
        if err:
            outcome = "aborted" if err.get("name") == "MessageAbortedError" else "failed"
            status_message = scrub_text(str((err.get("data") or {}).get("message") or ""))[:400]
        elif (d.get("time") or {}).get("completed") and d.get("finish") in (
            "stop",
            "end_turn",
            "length",
            None,
        ):
            outcome = "completed" if d.get("finish") != "length" else "failed"
        else:
            outcome = "no_terminal_event"
    model = _j(s.get("model"))
    provs = [r["provider_id"] for r in reqs if r["provider_id"]]
    run = {
        "run_id": run_id,
        "task_id": sid,
        "parent_task_id": s.get("parent_id"),
        "project_worktree": doc.get("project", {}).get("worktree"),
        "project": project_name(doc.get("project", {}).get("worktree"), s.get("directory")),
        "directory": s.get("directory"),
        "title": scrub_text(s.get("title") or "")[:200],
        "harness_version": s.get("version"),
        "agent": s.get("agent"),
        "provider_id": model.get("providerID") or (provs[-1] if provs else None),
        "model_id": model.get("id") or model.get("modelID"),
        "model_variant": model.get("variant"),
        "scope": doc.get("scope"),
        "scope_reason": doc.get("scope_reason"),
        "started_ms": s.get("time_created"),
        "ended_ms": max(last_ms or 0, 0) or s.get("time_updated"),
        "session_updated_ms": s.get("time_updated"),
        "outcome": outcome,
        "status_message": status_message,
        "n_messages": len(msgs),
        "tokens_in": s.get("tokens_input"),
        "tokens_out": s.get("tokens_output"),
        "tokens_reasoning": s.get("tokens_reasoning"),
        "tokens_cached": s.get("tokens_cache_read"),
        "tokens_cache_write": s.get("tokens_cache_write"),
        "cost": s.get("cost"),
        "archive_sha256": doc.get("_archive_sha256"),
        "evidence_id": f"{sid}#m0" if msgs else sid,
    }
    hops = [
        {
            "hop_id": f"{sid}>{c['child_task_id']}",
            "parent_run_id": run_id,
            "child_run_id": "kilo:" + c["child_task_id"],
            "parent_task_id": sid,
            **c,
        }
        for c in children
    ]
    return {
        "runs": [run],
        "model_requests": reqs,
        "tool_calls": tools,
        "events": events,
        "hops": hops,
    }


def project_name(worktree: str | None, directory: str | None) -> str | None:
    """Project label for task_id: the git worktree's folder, else the session directory's folder
    (Kilo worktrees under <repo>/.kilo/worktrees/<name> map back to <repo>)."""
    for path in (worktree, directory):
        p = (path or "").replace("\\", "/").rstrip("/")
        if "/.kilo/worktrees/" in p:
            p = p.split("/.kilo/worktrees/")[0]
        name = p.rsplit("/", 1)[-1] if p else ""
        if name and not name.endswith(":"):
            return name
    return None


def dedup(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    seen: dict[Any, dict[str, Any]] = {}
    for r in rows:
        seen[r[key]] = r
    return [seen[k] for k in sorted(seen, key=str)]


TABLE_KEYS = {
    "runs": "run_id",
    "hops": "hop_id",
    "model_requests": "request_id",
    "tool_calls": "tool_call_id",
    "events": None,
}


# ----------------------------------------------------------------------------------------------
# export: kilo.db snapshot -> data/kilo/<sessionId>/session-<sha16>.json.zst
# ----------------------------------------------------------------------------------------------
def _ro(db: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def inventory(con: sqlite3.Connection, provider_map: dict[str, str]) -> list[dict[str, Any]]:
    req: dict[str, list[str | None]] = {}
    sel: dict[str, list[str | None]] = {}
    nmsg: dict[str, int] = {}
    errs: dict[str, list[dict[str, Any]]] = {}
    for sid, mid, tc, data in con.execute("SELECT session_id, id, time_created, data FROM message"):
        d = _j(data)
        nmsg[sid] = nmsg.get(sid, 0) + 1
        if d.get("role") == "assistant":
            req.setdefault(sid, []).append(d.get("providerID"))
            e = d.get("error")
            if e:
                ed = e.get("data") or {}
                errs.setdefault(sid, []).append(
                    {
                        "message_id": mid,
                        "at_utc": ms_iso((d.get("time") or {}).get("created") or tc),
                        "name": e.get("name"),
                        "http_status": ed.get("statusCode"),
                        "provider_error_code": provider_error_code(
                            ed.get("responseBody"), ed.get("message")
                        ),
                        "message": scrub_text(str(ed.get("message") or ""))[:240],
                        "provider_id": d.get("providerID"),
                        "model_id": d.get("modelID"),
                    }
                )
        elif d.get("role") == "user":
            sel.setdefault(sid, []).append((d.get("model") or {}).get("providerID"))
    out = []
    for row in con.execute(
        "SELECT id, parent_id, version, agent, model, time_created, time_updated, directory "
        "FROM session ORDER BY time_created"
    ):
        sid, par, ver, agent, model, tc, tu, dirr = row
        scope, why = classify(req.get(sid, []), sel.get(sid, []), provider_map)
        out.append(
            {
                "task_id": sid,
                "parent_task_id": par,
                "harness_version": ver,
                "agent": agent,
                "session_model": _j(model),
                "created_utc": ms_iso(tc),
                "updated_utc": ms_iso(tu),
                "directory": dirr,
                "n_messages": nmsg.get(sid, 0),
                "request_providers": sorted({p for p in req.get(sid, []) if p}),
                "selected_providers": sorted({p for p in sel.get(sid, []) if p}),
                "scope": scope,
                "scope_reason": why,
                "errors": errs.get(sid, []),
            }
        )
    return out


def session_doc(con: sqlite3.Connection, sid: str, stats: Any = None) -> dict[str, Any]:
    cols = [r[1] for r in con.execute("PRAGMA table_info(session)")]
    srow = dict(
        zip(cols, con.execute("SELECT * FROM session WHERE id=?", (sid,)).fetchone(), strict=True)
    )
    srow.pop("summary_diffs", None)  # file diffs (source code) are not telemetry
    proj = con.execute(
        "SELECT id, worktree FROM project WHERE id=?", (srow["project_id"],)
    ).fetchone()
    msgs = [
        {"id": i, "time_created": a, "time_updated": b, "data": _j(d)}
        for i, a, b, d in con.execute(
            "SELECT id, time_created, time_updated, data FROM message WHERE session_id=?", (sid,)
        )
    ]
    parts = [
        {"id": i, "message_id": mid, "time_created": a, "time_updated": b, "data": _j(d)}
        for i, mid, a, b, d in con.execute(
            "SELECT id, message_id, time_created, time_updated, data FROM part WHERE session_id=?",
            (sid,),
        )
    ]
    doc = {
        "format": "kilo-session/v1",
        "session": srow,
        "project": {"id": proj[0], "worktree": proj[1]} if proj else {},
        "messages": msgs,
        "parts": parts,
    }
    return scrub_obj(doc, stats)


def export(
    db: str,
    snapshot_label: str,
    provider_map: dict[str, str],
    scope_providers: set[str] | None = None,
    kilo_dir: str | None = None,
    log=print,
) -> dict[str, Any]:
    """Write in-scope sessions as immutable archives. scope_providers overrides the InferHub rule
    (verification in a scratch AT_KILO_DIR only)."""
    import zstandard

    kd = kilo_dir or KILO_DIR
    con = _ro(db)
    inv = inventory(con, provider_map)
    if scope_providers:
        for r in inv:
            used = set(r["request_providers"]) | set(r["selected_providers"])
            if used and used <= scope_providers:
                r["scope"], r["scope_reason"] = (
                    "override",
                    "scope override " + ",".join(sorted(used)),
                )
    stats = C.ScrubStats()
    written, unchanged = [], []
    for r in inv:
        if r["scope"] not in ("inferhub", "override"):
            continue
        sid = r["task_id"]
        doc = session_doc(con, sid, stats)
        doc["scope"], doc["scope_reason"] = r["scope"], r["scope_reason"]
        raw = C.canon(doc).encode("utf-8")
        sha = C.sha256_hex(raw)
        d = os.path.join(kd, sid)
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, f"session-{sha[:16]}.json.zst")
        if os.path.exists(f):
            unchanged.append(sid)
            continue
        z = zstandard.ZstdCompressor(level=12).compress(raw)
        tmp = f + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(z)
        os.replace(tmp, f)
        os.chmod(f, 0o444)
        with open(os.path.join(d, "SHA256SUMS"), "a", encoding="utf-8") as fh:
            fh.write(
                C.canon(
                    {
                        "file": os.path.basename(f),
                        "json_sha256": sha,
                        "zst_sha256": C.sha256_hex(z),
                        "zst_bytes": len(z),
                        "json_bytes": len(raw),
                        "source_snapshot": snapshot_label,
                        "session_updated_ms": doc["session"].get("time_updated"),
                        "exported_at": C.now_iso(),
                    }
                )
                + "\n"
            )
        written.append(sid)
    con.close()
    os.makedirs(os.path.join(kd, "_inventory"), exist_ok=True)
    C.atomic_write_text(
        os.path.join(kd, "_inventory", f"{snapshot_label}.json"),
        json.dumps({"snapshot": snapshot_label, "sessions": inv}, indent=1),
    )
    res = {
        "sessions": len(inv),
        "written": written,
        "unchanged": unchanged,
        "scope_counts": _count(r["scope"] for r in inv),
        "scrub": stats.as_dict(),
    }
    log(
        f"[kilo] export {snapshot_label}: {len(inv)} sessions, scope={res['scope_counts']}, "
        f"written={len(written)} unchanged={len(unchanged)}"
    )
    return res


def _count(it: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out


# ----------------------------------------------------------------------------------------------
# build: archives -> clean/kilo/*.parquet
# ----------------------------------------------------------------------------------------------
def latest_archives(kilo_dir: str | None = None) -> list[tuple[str, str, str]]:
    """[(task_id, archive path, json_sha256)] newest archive per session, verified by manifest."""
    kd = kilo_dir or KILO_DIR
    out = []
    for man in sorted(glob.glob(os.path.join(kd, "*", "SHA256SUMS"))):
        sid = os.path.basename(os.path.dirname(man))
        if sid.startswith("_"):
            continue
        rows = C.read_jsonl(man)
        if not rows:
            continue
        best = max(rows, key=lambda r: (r.get("session_updated_ms") or 0, r["exported_at"]))
        out.append((sid, os.path.join(os.path.dirname(man), best["file"]), best))
    return out


SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "runs": [
        ("run_id", "s"),
        ("task_id", "s"),
        ("parent_task_id", "s"),
        ("project_worktree", "s"),
        ("project", "s"),
        ("directory", "s"),
        ("title", "s"),
        ("harness_version", "s"),
        ("agent", "s"),
        ("provider_id", "s"),
        ("model_id", "s"),
        ("model_variant", "s"),
        ("scope", "s"),
        ("scope_reason", "s"),
        ("started_ms", "i"),
        ("ended_ms", "i"),
        ("session_updated_ms", "i"),
        ("outcome", "s"),
        ("status_message", "s"),
        ("n_messages", "i"),
        ("tokens_in", "i"),
        ("tokens_out", "i"),
        ("tokens_reasoning", "i"),
        ("tokens_cached", "i"),
        ("tokens_cache_write", "i"),
        ("cost", "f"),
        ("archive_sha256", "s"),
        ("evidence_id", "s"),
    ],
    "hops": [
        ("hop_id", "s"),
        ("parent_run_id", "s"),
        ("child_run_id", "s"),
        ("parent_task_id", "s"),
        ("child_task_id", "s"),
        ("launch_evidence_id", "s"),
        ("launch_ms", "i"),
        ("subagent_type", "s"),
        ("background", "b"),
    ],
    "model_requests": [
        ("request_id", "s"),
        ("run_id", "s"),
        ("task_id", "s"),
        ("msg_index", "i"),
        ("step_index", "i"),
        ("message_id", "s"),
        ("provider_id", "s"),
        ("model_id", "s"),
        ("agent", "s"),
        ("started_ms", "i"),
        ("ended_ms", "i"),
        ("finish_reason", "s"),
        ("tokens_in", "i"),
        ("tokens_out", "i"),
        ("tokens_reasoning", "i"),
        ("tokens_cache_read", "i"),
        ("tokens_cache_write", "i"),
        ("cost", "f"),
        ("is_error", "b"),
        ("error_name", "s"),
        ("http_status", "i"),
        ("provider_error_code", "i"),
        ("error_message", "s"),
        ("is_retryable", "b"),
        ("evidence_id", "s"),
    ],
    "tool_calls": [
        ("tool_call_id", "s"),
        ("run_id", "s"),
        ("task_id", "s"),
        ("msg_index", "i"),
        ("part_index", "i"),
        ("call_id", "s"),
        ("tool_name", "s"),
        ("status", "s"),
        ("outcome", "s"),
        ("permission_denied", "b"),
        ("denial_kind", "s"),
        ("started_ms", "i"),
        ("ended_ms", "i"),
        ("duration_ms", "i"),
        ("error_message", "s"),
        ("arguments_bytes", "i"),
        ("arguments_sha256", "s"),
        ("output_bytes", "i"),
        ("output_sha256", "s"),
        ("child_task_id", "s"),
        ("evidence_id", "s"),
    ],
    "events": [
        ("run_id", "s"),
        ("task_id", "s"),
        ("t_ms", "i"),
        ("event", "s"),
        ("evidence_id", "s"),
    ],
}


def _coerce(v: Any, t: str) -> Any:
    if v is None:
        return None
    try:
        if t == "i":
            return int(v)
        if t == "f":
            return float(v)
        if t == "b":
            return bool(v)
        return v if isinstance(v, str) else str(v)
    except (TypeError, ValueError):
        return None


def _write(table: str, rows: list[dict[str, Any]], out_dir: str) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    ty = {"s": pa.string(), "i": pa.int64(), "f": pa.float64(), "b": pa.bool_()}
    cols = SCHEMAS[table]
    schema = pa.schema([(c, ty[t]) for c, t in cols])
    data = {c: [_coerce(r.get(c), t) for r in rows] for c, t in cols}
    f = os.path.join(out_dir, f"kilo_{table}.parquet")
    pq.write_table(pa.table(data, schema=schema), f + ".tmp", compression="zstd")
    os.replace(f + ".tmp", f)


def build(
    kilo_dir: str | None = None, out_dir: str | None = None, force: bool = False, log=print
) -> dict[str, Any]:
    import zstandard

    od = out_dir or OUT
    os.makedirs(od, exist_ok=True)
    arch = latest_archives(kilo_dir)
    set_sha = C.sha256_hex(C.canon(sorted((s, m["json_sha256"]) for s, _, m in arch)))
    have_all = all(os.path.exists(os.path.join(od, f"kilo_{t}.parquet")) for t in SCHEMAS)
    prev = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    code = C.file_sha256(__file__)
    if not force and have_all and prev.get("set_sha256") == set_sha and prev.get("code") == code:
        return {"kilo_set_sha256": set_sha, "sessions": len(arch), "rebuilt": False}
    acc: dict[str, list[dict[str, Any]]] = {t: [] for t in SCHEMAS}
    bad = []
    dctx = zstandard.ZstdDecompressor()
    for sid, path, man in arch:
        with open(path, "rb") as fh:
            z = fh.read()
        if C.sha256_hex(z) != man["zst_sha256"]:
            bad.append(sid)
            continue
        raw = dctx.decompress(z)
        doc = json.loads(raw)
        doc["_archive_sha256"] = man["json_sha256"]
        for t, rows in session_rows(doc).items():
            acc[t].extend(rows)
    # leak check (same rule as atpipe.run.leak_check): no e-mail address in any string column
    leaks = sum(
        1
        for rows in acc.values()
        for r in rows
        if any(isinstance(v, str) and "@" in v and C.EMAIL_RE.search(v) for v in r.values())
    )
    if leaks:
        raise RuntimeError(f"kilo leak check: {leaks} rows contain an e-mail address")
    counts = {}
    for t, rows in acc.items():
        k = TABLE_KEYS[t]
        if k:
            rows = dedup(rows, k)
        else:
            rows = dedup(
                [dict(r, _k=f"{r['evidence_id']}|{r['event']}|{r['t_ms']}") for r in rows], "_k"
            )
        _write(t, rows, od)
        counts[t] = len(rows)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    C.atomic_write_text(
        STATE_FILE,
        json.dumps(
            {"set_sha256": set_sha, "code": code, "counts": counts, "built_at": C.now_iso()}
        ),
    )
    log(f"[kilo] build: {len(arch)} sessions -> {counts}" + (f" BAD sha: {bad}" if bad else ""))
    if bad:
        raise RuntimeError(f"kilo archive sha256 mismatch: {bad}")
    return {"kilo_set_sha256": set_sha, "sessions": len(arch), "rebuilt": True, "rows": counts}


def run(log=print) -> dict[str, Any]:
    """Pipeline hook (atpipe.run): rebuild clean/kilo when the archive set changed."""
    return build(log=log)


MARTS = {
    "kilo_fact_runs": "run_id",
    "kilo_fact_hops": "hop_id",
    "kilo_fact_model_requests": "request_id",
    "kilo_fact_tool_calls": "tool_call_id",
    "kilo_fact_idle_gaps": "gap_id",
}


def fix_empty_marts(out_dir: str) -> None:
    """dbt-duckdb writes one all-NULL row for an empty external model; drop it (key IS NULL)."""
    import duckdb

    con = duckdb.connect()
    for t, key in MARTS.items():
        f = os.path.join(out_dir, t + ".parquet")
        if os.path.exists(f):
            con.execute(
                f"COPY (SELECT * FROM read_parquet('{f}') WHERE {key} IS NOT NULL) "
                f"TO '{f}.tmp' (FORMAT parquet, COMPRESSION zstd)"
            )
            os.replace(f + ".tmp", f)
    con.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="atpipe.ingest_kilo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser(
        "export", help="classify + archive in-scope sessions from a kilo.db snapshot"
    )
    e.add_argument("--db", required=True)
    e.add_argument("--snapshot", required=True, help="label, e.g. snap-20260924")
    e.add_argument("--config", help="kilo.jsonc provider block as JSON (no keys needed)")
    e.add_argument(
        "--scope-override",
        help="comma list of provider ids to treat as in scope "
        "(verification only, with a scratch AT_KILO_DIR)",
    )
    b = sub.add_parser("build", help="archives -> clean/kilo/*.parquet")
    b.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        pm = provider_map_from_config(json.load(open(a.config)) if a.config else None)
        so = set(a.scope_override.split(",")) if a.scope_override else None
        r = export(a.db, a.snapshot, pm, so)
        print(json.dumps({k: v for k, v in r.items() if k not in ("written", "unchanged")}))
    else:
        print(json.dumps(build(force=a.force)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
