#!/usr/bin/env bash
# Deploy the agent-telemetry source from a git checkout of this repository to /srv/agent-telemetry.
#
#   deploy.sh [--ref <git ref>] [--no-fetch] [--dry-run] [--run-pipeline] [--no-restart]
#
# Default flow (run on the telemetry host as the service user, which needs passwordless sudo for systemd):
#   1. git fetch + detached checkout of --ref (default origin/main) in $AT_SRC (default /srv/agent-telemetry/src)
#   2. waits for the pipeline lock, then rsyncs code into $AT_ROOT; replaced files are kept under
#      $AT_ROOT/deploy-backups/<stamp>/.  Never touches: data/, .env, collector/langfuse-auth.env,
#      pipeline/{venv,state,tmp,.git}, *.bak-*, or any file that is not in the repository (no --delete),
#      so modules added on the host by other workers survive.
#   3. installs changed systemd units (daemon-reload; timer stays enabled), pip-installs a changed
#      requirements.txt into pipeline/venv, validates + restarts the collector if its config changed,
#      validates docker-compose.yml (containers are only recreated with --apply-compose).
#   4. writes $AT_ROOT/DEPLOYED (sha, ref, time) which the pipeline records in every snapshot.
set -euo pipefail

AT_ROOT="${AT_ROOT:-/srv/agent-telemetry}"
AT_SRC="${AT_SRC:-$AT_ROOT/src}"
REF="origin/main"
FETCH=1
DRY=0
RUN_PIPELINE=0
RESTART=1
APPLY_COMPOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --no-fetch) FETCH=0; shift ;;
    --dry-run) DRY=1; shift ;;
    --run-pipeline) RUN_PIPELINE=1; shift ;;
    --no-restart) RESTART=0; shift ;;
    --apply-compose) APPLY_COMPOSE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

log() { printf '[deploy] %s\n' "$*"; }

if [ "$FETCH" = 1 ]; then
  git -C "$AT_SRC" fetch --quiet origin
fi
git -C "$AT_SRC" checkout --quiet --detach "$REF"
SHA="$(git -C "$AT_SRC" rev-parse HEAD)"
PKG="$AT_SRC/operational/telemetry/gravebuster"
[ -f "$PKG/deploy.sh" ] || { echo "package not found at $PKG" >&2; exit 1; }
log "source $SHA ($REF)"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$AT_ROOT/deploy-backups/$STAMP"
RSYNC=(rsync -rlt --checksum --itemize-changes --out-format='%i %n' --backup --backup-dir="$BACKUP"
       --exclude='__pycache__/' --exclude='*.pyc' --exclude='*.bak-*' --exclude='.env' --exclude='*.env')
[ "$DRY" = 1 ] && RSYNC+=(--dry-run)

CHANGES="$(mktemp)"
trap 'rm -f "$CHANGES"' EXIT
sync_path() {  # sync_path <src> <dest> [extra rsync args...]; aborts on rsync failure
  local src="$1" dest="$2" out; shift 2
  mkdir -p "$(dirname "$dest")"
  out="$("${RSYNC[@]}" "$@" "$src" "$dest")"
  [ -n "$out" ] && printf '%s\n' "$out" | sed "s#^#$(basename "$dest") #" | tee -a "$CHANGES"
  return 0
}

mkdir -p "$AT_ROOT/pipeline/state" "$AT_ROOT/bin" "$AT_ROOT/collector" "$AT_ROOT/loader"
exec 9>"$AT_ROOT/pipeline/state/pipeline.lock"
log "waiting for pipeline lock"
flock -w 900 9 || { echo "pipeline lock busy for 15 min; aborting" >&2; exit 1; }

sync_path "$PKG/pipeline/" "$AT_ROOT/pipeline/" \
  --exclude='/venv/' --exclude='/state/' --exclude='/tmp/' --exclude='/.git/' --exclude='/.gitignore'
sync_path "$PKG/bin/" "$AT_ROOT/bin/"
sync_path "$PKG/loader/" "$AT_ROOT/loader/"
sync_path "$PKG/collector/config.yaml" "$AT_ROOT/collector/config.yaml"
sync_path "$PKG/collector/langfuse-auth.env.example" "$AT_ROOT/collector/langfuse-auth.env.example"
sync_path "$PKG/docker-compose.yml" "$AT_ROOT/docker-compose.yml"
for f in env.example setup-env.sh deploy.sh README.md HOST-NOTES.md; do
  sync_path "$PKG/$f" "$AT_ROOT/deploy/$f"
done
flock -u 9

touched() { grep -qE "$1" "$CHANGES"; }

if [ "$DRY" = 1 ]; then log "dry run: nothing applied"; exit 0; fi

# systemd units
UNITS_CHANGED=0
for u in "$PKG"/systemd/*.service "$PKG"/systemd/*.timer; do
  n="$(basename "$u")"
  if ! cmp -s "$u" "/etc/systemd/system/$n"; then
    if [ -f "/etc/systemd/system/$n" ]; then mkdir -p "$BACKUP"; cp -p "/etc/systemd/system/$n" "$BACKUP/$n.etc"; fi
    sudo install -m 644 "$u" "/etc/systemd/system/$n"
    log "installed unit $n"
    UNITS_CHANGED=1
  fi
done
[ "$UNITS_CHANGED" = 1 ] && sudo systemctl daemon-reload

# python deps
if touched '^pipeline >f[^ ]* requirements\.txt$'; then
  log "requirements.txt changed: pip install"
  "$AT_ROOT/pipeline/venv/bin/pip" install --quiet -r "$AT_ROOT/pipeline/requirements.txt"
fi

cd "$AT_ROOT"
# collector config
if touched '^config\.yaml >f'; then
  IMG="$(docker compose config --images 2>/dev/null | grep -m1 opentelemetry-collector || echo otel/opentelemetry-collector-contrib:0.155.0)"
  log "collector config changed: validating with $IMG"
  docker run --rm -v "$AT_ROOT/collector/config.yaml:/etc/otelcol-contrib/config.yaml:ro" \
    --env-file "$AT_ROOT/collector/langfuse-auth.env" "$IMG" validate --config=/etc/otelcol-contrib/config.yaml
  if [ "$RESTART" = 1 ]; then docker compose restart otel-collector; log "collector restarted"; fi
fi
if touched '^docker-compose\.yml >f'; then
  docker compose config -q && log "docker-compose.yml valid"
  if [ "$APPLY_COMPOSE" = 1 ]; then docker compose up -d; else log "compose changed: run 'docker compose up -d' (or --apply-compose) to apply"; fi
fi

printf 'sha=%s\nref=%s\ndeployed_at=%s\nbackup=%s\n' "$SHA" "$REF" "$(date -u +%FT%TZ)" "$BACKUP" > "$AT_ROOT/DEPLOYED"
log "wrote $AT_ROOT/DEPLOYED; $(grep -c . "$CHANGES" || true) file changes; backups in $BACKUP (if any)"

if [ "$RUN_PIPELINE" = 1 ]; then
  log "starting agent-telemetry-pipeline.service"
  sudo systemctl start agent-telemetry-pipeline.service
  systemctl --no-pager --lines=5 status agent-telemetry-pipeline.service | tail -n 8 || true
fi
systemctl --no-pager list-timers agent-telemetry-pipeline.timer | head -n 3 || true
