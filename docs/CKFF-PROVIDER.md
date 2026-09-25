# CKFF provider candidate (agent-facing)

Status: candidate ingest for IRE [#60](https://github.com/Pukujan/inference-recommendation-engine/issues/60).
Source: Study-os operational handoff 2026-09-25. No secret values in this file.

Machine-readable sibling: `providers/ckff/route-candidates.v1.json`
Schema: `schemas/ckff-route-candidates.v1.schema.json`

## Provider identity

| Field | Value |
| --- | --- |
| provider_id | `ckff` |
| primary_base_url | `https://ckffai.com/v1` |
| backup_base_url | `https://aws.ckffai.com/v1` |
| inference_path | `POST /responses` |
| wire_api | `responses` |
| supported_endpoint_types | `openai` |

CKFF is a separate BYOK provider from InferHub. Do not collapse CKFF model IDs into InferHub `cb/` routes without an explicit failover step.

## Credentials (names and local paths only)

Never commit values. Never print values into GitHub, receipts, or logs.

| Purpose | Env var | Local store (Windows) |
| --- | --- | --- |
| Astra inference (Codex / Responses) | `ckff_astra` | `C:\Users\pujan\OneDrive\Desktop\configs\.env` (also mirrored into Study-os `.env`, gitignored) |
| Account metrics read | `ckff_access_token` | same desktop configs `.env` |
| InferHub Astra backup | `INFERHUB_API_KEY` | `D:\claude\inferhub\.env` |
| Optional base overrides | `CKFF_ASTRA_BASE_URL`, `CKFF_CODEX_BACKUP_BASE_URL` | Study-os `.env` |

`ckff_access_token` is **not** an inference key. It works on account metrics (`/api/user/self`, `/api/log/self`, `/api/pricing`, `/api/status`) and returns 401 on `/v1/models`. Inference uses `ckff_astra` on the `/v1/responses` wire.

Local operational note (outside this repo): `D:\claude\_workspace\study-os-astra\CKFF-ASTRA-OPS-FOR-IRE.md` and Study-os `docs/ops/CKFF-ASTRA-OPS-FOR-IRE.md`. Related LiteLLM note: `D:\claude\litellm\docs\CKFF-ACCOUNT-TELEMETRY.md`.

## Pricing unit: chicken tokens

**Durable rule (Alex, 2026-09-25):** CKFF prices are denominated in **chicken tokens**. Chicken tokens are about **100× cheaper than USD** when comparing to InferHub `$/MTok` tables.

Conversion annotation for ranking against InferHub USD routes:

```text
approx_usd = chicken / 100
# equivalently: 1 chicken unit ≈ $0.01 USD
```

Rules for agents and recommenders:

1. Store CKFF prices in `price_unit: "chicken_token"`.
2. When comparing to InferHub USD, emit an explicit `usd_approx` field using ÷100. Never silently treat chicken quota ints as USD.
3. Provider UI may still show `$` (`display_in_currency=True`, `custom_currency_symbol=$`, `usd_exchange_rate=1`). The chicken rule **overrides** naive USD interpretation for IRE comparisons.
4. Do not mix chicken and USD in the same numeric field without a unit tag.

### Snapshot facts (2026-09-25, sanitized)

From `/api/status`:

- `quota_display_type=CUSTOM`
- `quota_per_unit=500000`

From `/api/pricing` for `gpt-6-astra`:

- `model_ratio=37.5`
- `completion_ratio=2`
- `billing_mode=tiered_expr`
- `billing_expr`: `len <= 272000 ? tier("base", p*10 + c*50 + cr*1 + cc*12.5) : tier("tier2", p*20 + c*75 + cr*2 + cc*25)`
- `enable_groups` includes a `codex-gpt-6-.` group

`gpt-6-luna` is catalogued as a sibling CKFF model candidate; refresh live `/api/pricing` before treating its ratios as current.

## Catalogued models

See `providers/ckff/route-candidates.v1.json`.

| model_id | role | notes |
| --- | --- | --- |
| `gpt-6-astra` | primary coding owner / planner | Live pricing snapshot captured 2026-09-25 |
| `gpt-6-luna` | sibling CKFF candidate | Catalog entry; re-fetch pricing before spend-sensitive use |

InferHub mirror route used only as **failover hop 3**: `cb/gpt-6-astra` on `https://api.inferhub.dev/v1` (USD, InferHub price policy in `docs/INFERHUB-API-SETUP.md`).

## Launcher failover pattern

Codex (and similar coding CLIs) have **no** in-process primary/backup model switch. Failover is **launcher-level**:

1. CKFF primary: `https://ckffai.com/v1`, model `gpt-6-astra`, key env `ckff_astra`
2. CKFF AWS backup: `https://aws.ckffai.com/v1`, same model/key (idle budget ~180s-class observed)
3. InferHub: `https://api.inferhub.dev/v1`, model `cb/gpt-6-astra`, key env `INFERHUB_API_KEY`

Cross-provider handoff rules:

- Start a **new** `exec` (new process/session).
- Continue from a durable `CHECKPOINT.md` / checkpoint prompt stating what is done.
- **Never** resume the same Codex/Claude/Pi thread across providers.
- Record which hop served the turn in the private receipt (provider_id, base_url host, model_id). No secrets.

Observed failure mode that motivated this: CKFF Astra streams dying mid-stream (503 / upstream reject).

## Relation to InferHub artefacts

- InferHub reliability catalogue (`ihub-route-catalogue/v1`) stays USD and InferHub-only.
- CKFF candidates live under `providers/ckff/` with their own schema until a multi-provider catalogue exists.
- Do not write chicken prices into InferHub `route-catalogue.json` price fields.

## Out of scope (issue #60)

Live CKFF collector, auto polling, or changing the InferHub route-catalogue schema.
