# DjangoERP

A Django 6 + Django REST Framework ERP built outward from a double-entry accounting core into a modular enterprise system: chart of accounts, journal entries, invoices/payments and async financial reports, plus business partners, products, multi-warehouse inventory with a stock ledger and moving-average costing, purchasing, sales, manufacturing (BOMs and work orders), and an MRP planning engine — the bare minimum for end-to-end order-to-cash, procure-to-pay and make-to-stock processing.

The financial core was consolidated from [`amrs-project`](https://github.com/bamr87/amrs-project) (AMRS), the most mature of several overlapping accounting prototypes in the fleet. The supply-chain and planning modules re-implement proven designs from ERPNext, Odoo, Tryton, InvenTree and frePPLe — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the module map, invariants, posting matrix, MRP algorithm, and exactly which pattern came from where (and why no copyleft code was copied). See [CLAUDE.md](CLAUDE.md) for the domain rules that must not be broken.

## Technology stack

* **Backend:** Python 3.12+, Django 6.1, Django REST Framework
* **Database:** PostgreSQL in production, SQLite by default in development
* **Authentication:** JSON Web Tokens (JWT) via `djangorestframework-simplejwt`
* **API docs:** Swagger & ReDoc (`drf-yasg`)
* **Task queue:** Celery (Redis broker); runs synchronously in dev — reports and MRP runs both use it
* **Testing:** `pytest` / `pytest-django`, plus `manage.py test`

## Getting started

```bash
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env

python manage.py migrate            # migrations are committed — no app-label dance needed
python manage.py createsuperuser

python manage.py runserver          # http://localhost:8000/
```

Migrating seeds the five `AccountType` codes (`AS`/`LI`/`EQ`/`RE`/`EX`) that `apps/reports/report_generators.py` hard-codes, via `coa`'s `0002_seed_account_types` data migration — a fresh database has a working chart of accounts from the start.

### Or run it in Docker

The compose stack runs the app against PostgreSQL and Redis rather than the SQLite/eager-Celery defaults, so local development matches production topology. Nothing needs to be installed beyond Docker.

```bash
docker compose up                      # web + postgres + redis, autoreload on
docker compose --profile celery up     # ...plus a real Celery worker
docker compose down -v                 # stop and drop the database volume
```

Migrations are applied by the container entrypoint on every start, so `http://localhost:8000/` serves the landing page as soon as `up` returns.

The entrypoint also seeds an admin account from `.env` (copy `.env.example` if you have not already) — `djangoerp` / `djangoerp` by default, good for both `http://localhost:8000/admin/` and `POST /api/auth/login/`. It creates the `accounts.UserRole` row alongside the superuser, which `manage.py createsuperuser` does not: every DRF permission class reads `request.user.role.role`, so a superuser without that row can open the admin but is denied by `IsAdmin`, `IsAccountant` and `IsAuditor` on every API endpoint. Existing accounts are never touched — rotate the password with `docker compose exec web python manage.py changepassword djangoerp`, not by editing `.env`.

Published ports bind to `127.0.0.1`, because an open debugpy port is remote code execution and this stack ships a known admin password. Set `BIND_HOST=0.0.0.0` in `.env` if you deliberately want it reachable from your network.

| Host port | Service |
| --- | --- |
| `8000` | Django (`/`, `/admin/`, `/api/…`, `/swagger/`, `/health/`) |
| `5678` | debugpy — Django |
| `5679` | debugpy — Celery worker (`celery` profile) |
| `5680` | debugpy — one-shot commands (tests, `demo_erp`) |
| `5433` | PostgreSQL (off `5432` so it never collides with a local instance) |
| `6380` | Redis (off `6379`, same reason) |

### Debugging in VS Code

Press **F5** and pick **Docker: Attach to Django**. The launch configuration's `preLaunchTask` builds and starts the stack with the debug adapter enabled and blocks on the compose healthcheck, so attaching cannot race startup — a cold machine to a live breakpoint is one keystroke.

Configurations are defined in [.vscode/launch.json](.vscode/launch.json), the compose plumbing they call in [.vscode/tasks.json](.vscode/tasks.json):

| Configuration | Attaches to |
| --- | --- |
| `Docker: Attach to Django` | The `web` container — views, serializers, `services.py` |
| `Docker: Attach to Celery worker` | The `celery` profile worker — `apps/mrp/engine.py`, `apps/reports/report_generators.py` as real async tasks |
| `Docker: Attach to one-shot command` | A `docker: debug …` task (tests, pytest, `demo_erp`), which waits for you to attach before running |
| `Docker: Django + Celery worker` | Both long-running containers at once |
| `Local: …` | The plain virtualenv workflow, for when Docker is overkill |

Two behaviours worth knowing. Debugging runs Django with `--noreload`, because Django's autoreloader forks a child to serve requests while the debugger stays attached to the parent — so breakpoints would silently never hit. Use the **docker: restart web** task to pick up code changes during a debug session, or plain `docker compose up` when you want autoreload and no debugger. Separately, dev settings run Celery tasks eagerly inside the web process, so report generation and MRP runs are hit by the Django debugger by default; the `celery` profile flips that off and dispatches them to the worker for real.

Tests, the migration-drift gate and the demo loop all run in the container too — see the `docker: test`, `docker: pytest`, `docker: check` and `docker: demo_erp` tasks.

### See the whole ERP loop run

```bash
python manage.py demo_erp
```

builds a demo company (chart of accounts, GL mappings, partners, warehouse, a make/buy product structure with a BOM), then runs the full loop through the same services the API uses: sales order → MRP run → planned orders (pegged, lead-time offset) → purchase order + work order → goods receipt → production at rolled-up cost → shipment → customer invoice → payment → supplier settlement → trial balance / balance sheet / income statement. It exits non-zero if the books don't balance. The same flow is pinned with exact numbers in `apps/core/tests.py::EndToEndERPFlowTests`.

## Project structure

```
manage.py            # Django entry point; every command below runs through it
djangoerp/           # Project settings (base/dev/prod split), root urls, celery app
apps/                # Django applications — INSTALLED_APPS entries are apps.<name>
  core/              # TimestampMixin, DocumentSequence numbering, audit writer, demo company + demo_erp
  company/           # Company (the entity whose books these are), the landing page and /health/
  accounts/          # UserRole (admin/accountant/viewer/auditor), AuditLog, JWT login, permissions
  coa/               # Chart of accounts: AccountType + self-referencing Account tree
  journal/           # JournalEntry + JournalLine — the double-entry ledger — and the posting service
  invoices/          # Invoice, InvoiceLineItem, Payment — AR with GL posting actions
  reports/           # ReportTemplate + SavedReport, and report_generators.py (the report math)
  partners/          # BusinessPartner — customers/suppliers with AR/AP control accounts
  products/          # UnitOfMeasure, ProductCategory (GL mappings), Product (buy/make, MRP parameters)
  inventory/         # Warehouse, StockMove ledger, StockLevel cache with moving-average costing
  purchasing/        # PurchaseOrder + lines, confirm/receive flow (stock in + AP accrual)
  sales/             # SalesOrder + lines, confirm/ship/invoice flow (COGS + billing)
  manufacturing/     # BillOfMaterials + BOMLine, WorkOrder complete flow (backflush + rolled-up cost)
  mrp/               # MRPRun + PlannedOrder, the planning engine, plan-to-order conversion
docs/                # ARCHITECTURE.md — module map, invariants, posting matrix, MRP algorithm
docker/              # Container entrypoint, admin bootstrap, dev server/worker launchers, healthchecks
tools/               # unwrap-prose.py — the markdown CI gate, vendored from the hub; do not edit
```

## API overview

| Path | Description |
| --- | --- |
| `/api/auth/login/`, `/api/auth/refresh/` | JWT obtain/refresh; `users/`, `roles/`, `audit-logs/` |
| `/api/coa/accounts/` | Chart of accounts; `/hierarchy/` for the tree view; `account-types/` |
| `/api/journal/entries/` | Journal entries (balanced, double-entry; posted entries immutable) |
| `/api/invoices/invoices/`, `/api/invoices/payments/` | AR documents; `{id}/post/` posts to the ledger |
| `/api/reports/templates/`, `/api/reports/saved-reports/` | Async reports; `saved-reports/{id}/regenerate/` |
| `/api/partners/partners/` | Customers and suppliers with AR/AP control accounts |
| `/api/products/products/`, `categories/`, `uoms/` | Item master, GL mappings, units |
| `/api/inventory/warehouses/`, `stock-moves/`, `stock-levels/` | Stock ledger; `stock-moves/{id}/complete/` applies a move |
| `/api/purchasing/orders/` | `{id}/confirm/`, `{id}/receive/`, `{id}/cancel/` |
| `/api/sales/orders/` | `{id}/confirm/`, `{id}/ship/`, `{id}/invoice/`, `{id}/cancel/` |
| `/api/manufacturing/boms/`, `work-orders/` | `{id}/confirm/`, `{id}/start/`, `{id}/complete/`, `{id}/availability/` |
| `/api/mrp/runs/`, `planned-orders/` | Create a run to plan (async); `runs/{id}/convert/` creates draft POs/WOs |
| `/api/company/companies/` | The legal entity whose books this installation keeps |
| `/admin/`, `/swagger/`, `/redoc/` | Django admin and API schema docs |
| `/`, `/health/` | Landing page (company charter + API index) and a JSON liveness probe |

Reports are generated by creating a `ReportTemplate` with the desired `report_type` and then a `SavedReport` from it; MRP runs follow the same async pattern — create a run, poll it, then convert its planned orders.

## Consolidation roadmap

1. **Done** — core ledger (`accounts`, `coa`, `journal`, `invoices`, `reports`) ported from `amrs-project`, with `amrs-django`'s settings-split/test scaffolding adopted and real migrations committed from day one.
2. **Done** — enterprise ERP consolidation: partners, products, inventory (stock ledger + moving-average costing), purchasing, sales, manufacturing and MRP, integrated with the GL through idempotent posting services, with the audit trail wired through every document transition and the whole loop demonstrable via `manage.py demo_erp`.
3. **Planned** — port `amrs-project`'s `edgar` app (SEC/XBRL-to-ledger integration) as a self-contained optional module.
4. **Planned** — port `amrs-django`'s React frontend as DjangoERP's UI, rewired from its Token auth to this project's JWT convention.
5. **Future** — manufacturing depth (routings/work centers, WIP staging, reservations), vendor-bill three-way match, UoM conversions, lot/serial tracking, HR/payroll — see the deliberate-exclusions list in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

`amrs-project`, `afms`, and `amrs-django` remain in place with pointers back to this repo; nothing has been deleted from them.
