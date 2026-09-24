-- Schema only. Rows are written by atpipe/detectors.py after dbt (catalog: detectors/catalog.json,
-- IRE #41 P4). Keep these columns in sync with detectors.INCIDENT_COLUMNS.
SELECT NULL::VARCHAR AS incident_id, NULL::VARCHAR AS run_id, NULL::VARCHAR AS hop_id,
       NULL::VARCHAR AS incident_type, NULL::VARCHAR AS mast_mode, NULL::VARCHAR AS fingerprint_v,
       NULL::VARCHAR AS fingerprint, NULL::VARCHAR AS severity, NULL::BOOLEAN AS justified,
       NULL::TIMESTAMP AS detected_at_utc, NULL::VARCHAR AS detector_version,
       NULL::VARCHAR AS ledger_event_id, NULL::VARCHAR[] AS evidence_refs, {{ snap() }},
       NULL::VARCHAR AS catalog_version, NULL::VARCHAR AS catalog_sha256, NULL::VARCHAR AS root_run_id,
       NULL::VARCHAR AS parent_run_id, NULL::VARCHAR AS run_role, NULL::VARCHAR AS harness,
       NULL::VARCHAR AS route_id, NULL::VARCHAR AS task_id, NULL::VARCHAR AS receipt_stamp,
       NULL::TIMESTAMP AS occurred_at_utc, NULL::VARCHAR AS summary, NULL::VARCHAR AS metrics_json,
       NULL::VARCHAR AS proposed_fix_id, NULL::VARCHAR AS proposed_fix_status
LIMIT 0
