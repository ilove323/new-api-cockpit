# Changelog

## Unreleased

- Add a separate administrator quota page backed by New API's atomic add/subtract API, with preview and a 100-user batch limit.
- Show each user's New API group and support selecting or deselecting users from multiple groups before a quota operation.
- Document the `/quota/` Nginx route, administrator PAT requirement and read-only database boundary.

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
