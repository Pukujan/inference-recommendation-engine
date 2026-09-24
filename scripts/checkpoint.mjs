#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repository = 'Pukujan/inference-recommendation-engine';
const forbiddenPathSegment = /^(?:\.ire|\.env(?:\..*)?|secrets?|credentials?|id_rsa|id_ed25519)$/i;
const secretPatterns = [
  /\bgh[pousr]_[A-Za-z0-9]{20,}\b/,
  /\bgithub_pat_[A-Za-z0-9_]{20,}\b/,
  /\bsk-[A-Za-z0-9]{20,}\b/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----/,
];

function fail(message) {
  throw new Error(message);
}

function invoke(command, args, { capture = false, allowFailure = false } = {}) {
  const windowsPackageManager = ['npm', 'pnpm'].includes(command) && process.platform === 'win32';
  const result = spawnSync(windowsPackageManager ? `${command}.cmd` : command, args, {
    cwd: root,
    encoding: 'utf8',
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    shell: windowsPackageManager,
    windowsHide: true,
  });
  if (result.error) fail(`${command} could not start: ${result.error.message}`);
  if (!allowFailure && result.status !== 0) {
    const detail = capture ? result.stderr.trim() : '';
    fail(`${command} ${args.join(' ')} failed${detail ? `: ${detail}` : ''}`);
  }
  return { status: result.status, stdout: result.stdout ?? '', stderr: result.stderr ?? '' };
}

function git(...args) {
  return invoke('git', args, { capture: true }).stdout.trim();
}

export function checkpointPrBody(issue) {
  return `Automated repository checkpoint.\n\nLocal gates and required GitHub CI must pass before this checkpoint is merged. The canonical checkout will return to main after merge.\n\nPart of #${issue}`;
}

export function autoMergeArgs(prNumber, headSha) {
  return ['pr', 'merge', String(prNumber), '--auto', '--squash', '--match-head-commit', headSha];
}

function validatePath(input) {
  const normalized = input.replaceAll('\\', '/');
  if (
    !normalized ||
    normalized.startsWith('/') ||
    /^[A-Za-z]:/.test(normalized) ||
    normalized === '.' ||
    normalized.split('/').some((part) => part === '..' || part === '.' || part === '') ||
    /[*?\[\]]/.test(normalized)
  ) fail(`Unsafe checkpoint path: ${input}`);
  if (normalized.split('/').some((part) => forbiddenPathSegment.test(part))) {
    fail(`Sensitive path cannot be checkpointed: ${input}`);
  }
  const resolved = path.resolve(root, normalized);
  if (resolved !== root && !resolved.startsWith(`${root}${path.sep}`)) fail(`Path escapes repository: ${input}`);
  return normalized;
}

function parseArgs(argv) {
  const options = { paths: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === '--help' || flag === '-h') return { help: true };
    if (!['--name', '--message', '--path', '--issue'].includes(flag)) fail(`Unknown option: ${flag}`);
    const value = argv[index + 1];
    if (!value || value.startsWith('--')) fail(`Missing value for ${flag}`);
    index += 1;
    if (flag === '--path') options.paths.push(value);
    else {
      if (options[flag.slice(2)]) fail(`${flag} may be provided only once`);
      options[flag.slice(2)] = value;
    }
  }
  if (!options.name || !/^[a-z0-9][a-z0-9-]{0,39}$/.test(options.name)) {
    fail('--name must be a lowercase slug of 1–40 letters, digits, and hyphens');
  }
  if (!options.message || /[\r\n\0]/.test(options.message)) fail('--message must be one non-empty line');
  if (options.issue === undefined) fail('--issue is required so every checkpoint links to its GitHub task.');
  if (options.issue !== undefined && !/^[1-9][0-9]*$/.test(options.issue)) {
    fail('--issue must be a positive GitHub issue number');
  }
  if (options.paths.length === 0) fail('Provide at least one explicit --path');
  options.paths = [...new Set(options.paths.map(validatePath))];
  return options;
}

function assertNoStagedChanges() {
  const result = invoke('git', ['diff', '--cached', '--quiet'], { capture: true, allowFailure: true });
  if (result.status === 1) fail('The index already contains staged changes; preserve them and start from a clean index.');
  if (result.status !== 0) fail('Unable to inspect the Git index.');
}

function assertNoUnselectedChanges() {
  const tracked = invoke('git', ['diff', '--quiet'], { capture: true, allowFailure: true });
  const untracked = git('ls-files', '--others', '--exclude-standard');
  if (tracked.status !== 0 || untracked) {
    fail('Unstaged or untracked files remain outside the staged checkpoint. Add their exact paths or leave them for a later checkpoint.');
  }
}

function verifyProtection() {
  const result = invoke('gh', ['api', `repos/${repository}/branches/main/protection`], { capture: true });
  let protection;
  try {
    protection = JSON.parse(result.stdout);
  } catch {
    fail('Could not read the main-branch protection settings.');
  }
  const checks = protection.required_status_checks;
  if (!checks?.strict || !checks.contexts?.includes('test')) {
    fail('main must require strict, up-to-date CI status check `test`.');
  }
  if (protection.required_pull_request_reviews?.required_approving_review_count !== 0) {
    fail('main must require pull requests with zero required approvals for automated checkpoint delivery.');
  }
  if (protection.enforce_admins?.enabled !== true) fail('main protection must apply to administrators.');
  if (protection.allow_force_pushes?.enabled !== false || protection.allow_deletions?.enabled !== false) {
    fail('main protection must disallow force pushes and branch deletion.');
  }
  const settingsResult = invoke('gh', ['api', `repos/${repository}`], { capture: true });
  const settings = JSON.parse(settingsResult.stdout);
  if (settings.allow_auto_merge !== true) fail('GitHub auto-merge must be enabled for this repository.');
  if (settings.delete_branch_on_merge !== true) fail('GitHub must delete checkpoint branches after they merge.');
}

function printHelp() {
  process.stdout.write(
    'Usage: node scripts/checkpoint.mjs --name <slug> --message <title> --issue <number> --path <path> [--path <path> ...]\n' +
      'Runs local gates, commits and pushes one explicit checkpoint branch, opens or updates its PR, and requests auto-merge without waiting for CI.\n' +
      'After GitHub confirms the merge, run node scripts/finalize-checkpoint.mjs --pr <number> to sync main and clean up the local branch.\n',
  );
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) return printHelp();

  const remote = git('remote', 'get-url', 'origin');
  if (!/github\.com[:/]Pukujan\/inference-recommendation-engine(?:\.git)?$/i.test(remote)) {
    fail(`Unexpected origin remote; expected ${repository}.`);
  }
  invoke('gh', ['auth', 'status'], { capture: true });
  verifyProtection();
  assertNoStagedChanges();
  const issue = JSON.parse(invoke('gh', ['issue', 'view', options.issue, '--json', 'state'], { capture: true }).stdout);
  if (issue.state !== 'OPEN') fail(`GitHub issue #${options.issue} is not open.`);

  const branch = `codex/checkpoint/${options.name}`;
  const currentBranch = git('branch', '--show-current');
  if (currentBranch !== 'main' && currentBranch !== branch) {
    fail(`Use canonical main or this checkpoint's branch (${branch}); found ${currentBranch}.`);
  }
  if (currentBranch === 'main') {
    git('fetch', 'origin', 'main');
    if (git('rev-parse', 'HEAD') !== git('rev-parse', 'origin/main')) {
      fail('Canonical main is not synchronized with origin/main. Sync it before starting a checkpoint.');
    }
  }

  for (const [command, args] of [
    ['uv', ['sync', '--locked']],
    ['pnpm', ['install', '--frozen-lockfile']],
    ['pnpm', ['test']],
    ['pnpm', ['check:public']],
    ['pnpm', ['test:operational']],
    ['pnpm', ['pack', '--dry-run']],
  ]) invoke(command, args);

  invoke('git', ['add', '--', ...options.paths]);
  const staged = git('diff', '--cached', '--name-only').split(/\r?\n/).filter(Boolean);
  try {
    assertNoUnselectedChanges();
    if (staged.length === 0) fail('The requested checkpoint paths contain no staged changes.');
    if (staged.some((file) => file.split('/').some((part) => forbiddenPathSegment.test(part)))) {
      fail('The staged checkpoint contains a sensitive path.');
    }
    invoke('git', ['diff', '--cached', '--check']);
    const stagedDiff = git('diff', '--cached', '--no-ext-diff', '--unified=0', '--no-color');
    if (secretPatterns.some((pattern) => pattern.test(stagedDiff))) {
      fail('Credential-like content detected in the staged diff; nothing was committed or pushed.');
    }
  } catch (error) {
    if (staged.length > 0) {
      const restored = invoke('git', ['restore', '--staged', '--', ...staged], { capture: true, allowFailure: true });
      if (restored.status !== 0) {
        error.message += ' (automatic unstage failed; inspect the index before resuming)';
      }
    }
    throw error;
  }

  if (currentBranch === 'main') {
    const exists = invoke('git', ['show-ref', '--verify', '--quiet', `refs/heads/${branch}`], { capture: true, allowFailure: true });
    if (exists.status === 0) {
      fail(`Local branch ${branch} already exists; inspect it and resume from that branch explicitly.`);
    }
    invoke('git', ['switch', '-c', branch]);
  }
  invoke('git', ['commit', '-m', options.message]);
  invoke('git', ['push', '--set-upstream', 'origin', branch]);

  let rows = JSON.parse(invoke('gh', ['pr', 'list', '--state', 'open', '--head', branch, '--base', 'main', '--json', 'number,url'], { capture: true }).stdout);
  if (rows.length > 1) fail(`Multiple open PRs found for checkpoint branch ${branch}.`);
  let pr = rows[0];
  const body = checkpointPrBody(options.issue);
  if (!pr) {
    const url = invoke('gh', ['pr', 'create', '--base', 'main', '--head', branch, '--title', options.message, '--body', body], { capture: true }).stdout.trim();
    rows = JSON.parse(invoke('gh', ['pr', 'view', url, '--json', 'number,url'], { capture: true }).stdout);
    pr = rows;
  } else {
    invoke('gh', ['pr', 'edit', String(pr.number), '--title', options.message, '--body', body]);
  }
  if (!pr?.number) fail('Could not identify the checkpoint pull request.');

  const beforeMerge = JSON.parse(invoke('gh', ['pr', 'view', String(pr.number), '--json', 'state,headRefName,headRefOid,baseRefName,isDraft'], { capture: true }).stdout);
  if (beforeMerge.state !== 'OPEN' || beforeMerge.headRefName !== branch || beforeMerge.baseRefName !== 'main') {
    fail('The checkpoint PR changed state or target; inspect it before proceeding.');
  }
  if (beforeMerge.isDraft) fail('Checkpoint PR is a draft; mark it ready before requesting auto-merge.');
  invoke('gh', autoMergeArgs(pr.number, beforeMerge.headRefOid));
  process.stdout.write(`Checkpoint published: ${pr.url}\nGitHub auto-merge is requested. Required checks are running asynchronously.\n`);
  process.stdout.write(`After GitHub confirms the merge, run: node scripts/finalize-checkpoint.mjs --pr ${pr.number}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`Checkpoint stopped: ${error.message}\n`);
    process.exitCode = 1;
  });
}
