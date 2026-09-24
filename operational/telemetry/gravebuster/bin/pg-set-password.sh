#!/usr/bin/env bash
# The restored cortex-langfuse postgres volume was initialised with an unknown (non-default) password
# and its original compose/.env no longer exists. This sets a fresh random password for role 'postgres'
# over the in-container unix socket (trust auth) and stores it in .env. Prints nothing secret.
set -euo pipefail
cd /srv/agent-telemetry
np=$(openssl rand -hex 24)
docker compose exec -T postgres psql -U postgres -q -v ON_ERROR_STOP=1 -c "ALTER ROLE postgres WITH PASSWORD '$np';"
cp -p .env .env.bak-20260924
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$np/" .env
echo "postgres password rotated; .env updated (backup .env.bak-20260924)"
