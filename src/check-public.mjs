import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const forbidden = [new RegExp(['infer', 'hub'].join(''), 'ig'), new RegExp(['tele', 'metry'].join(''), 'ig'), new RegExp(`\\b${['da', 'ta'].join('')}\\b`, 'ig')];
const allowed = new Set(['.git', '.venv', 'node_modules', 'coverage', 'local']);
const operationalLedgerPaths = [
  /^checkpoints\/2026-09-22-operational-ledger-relocation\.md$/i,
  /^docs\/ISSUE-LEDGER-/i,
  /^schemas\/issue-ledger\//i,
  /^operational\//i,
  /^AGENTS\.md$/i,
];
const binaryExtensions = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.woff', '.woff2']);
const failures = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (allowed.has(entry.name)) continue;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(absolute);
    else {
      const relative = path.relative(root, absolute).split(path.sep).join('/');
      // Keep the product-name guard global; ledger docs use broad technical terms.
      const operationalLedger = operationalLedgerPaths.some((pattern) => pattern.test(relative));
      if (binaryExtensions.has(path.extname(entry.name).toLowerCase())) continue;
      const text = fs.readFileSync(absolute, 'utf8');
      for (let index = 0; index < forbidden.length; index += 1) {
        const pattern = forbidden[index];
        if (operationalLedger && index > 0) continue;
        pattern.lastIndex = 0;
        if (pattern.test(text)) failures.push(`${relative} matches ${pattern}`);
      }
    }
  }
}
walk(root);
if (failures.length) {
  console.error(failures.join('\n'));
  process.exitCode = 1;
} else {
  console.log('public-surface check passed');
}
