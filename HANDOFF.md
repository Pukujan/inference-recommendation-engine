# Handoff

Read these files in order:

1. `PROJECT.md` for the stable contract.
2. `checkpoints/CURRENT.md` for the latest verified state.
3. `checkpoints/2026-09-22-operational-ledger-relocation.md` for the paused
   relocation task and exact repository boundary.
4. The active task under `tasks/`.
5. `spec/INVARIANTS.md` before changing scoring behavior.

Before ending a bounded task, record the command results and one concrete next action in a checkpoint. Keep the package provider-neutral.
