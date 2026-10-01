# Changelog

## Unreleased

- Run web/API, daily balance monitoring and scheduled quota adjustments in one application container; start fork-safe internal timers automatically with distinct database leadership locks, signal-aware shutdown and timer-aware health checks. Retain existing rule/archive data; remove worker services/profiles from current Compose templates.
- Replace the stale quota settings help that required a standalone worker; document automatic timer startup, health verification and non-destructive upgrades from the old deployment.

- Fix advisory-lock collision between the quota scheduler and notification delivery; centralize distinct lock IDs and add real PostgreSQL cross-channel regression coverage.

- Treat all external Excel strings as literal text across worksheets while retaining generated totals and numeric precision.
- Validate pathological quota amounts before arithmetic and invalidate pending previews after changes to users, amount, operation or status filters.
- Recover reports containing corrupt log metadata via an exceptional read-only streamed fallback; retain billed amounts and show unavailable token categories as unknown rather than zero.
- Discover historical channel IDs once or explicitly; synchronize only changed catalog metadata without advancing ledger IDs on unchanged reads.
- Share one fresh current-month channel aggregate within each daily multi-ledger check; manual checks and the balance API remain uncached.
- Add opt-in slim report responses and owner/scoped immutable tooltip snapshots with bounded retention in monitoring migration 008.
- Move monitoring migrations to process startup/deployment; normal schedule requests only verify the migration version.

## 0.1.3 - 2026-10-01

- Recover request-time prices from legacy ratio and supported expression billing logs; merge equal-price requests and split changed prices into separate rows, matching current tiers only when uniquely identifiable.
- Keep ratio outside row grouping; reconcile cache-read tokens only for valid current-tier matches whose historical group ratio changed, without rewriting logs or billed amounts.
- Add hover/click formula details and expression price reference sheets while preserving unknown historical prices rather than substituting current prices.
- Add opt-in group-based quota schedules at Beijing midnight (daily, Monday weekly, first-of-month monthly), a settings dialog and paginated execution history.
- Add migration 007 and a single-leader quota worker with frozen rules, live enabled-user selection, durable per-user request states, five-user concurrent waves and no replay of missed or uncertain adjustments.
- Include the opt-in quota worker in source and release Compose templates; document schema, administrator ownership, deployment, management APIs and non-destructive rollback.
- Display monetary values and unit prices in all page tables with exactly two decimal places; preserve calculation, API, Excel and hover-detail precision.

- Add a separate administrator quota page backed by New API's atomic add/subtract API, with preview and five-user concurrent waves without a selection-count cap; show per-user outcomes and stop future waves after an error.
- Show each user's New API group and support selecting or deselecting users from multiple groups before a quota operation.
- Default the quota user list to enabled accounts; status multi-select also limits select-all and group selection, and removes hidden statuses from the operation selection.
- Document the `/quota/` Nginx route, administrator PAT requirement and read-only database boundary.
- Keep statistics/quota navigation on the current origin, including nonstandard ports, using canonical path-only links and relative Nginx entry redirects.

See [release notes](docs/releases/v0.1.3.md) for migration 007, the opt-in worker and quota operation safety requirements.

## 0.1.2 - 2026-09-23

- Add separate usage and budget ledgers for all channels, each active New API channel tag, and ungrouped channels.
- Give each ledger independent budget settings, monthly balance, alert state, and Excel/report scope; notification configuration remains global.
- Preserve monthly per-channel charges and aggregate historical ledger totals using each channel's current assignment. Moving a channel to another tag immediately reclassifies its archived charges without rereading logs.
- Hide tags no longer present in the live New API channel catalog and skip their independent alert checks, while retaining their settings and history for restoration.
- Add incremental monitoring database migrations `005` and `006`, regression tests, and updated deployment/upgrade guidance.

See [release notes](docs/releases/v0.1.2.md) for upgrade requirements and behavior changes.

## 0.1.1 - 2026-09-20

- Add uncached live balance JSON and independent alert endpoints using New API administrator PAT Bearer authentication.
- Add browser-only dev=2 failure status diagnostics across report dimensions.
- Fix PostgreSQL test fixtures missing the group column and expand API regression coverage.

- Add searchable multi-select user, model, token and group filters to the usage detail table.
- Add token/model aggregation modes and context-aware detail column visibility controls.
- Place New API display names next to usernames in Excel exports.
- Replace DingTalk enterprise application delivery with an encrypted custom Webhook robot configuration and optional signing.
- Archive monthly billing by channel ID while retaining disabled and deleted channels and synchronizing channel renames.
- Apply channel exclusions dynamically to archived totals, remaining balance and alerts without deleting raw channel history.
- Add guarded historical billing recalculation with monthly comparison and explicit confirmation before replacement.
- Add upgrade migrations for channel exclusions, per-channel archives and v0.1.0 DingTalk configuration compatibility.

See [release notes](docs/releases/v0.1.1.md) for upgrade requirements and behavior changes.

## 0.1.0 - 2026-09-17

First release. Requires an existing New API installation backed by PostgreSQL.

- Add user/model usage reports, token breakdowns, rankings and Excel exports.
- Reuse New API administrator authentication.
- Add monthly budget archives, daily balance checks and Feishu/DingTalk notifications.
- Package application under src/new_api_statistics.
- Add Apache-2.0 license, maintainer information and deployment documentation.
- Exclude Git metadata and private files from container builds.
- Add versioned monitoring database migrations.
- Add CI, dependency updates and container release automation.

See [release notes](docs/releases/v0.1.0.md) for deployment requirements and upgrade notes.
