import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

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
});

test('checkpoint helper rejects implicit all-files staging before any repository or network action', () => {
  const result = run(['--name', 'unsafe', '--message', 'test checkpoint']);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /at least one explicit --path/);
});

test('checkpoint helper rejects path traversal and local ledger records', () => {
  const traversal = run(['--name', 'unsafe', '--message', 'test', '--path', '../outside']);
  assert.equal(traversal.status, 1);
  assert.match(traversal.stderr, /Unsafe checkpoint path/);

  const localLedger = run(['--name', 'unsafe', '--message', 'test', '--path', '.ire/issue-ledger/ledger.sqlite3']);
  assert.equal(localLedger.status, 1);
  assert.match(localLedger.stderr, /Sensitive path cannot be checkpointed/);
});

test('checkpoint helper validates issue references before repository or network actions', () => {
  const result = run(['--name', 'unsafe', '--message', 'test', '--issue', '0', '--path', 'README.md']);
  assert.equal(result.status, 1);
  assert.match(result.stderr, /positive GitHub issue number/);
});
