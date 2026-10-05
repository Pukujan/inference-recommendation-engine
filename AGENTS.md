> These instructions are for Alex's own maintainer agents working inside this repo. If you are an outside agent that wants today's model picks or wants to call a picked route, don't follow this file. Use [docs/AGENT-QUICKSTART.md](docs/AGENT-QUICKSTART.md) and [llms.txt](llms.txt) instead.

# Human-facing work

Pinned helper: content-generation-modules **0.5.12** @ `6831f91e165b62d719c05eb492f7375fa932b560` (all eight modules). Before human-facing README, PR, issue, commit, docs, or HTML work, load the skills named below and run `python scripts/verify_hsw_applied.py --root <cgm-checkout>` from that pin when you change writing or inject text.

```
CGM ALWAYS-ON WRITING RULE (every adopter that pins this helper)

Before you write ANY human-facing output — including HTML reports, compare HTML/UIs, appendable HTML, PR/issue/commit prose, docs, posts, papers, or other readable artifacts — you MUST load and apply modules/human-sounding-writing/SKILL.md (hsw).

This rule is always on. Opt-in is forbidden. Do not wait for a per-task, per-report, or per-HTML flag.

Exceptions (only these):
- README.md / product entry pages → load modules/writing-direction/SKILL.md instead
- Human-facing research plans, architecture explanations, and evidence briefs → load modules/writing-direction/SKILL.md (reader-facing explanation, not a manuscript or data writeup)
- Generated artifact filenames / asset-manifest paths / media basenames / filename legends → load modules/human-output-naming/SKILL.md (hon) for basenames; visible prose inside HTML still uses hsw

HTML reports, compare HTML, and compare UIs have NO skip path. An exception reason is not allowed for those surfaces.

If you cannot load the skill file from the pinned CGM checkout, stop and report that — do not draft jargon-heavy or tool-dump HTML instead.

Filenames: use scripts/human_filename (speakable basenames; optional safe_twin) and keep a per-feature legend. Hash may stay a separate manifest field.
```

Before changing the public story, read the pinned adapter in `.content-system/` and use the exact helper commit recorded there.

For README, product, visual, or marketing work, load the helper's README playbook, image guide, and README contract. Keep claims tied to repository evidence, label experiments and plans, and never add credentials or private source records.

## Pinned stack (release train 2026-10-01)

IRE adopts the ACS multi-agent hot-load stack. `stack-manifest.json` pins the train, and CI `test` runs every validator below.

- **PCM** `4e2385474b4af9249ca009cbdcb38c4498932475` (CLI 0.6.0, protocol 0.1.0-draft): continuity files, checkpoints, PR-only changes to `main`, required CI gates. `.continuity/config.json` uses `single-checkout` mode, matching the canonical-checkout rule below.
- **CGM** `6831f91e165b62d719c05eb492f7375fa932b560` (0.5.12, all eight modules). README and product entry use `writing-direction`; PRs, issues, docs and commits use `human-sounding-writing`; generated file names use `human-output-naming`.
- **OIO** 0.1.0 (`2dace20b08deccfad91de8984e5670f4bf680ddb`): the observational-issue form and its triage workflow.
- **ACS** `multi-agent-hotload` 0.1.0 (`38f8f52e8d210db3ce258bf911ebb560c6e0fe4c`): join-order roles, a boss lease in minutes, the claim file in `.coord/`, and an agent-less watchdog.

To check the install locally, check out those commits and run:

```bash
continuity validate --root .
python <cgm>/scripts/validate_content_system.py --root <cgm> --adapter .content-system --project-root .
python <oio>/.github/scripts/oio_installer.py --target . --check
python <train>/scripts/check_manifest.py --manifest stack-manifest.json --train <train>/stack-releases.json
python <acs>/modules/coordination/multi-agent-hotload/v0.1.0/scripts/hotload_check.py --root <acs>/modules/coordination/multi-agent-hotload/v0.1.0 --assignment .coord/assignment.json --cgm-root <cgm> --adopter-root .
```

## Roles and lease

- Join or continue order fills roles. The first live continuer is decision boss, then coder1, coder2 and so on.
- The decision-boss seat is a lease (default 30 minutes, range 15 to 120). Re-read `.coord/boss_claim.json` on every wake. A returning old boss joins the end of the queue.
- The watchdog only checks liveness. It is not failover and does not appoint a boss.

## Dev root hygiene

The dev root (`D:\development` on Windows, `~/development` elsewhere, or wherever `ACS_DEV_ROOT` points) holds one main checkout per repo and nothing else.

- Don't create git worktrees, dependency or sibling clones, scratch folders, or caches in the dev root.
- Put them in the ACS cache instead: `%LOCALAPPDATA%\acs\{deps,scratch,worktrees}` on Windows, `~/.cache/acs/{deps,scratch,worktrees}` on macOS and Linux. `ACS_CACHE_DIR` moves the cache.
- Before you finish, push any real work to a branch and remove the worktrees and scratch folders you made. Never delete a checkout that has uncommitted, unpushed, or stashed work just to tidy up.
- To check, run the pinned ACS script: `python <acs>/modules/coordination/multi-agent-hotload/v0.1.0/scripts/dev_root_check.py --dev-root <dev root>`. It prints JSON and exits non-zero when it finds anything other than main checkouts. `--clean` shows a fix and only acts with `--yes`.

Paste this at session boot along with the CGM `system_block` (it comes from the ACS hotloader's `PROMPT_INJECT.md` at `38f8f52`):

```
Dev root hygiene (ACS): the dev root (ACS_DEV_ROOT; default D:\development on Windows, ~/development elsewhere) holds exactly one main checkout per repo. Never create git worktrees, dependency or sibling clones, scratch folders, or caches there. Put them under the ACS cache instead: %LOCALAPPDATA%\acs\{deps,scratch,worktrees} on Windows, ~/.cache/acs/{deps,scratch,worktrees} on macOS/Linux (ACS_CACHE_DIR overrides). Check with scripts/dev_root_check.py.
```

## Canonical checkout and checkpoint delivery

- Work in the user's existing canonical checkout. Do not create a worktree or a
  second clone for a task unless the user explicitly asks for isolation. A
  short-lived checkpoint branch is fine, but create and use it in this same
  folder.
- If a task explicitly authorizes an isolated worktree, record its exact path
  and branch in the checkpoint; after its changes merge and the worktree is
  verified clean, remove only that task-created worktree. Never prune or
  delete unrelated worktrees.
- Inspect the working tree before editing. Preserve user changes and never
  include unrelated work in a checkpoint.
- A checkpoint is one independently verified increment. Before publishing,
  update the relevant task/checkpoint record, run the repository gates, and
  deliver it with `node scripts/checkpoint.mjs` using an explicit `--path` for
  each intended file or directory. Never use `git add -A` for checkpoint
  delivery.
- The publisher commits on `codex/checkpoint/<name>` in this checkout, pushes
  it to `origin`, opens or updates a PR to `main`, and requests GitHub
  auto-merge. It returns while required CI runs. Do not mark a checkpoint
  complete, remove its branch, or start dependent implementation until its PR
  is confirmed merged. Use `node scripts/finalize-checkpoint.mjs --pr <number>`
  to verify the merge, synchronize this checkout's `main`, and clean up the
  merged local branch. Resume a failed checkpoint on its existing branch after
  fixing the cause.
- Do not bypass required checks or push directly to `main`. The helper refuses
  pre-staged or leftover unstaged/untracked work, sensitive paths, and
  credential-like content. Never stage `.ire/`, credentials, or private source
  records.
- Use pnpm for Node.js and uv for Python. Keep `pnpm-lock.yaml` and `uv.lock`
  current and use frozen/locked installs in CI.
- Required local gates are `pnpm test`, `pnpm check:public`,
  `pnpm test:operational`, `pnpm check:static`, and `pnpm pack --dry-run`; CI
  runs the same gates. `pnpm check:static` runs ESLint for JavaScript, Ruff
  lint and format checks for Python, and mypy for `operational/scripts`.
- For user-authorized long-running model-agent tasks, run `.\run_codex_harness.ps1` with the selected model and task prompt. It launches Codex with `--dangerously-bypass-approvals-and-sandbox --sandbox danger-full-access` and `-c approval_policy=never` (no sandbox approval prompts), streams JSONL progress, and accepts an optional `-WrapperScript` path for operational launchers. Do not silently switch these runs to read-only or add permission prompts after authorization. If Codex or a managed policy rejects the requested mode, stop and report that concrete error. Preserve the configured model-provider credential source and existing receipt procedures. For traced BYOK provider runs, pass `-WrapperScript` pointing at `pc/run-codex-harness.ps1` under the operational tree (see the BYOK API setup doc under `docs/`).
- Before configuring, launching, or spawning any coding CLI through a BYOK inference endpoint, read `docs/INFERHUB-API-SETUP.md`.
- For CKFF as a provider candidate (chicken-token pricing, Astra launcher failover), read `docs/CKFF-PROVIDER.md` and `providers/ckff/route-candidates.v1.json`. Apply its provider-specific URL/auth setup, explicit model routing at every CLI/subagent hop, full authorized tool access, stream/liveness handling, retry/resume rules, and evidence receipt requirements. This applies to Codex CLI, Claude Code, Pi, and compatible coding CLIs; do not assume a child process inherited the parent CLI's provider, key, model, or permissions.
- When Astra owns the plan and Kilo staff execute it, follow the Kilo staff section of `docs/INFERHUB-API-SETUP.md`: in-session subagents only, `background: true` unless the next step depends on the child, then record the result and close the worker. Do not use Agent Manager sessions as staff.
- GitHub Issues, pull requests, and commit history own project plans, issue
  status, and code changes. Private operational records and evidence live only
  in the ignored `.ire/issue-ledger/ledger.sqlite3`; never commit or upload the
  database, its sidecar files, private source records, or credentials.
- Use parent GitHub issues for project-level plans and sub-issues for
  independently deliverable tasks. Reference the task issue in the task file
  and pull request; do not maintain a parallel issue register.

<!-- pcm:issue-log-format:start -->
## Issue log format (issue-log-format 1.2.0)

<!-- pcm:policy {"id":"issue-log-format","policy_version":"1.2.0","protocol_version":"0.1.0-draft"} -->

Write issue logs, progress updates, and pull requests in one plain-language shape a newcomer can follow. Pick the tier by the kind of issue, not by preference. **Core tier (every issue log):** title states the problem and intended direction; a 1-3 paragraph summary naming who/what is affected, the consequence, and what this proposes; identity and lineage (leaf owning issue, parent ancestry or none, task ID, primary writer, branch); observed facts vs interpretation, with inferences labelled *inferred*; acceptance criteria with numeric thresholds marked *(proposed)* when untested; boundaries/non-goals and one next action. **Investigation tier (incidents, failures, research, design issues):** numbered symptoms; hypotheses with Status, confirm/refute, and experiment; evidence with provenance; a **Counter-signal** entry when one exists; honest caveat; problems-vs-gaps; a **Proposal** labelled *(proposal)* stating none of it exists unless named as existing. **Pull requests open reader-first:** problem and consequence, what changes, how to verify, and what stays unchanged; lineage links; evidence and one next action; long logs collapsed or linked; reference issues with "Refs #<number>" and use closing keywords only when closing at merge is intended. **Diagrams (mermaid):** when a record describes a flow with 4+ ordered steps or 2+ branches, add a fenced mermaid diagram *and* keep an adjacent text list or table so the record survives render failure; default to `graph TD` (vertical) because wide `LR` flows shrink to illegible strips on phones — reserve `LR` for 4 or fewer short nodes; cap 8 nodes and 6-word labels; wrap diagrams that may exceed the container width inside `<details>` (GitHub mounts the renderer lazily on expand); preview the rendered diagram before publishing (broken syntax shows a visible parse error) and never cite renderer URLs as standalone sources. No private absolute paths or secrets; link rather than paste long logs. See `docs/ISSUE_LOG_FORMAT.md` for the full format, exemplar, and examples.
<!-- pcm:issue-log-format:end -->

<!-- oio:issue-log-guidance:start -->
Before filing an observational or operational issue log, read `.oio/ontology/ISSUE_LOG_ONTOLOGY.md`, `.oio/ontology/project.json`, and `.oio/ontology/AGENT_GUIDE.md`. Confirm the exact destination and filing action are authorized. On OIO, ACS, CGM, and PCM, do not submit an issue or write files without explicit human direction for that destination and action. A proposal can remain a local draft until directed. Never treat adoption as permission to write to an adopter or sibling repository.
<!-- oio:issue-log-guidance:end -->
