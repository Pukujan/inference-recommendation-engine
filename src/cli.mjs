import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ENGINE_VERSION, rankCandidates } from './engine.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const inputFile = process.env.IRE_INPUT_FILE ?? path.join(root, 'examples', 'routes.json');
const policyFile = process.env.IRE_POLICY_FILE ?? path.join(root, 'policy.example.json');
const outputFile = process.env.IRE_OUTPUT_FILE ?? path.join(root, 'local', 'recommendations.json');
const candidates = JSON.parse(fs.readFileSync(inputFile, 'utf8'));
const policy = JSON.parse(fs.readFileSync(policyFile, 'utf8'));
const ranked = rankCandidates(candidates, policy, 'public');
fs.mkdirSync(path.dirname(outputFile), { recursive: true });
fs.writeFileSync(outputFile, `${JSON.stringify({ engineVersion: ENGINE_VERSION, generatedAt: new Date().toISOString(), ranked }, null, 2)}\n`);
console.log(`wrote ${ranked.length} recommendations to ${outputFile}`);
