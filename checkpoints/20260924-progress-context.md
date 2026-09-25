# IRE checkpoint context pack — 2026-09-24 (EDT)

This pack preserves resumable context for stopped/in-progress workstreams. It contains no credentials, tokens, or holdout contents. Repo: `Pukujan/inference-recommendation-engine`.

## 1. JEV judge package into IRE recommendation/ledger

- **Goal:** Carry the durable JEV judge research into IRE as an agent-facing recommendation while keeping deterministic checks as the mandatory hard gate.
- **Done so far:** A medium-confidence recommendation records the TypeSafe `typesafe/jev-1.13` decision contract at `POST https://openrouter.ai/api/alpha/decisions`, optional use for hidden holdouts and meaning-level metamorphic checks, abstain/uncertain/provider/parse errors as not-pass, and staff never seeing holdout contents. The recommendation explicitly marks live holdout execution, catalogue availability, and calibration as unverified.
- **Exact IRE paths changed/created (this checkpoint):**
  - `operational/recommendations/jev-judge-agent-tests.v1.json` (uncommitted before this checkpoint; now committed here)
  - `schemas/recommendations/v1.agent-recommendation.schema.json` (uncommitted before this checkpoint; now committed here)
- **Source/evidence paths (outside IRE, not copied):**
  - `D:/claude/_workspace/pcm-astra-owner/guides/JEV-JUDGE-TESTS.md`
  - `D:/claude/_workspace/pcm-astra-owner/guides/README.md`
  - `D:/claude/_workspace/pcm-astra-owner/holdout/README.md`
  - `D:/claude/eval-lab/docs/JEV_RESEARCH_AND_USAGE_AUDIT.md`
  - `D:/claude/eval-lab/docs/JEV_EVAL_LAB_INTEGRATION_CONTRACT.md`
  - `D:/claude/eval-lab/docs/TASK-0010-OPENROUTER-JEV.md`
  - `D:/claude/eval-lab/docs/INDEPENDENT_JEV_BENCHMARK_AUDIT.md`
- **Ledger IDs:** `ILE-6bf5001780c0acbef3a8d952`; correlation `pcm-astra-jev-judge-tests-20260924`; follow-up event is null.
- **Related issues:** #40, #29; continue coordination with #41 and #46 where recommendation/telemetry integration is needed.
- **Open questions:** Is `typesafe/jev-1.13` available through the project catalogue BYOK endpoint? Can `run-holdout.ps1` execute live? How does confidence calibrate against PCM gold packs? Keep deterministic gates mandatory regardless.
- **Next commands:** From the IRE root, validate both JSON documents and run `git diff --check`; then follow the JEV guide for the live holdout/calibration run. Do not expose holdout contents or credentials.
- **Receipts/evidence:** The cited guide files and ledger event above; recommendation provenance includes the PCM durable write and a tiny OpenRouter smoke. No secret or key material is included.

## 2. All-CLI fullpriv sweep (Claude/Pi/DeepSeek/Grok Build/Codex)

- **Goal:** Make the staff launcher/harness behavior resumable and consistent across the CLI family, with the Codex full-access path and receipt behavior documented; do not begin new feature work.
- **Done so far:** IRE already has remote checkpoint branch `codex/checkpoint/staff-fullpriv-all-clis`; latest observed commit is `775beb8` (`fix(harness): default staff Codex launcher to danger-full-access`) at 2026-09-24 21:18 EDT. Existing checkpoint history includes the full-access Codex launcher, stdin prompt delivery, PATH invocation, and exit/receipt hardening. Local workspace notes/scripts were found but were not copied into IRE because they live outside the IRE repo.
- **Exact paths to resume/inspect:**
  - `D:/claude/_workspace/STAFF-FULLPRIV-NOTE-20260924.md`
  - `D:/claude/_workspace/run_codex_harness.fullpriv.ps1`
  - IRE `run_codex_harness.ps1`
  - IRE `operational/telemetry/pc/run-codex-harness.ps1`
  - IRE `operational/telemetry/pc/launch-astra.ps1`
  - IRE `operational/telemetry/pc/resume-astra.ps1`
- **Related issues:** #41, #46 (and the existing checkpoint's #54/#55/#56/#57 history).
- **Open questions:** Whether all five CLIs have equivalent fullpriv launch notes/receipts, and whether any local launcher edits remain outside the already-pushed checkpoint. Do not print or copy environment values.
- **Next commands:** `git log origin/codex/checkpoint/staff-fullpriv-all-clis --oneline -10`; inspect the two workspace notes/scripts; compare each launcher against its branch; run only existing harness checks. Push any genuinely new local commit to the existing branch or a new checkpoint branch; do not merge.
- **Receipts/evidence:** `D:/claude/_workspace/STAFF-FULLPRIV-NOTE-20260924.md`; existing IRE `operational/telemetry/pc` receipt/harness paths; remote branch above.

## 3. Sandbox/OTel leftovers

- **Goal:** Preserve stopped OTel/sandbox telemetry work and identify what still needs a proper owner checkpoint.
- **Done so far:** IRE has remote OTel checkpoint branches `codex/checkpoint/codex-harness-otel` and `codex/checkpoint/codex-harness-otel-exitfix`; the latter contains the IRE OTel/harness and telemetry tree. The separate Hades checkout is dirty with OTel-adjacent edits and was intentionally not committed to IRE.
- **Exact paths changed/created outside IRE:**
  - `D:/claude/hades-v2/.gitignore`
  - `D:/claude/hades-v2/scripts/research/run_inferhub_codex.ps1`
  - `D:/claude/hades-v2/scripts/research/run_inferhub_codex.ps1.bak-20260924-pre-otel`
  - `D:/claude/hades-v2/scripts/research/run_inferhub_codex.ps1.bak-otel-20260924`
  - `D:/claude/_workspace/telemetry/astra-telemetry.ps1`
  - `D:/claude/_workspace/telemetry/astra_otel.py`
  - `D:/claude/_workspace/telemetry/_patch_hades_otel.py`
  - `D:/claude/_workspace/telemetry/astra-telemetry.ps1.bak-20260924-inferhub`
  - `D:/claude/_workspace/telemetry/astra_otel.py.bak-20260924-redact`
  - `D:/claude/_workspace/telemetry/astra_otel.py.bak-20260924-p4`
  - `D:/claude/_workspace/telemetry/astra-telemetry.ps1.bak-20260924-p4`
- **Related issues:** #46 (telemetry/operational work); inspect #40/#41 for cross-links.
- **Open questions:** Which OTel edits are intended for IRE versus Hades, and which sandbox receipts are authoritative? Do not move Hades files into IRE without an explicit ownership decision.
- **Next commands:** Inspect `git -C D:/claude/hades-v2 diff -- .gitignore scripts/research/run_inferhub_codex.ps1`; compare with `origin/codex/checkpoint/codex-harness-otel-exitfix`; run existing telemetry tests only after ownership is resolved.
- **Receipts/evidence:** `D:/claude/_workspace/telemetry/` and `D:/claude/_workspace/telemetry-backup-20260924.txt` (if present); IRE remote OTel checkpoint branches.

## Other bots must checkpoint (not committed to IRE)

- `D:/claude/hades-v2` — dirty and behind origin: `.gitignore`, `scripts/research/run_inferhub_codex.ps1`, and two dated backups. These edits were not clearly IRE-owned, so they remain unpushed here.
- `D:/claude/projects/project-continuity-modules` — dirty branch `task/PCM-0026-post-push-plan` behind origin/main with `HANDOFF.md`, `checkpoints/CURRENT.md`, `docs/research/PCM-0026-issue-67-retry-safe-receipts.md`, and `tasks/TASK-PCM-0026-github-receipts.md`; unrelated to IRE, so not copied.
- `D:/claude/_workspace/telemetry` — evidence/staging directory, not a Git checkout; owner should checkpoint the relevant files.

## Checkpoint boundary / safety

- This pack and the two recommendation files are the only new IRE work in this branch.
- Local `main` remained untouched; no force-push was performed.
- PR #39 was not opened, modified, or merged. No merge or CI wait is requested.
- Skip `.ire` receipts, secrets, sqlite, holdout contents, and unrelated repository changes.
