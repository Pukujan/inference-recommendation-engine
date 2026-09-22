import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const forbidden = [new RegExp(['infer', 'hub'].join(''), 'ig'), new RegExp(['tele', 'metry'].join(''), 'ig'), new RegExp(`\\b${['da', 'ta'].join('')}\\b`, 'ig')];
const allowed = new Set(['.git', 'node_modules', 'coverage', 'local']);
const binaryExtensions = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.woff', '.woff2']);
const failures = [];
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    if (allowed.has(entry.name)) continue;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) walk(absolute);
    else {
      if (binaryExtensions.has(path.extname(entry.name).toLowerCase())) continue;
      const text = fs.readFileSync(absolute, 'utf8');
      for (const pattern of forbidden) if (pattern.test(text)) failures.push(`${path.relative(root, absolute)} matches ${pattern}`);
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
