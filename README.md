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

Migrating seeds the five `AccountType` codes (`AS`/`LI`/`EQ`/`RE`/`EX`) that `reports/report_generators.py` hard-codes, via `coa`'s `0002_seed_account_types` data migration — a fresh database has a working chart of accounts from the start.

### See the whole ERP loop run

```bash
python manage.py demo_erp
```

builds a demo company (chart of accounts, GL mappings, partners, warehouse, a make/buy product structure with a BOM), then runs the full loop through the same services the API uses: sales order → MRP run → planned orders (pegged, lead-time offset) → purchase order + work order → goods receipt → production at rolled-up cost → shipment → customer invoice → payment → supplier settlement → trial balance / balance sheet / income statement. It exits non-zero if the books don't balance. The same flow is pinned with exact numbers in `core/tests.py::EndToEndERPFlowTests`.

## Project structure

```
djangoerp/           # Project settings (base/dev/prod split), root urls, celery app
core/                # TimestampMixin, DocumentSequence numbering, audit writer, demo company + demo_erp
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
docs/                # ARCHITECTURE.md — module map, invariants, posting matrix, MRP algorithm
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
| `/admin/`, `/swagger/`, `/redoc/` | Django admin and API schema docs |

Reports are generated by creating a `ReportTemplate` with the desired `report_type` and then a `SavedReport` from it; MRP runs follow the same async pattern — create a run, poll it, then convert its planned orders.

## Consolidation roadmap

1. **Done** — core ledger (`accounts`, `coa`, `journal`, `invoices`, `reports`) ported from `amrs-project`, with `amrs-django`'s settings-split/test scaffolding adopted and real migrations committed from day one.
2. **Done** — enterprise ERP consolidation: partners, products, inventory (stock ledger + moving-average costing), purchasing, sales, manufacturing and MRP, integrated with the GL through idempotent posting services, with the audit trail wired through every document transition and the whole loop demonstrable via `manage.py demo_erp`.
3. **Planned** — port `amrs-project`'s `edgar` app (SEC/XBRL-to-ledger integration) as a self-contained optional module.
4. **Planned** — port `amrs-django`'s React frontend as DjangoERP's UI, rewired from its Token auth to this project's JWT convention.
5. **Future** — manufacturing depth (routings/work centers, WIP staging, reservations), vendor-bill three-way match, UoM conversions, lot/serial tracking, HR/payroll — see the deliberate-exclusions list in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

`amrs-project`, `afms`, and `amrs-django` remain in place with pointers back to this repo; nothing has been deleted from them.
