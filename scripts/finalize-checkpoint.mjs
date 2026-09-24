#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repository = 'Pukujan/inference-recommendation-engine';
const checkpointBranch = /^codex\/checkpoint\/[a-z0-9][a-z0-9-]{0,39}$/;

function fail(message) {
  throw new Error(message);
}

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  if (result.error) fail(`${command} could not start: ${result.error.message}`);
  if (result.status !== 0) {
    const detail = result.stderr.trim() || result.stdout.trim();
    fail(`${command} ${args.join(' ')} failed${detail ? `: ${detail}` : ''}`);
  }
  return result.stdout.trim();
}

function parsePr(value) {
  if (!/^[1-9][0-9]*$/.test(value ?? '')) fail('--pr must be a positive GitHub pull request number.');
  return Number(value);
}

function getLocalBranchHead(branch) {
  const result = spawnSync('git', ['rev-parse', '--verify', `refs/heads/${branch}`], {
    cwd: root,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  if (result.error) fail(`git could not start: ${result.error.message}`);
  if (result.status === 128) return null;
  if (result.status !== 0) fail('Unable to inspect the checkpoint branch.');
  return result.stdout.trim();
}

export function finalize(prNumber, execute = run, branchHead = getLocalBranchHead) {
  const remote = execute('git', ['remote', 'get-url', 'origin']);
  if (!/github\.com[:/]Pukujan\/inference-recommendation-engine(?:\.git)?$/i.test(remote)) {
    fail(`Unexpected origin remote; expected ${repository}.`);
  }

  const status = execute('git', ['status', '--porcelain']);
  if (status) fail('The canonical checkout has local changes; preserve them before finalizing.');

  const pr = JSON.parse(
    execute('gh', [
      'pr', 'view', String(prNumber), '--json',
      'state,mergedAt,headRefName,headRefOid,baseRefName,mergeCommit,statusCheckRollup,url',
    ]),
  );
  if (pr.state !== 'MERGED' || !pr.mergedAt || pr.baseRefName !== 'main' || !pr.mergeCommit?.oid) {
    fail(`PR #${prNumber} is not confirmed merged into main.`);
  }
  if (!checkpointBranch.test(pr.headRefName ?? '') || !/^[0-9a-f]{40}$/i.test(pr.headRefOid ?? '')) {
    fail(`PR #${prNumber} does not identify a valid checkpoint branch and head commit.`);
  }
  const passedTest = (pr.statusCheckRollup ?? []).some((check) =>
    (check.name ?? check.context) === 'test' &&
    ((check.conclusion ?? check.state) === 'SUCCESS'),
  );
  if (!passedTest) fail(`PR #${prNumber} has no successful required test check on its merged head.`);

  const currentBranch = execute('git', ['branch', '--show-current']);
  if (currentBranch !== 'main' && currentBranch !== pr.headRefName) {
    fail(`Current branch ${currentBranch} is not main or this PR's checkpoint branch ${pr.headRefName}.`);
  }

  const localHead = branchHead(pr.headRefName);
  if (localHead !== null && localHead !== pr.headRefOid) {
    fail(`Local branch ${pr.headRefName} no longer points to the merged PR head; preserving it.`);
  }
  const hasLocalBranch = localHead !== null;
  if (currentBranch === pr.headRefName && !hasLocalBranch) {
    fail(`The current checkpoint branch ${pr.headRefName} is missing locally.`);
  }

  if (currentBranch !== 'main') execute('git', ['switch', 'main']);
  execute('git', ['fetch', 'origin', 'main']);
  execute('git', ['merge', '--ff-only', 'origin/main']);
  if (execute('git', ['status', '--porcelain'])) {
    fail('The canonical checkout is not clean after synchronizing main.');
  }
  if (hasLocalBranch) execute('git', ['branch', '-D', pr.headRefName]);
  if (execute('git', ['rev-parse', 'HEAD']) !== execute('git', ['rev-parse', 'origin/main'])) {
    fail('Canonical main did not synchronize to origin/main.');
  }

  return `PR #${prNumber} merged at ${pr.mergeCommit.oid}; canonical main is synchronized and its verified local checkpoint branch is removed.`;
}

function main(argv) {
  if (argv.length !== 2 || argv[0] !== '--pr') {
    process.stderr.write('Usage: node scripts/finalize-checkpoint.mjs --pr <number>\n');
    return 2;
  }
  try {
    process.stdout.write(`${finalize(parsePr(argv[1]))}\n`);
    return 0;
  } catch (error) {
    process.stderr.write(`Checkpoint finalization stopped: ${error.message}\n`);
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  process.exitCode = main(process.argv.slice(2));
}
