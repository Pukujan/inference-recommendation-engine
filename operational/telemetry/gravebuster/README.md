# Telemetry host: deploy and operate

Target layout: `/srv/agent-telemetry` (owned by the service user, passwordless sudo only needed for
systemd units). Data (`data/`), secrets (`.env`, `collector/langfuse-auth.env`) and the pipeline's
`venv/`, `state/`, `tmp/` exist only on the host and are never overwritten by a deploy.

## First install

```bash
sudo mkdir -p /srv/agent-telemetry && sudo chown "$USER": /srv/agent-telemetry
git clone https://github.com/Pukujan/inference-recommendation-engine /srv/agent-telemetry/src
cd /srv/agent-telemetry
src/operational/telemetry/gravebuster/setup-env.sh          # .env + langfuse-auth.env from the examples
$EDITOR .env collector/langfuse-auth.env                    # NEXTAUTH_URL, Langfuse OTLP basic auth
python3 -m venv pipeline/venv
src/operational/telemetry/gravebuster/deploy.sh --no-fetch   # copies code, installs systemd units
pipeline/venv/bin/pip install -r pipeline/requirements.txt
docker compose up -d
sudo systemctl enable --now agent-telemetry-pipeline.timer
```

## Update (normal path)

Merge to `main` via PR, then on the host:

```bash
/srv/agent-telemetry/src/operational/telemetry/gravebuster/deploy.sh --run-pipeline
```

`deploy.sh` fetches `origin/main`, checks it out detached in `src/`, waits for the pipeline lock, and
rsyncs code (checksum-based, **no `--delete`**, so host-only modules added by other workers survive).
Every replaced file is kept in `deploy-backups/<UTC stamp>/`. It then:

- installs changed systemd units and runs `daemon-reload`;
- runs `pip install -r requirements.txt` when it changed;
- validates a changed collector config with the pinned collector image, then restarts the collector;
- validates a changed `docker-compose.yml` (recreate containers only with `--apply-compose`);
- writes `DEPLOYED` (`sha=`, `ref=`, time). The pipeline records `ire@<sha>` in each snapshot's inputs.

Options: `--ref <ref>` (e.g. a PR branch for a trial), `--dry-run`, `--no-fetch`, `--no-restart`.

Rollback: `deploy.sh --ref <previous sha>`, or copy files back from `deploy-backups/<stamp>/`.

## Verify

```bash
systemctl list-timers agent-telemetry-pipeline.timer
journalctl -u agent-telemetry-pipeline.service -n 20 --no-pager   # "[run] ok <id> snapshot=..."
pipeline/venv/bin/python bin/at-duck.py etl 3
pipeline/venv/bin/python bin/at-duck.py tables
```

InferHub market + billing collector (IRE #46): `pipeline/ihub/README.md`. Its key lives in
`secrets/inferhub.env` (0600, host only); enable its timers once with
`sudo systemctl enable --now inferhub-collect-fast.timer inferhub-collect-logs.timer inferhub-snapshot-publish.timer`.

See `pipeline/README.md` for the data layers and `HOST-NOTES.md` for how the host was set up.
