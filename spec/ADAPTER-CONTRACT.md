# Adapter contract

IRE ranks routes from records an adapter produces. Two versioned contracts fix
the shape of those records so a new adapter cannot change how a route or an
observation is read. Both are JSON Schema 2020-12 documents in `schemas/adapter/`,
and `src/contract.mjs` enforces the same rules at run time before the CLI ranks
anything.

The core never talks to a provider. A provider client, its credentials and its
HTTP calls live outside this repository; the adapter's only job is to map what a
provider returns into the records below.

## Route record (`ire-adapter-route/v1`)

Required fields:

| Field | Meaning |
| --- | --- |
| `schemaVersion` | `ire-adapter-route/v1` |
| `id` | stable route identifier |
| `currency` | `USD`; every price in the record uses it |
| `providerCount` | non-negative integer |
| `routeCount` | non-negative integer |
| `price` | `{ inputPerMillion, outputPerMillion }`, both non-negative |

Optional fields: `discountPct` (0–100), `runtime`, `publicObservations`,
`privateObservations` (each an observation record), `validAt`/`knownAt`,
`sourceRevision`. Unknown fields are allowed on a route so an adapter can carry
its own extras; the ranking core ignores what it does not use.

## Observation record (`ire-adapter-observation/v1`)

Required fields: `schemaVersion` (`ire-adapter-observation/v1`), `eventId`,
`result`, `clientExcluded`, `validAt`, `knownAt`.

`result` is one of `success`, `failure`, `timeout`, `cancelled`. Optional
measurements are `ttftMs`, `durationMs`, `routingMs`, `completionTokens` and
`costUnits`; each is a non-negative number. Unlike a route, an observation is
closed: an unknown field is rejected, so adapter-specific detail cannot change
how an attempt is counted.

## Units, time and provenance

Prices and `costUnits` are USD per million tokens. The observation does not
repeat the currency; it inherits it from the route it belongs to.

`validAt` is when the record was true in the provider; `knownAt` is when the
ledger learned it. Both are RFC 3339 timestamps, and a record must carry either
both or neither. `sourceRevision` names where the record came from:
`{ sourceId, revision, retrievedAt }` with an optional `uri`.

## Versioning

The version is per record, in `schemaVersion`. A v2 contract arrives as a new
file (`v2.route.schema.json`) with a different `schemaVersion` value; a v1
record keeps validating against v1. A record at an unknown major fails with a
version-drift message rather than a field error, so a mixed adapter is caught at
the boundary.

## Where it is enforced

`src/cli.mjs` validates the input file with `validateRouteRecords` before
ranking and exits non-zero on the first violation. `validateRouteRecord` and
`validateObservationRecord` are exported from `src/index.mjs` for an adapter to
call on its own records.

## Non-goals

The contract does not fetch anything, hold credentials, or name a provider. It
does not replace the policy document; policy stays separate and versioned on its
own.
