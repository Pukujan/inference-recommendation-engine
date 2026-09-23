#!/usr/bin/env python3
"""Local, deterministic Issue Ledger reducer.

This module is intentionally a pure projection core. It does not contact a
provider, write live state, change IRE scores, or infer that an agent report is
true. Adapters may append validated events; this reducer deduplicates them and
builds a reproducible issue projection.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable


EVENT_TYPES = {
    "report_submitted",
    "evidence_attached",
    "fingerprint_assigned",
    "occurrence_correlated",
    "reproduction_attempted",
    "reproduction_completed",
    "resolution_proposed",
    "resolution_verified",
    "issue_reopened",
    "issue_superseded",
    "issue_retracted",
}
OUTCOMES = {"success", "failure", "timeout", "cancelled", "partial", "unknown"}
EVIDENCE_LEVELS = (
    "reported_only",
    "observed_once",
    "receipt_backed",
    "replayable",
    "operationally_reproduced",
    "independently_corroborated",
    "accepted",
    "retracted",
    "stale_pending_review",
)
EVIDENCE_RANK = {name: index for index, name in enumerate(EVIDENCE_LEVELS)}
EVIDENCE_RANK.update({"reproduced": 4, "corroborated": 5, "verified": 6})
DEFAULT_TRUSTED_VERIFIER_IDS = frozenset({"system:issue-ledger-verifier"})
REPORT_PACKET_SCHEMA_VERSION = "issue-ledger/report/v1"
REPORT_PACKET_FIELDS = frozenset({
    "schema_version", "event_id", "idempotency_key", "actor", "subject",
    "execution_id", "correlation_id", "observed_at", "recorded_at", "known_at",
    "subject_refs", "receipt_refs", "summary", "classification", "outcome", "observation",
    "attempted_recovery", "proposed_next_action", "runbook_refs",
})
REPORT_ACTOR_FIELDS = frozenset({"id", "kind", "harness", "model", "software_commit"})
REPORT_SUBJECT_FIELDS = frozenset({
    "provider", "route", "model", "operation", "workload_class", "agent_harness",
    "stream_mode", "configuration_hash", "environment_hash",
})
REPORT_OBSERVATION_FIELDS = frozenset({
    "failure_phase", "stream_mode", "error_code", "finish_reason_capture_status",
    "configuration_hash", "environment_hash", "timeout_policy", "timeout_seconds",
    "response_capture_status",
})
REPORT_RECOVERY_FIELDS = frozenset({"status", "action", "result"})
EVENT_FIELDS = frozenset({
    "schema_version", "event_id", "event_type", "recorded_at", "valid_at", "known_at",
    "actor", "correlation_id", "idempotency_key", "subject_refs", "evidence_refs",
    "provenance", "payload",
})
EVENT_ACTOR_FIELDS = frozenset({"id", "kind", "harness", "model", "software_commit"})
EVIDENCE_ROLES = frozenset({"receipt", "trace", "artifact", "source", "reproduction", "counterexample"})
OCCURRENCE_LEVEL = {
    "reported_only": "reported_only",
    "observed_once": "reported_only",
    "receipt_backed": "receipt_backed",
    "replayable": "reproduced",
    "operationally_reproduced": "reproduced",
    "independently_corroborated": "corroborated",
    "accepted": "verified",
}


class LedgerError(ValueError):
    """Raised when an event is ambiguous, invalid, or conflicts on replay."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(f"{field} must be a non-empty string")
    return value.strip()


def _stamp(value: Any, field: str) -> str:
    value = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise LedgerError(f"{field} must include a timezone")
    return value


def _parse_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _normal(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().lower() or None


def _reject_unknown(value: dict[str, Any], allowed: set[str] | frozenset[str], field: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise LedgerError(f"{field} contains unsupported fields: {', '.join(unknown)}")


def _optional_text(value: Any, field: str) -> None:
    if value is not None:
        _required_text(value, field)


def validate_report_packet(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate the closed adapter-facing report packet contract."""
    if not isinstance(spec, dict):
        raise LedgerError("report packet must be an object")
    _reject_unknown(spec, REPORT_PACKET_FIELDS, "report")
    if spec.get("schema_version") != REPORT_PACKET_SCHEMA_VERSION:
        raise LedgerError("unsupported issue-ledger report packet schema")

    actor = spec.get("actor")
    subject = spec.get("subject")
    for field, value in (("actor", actor), ("subject", subject)):
        if not isinstance(value, dict):
            raise LedgerError(f"report.{field} must be an object")
    _reject_unknown(actor, REPORT_ACTOR_FIELDS, "report.actor")
    _reject_unknown(subject, REPORT_SUBJECT_FIELDS, "report.subject")
    _required_text(actor.get("id"), "report.actor.id")
    if actor.get("kind") not in {"human", "agent", "service", "importer", "system"}:
        raise LedgerError("report.actor.kind is not recognized")
    for field in ("harness", "model", "software_commit"):
        _optional_text(actor.get(field), f"report.actor.{field}")
    for field in ("provider", "route", "operation", "workload_class"):
        _required_text(subject.get(field), f"report.subject.{field}")
    for field in ("model", "agent_harness", "stream_mode", "configuration_hash", "environment_hash"):
        _optional_text(subject.get(field), f"report.subject.{field}")

    _required_text(spec.get("execution_id"), "report.execution_id")
    _required_text(spec.get("summary"), "report.summary")
    if spec.get("outcome", "unknown") not in OUTCOMES:
        raise LedgerError("report.outcome is not recognized")
    classification = spec.get("classification", "unknown")
    if classification not in {"protocol", "client", "provider", "model_behavior", "harness", "telemetry", "evaluation", "unknown"}:
        raise LedgerError("report.classification is not recognized")
    for field in ("correlation_id", "event_id", "idempotency_key"):
        _optional_text(spec.get(field), f"report.{field}")
    for field in ("observed_at", "recorded_at", "known_at"):
        if spec.get(field) is not None:
            _stamp(spec[field], f"report.{field}")

    subject_refs = spec.get("subject_refs")
    if subject_refs is not None:
        if not isinstance(subject_refs, list) or not subject_refs or len(set(subject_refs)) != len(subject_refs):
            raise LedgerError("report.subject_refs must be a non-empty unique array")
        for index, ref in enumerate(subject_refs):
            _required_text(ref, f"report.subject_refs[{index}]")

    receipt_refs = spec.get("receipt_refs")
    if receipt_refs is not None:
        if not isinstance(receipt_refs, list) or not receipt_refs or len(set(receipt_refs)) != len(receipt_refs):
            raise LedgerError("report.receipt_refs must be a non-empty unique array")
        for index, ref in enumerate(receipt_refs):
            _required_text(ref, f"report.receipt_refs[{index}]")

    runbook_refs = spec.get("runbook_refs")
    if runbook_refs is not None:
        if not isinstance(runbook_refs, list) or not runbook_refs or len(set(runbook_refs)) != len(runbook_refs):
            raise LedgerError("report.runbook_refs must be a non-empty unique array")
        for index, ref in enumerate(runbook_refs):
            _required_text(ref, f"report.runbook_refs[{index}]")

    recovery = spec.get("attempted_recovery")
    if recovery is not None:
        if not isinstance(recovery, dict):
            raise LedgerError("report.attempted_recovery must be an object")
        _reject_unknown(recovery, REPORT_RECOVERY_FIELDS, "report.attempted_recovery")
        if recovery.get("status") not in {"not_attempted", "attempted", "succeeded", "failed", "unknown"}:
            raise LedgerError("report.attempted_recovery.status is not recognized")
        for field in ("action", "result"):
            _optional_text(recovery.get(field), f"report.attempted_recovery.{field}")
    if spec.get("proposed_next_action") is not None:
        _required_text(spec["proposed_next_action"], "report.proposed_next_action")

    observation = spec.get("observation", {})
    if not isinstance(observation, dict):
        raise LedgerError("report.observation must be an object")
    _reject_unknown(observation, REPORT_OBSERVATION_FIELDS, "report.observation")
    for field in (
        "failure_phase", "stream_mode", "error_code", "finish_reason_capture_status",
        "configuration_hash", "environment_hash", "timeout_policy", "response_capture_status",
    ):
        _optional_text(observation.get(field), f"report.observation.{field}")
    if observation.get("timeout_seconds") is not None:
        timeout_seconds = observation["timeout_seconds"]
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds < 0:
            raise LedgerError("report.observation.timeout_seconds must be a non-negative number")
    return deepcopy(spec)


def make_fingerprint(
    subject: dict[str, Any],
    observation: dict[str, Any],
    version: str = "fp-v1",
) -> dict[str, Any]:
    """Return a versioned deterministic fingerprint for one observation."""
    if not isinstance(subject, dict) or not isinstance(observation, dict):
        raise LedgerError("subject and observation must be objects")
    fields = {
        "provider": subject.get("provider"),
        "route": subject.get("route"),
        "model": subject.get("model"),
        "operation": subject.get("operation"),
        "workload_class": subject.get("workload_class"),
        "failure_phase": observation.get("failure_phase"),
        "stream_mode": observation.get("stream_mode"),
        "error_code": observation.get("error_code"),
        "finish_reason_capture_status": observation.get("finish_reason_capture_status"),
        "configuration_hash": observation.get("configuration_hash"),
        "environment_hash": observation.get("environment_hash"),
    }
    components = {key: _normal(value) for key, value in fields.items()}
    return {"version": _required_text(version, "fingerprint.version"),
            "value": _hash({"version": version, "components": components}),
            "components": components}


def _event_semantics(event: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(event)
    result.pop("event_id", None)
    result.pop("idempotency_key", None)
    return result


def validate_event(event: dict[str, Any]) -> dict[str, Any]:
    """Validate the envelope and return a defensive copy."""
    if not isinstance(event, dict):
        raise LedgerError("event must be an object")
    _reject_unknown(event, EVENT_FIELDS, "event")
    for field in ("schema_version", "event_id", "event_type", "recorded_at", "actor", "subject_refs", "payload"):
        if field not in event:
            raise LedgerError(f"event.{field} is required")
    if event["schema_version"] != "issue-ledger/v1":
        raise LedgerError("unsupported issue-ledger event schema")
    event_id = _required_text(event["event_id"], "event.event_id")
    if not event_id.startswith("ILE-"):
        raise LedgerError("event.event_id must start with ILE-")
    if event["event_type"] not in EVENT_TYPES:
        raise LedgerError("unknown event_type")
    recorded_at = _stamp(event["recorded_at"], "event.recorded_at")
    actor = event["actor"]
    if not isinstance(actor, dict):
        raise LedgerError("event.actor must be an object")
    _reject_unknown(actor, EVENT_ACTOR_FIELDS, "event.actor")
    _required_text(actor.get("id"), "event.actor.id")
    if actor.get("kind") not in {"human", "agent", "service", "importer", "system"}:
        raise LedgerError("event.actor.kind is not recognized")
    refs = event["subject_refs"]
    if not isinstance(refs, list) or not refs or len(set(refs)) != len(refs):
        raise LedgerError("event.subject_refs must be a non-empty unique array")
    for index, ref in enumerate(refs):
        _required_text(ref, f"event.subject_refs[{index}]")
    if not isinstance(event["payload"], dict):
        raise LedgerError("event.payload must be an object")
    for field in ("correlation_id", "idempotency_key"):
        if event.get(field) is not None:
            _required_text(event[field], f"event.{field}")
    evidence_refs = event.get("evidence_refs")
    if evidence_refs is not None:
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise LedgerError("event.evidence_refs must be a non-empty array")
        seen_refs: set[str] = set()
        for index, evidence in enumerate(evidence_refs):
            if not isinstance(evidence, dict):
                raise LedgerError(f"event.evidence_refs[{index}] must be an object")
            _reject_unknown(evidence, {"ref_id", "role", "content_hash"}, f"event.evidence_refs[{index}]")
            ref_id = _required_text(evidence.get("ref_id"), f"event.evidence_refs[{index}].ref_id")
            if ref_id in seen_refs:
                raise LedgerError("event.evidence_refs must contain unique ref_id values")
            seen_refs.add(ref_id)
            if evidence.get("role") not in EVIDENCE_ROLES:
                raise LedgerError(f"event.evidence_refs[{index}].role is not recognized")
            if evidence.get("content_hash") is not None:
                _required_text(evidence["content_hash"], f"event.evidence_refs[{index}].content_hash")
    provenance = event.get("provenance")
    if provenance is not None:
        if not isinstance(provenance, dict):
            raise LedgerError("event.provenance must be an object")
        _reject_unknown(provenance, {"producer", "software_commit", "policy_hash", "trace_id"}, "event.provenance")
        _required_text(provenance.get("producer"), "event.provenance.producer")
        _required_text(provenance.get("policy_hash"), "event.provenance.policy_hash")
    if event.get("known_at") is not None:
        _stamp(event["known_at"], "event.known_at")
    if event.get("valid_at") is not None:
        valid_at = event["valid_at"]
        if not isinstance(valid_at, dict) or "from" not in valid_at:
            raise LedgerError("event.valid_at must contain from")
        valid_from = _stamp(valid_at["from"], "event.valid_at.from")
        valid_to = valid_at.get("to")
        if valid_to is not None:
            valid_to = _stamp(valid_to, "event.valid_at.to")
            if _parse_stamp(valid_to) < _parse_stamp(valid_from):
                raise LedgerError("event.valid_at.to cannot precede from")
    copy = deepcopy(event)
    copy["recorded_at"] = recorded_at
    return copy


def make_report_event(spec: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized report event from the adapter-facing report packet."""
    spec = validate_report_packet(spec)
    actor = spec.get("actor")
    subject = spec.get("subject")
    actor_id = _required_text(actor.get("id"), "report.actor.id")
    actor_kind = actor.get("kind")
    execution_id = _required_text(spec.get("execution_id"), "report.execution_id")
    recorded_at = spec.get("recorded_at")
    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    recorded_at = _stamp(recorded_at, "report.recorded_at")
    observed_at = _stamp(spec.get("observed_at"), "report.observed_at")
    outcome = spec.get("outcome", "unknown")
    observation = spec.get("observation", {})
    identity_material = {
        "actor": actor_id,
        "subject": subject,
        "execution_id": execution_id,
        "observed_at": observed_at,
        "outcome": outcome,
        "observation": observation,
    }
    idempotency_key = spec.get("idempotency_key") or "report:" + _hash(identity_material)[len("sha256:"):]
    event_id = spec.get("event_id") or "ILE-" + _hash(identity_material)[len("sha256:"):][:24]
    payload = {
        "summary": _required_text(spec.get("summary"), "report.summary"),
        "classification": spec.get("classification", "unknown"),
        "execution_id": execution_id,
        "observed_at": observed_at,
        "outcome": outcome,
        "subject": deepcopy(subject),
        "observation": deepcopy(observation),
    }
    for field in ("receipt_refs", "attempted_recovery", "proposed_next_action", "runbook_refs"):
        if field in spec:
            payload[field] = deepcopy(spec[field])
    return validate_event({
        "schema_version": "issue-ledger/v1",
        "event_id": _required_text(event_id, "report.event_id"),
        "event_type": "report_submitted",
        "recorded_at": recorded_at,
        "valid_at": {"from": observed_at, "to": None},
        "known_at": _stamp(spec.get("known_at") or recorded_at, "report.known_at"),
        "actor": deepcopy(actor),
        "correlation_id": spec.get("correlation_id") or execution_id,
        "idempotency_key": _required_text(idempotency_key, "report.idempotency_key"),
        "subject_refs": spec.get("subject_refs") or [f"{subject['provider']}:{subject['route']}"],
        "payload": payload,
    })


def deduplicate_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate, deduplicate, and canonically order events.

    A repeated idempotency key may arrive through multiple adapters. Identical
    semantics collapse to one event; conflicting semantics fail closed.
    """
    by_event_id: dict[str, dict[str, Any]] = {}
    by_key: dict[str, dict[str, Any]] = {}
    unique: list[dict[str, Any]] = []
    for raw in events:
        event = validate_event(raw)
        event_id = event["event_id"]
        key = event.get("idempotency_key") or event_id
        prior = by_event_id.get(event_id) or by_key.get(key)
        if prior is not None:
            if _canonical(_event_semantics(prior)) != _canonical(_event_semantics(event)):
                raise LedgerError("conflicting replay for event identity or idempotency key")
            continue
        by_event_id[event_id] = event
        by_key[key] = event
        unique.append(event)
    return sorted(unique, key=lambda item: (item["recorded_at"], item["event_id"]))


def _subject(payload: dict[str, Any]) -> dict[str, Any]:
    subject = payload.get("subject")
    if not isinstance(subject, dict):
        raise LedgerError("event.payload.subject is required for projection")
    required = ("provider", "route", "operation", "workload_class")
    for field in required:
        _required_text(subject.get(field), f"payload.subject.{field}")
    result = {key: subject.get(key) for key in (
        "provider", "route", "model", "operation", "workload_class",
        "agent_harness", "stream_mode", "configuration_hash", "environment_hash",
    )}
    return result


def _fingerprint(event: dict[str, Any], subject: dict[str, Any]) -> dict[str, Any]:
    payload = event["payload"]
    supplied = payload.get("fingerprint")
    if isinstance(supplied, dict) and supplied.get("version") and supplied.get("value") and isinstance(supplied.get("components"), dict):
        return deepcopy(supplied)
    observation = payload.get("observation")
    if not isinstance(observation, dict):
        observation = {
            "failure_phase": payload.get("failure_phase"),
            "stream_mode": subject.get("stream_mode"),
            "error_code": payload.get("error_code"),
            "finish_reason_capture_status": payload.get("finish_reason_capture_status"),
            "configuration_hash": subject.get("configuration_hash"),
            "environment_hash": subject.get("environment_hash"),
        }
    return make_fingerprint(subject, observation)


def _trustworthy_verifier(event: dict[str, Any], trusted_verifier_ids: Iterable[str] | None = None) -> bool:
    actor = event["actor"]
    payload = event["payload"]
    trusted = set(trusted_verifier_ids) if trusted_verifier_ids is not None else DEFAULT_TRUSTED_VERIFIER_IDS
    return (
        actor.get("id") in trusted
        and actor.get("kind") in {"human", "service", "system"}
        and payload.get("verified") is True
    )


def _authoritative_verifier(event: dict[str, Any], trusted_verifier_ids: Iterable[str] | None = None) -> bool:
    """Require both trusted identity and explicit verifier provenance."""
    provenance = event.get("provenance")
    return (
        _trustworthy_verifier(event, trusted_verifier_ids)
        and isinstance(provenance, dict)
        and bool(provenance.get("producer"))
        and bool(provenance.get("policy_hash"))
    )


def _resolution_evidence_complete(event: dict[str, Any], trusted_verifier_ids: Iterable[str] | None = None) -> bool:
    """Require explicit replay and regression receipts for a verified fix."""
    payload = event["payload"]
    evidence_refs = event.get("evidence_refs")
    return (
        event["event_type"] == "resolution_verified"
        and _authoritative_verifier(event, trusted_verifier_ids)
        and bool(payload.get("replay_receipt_ref"))
        and bool(payload.get("regression_receipt_ref"))
        and isinstance(evidence_refs, list)
        and bool(evidence_refs)
        and isinstance(event.get("provenance"), dict)
    )


def _event_level(event: dict[str, Any], trusted_verifier_ids: Iterable[str] | None = None) -> str:
    payload = event["payload"]
    if event["event_type"] == "report_submitted":
        return "reported_only"
    if event["event_type"] == "evidence_attached" and payload.get("receipt_ref"):
        return "receipt_backed"
    if event["event_type"] == "reproduction_completed" and payload.get("result") == "reproduced":
        return "independently_corroborated" if payload.get("independent") and _trustworthy_verifier(event, trusted_verifier_ids) else "operationally_reproduced"
    if _resolution_evidence_complete(event, trusted_verifier_ids):
        return "accepted"
    return "observed_once"


def _occurrence(event: dict[str, Any], level: str) -> dict[str, Any]:
    payload = event["payload"]
    execution_id = _required_text(payload.get("execution_id") or event.get("correlation_id") or event["event_id"], "payload.execution_id")
    correlation_id = _required_text(event.get("correlation_id") or execution_id, "event.correlation_id")
    observed_at = _stamp(payload.get("observed_at") or event["recorded_at"], "payload.observed_at")
    outcome = payload.get("outcome", "unknown")
    if outcome not in OUTCOMES:
        raise LedgerError("payload.outcome is not recognized")
    return {
        "occurrence_id": "OCC-" + hashlib.sha256(execution_id.encode("utf-8")).hexdigest()[:24],
        "event_ref": event["event_id"],
        "execution_id": execution_id,
        "correlation_id": correlation_id,
        "actor_id": event["actor"]["id"],
        "observed_at": observed_at,
        "outcome": outcome,
        "evidence_level": OCCURRENCE_LEVEL.get(level, "reported_only"),
        "receipt_ref": payload.get("receipt_ref"),
    }


def _merge_occurrence(occurrences: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> None:
    prior = occurrences.get(candidate["execution_id"])
    if prior is None:
        occurrences[candidate["execution_id"]] = candidate
        return
    if prior["correlation_id"] != candidate["correlation_id"]:
        raise LedgerError("conflicting correlation IDs for one execution")
    if EVIDENCE_RANK.get(candidate["evidence_level"], 0) > EVIDENCE_RANK.get(prior["evidence_level"], 0):
        prior["evidence_level"] = candidate["evidence_level"]
    if prior.get("receipt_ref") is None and candidate.get("receipt_ref") is not None:
        prior["receipt_ref"] = candidate["receipt_ref"]
    if prior["outcome"] == "unknown" and candidate["outcome"] != "unknown":
        prior["outcome"] = candidate["outcome"]


def reduce_events(
    events: Iterable[dict[str, Any]],
    policy_hash: str = "policy:issue-ledger-v1",
    trusted_verifier_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Build canonical issue projections from an event set."""
    normalized = deduplicate_events(events)
    groups: dict[str, dict[str, Any]] = {}
    for event in normalized:
        subject = _subject(event["payload"])
        fingerprint = _fingerprint(event, subject)
        key = fingerprint["value"]
        group = groups.setdefault(key, {"events": [], "subject": subject, "fingerprint": fingerprint, "occurrences": {}})
        group["events"].append(event)
        level = _event_level(event, trusted_verifier_ids)
        _merge_occurrence(group["occurrences"], _occurrence(event, level))

    projections: list[dict[str, Any]] = []
    for fingerprint_value, group in sorted(groups.items()):
        group_events = group["events"]
        occurrence_values = list(group["occurrences"].values())
        report_events = [event for event in group_events if event["event_type"] == "report_submitted"]
        reproduction_events = [event for event in group_events if event["event_type"] == "reproduction_completed" and event["payload"].get("result") == "reproduced"]
        verified_reproductions = [event for event in reproduction_events if event["payload"].get("independent") is True and _authoritative_verifier(event, trusted_verifier_ids)]
        deterministic_receipts = [event for event in group_events if event["payload"].get("deterministic_verifier") is True and _authoritative_verifier(event, trusted_verifier_ids) and event["payload"].get("receipt_ref")]
        counterexamples = [event for event in group_events if (event["payload"].get("counterexample") is True or event["event_type"] == "issue_retracted") and _authoritative_verifier(event, trusted_verifier_ids)]
        accepted_events = [event for event in group_events if event["payload"].get("accept_for_recommendation") is True and _authoritative_verifier(event, trusted_verifier_ids)]
        verified_resolutions = [event for event in group_events if _resolution_evidence_complete(event, trusted_verifier_ids)]
        accepted = bool(accepted_events and (verified_reproductions or deterministic_receipts) and not counterexamples)
        latest_event = max(group_events, key=lambda event: (event["recorded_at"], event["event_id"]))
        trusted_retractions = [event for event in counterexamples if event["event_type"] == "issue_retracted"]
        trusted_reopens = [event for event in group_events if event["event_type"] == "issue_reopened" and _authoritative_verifier(event, trusted_verifier_ids)]
        if trusted_retractions or counterexamples:
            lifecycle = "RETRACTED" if accepted else "REJECTED"
            level = "retracted"
        elif trusted_reopens:
            lifecycle = "REGRESSED"
            level = "independently_corroborated" if verified_reproductions else "observed_once"
        elif accepted:
            lifecycle = "ACCEPTED"
            level = "accepted"
        elif verified_resolutions:
            lifecycle = "RESOLVED"
            level = "operationally_reproduced"
        elif verified_reproductions:
            lifecycle = "CORROBORATED"
            level = "independently_corroborated"
        elif reproduction_events:
            lifecycle = "REPRODUCING"
            level = "operationally_reproduced"
        elif any(event["event_type"] == "evidence_attached" for event in group_events):
            lifecycle = "OPEN"
            level = "receipt_backed"
        else:
            lifecycle = "CANDIDATE"
            level = "reported_only"
        timestamps = [event["recorded_at"] for event in group_events]
        observed = [occurrence["observed_at"] for occurrence in occurrence_values]
        first = group_events[0]
        reporting_events = report_events or group_events
        distinct_reporting_actors = {event["actor"]["id"] for event in reporting_events}
        distinct_correlations = {occurrence["correlation_id"] for occurrence in occurrence_values}
        diagnostic_receipts: list[str] = []
        diagnostic_runbooks: list[str] = []
        next_action = None
        for event in group_events:
            payload = event["payload"]
            refs = payload.get("receipt_refs")
            if isinstance(refs, list):
                diagnostic_receipts.extend(ref for ref in refs if isinstance(ref, str))
            if isinstance(payload.get("receipt_ref"), str):
                diagnostic_receipts.append(payload["receipt_ref"])
            for evidence in event.get("evidence_refs", []):
                if isinstance(evidence, dict) and isinstance(evidence.get("ref_id"), str):
                    diagnostic_receipts.append(evidence["ref_id"])
            runbooks = payload.get("runbook_refs")
            if isinstance(runbooks, list):
                diagnostic_runbooks.extend(ref for ref in runbooks if isinstance(ref, str))
            if isinstance(payload.get("proposed_next_action"), str) and payload["proposed_next_action"].strip():
                next_action = payload["proposed_next_action"]
        diagnostic_receipts = list(dict.fromkeys(diagnostic_receipts))
        diagnostic_runbooks = list(dict.fromkeys(diagnostic_runbooks))
        projections.append({
            "schema_version": "issue-ledger/issue/v1",
            "issue_id": "ISSUE-" + fingerprint_value.removeprefix("sha256:")[:24],
            "lifecycle": lifecycle,
            "claim": {
                "summary": first["payload"].get("summary", "Operational issue observation"),
                "classification": first["payload"].get("classification", "unknown"),
                "reported_by": first["actor"]["id"],
            },
            "subject": deepcopy(group["subject"]),
            "fingerprint": deepcopy(group["fingerprint"]),
            "evidence_summary": {
                "level": level,
                "report_count": len(report_events),
                "distinct_execution_count": len({item["execution_id"] for item in occurrence_values}),
                "distinct_actor_count": len(distinct_reporting_actors),
                "distinct_correlation_count": len(distinct_correlations),
                "independent_reproduction_count": len(verified_reproductions),
                "deterministic_receipt_count": len(deterministic_receipts),
                "counterexample_count": len(counterexamples),
                "accepted_for_recommendation": accepted,
            },
            "occurrences": sorted(occurrence_values, key=lambda item: (item["observed_at"], item["occurrence_id"])),
            "reproduction": {
                "status": "independently_reproduced" if verified_reproductions else ("reproduced" if reproduction_events else "not_requested"),
                "attempt_count": sum(event["event_type"] in {"reproduction_attempted", "reproduction_completed"} for event in group_events),
                "recipe_ref": next((event["payload"].get("recipe_ref") for event in group_events if event["payload"].get("recipe_ref")), None),
                "last_attempt_ref": next((event["event_id"] for event in reversed(group_events) if event["event_type"] in {"reproduction_attempted", "reproduction_completed"}), None),
            },
            "resolution": {
                "status": "fix_verified" if verified_resolutions else ("fix_proposed" if any(event["event_type"] == "resolution_proposed" for event in group_events) else "unknown"),
                "fix_refs": [event["payload"]["fix_ref"] for event in group_events if event["payload"].get("fix_ref")],
                "verification_refs": [event["event_id"] for event in verified_resolutions],
                "verified_by": next((event["actor"]["id"] for event in reversed(verified_resolutions)), None),
            },
            "diagnostics": {
                "next_action": next_action,
                "receipt_refs": diagnostic_receipts,
                "runbook_refs": diagnostic_runbooks,
            },
            "timestamps": {
                "known_at": min((event.get("known_at") or event["recorded_at"] for event in group_events), key=_parse_stamp),
                "last_updated_at": max(timestamps, key=_parse_stamp),
                "valid_from": min(observed or timestamps, key=_parse_stamp),
                "valid_to": None,
            },
            "provenance": {
                "event_refs": [event["event_id"] for event in group_events],
                "projection_policy_hash": _required_text(policy_hash, "policy_hash"),
                "source_snapshot_ref": None,
            },
        })
    return projections


def export_ire(
    projections: Iterable[dict[str, Any]],
    policy_hash: str = "policy:issue-ledger-v1",
    window_days: int | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Return a defensive, read-only recommendation input projection.

    With ``window_days`` set, only observations inside the explicit valid-time
    window are summarized. The canonical issue projections are never changed.
    If ``as_of`` is omitted, the latest observed timestamp in the supplied
    projections is used so offline replay remains deterministic.
    """
    if window_days is not None and window_days <= 0:
        raise LedgerError("window_days must be positive")
    source = deepcopy(list(projections))
    window_from: datetime | None = None
    window_to: datetime | None = None
    if window_days is not None:
        observed_times = [
            _parse_stamp(occurrence["observed_at"])
            for issue in source
            for occurrence in issue.get("occurrences", [])
        ]
        if as_of is None:
            if not observed_times:
                return {"schema_version": "ire/issue-ledger/v1", "policy_hash": _required_text(policy_hash, "policy_hash"), "window": None, "issues": []}
            window_to = max(observed_times)
        else:
            window_to = _parse_stamp(_stamp(as_of, "as_of"))
        window_from = window_to - timedelta(days=window_days)
    selected = []
    for issue in source:
        summary = issue["evidence_summary"]
        if not summary["accepted_for_recommendation"]:
            continue
        occurrences = issue.get("occurrences", [])
        if window_from is not None and window_to is not None:
            occurrences = [
                occurrence for occurrence in occurrences
                if window_from <= _parse_stamp(occurrence["observed_at"]) <= window_to
            ]
            if not occurrences:
                continue
        outcomes = [occurrence["outcome"] for occurrence in occurrences]
        selected.append({
            "issue_id": issue["issue_id"],
            "subject": issue["subject"],
            "fingerprint": issue["fingerprint"],
            "evidence_level": summary["level"],
            "report_count": summary["report_count"],
            "distinct_execution_count": summary["distinct_execution_count"],
            "distinct_actor_count": summary["distinct_actor_count"],
            "distinct_correlation_count": summary["distinct_correlation_count"],
            "independent_reproduction_count": summary["independent_reproduction_count"],
            "lifecycle": issue["lifecycle"],
            "diagnostics": deepcopy(issue.get("diagnostics", {"next_action": None, "receipt_refs": [], "runbook_refs": []})),
            "reproduction": deepcopy(issue.get("reproduction", {})),
            "resolution": deepcopy(issue.get("resolution", {})),
            "valid_from": issue["timestamps"]["valid_from"],
            "known_at": issue["timestamps"]["known_at"],
            "source_event_refs": issue["provenance"]["event_refs"],
            "policy_hash": issue["provenance"]["projection_policy_hash"],
            "window": {
                "from": window_from.isoformat().replace("+00:00", "Z") if window_from is not None else None,
                "to": window_to.isoformat().replace("+00:00", "Z") if window_to is not None else None,
                "occurrence_count": len(occurrences),
                "failure_count": sum(outcome == "failure" for outcome in outcomes),
                "timeout_count": sum(outcome == "timeout" for outcome in outcomes),
                "partial_count": sum(outcome == "partial" for outcome in outcomes),
                "success_count": sum(outcome == "success" for outcome in outcomes),
                "unknown_count": sum(outcome == "unknown" for outcome in outcomes),
                "distinct_execution_count": len({occurrence["execution_id"] for occurrence in occurrences}),
                "distinct_correlation_count": len({occurrence["correlation_id"] for occurrence in occurrences}),
            },
        })
    return {
        "schema_version": "ire/issue-ledger/v1",
        "policy_hash": _required_text(policy_hash, "policy_hash"),
        "window": {
            "from": window_from.isoformat().replace("+00:00", "Z") if window_from is not None else None,
            "to": window_to.isoformat().replace("+00:00", "Z") if window_to is not None else None,
            "days": window_days,
        },
        "issues": selected,
    }


def _load_events(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LedgerError("cannot read UTF-8 JSON-lines event file") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project an Issue Ledger JSON-lines file locally.")
    parser.add_argument("events", type=Path, help="append-only JSON-lines event file")
    parser.add_argument("--ire", action="store_true", help="emit only accepted read-only IRE inputs")
    parser.add_argument("--policy-hash", default="policy:issue-ledger-v1")
    args = parser.parse_args(argv)
    try:
        projections = reduce_events(_load_events(args.events), args.policy_hash)
        result: Any = export_ire(projections, args.policy_hash) if args.ire else projections
    except (LedgerError, OSError) as exc:
        print(f"issue-ledger: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
