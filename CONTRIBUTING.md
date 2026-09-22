# Contributing

Keep changes small and contract-led.

Before opening a change:

```bash
npm install
npm test
npm run check:public
npm pack --dry-run
```

Any scoring change must update the invariant or relation it affects, add a generated or metamorphic test, and change the policy or engine revision when behavior changes.

Do not add credentials, provider-specific source clients, or private source captures to this repository. Keep those in a separate local adapter.
