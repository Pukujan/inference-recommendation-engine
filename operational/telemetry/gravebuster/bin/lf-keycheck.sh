#!/usr/bin/env bash
# Reads a Langfuse public key on stdin; prints only the matching project id (never the key).
set -euo pipefail
read -r pk; pk=$(printf %s "$pk" | tr -cd "A-Za-z0-9-")
[[ "$pk" =~ ^pk-lf-[A-Za-z0-9-]+$ ]] || { echo "badformat"; exit 1; }
cd /srv/agent-telemetry
r=$(docker compose exec -T postgres psql -U postgres -At -c "select project_id from api_keys where public_key = '$pk'")
echo "match_project=${r:-NONE}"
