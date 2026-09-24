import test from 'node:test';
import assert from 'node:assert/strict';
import { finalize } from '../scripts/finalize-checkpoint.mjs';

const head = 'a'.repeat(40);
const merge = 'b'.repeat(40);

function successfulCommands() {
  const calls = [];
  const execute = (command, args) => {
    calls.push([command, ...args]);
    if (command === 'git' && args.join(' ') === 'remote get-url origin') {
      return 'https://github.com/Pukujan/inference-recommendation-engine.git';
    }
    if (command === 'git' && args.join(' ') === 'status --porcelain') return '';
    if (command === 'git' && args.join(' ') === 'branch --show-current') return 'codex/checkpoint/example';
    if (command === 'git' && args[0] === 'rev-parse' && args[1] === '--verify') return head;
    if (command === 'git' && args[0] === 'rev-parse') return merge;
    if (command === 'gh') {
      return JSON.stringify({
        state: 'MERGED',
        mergedAt: '2026-09-23T23:00:00Z',
        headRefName: 'codex/checkpoint/example',
        headRefOid: head,
        baseRefName: 'main',
        mergeCommit: { oid: merge },
        statusCheckRollup: [{ name: 'test', conclusion: 'SUCCESS' }],
        url: 'https://github.com/Pukujan/inference-recommendation-engine/pull/12',
      });
    }
    return '';
  };
  return { calls, execute };
}

test('finalizer validates merged PR evidence, fast-forwards main, and removes only its checkpoint branch', () => {
  const { calls, execute } = successfulCommands();
  const result = finalize(12, execute, () => head);

  assert.match(result, /PR #12 merged/);
  assert.ok(calls.some((call) => call.join(' ') === 'git merge --ff-only origin/main'));
  assert.ok(calls.some((call) => call.join(' ') === 'git branch -D codex/checkpoint/example'));
});

test('finalizer does not mutate refs when the PR is not confirmed merged', () => {
  const { calls, execute: success } = successfulCommands();
  const execute = (command, args) => {
    if (command === 'gh') {
      return JSON.stringify({
        state: 'OPEN', mergedAt: null, headRefName: 'codex/checkpoint/example',
        headRefOid: head, baseRefName: 'main', mergeCommit: null,
        statusCheckRollup: [], url: 'https://example.invalid/pr/12',
      });
    }
    return success(command, args);
  };

  assert.throws(() => finalize(12, execute, () => head), /not confirmed merged/);
  assert.equal(calls.some((call) => call[0] === 'git' && ['switch', 'branch', 'fetch', 'merge'].includes(call[1])), false);
});

test('finalizer preserves a changed local checkpoint branch', () => {
  const { execute: success } = successfulCommands();
  const execute = (command, args) => {
    if (command === 'git' && args[0] === 'rev-parse' && args[1] === '--verify') return 'c'.repeat(40);
    return success(command, args);
  };

  assert.throws(() => finalize(12, execute, () => 'c'.repeat(40)), /no longer points to the merged PR head/);
});

test('finalizer refuses non-checkpoint branches and unsuccessful required checks', () => {
  const { execute: success } = successfulCommands();
  const wrongBranch = (command, args) => {
    if (command === 'gh') {
      return JSON.stringify({
        state: 'MERGED', mergedAt: '2026-09-23T23:00:00Z', headRefName: 'feature/other',
        headRefOid: head, baseRefName: 'main', mergeCommit: { oid: merge },
        statusCheckRollup: [{ name: 'test', conclusion: 'SUCCESS' }], url: 'https://example.invalid/pr/12',
      });
    }
    return success(command, args);
  };
  assert.throws(() => finalize(12, wrongBranch, () => head), /valid checkpoint branch/);

  const failedCheck = (command, args) => {
    if (command === 'gh') {
      return JSON.stringify({
        state: 'MERGED', mergedAt: '2026-09-23T23:00:00Z', headRefName: 'codex/checkpoint/example',
        headRefOid: head, baseRefName: 'main', mergeCommit: { oid: merge },
        statusCheckRollup: [{ name: 'test', conclusion: 'FAILURE' }], url: 'https://example.invalid/pr/12',
      });
    }
    return success(command, args);
  };
  assert.throws(() => finalize(12, failedCheck, () => head), /no successful required test check/);
});
