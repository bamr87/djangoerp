# Changelog

## [0.3.0](https://github.com/bamr87/djangoerp/compare/v0.2.0...v0.3.0) (2026-11-18)


### Added

* **company:** add /healthz and /readyz probes, drop debug from /health/ ([89c9d27](https://github.com/bamr87/djangoerp/commit/89c9d270d34bf28be7b25e2051031640aaaabc75))
* **company:** add GET /version and a skip link to the landing page ([8dd0392](https://github.com/bamr87/djangoerp/commit/8dd0392ef7da96958ced680856980d2176013ff3)), closes [#33](https://github.com/bamr87/djangoerp/issues/33)
* **company:** feedback widget, /version and landing-page a11y ([07e398e](https://github.com/bamr87/djangoerp/commit/07e398e34388ea71b44808e68ef179e042d55ff9))
* **company:** mount the fleet feedback widget on the landing page ([d1c1fb3](https://github.com/bamr87/djangoerp/commit/d1c1fb30b281b3bce71005524e0370776257dd17)), closes [#32](https://github.com/bamr87/djangoerp/issues/32)
* **core:** stamp X-Request-Id and X-App-Version on every response ([ad10d60](https://github.com/bamr87/djangoerp/commit/ad10d60465b744c6598066b23fd04872d5824aaf))
* **journal:** reverse posted manual entries through the API (DJERP-007) ([b218979](https://github.com/bamr87/djangoerp/commit/b218979dca993ba1b01588e607859c70b5af3a3b))
* **journal:** reverse posted manual entries through the API (DJERP-007) ([be45815](https://github.com/bamr87/djangoerp/commit/be4581542763b90874cd0119e902381c3e3f3461))
* **ops:** health probes, request-id headers and a required prod SECRET_KEY ([b8a9543](https://github.com/bamr87/djangoerp/commit/b8a95430c0cbdd01739fe6cc5ced89c3cf9abb2e))


### Fixed

* **api:** refuse DELETE of posted history ([07ff42c](https://github.com/bamr87/djangoerp/commit/07ff42cbfb82e264e3791118222fd04980a2fc74))
* **api:** refuse DELETE of posted history ([4187ebf](https://github.com/bamr87/djangoerp/commit/4187ebfce4c58cdfee6a0e5cbf5ac1be7a5b4f18))
* **api:** require accountant for ledger, invoice, payment, company and report writes ([935bd79](https://github.com/bamr87/djangoerp/commit/935bd794a5e49923f87f6b6575b6998c78a39d1a))
* **api:** require accountant for ledger, invoice, payment, company and report writes ([028d106](https://github.com/bamr87/djangoerp/commit/028d106eb971eb549c5ddd25578d367f48d81a86))
* **company:** landing page contrast, table semantics, setup steps and meta ([ae572c9](https://github.com/bamr87/djangoerp/commit/ae572c9000044a4b431b3d771fca26ae81ae4eea)), closes [#42](https://github.com/bamr87/djangoerp/issues/42)
* **journal:** protect and validate ledger lines, server-assign entry numbers ([226b718](https://github.com/bamr87/djangoerp/commit/226b718a31baedcc72a9624a3f2cc4e8546485a4))
* **journal:** protect and validate ledger lines, server-assign entry numbers ([14a93c1](https://github.com/bamr87/djangoerp/commit/14a93c13d7995c220a12498785b31161f1c1fb47))
* **settings:** refuse an unset, empty or django-insecure SECRET_KEY in production ([7fe2752](https://github.com/bamr87/djangoerp/commit/7fe2752091ee8887b4dfc3b3876f2a43bc370c42))

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
