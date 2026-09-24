-- STUB (P4 scope: incident detectors + signature catalog). Schema per design doc 5.3; no rows yet.
SELECT NULL::VARCHAR AS incident_id, NULL::VARCHAR AS run_id, NULL::VARCHAR AS hop_id,
       NULL::VARCHAR AS incident_type, NULL::VARCHAR AS mast_mode, NULL::VARCHAR AS fingerprint_v,
       NULL::VARCHAR AS fingerprint, NULL::VARCHAR AS severity, NULL::BOOLEAN AS justified,
       NULL::TIMESTAMP AS detected_at_utc, NULL::VARCHAR AS detector_version,
       NULL::VARCHAR AS ledger_event_id, NULL::VARCHAR[] AS evidence_refs, {{ snap() }}
LIMIT 0
