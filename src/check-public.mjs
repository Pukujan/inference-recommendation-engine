import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const forbidden = [new RegExp(['infer', 'hub'].join(''), 'ig'), new RegExp(['tele', 'metry'].join(''), 'ig'), new RegExp(`\\b${['da', 'ta'].join('')}\\b`, 'ig')];
const allowed = new Set([
  '.git',
  '.mypy_cache',
  '.ruff_cache',
  '.venv',
  'node_modules',
  'coverage',
  'local',
  '.ire',
  '.kilo',
]);
const operationalLedgerPaths = [
  /^checkpoints\/2026-09-22-operational-ledger-relocation\.md$/i,
  /^docs\/ISSUE-LEDGER-/i,
  /^schemas\/issue-ledger\//i,
  /^operational\//i,
  /^tasks\/IRE-0010-agent-run-evidence-pipeline\.md$/i,
  /^AGENTS\.md$/i,
  /^docs\/CKFF-PROVIDER\.md$/i,
  /^schemas\/ckff-route-candidates/i,
  /^providers\/ckff\//i,
];
// Files installed and checked byte-for-byte by the pinned agent stack (OIO installer, ACS assignment schema).
// They use broad technical terms; the product-name guard still applies to them.
const vendoredStackPaths = [
  /^\.oio\//,
  /^\.coord\//,
  /^\.github\/scripts\/oio_[a-z_]+\.py$/,
  /^\.github\/ISSUE_TEMPLATE\/observational-issue\.yml$/,
];
const namedIntegration = ['infer', 'hub'].join('');
const explicitlyDocumentedPaths = new Set([
  'agents.md',
  `docs/${namedIntegration}-api-setup.md`,
  `tasks/ire-0004-${namedIntegration}-api-setup.md`,
  'tasks/ire-0005-codex-harness-permissions.md',
  `tasks/ire-0006-${namedIntegration}-cli-agent-runbook.md`,
  `tasks/ire-0007-${namedIntegration}-agent-behavior-benchmark.md`,
  'tasks/ire-0008-kilo-staff-background-helpers.md',
  'checkpoints/current.md',
  'docs/ckff-provider.md',
  'schemas/ckff-route-candidates.v1.schema.json',
  'providers/ckff/route-candidates.v1.json',
]);
// Operational agent-run evidence source (issue #41) configures the named BYOK integration by design.
const explicitlyDocumentedPrefixes = [`operational/${['tele', 'metry'].join('')}/`];
// First-run onboarding surfaces (#72, #73, #74). They exist to tell someone with their own key for the
// named BYOK integration how to use today's picks, and they link the published feed branch, so both
// guards are lifted for exactly these files.
const onboardingPaths = new Set([
  'start-here.md',
  'llms.txt',
  'docs/agent-quickstart.md',
  'scripts/doctor.mjs',
  'tests/doctor.test.mjs',
]);
// The daily refresh workflow (#84) runs the operational list builders, reads the named BYOK key from a
// repo secret and pushes the published feed branch, so both guards are lifted for exactly this file.
const scheduledRefreshPaths = new Set(['.github/workflows/daily-refresh.yml']);
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
      const operationalLedger =
        operationalLedgerPaths.some((pattern) => pattern.test(relative)) ||
        vendoredStackPaths.some((pattern) => pattern.test(relative));
      const explicitIntegrationReference =
        explicitlyDocumentedPaths.has(relative.toLowerCase()) ||
        explicitlyDocumentedPrefixes.some((prefix) => relative.toLowerCase().startsWith(prefix));
      if (binaryExtensions.has(path.extname(entry.name).toLowerCase())) continue;
      if (onboardingPaths.has(relative.toLowerCase())) continue;
      if (scheduledRefreshPaths.has(relative.toLowerCase())) continue;
      const text = fs.readFileSync(absolute, 'utf8');
      for (let index = 0; index < forbidden.length; index += 1) {
        const pattern = forbidden[index];
        if ((operationalLedger && index > 0) || (explicitIntegrationReference && index === 0)) continue;
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
