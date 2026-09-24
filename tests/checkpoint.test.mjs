import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { autoMergeArgs, checkpointPrBody, localGateCommands } from '../scripts/checkpoint.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const helper = path.join(root, 'scripts', 'checkpoint.mjs');

function run(args) {
  return spawnSync(process.execPath, [helper, ...args], { cwd: root, encoding: 'utf8', windowsHide: true });
}

test('checkpoint helper documents the explicit-path interface without side effects', () => {
  const result = run(['--help']);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /--name <slug>/);
  assert.match(result.stdout, /--path <path>/);
  assert.match(result.stdout, /--issue <number>/);
  assert.match(result.stdout, /without waiting for CI/);
  assert.match(result.stdout, /finalize-checkpoint\.mjs/);
});

test('checkpoint PR links its issue and requests GitHub auto-merge without waiting', () => {
  assert.match(checkpointPrBody(11), /Part of #11/);
  const args = autoMergeArgs(12, 'a'.repeat(40));
  assert.deepEqual(args, ['pr', 'merge', '12', '--auto', '--squash', '--match-head-commit', 'a'.repeat(40)]);
  assert.equal(args.includes('--watch'), false);
});

test('checkpoint publisher runs static checks before tests and publication', () => {
  const gates = localGateCommands();
  const staticGate = gates.findIndex(([command, args]) => command === 'pnpm' && args[0] === 'check:static');
  const testGate = gates.findIndex(([command, args]) => command === 'pnpm' && args[0] === 'test');

  assert.notEqual(staticGate, -1);
  assert.ok(staticGate < testGate);
});

test('checkpoint helper rejects implicit all-files staging before any repository or network action', () => {
  const result = run(['--name', 'unsafe', '--message', 'test checkpoint', '--issue', '11']);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /at least one explicit --path/);
});

test('checkpoint helper rejects path traversal and local ledger records', () => {
  const traversal = run(['--name', 'unsafe', '--message', 'test', '--issue', '11', '--path', '../outside']);
  assert.equal(traversal.status, 1);
  assert.match(traversal.stderr, /Unsafe checkpoint path/);

  const localLedger = run(['--name', 'unsafe', '--message', 'test', '--issue', '11', '--path', '.ire/issue-ledger/ledger.sqlite3']);
  assert.equal(localLedger.status, 1);
  assert.match(localLedger.stderr, /Sensitive path cannot be checkpointed/);
});

test('checkpoint helper validates issue references before repository or network actions', () => {
  const missing = run(['--name', 'unsafe', '--message', 'test', '--path', 'README.md']);
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /--issue is required/);

  const result = run(['--name', 'unsafe', '--message', 'test', '--issue', '0', '--path', 'README.md']);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /positive GitHub issue number/);
});
