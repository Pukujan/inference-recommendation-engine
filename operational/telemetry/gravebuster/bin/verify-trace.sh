#!/usr/bin/env bash
# Usage: verify-trace.sh <trace_id_hex>   -- checks the Parquet lake + live collector files, Phoenix and Langfuse for a trace. Prints no secrets.
set -uo pipefail
tid="${1:?trace id}"; tid=$(printf %s "$tid" | tr -cd 'a-f0-9')
cd /srv/agent-telemetry
echo "== Lake + live collector files (SQLite loader retired 2026-09-24; see pipeline/README.md)"
/srv/agent-telemetry/pipeline/venv/bin/python /srv/agent-telemetry/bin/at-duck.py trace "$tid" --live
echo "== Phoenix"
curl -s "http://127.0.0.1:6006/v1/projects/default/spans?limit=200" | python3 -c "
import sys,json; t=sys.argv[1]
try: d=json.load(sys.stdin)
except Exception as e: print('phoenix parse error', e); sys.exit()
sp=[s for s in d.get('data',[]) if (s.get('context') or {}).get('trace_id')==t]
print('phoenix_spans=%d' % len(sp)); [print(s.get('name'), s.get('status_code'), {k:v for k,v in (s.get('attributes') or {}).items() if k in ('note','secret_probe','test')}) for s in sp]" "$tid"
echo "== Langfuse"
set -a; . collector/langfuse-auth.env; set +a
curl -s -H "Authorization: Basic $LANGFUSE_OTEL_BASIC_AUTH" "http://127.0.0.1:3000/api/public/traces/$tid" | python3 -c "
import sys,json
try: d=json.load(sys.stdin)
except Exception as e: print('langfuse parse error', e); sys.exit()
if 'id' in d: print('langfuse_trace=FOUND', d.get('name'), 'observations=%d' % len(d.get('observations',[])), 'metadata_keys=', sorted((d.get('metadata') or {}).keys())[:15])
else: print('langfuse_trace=NOT_FOUND', {k:d.get(k) for k in ('message','error')})"
echo "== collector exporter errors (last 5 min)"
docker compose logs --since 5m otel-collector 2>&1 | grep -iE 'error|fail|drop' | tail -5
