#!/usr/bin/env bash
# Creates /srv/agent-telemetry/.env from env.example once (chmod 600), filling every CHANGE_ME with a
# random value. Never prints values. For a host that reuses existing Langfuse volumes, edit .env by hand
# afterwards so SALT / ENCRYPTION_KEY / DB credentials match those volumes.
set -euo pipefail
AT_ROOT="${AT_ROOT:-/srv/agent-telemetry}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$AT_ROOT"
if [ ! -f .env ]; then
  umask 077
  while IFS= read -r line; do
    case "$line" in
      *=CHANGE_ME_64_HEX) printf '%s=%s\n' "${line%%=*}" "$(openssl rand -hex 32)" ;;
      *=CHANGE_ME) printf '%s=%s\n' "${line%%=*}" "$(openssl rand -hex 24)" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done < "$HERE/env.example" > .env
  echo ".env created (edit NEXTAUTH_URL)"
else
  echo ".env exists, untouched"
fi
mkdir -p collector
[ -f collector/langfuse-auth.env ] || { umask 077; cp "$HERE/collector/langfuse-auth.env.example" collector/langfuse-auth.env; echo "placeholder collector/langfuse-auth.env created"; }
chmod 600 .env collector/langfuse-auth.env
