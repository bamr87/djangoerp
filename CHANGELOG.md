# Changelog

## [0.2.0](https://github.com/bamr87/djangoerp/compare/v0.1.0...v0.2.0) (2026-10-30)


### Added

* **company:** company charter, landing page and health probe ([#13](https://github.com/bamr87/djangoerp/issues/13)) ([a6ee9f3](https://github.com/bamr87/djangoerp/commit/a6ee9f3fe04182390d2c7d18ed135d76843081cf))
* **docker:** local Docker stack on PostgreSQL, Redis and Celery ([#14](https://github.com/bamr87/djangoerp/issues/14)) ([94ebc64](https://github.com/bamr87/djangoerp/commit/94ebc64c2017302bba54c0a354868059b710fe5a))
* **erp:** supply chain, manufacturing and MRP core around the ledger ([#11](https://github.com/bamr87/djangoerp/issues/11)) ([81ee701](https://github.com/bamr87/djangoerp/commit/81ee7016b0df3070a15cb7e58a306be9d31ebfc9))


### Fixed

* **manufacturing:** keep the actionable active-BOM error under DRF 3.18.2 ([35dc48e](https://github.com/bamr87/djangoerp/commit/35dc48e80a0266cac95e826398fa089b94e5c23c))
* **release:** skip release-please when it has no credential to open the PR ([5667ed7](https://github.com/bamr87/djangoerp/commit/5667ed726d8a3a342a56e7eb23bff88bb7837427))
* **release:** skip release-please when it has no credential to open the PR ([6dbb7d0](https://github.com/bamr87/djangoerp/commit/6dbb7d06279910c99edd313ee52cd447a1a267d7))
* **reports:** make SavedReport/ReportTemplate JSONFields Decimal-safe ([#12](https://github.com/bamr87/djangoerp/issues/12)) ([207d74e](https://github.com/bamr87/djangoerp/commit/207d74ed1a530b3ba129f5783d923b79cc935e6b))

## Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- `CHANGELOG.md`, in Keep a Changelog format.

## History

No versions have been tagged yet. The project started on 2025-03-08 and has 20 commits on `main`. The most recent changes:

- 2026-10-17: docs(readme): align README with the portfolio standard ([#22](https://github.com/bamr87/djangoerp/pull/22))
- 2026-10-15: build(deps): bump django from 6.1 to 6.1.1 ([#20](https://github.com/bamr87/djangoerp/pull/20))
- 2026-10-14: ci: pin standard-ci to @v2 (needs the v2 tag) ([#21](https://github.com/bamr87/djangoerp/pull/21))
- 2026-10-03: Add MIT license
- 2026-09-25: build(docker): harmonize Docker configuration to the fleet standard ([#19](https://github.com/bamr87/djangoerp/pull/19))
- 2026-09-12: style(markdown): one paragraph per line + CI enforcement ([#18](https://github.com/bamr87/djangoerp/pull/18)) (×2)
- 2026-08-09: feat(docker): local Docker stack on PostgreSQL, Redis and Celery ([#14](https://github.com/bamr87/djangoerp/pull/14))
- 2026-08-08: feat(company): company charter, landing page and health probe ([#13](https://github.com/bamr87/djangoerp/pull/13))
- 2026-08-08: fix(reports): make SavedReport/ReportTemplate JSONFields Decimal-safe ([#12](https://github.com/bamr87/djangoerp/pull/12))
- 2026-08-08: feat(erp): supply chain, manufacturing and MRP core around the ledger ([#11](https://github.com/bamr87/djangoerp/pull/11))
- 2026-08-08: Consolidate AMRS accounting core from amrs-project into djangoerp ([#7](https://github.com/bamr87/djangoerp/pull/7))
- 2026-08-07: chore: adopt standardization baseline (claude) ([#6](https://github.com/bamr87/djangoerp/pull/6))
- …and 5 earlier changes.

The full record is in the [commit history](https://github.com/bamr87/djangoerp/commits/main) and the [merged pull requests](https://github.com/bamr87/djangoerp/pulls?q=is%3Apr+is%3Amerged).
