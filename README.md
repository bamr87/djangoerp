# DjangoERP

> Modular Django REST ERP: double-entry accounting, inventory, purchasing, sales, manufacturing and MRP planning.

[![CI](https://github.com/bamr87/djangoerp/actions/workflows/ci.yml/badge.svg)](https://github.com/bamr87/djangoerp/actions/workflows/ci.yml) [![License](https://img.shields.io/github/license/bamr87/djangoerp)](LICENSE)

## Overview

A Django 6 + Django REST Framework ERP built outward from a double-entry accounting core into a modular enterprise system: chart of accounts, journal entries, invoices/payments and async financial reports, plus business partners, products, multi-warehouse inventory with a stock ledger and moving-average costing, purchasing, sales, manufacturing (BOMs and work orders), and an MRP planning engine — the bare minimum for end-to-end order-to-cash, procure-to-pay and make-to-stock processing.

The financial core was consolidated from [`amrs-project`](https://github.com/bamr87/amrs-project) (AMRS), the most mature of several overlapping accounting prototypes in the fleet. The supply-chain and planning modules re-implement proven designs from ERPNext, Odoo, Tryton, InvenTree and frePPLe — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the module map, invariants, posting matrix, MRP algorithm, and exactly which pattern came from where (and why no copyleft code was copied). See [AGENTS.md](AGENTS.md) for the domain rules that must not be broken.

## Features

- Double-entry accounting core: chart of accounts, journal entries, invoices and payments
- Asynchronous financial reports via Celery
- Business partners, products, multi-warehouse inventory with a stock ledger and moving-average costing
- Purchasing, sales, manufacturing (BOMs and work orders) and an MRP planning engine
- JWT-authenticated REST API with Swagger and ReDoc docs

## Quick start

From a clone of [bamr87/djangoerp](https://github.com/bamr87/djangoerp), with Python 3.12+:

```bash
# install
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env

# run on http://localhost:8000/ (migrations are committed)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver

# test
python manage.py test
python -m pytest

# lint and verify
flake8 --max-line-length=120 --exclude=.venv,migrations .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py demo_erp
```

Migrating seeds the five `AccountType` codes (`AS`/`LI`/`EQ`/`RE`/`EX`) that `reports/report_generators.py` hard-codes, via `coa`'s `0002_seed_account_types` data migration — a fresh database has a working chart of accounts from the start. A `createsuperuser` account can open `/admin/` but needs an `accounts.UserRole` row before the API lets it in (every permission class reads `request.user.role.role`).

### Or run it in Docker

The compose stack runs the app against PostgreSQL and Redis rather than the SQLite/eager-Celery defaults, and seeds an admin account (`djangoerp` / `djangoerp` by default, from `.env`) with its `UserRole` row:

```bash
docker compose up                      # web + postgres + redis, autoreload on
docker compose --profile celery up     # ...plus a real Celery worker
docker compose down -v                 # stop and drop the database volume
```

Published ports bind to `127.0.0.1`. The port map, the in-container test loop, and attaching the VS Code debugger (**F5 → Docker: Attach to Django**) are in [docs/DOCKER.md](docs/DOCKER.md).

### See the whole ERP loop run

`python manage.py demo_erp` builds a demo company (chart of accounts, GL mappings, partners, warehouse, a make/buy product structure with a BOM), then runs the full loop through the same services the API uses: sales order → MRP run → planned orders (pegged, lead-time offset) → purchase order + work order → goods receipt → production at rolled-up cost → shipment → customer invoice → payment → supplier settlement → trial balance / balance sheet / income statement. It exits non-zero if the books don't balance. The same flow is pinned with exact numbers in `core/tests.py::EndToEndERPFlowTests`.

## Technology stack

* **Backend:** Python 3.12+, Django 6.1, Django REST Framework
* **Database:** PostgreSQL in production, SQLite by default in development
* **Authentication:** JSON Web Tokens (JWT) via `djangorestframework-simplejwt`
* **API docs:** Swagger & ReDoc (`drf-yasg`)
* **Task queue:** Celery (Redis broker); runs synchronously in dev — reports and MRP runs both use it
* **Testing:** `pytest` / `pytest-django`, plus `manage.py test`

## API overview

Every app exposes a DRF router under `/api/<area>/` — `auth`, `coa`, `journal`, `invoices`, `reports`, `partners`, `products`, `inventory`, `purchasing`, `sales`, `manufacturing`, `mrp`, `company` — plus `/admin/`, `/swagger/`, `/redoc/`, the landing page at `/` and a JSON liveness probe at `/health/`. Authenticate with `POST /api/auth/login/` and send the JWT as a bearer token. Document state changes are actions on the document (`/api/sales/orders/{id}/ship/`, `/api/invoices/invoices/{id}/post/`), never direct status writes.

The full route table — method, path, view, permission classes and summary — is generated from the URLconf in [docs/reference/api-routes.md](docs/reference/api-routes.md) (`python3 tools/gen_api_routes.py --write`). Reports are generated by creating a `ReportTemplate` with the desired `report_type` and then a `SavedReport` from it; MRP runs follow the same async pattern — create a run, poll it, then convert its planned orders.

## Consolidation roadmap

1. **Done** — core ledger (`accounts`, `coa`, `journal`, `invoices`, `reports`) ported from `amrs-project`, with `amrs-django`'s settings-split/test scaffolding adopted and real migrations committed from day one.
2. **Done** — enterprise ERP consolidation: partners, products, inventory (stock ledger + moving-average costing), purchasing, sales, manufacturing and MRP, integrated with the GL through idempotent posting services, with the audit trail wired through every document transition and the whole loop demonstrable via `manage.py demo_erp`.
3. **Planned** — port `amrs-project`'s `edgar` app (SEC/XBRL-to-ledger integration) as a self-contained optional module.
4. **Planned** — port `amrs-django`'s React frontend as DjangoERP's UI, rewired from its Token auth to this project's JWT convention.
5. **Future** — manufacturing depth (routings/work centers, WIP staging, reservations), vendor-bill three-way match, UoM conversions, lot/serial tracking, HR/payroll — see the deliberate-exclusions list in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

`amrs-project`, `afms`, and `amrs-django` remain in place with pointers back to this repo; nothing has been deleted from them.

## Documentation

- [docs/README.md](docs/README.md) — the index of every document in the repo.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map, invariants, GL posting matrix, MRP algorithm, and which open-source pattern came from where.
- [docs/DOCKER.md](docs/DOCKER.md) — the Docker stack, ports and VS Code debugging.
- [docs/reference/api-routes.md](docs/reference/api-routes.md) — every route, generated from the URLconf.
- [AGENTS.md](AGENTS.md) — commands, conventions and the domain rules that must not be broken.

## Project structure

```
djangoerp/           # Project settings (base/dev/prod split), root urls, celery app
core/                # TimestampMixin, DocumentSequence numbering, audit writer, demo company + demo_erp
company/             # Company (the entity whose books these are), the landing page and /health/
accounts/            # UserRole (admin/accountant/viewer/auditor), AuditLog, JWT login, permissions
coa/                 # Chart of accounts: AccountType + self-referencing Account tree
journal/             # JournalEntry + JournalLine — the double-entry ledger — and the posting service
invoices/            # Invoice, InvoiceLineItem, Payment — AR with GL posting actions
reports/             # ReportTemplate + SavedReport, and report_generators.py (the report math)
partners/            # BusinessPartner — customers/suppliers with AR/AP control accounts
products/            # UnitOfMeasure, ProductCategory (GL mappings), Product (buy/make, MRP parameters)
inventory/           # Warehouse, StockMove ledger, StockLevel cache with moving-average costing
purchasing/          # PurchaseOrder + lines, confirm/receive flow (stock in + AP accrual)
sales/               # SalesOrder + lines, confirm/ship/invoice flow (COGS + billing)
manufacturing/       # BillOfMaterials + BOMLine, WorkOrder complete flow (backflush + rolled-up cost)
mrp/                 # MRPRun + PlannedOrder, the planning engine, plan-to-order conversion
docs/                # ARCHITECTURE.md, DOCKER.md, the generated reference/ and the index
docker/              # Container entrypoint, admin bootstrap, dev server/worker launchers, healthchecks
tools/               # gen_api_routes.py (route reference), docs_index.py + unwrap-prose.py (vendored from the hub)
```

## Development

Build, test and lint commands are in [Quick start](#quick-start). CI is the hub's shared `standard-ci.yml` gate, called from [`.github/workflows/ci.yml`](.github/workflows/ci.yml). After changing a route or a view docstring, regenerate the route reference with `python3 tools/gen_api_routes.py --write` — `core/test_docs_reference.py` fails while it is stale. Full commands, conventions and gotchas for humans and AI agents live in [AGENTS.md](AGENTS.md).

## Contributing

Contributions are welcome via pull request. Use [Conventional Commits](https://www.conventionalcommits.org/) and keep the CI checks green. The fleet-wide workflow is described in the hub's [CONTRIBUTING.md](https://github.com/bamr87/bamr87/blob/main/CONTRIBUTING.md). AI agents should start with [AGENTS.md](AGENTS.md).

## Changelog

Notable changes are recorded in [CHANGELOG.md](CHANGELOG.md); the full record is in the [commit history](https://github.com/bamr87/djangoerp/commits/main).

## License

Released under the [MIT License](LICENSE).

---

<!-- bamr87-portfolio-footer -->
Part of the [bamr87 portfolio](https://github.com/bamr87) — browse every project on [Amr's GitHub profile](https://github.com/bamr87).
