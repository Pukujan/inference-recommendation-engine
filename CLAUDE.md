# Claude Code

<!-- acs:claude-bridge:start -->
## Claude Code: load the stack's rules

Other agents read `AGENTS.md` by themselves; Claude Code reads this file. The import below gives Claude the same rules every other agent follows. They bind here exactly as they do there.

@AGENTS.md

### GitHub is the authority

Live GitHub state decides scope, status and what is accepted: the issues, pull requests, checks and merged default branch of this repository. Read them with `gh` before you plan, resume or report, and work through them: a branch and a pull request, never a push to the default branch. Local files are projections and caches (checkpoints, `HANDOFF.md`, `.oio/`, `.content-system/`, `.coord/`, any clone under `~/.cache`). When one disagrees with GitHub, GitHub wins; say so on the owning issue.

The stack's rules live in [`Pukujan/agent-stack-train`](https://github.com/Pukujan/agent-stack-train) at commit `2e32f2c5ef01`, the certified release in its `stack-releases.json`. This repository still pins the four component repositories; until it moves to the one-checkout stack, read a stack file there, not from a local copy or one of the four frozen component repositories.

### Boot rules

The writing rules come from `AGENTS.md` above; this repository's pinned CGM does not yet accept skill links in this file.

Dev root hygiene (ACS): the dev root (ACS_DEV_ROOT; default D:\development on Windows, ~/development elsewhere) holds exactly one main checkout per repo. Never create git worktrees, dependency or sibling clones, scratch folders, or caches there. Put them under the ACS cache instead: %LOCALAPPDATA%\acs\{deps,scratch,worktrees} on Windows, ~/.cache/acs/{deps,scratch,worktrees} on macOS/Linux (ACS_CACHE_DIR overrides). Check with scripts/dev_root_check.py.
<!-- acs:claude-bridge:end -->
