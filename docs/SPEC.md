# DjangoERP functional specification

This is the normative contract of the system: what each surface accepts, what it guarantees, and what it refuses. It is the companion to [ARCHITECTURE.md](ARCHITECTURE.md), which explains *why* the system is shaped the way it is — module map, invariant rationale, GL posting matrix, MRP algorithm and the open-source designs adopted. Where this document and the code disagree, the code wins and this document is a bug; every claim below is anchored to the file that implements it.

Capability status — what exists, what is partial, what is absent — lives in [FEATURES.md](FEATURES.md).

## 1. Scope and conformance

DjangoERP keeps the books of a single legal entity ([`Company`](../apps/company/models.py)) and runs three operational loops against a double-entry ledger: order-to-cash, procure-to-pay and make-to-stock, planned by an MRP engine.

An implementation conforms to this specification when the verification gates in section 11 pass. Those gates are executable, not aspirational: `python manage.py demo_erp` fails non-zero if the books do not balance, and [apps/core/tests.py](../apps/core/tests.py) pins the same flow with exact numbers.

Out of scope by deliberate design decision — routings and finite capacity, WIP staging, vendor-bill three-way matching, stock reservations, UoM conversion, product variants, lot/serial tracking, multi-currency, backdated valuation reposting, credit notes and per-location bins. Each exclusion names its upgrade path in [ARCHITECTURE.md](ARCHITECTURE.md#deliberate-exclusions-roadmap).

## 2. Actors, roles and the permission model

Authentication is JWT-only. `DEFAULT_AUTHENTICATION_CLASSES` is SimpleJWT and `DEFAULT_PERMISSION_CLASSES` is `IsAuthenticated` ([djangoerp/settings/base.py](../djangoerp/settings/base.py)), so every endpoint requires a bearer token unless a viewset overrides it. Access tokens live 60 minutes and refresh tokens 7 days by default, both overridable via environment (section 10).

`POST /api/auth/login/` returns `access`, `refresh` and a `user` object carrying `id`, `username`, `email`, `first_name`, `last_name` and `role` ([apps/accounts/serializers.py](../apps/accounts/serializers.py)).

### 2.1 Roles

Roles live on [`UserRole`](../apps/accounts/models.py), a `OneToOne` to `django.contrib.auth.User`. There is no auto-creating signal — **a Django superuser is not an API user**. An account created by `manage.py createsuperuser` can open `/admin/` but is refused by every role-gated endpoint until a `UserRole` row exists. [docker/bootstrap-admin.py](../docker/bootstrap-admin.py) creates both for the Docker stack; [apps/core/demo.py](../apps/core/demo.py) does it for the demo user.

| Role | Intent |
| --- | --- |
| `admin` | Full access; implied by every other role check |
| `accountant` | May write transactional documents and post to the ledger |
| `viewer` | Read-only intent (see the gap in 2.4) |
| `auditor` | Read-only audit intent (not currently wired to any endpoint) |

### 2.2 Permission classes

All four classes in [apps/accounts/permissions.py](../apps/accounts/permissions.py) read `request.user.role.role` inside `try/except AttributeError`, so a user with no `UserRole` row is denied rather than crashing. Never re-implement a role check inline in a viewset.

| Class | Grants | Notes |
| --- | --- | --- |
| `IsAdmin` | `admin` | |
| `IsAccountant` | `accountant`, `admin` | Gates `has_permission`, so it governs reads as well as writes |
| `IsAuditor` | `auditor`, `admin` | Defined but not referenced by any viewset |
| `IsAdminOrSelf` | object-level; safe methods always, own object, or `admin` | Used by `UserViewSet` |

### 2.3 Endpoint permission matrix

Master data is writable by any authenticated user; transactional documents require `IsAccountant`. Two viewsets escalate per action rather than per class — [apps/invoices/views.py](../apps/invoices/views.py) applies `IsAccountant` only to `post_to_ledger`, so drafting an invoice is open but posting it to the general ledger is not.

| Surface | Permission | Source |
| --- | --- | --- |
| `/api/auth/users/` | `IsAdminOrSelf` | [apps/accounts/views.py](../apps/accounts/views.py) |
| `/api/auth/roles/`, `/api/auth/audit-logs/` | `IsAdmin` | [apps/accounts/views.py](../apps/accounts/views.py) |
| `/api/coa/`, `/api/partners/`, `/api/products/`, `/api/company/`, `/api/inventory/warehouses/` | `IsAuthenticated` | master data |
| `/api/inventory/stock-levels/`, `/api/journal/lines/` | `IsAuthenticated` (read-only viewsets) | derived data |
| `/api/manufacturing/boms/` | `IsAuthenticated` | master data |
| `/api/journal/entries/`, `/api/inventory/stock-moves/`, `/api/purchasing/orders/`, `/api/sales/orders/`, `/api/manufacturing/work-orders/`, `/api/mrp/` | `IsAccountant` | transactional |
| `/api/invoices/invoices/`, `/api/invoices/payments/` | `IsAuthenticated`; `IsAccountant` on `{id}/post/` only | [apps/invoices/views.py](../apps/invoices/views.py) |
| `/api/reports/templates/` | `IsAuthenticated` to read, `IsAccountant` to write | [apps/reports/views.py](../apps/reports/views.py) |
| `/api/reports/saved-reports/` | `IsAuthenticated`, row-scoped to `created_by` unless admin/accountant | [apps/reports/views.py](../apps/reports/views.py) |

### 2.4 Known gaps in the role model

`IsAccountant` gates `has_permission`, which DRF consults for reads as well as writes. A `viewer` therefore receives `403` on `GET /api/journal/entries/`, `/api/sales/orders/`, `/api/purchasing/orders/` and `/api/inventory/stock-moves/`, while receiving `200` on `GET /api/journal/lines/`, `/api/inventory/stock-levels/`, `/api/coa/accounts/` and `/api/invoices/invoices/`. The ledger is readable line-by-line but not entry-by-entry, which is inconsistent with the role's name and intent. Resolving it is a policy decision — either a read-only role genuinely reads the transactional documents, or it is renamed — and is deliberately left open rather than changed silently.

`IsAuditor` is implemented and untested against any route because no viewset references it.

## 3. Data dictionary

Twenty-five models across fourteen apps. Every app label is the bare name (`journal`, `coa`) even though the Python package is `apps.journal` — migrations, `ForeignKey('coa.Account')` strings and the admin all key off the label.

| App | Model | Natural key | Enforced constraint |
| --- | --- | --- | --- |
| `core` | `DocumentSequence` | `prefix` unique | Atomic numbering, see section 4 |
| `company` | `Company` | — | The entity whose books these are |
| `accounts` | `UserRole` | `user` unique | `OneToOne` to `auth.User` |
| `accounts` | `AuditLog` | — | Append-only event trail |
| `coa` | `AccountType` | `code`, `name` unique | Codes `AS`/`LI`/`EQ`/`RE`/`EX` are load-bearing |
| `coa` | `Account` | `code` unique | Self-referencing tree; `parent_account` and `account_type` are `PROTECT` |
| `journal` | `JournalEntry` | `entry_number`, `source_reference` unique | `source_reference` uniqueness is the idempotency key |
| `journal` | `JournalLine` | — | `debit`/`credit` are `Decimal(20,2)` |
| `invoices` | `Invoice` | `invoice_number` unique | |
| `invoices` | `InvoiceLineItem` | — | |
| `invoices` | `Payment` | `payment_reference` unique | |
| `reports` | `ReportTemplate` | — | `report_type` drives generator dispatch |
| `reports` | `SavedReport` | — | `status` defaults to `generating` |
| `partners` | `BusinessPartner` | `code` unique | One row carries `is_customer` and/or `is_supplier` |
| `products` | `UnitOfMeasure` | `code` unique | |
| `products` | `ProductCategory` | `name` unique | Carries the inventory/revenue/COGS account mapping |
| `products` | `Product` | `sku` unique | `procurement_type` is `buy` or `make`; `product_type` is `stocked` or `service` |
| `inventory` | `Warehouse` | `code` unique | |
| `inventory` | `StockMove` | `reference` unique | Append-only stock ledger |
| `inventory` | `StockLevel` | — | `UniqueConstraint: unique_stock_level` per product/warehouse |
| `purchasing` | `PurchaseOrder` / `PurchaseOrderLine` | `number` unique | |
| `sales` | `SalesOrder` / `SalesOrderLine` | `number` unique | |
| `manufacturing` | `BillOfMaterials` | — | `UniqueConstraint: one_active_bom_per_product` (partial, on `is_active`) |
| `manufacturing` | `BOMLine` | — | `UniqueConstraint: unique_component_per_bom` |
| `manufacturing` | `WorkOrder` | `number` unique | |
| `mrp` | `MRPRun` | `number` unique | |
| `mrp` | `PlannedOrder` | — | Carries `demand_reference` pegging |

Decimal precision is normative and money is never a `FloatField`: quantities `Decimal(12,3)`, unit costs and prices `Decimal(12,4)`, monetary document totals 2dp, and journal lines the wider `Decimal(20,2)`. GL amounts are quantized to the cent with `Decimal('0.01')` before posting.

## 4. Document numbering

Every document number is claimed atomically from [`DocumentSequence.next_number(prefix)`](../apps/core/models.py). Never construct a number any other way.

| Prefix | Document | Claimed by |
| --- | --- | --- |
| `JE` | Journal entry | [apps/journal/services.py](../apps/journal/services.py) |
| `SM` | Stock move | [apps/inventory/services.py](../apps/inventory/services.py) |
| `GRN` | Goods receipt | [apps/purchasing/services.py](../apps/purchasing/services.py) |
| `PO` | Purchase order | [apps/purchasing/services.py](../apps/purchasing/services.py) |
| `SO` | Sales order | [apps/sales/services.py](../apps/sales/services.py) |
| `SHP` | Shipment | [apps/sales/services.py](../apps/sales/services.py) |
| `INV` | Customer invoice | [apps/invoices/services.py](../apps/invoices/services.py) |
| `PAY` | Payment | [apps/invoices/services.py](../apps/invoices/services.py) |
| `WO` | Work order | [apps/manufacturing/services.py](../apps/manufacturing/services.py) |
| `MRP` | MRP run | [apps/mrp/views.py](../apps/mrp/views.py) |

## 5. Ledger contracts

**Double entry.** `JournalEntrySerializer.validate` in [apps/journal/serializers.py](../apps/journal/serializers.py) rejects any entry whose debits and credits differ, and any entry with no lines. Both return `400`.

**Only posted rows exist.** `JournalEntry.status` is one of `draft`, `posted`, `voided`; `StockMove.status` must be `done` to affect anything. Every report generator filters `entry__status='posted'`, and `StockLevel` caches the sum of done moves only.

**One write path for system postings.** [`journal.services.post_entry`](../apps/journal/services.py) enforces balance, quantizes to the cent, claims a `JE` number and is idempotent per `source_reference`. Never create a system journal entry with raw ORM calls.

**Idempotency keys.** One business event yields one entry, enforced by the unique `source_reference` column:

| Event | Key |
| --- | --- |
| Goods receipt | `grn:{grn_number}` |
| Shipment | `shipment:{shipment_number}` |
| Work order completion | `wo:{number}:complete` |
| Invoice posting | `invoice:{invoice_number}` |
| Payment posting | `payment:{payment_reference}` |

**Immutability.** A posted journal entry may only be voided; a done stock move, a posted invoice and a posted payment all reject edits with `400`. Corrections are reversing entries and opposing moves, never edits.

**Valuation.** Stock is valued at moving average per product per warehouse. Receipts re-weight `average_cost`; issues leave at the current average and raise `InsufficientStockError` rather than go negative. Completed moves snapshot `unit_cost` and `total_value`, and GL postings use exactly `move.total_value`, so the stock ledger and the general ledger cannot drift apart. `inventory.services.rebuild_stock_levels()` reconstructs the cache from the ledger.

**Normal balances.** Assets and expenses are debit-normal; liabilities, equity and revenue are credit-normal. `calculate_account_balances` applies that signing. `generate_trial_balance` deliberately does not — a trial balance lists raw debit-minus-credit balances so the two columns agree. The balance sheet folds open P&L into equity via `current_period_earnings`, without which no book with posted P&L would ever foot.

## 6. Document state machines

State is written only by the named transition functions in each app's `services.py`. Views call services from `@action` endpoints; serializers reject edits on non-draft documents; nothing else assigns `status`.

| Document | States | Transitions |
| --- | --- | --- |
| Purchase order | `draft` → `confirmed` → `partially_received` → `received`, or `cancelled` | `confirm`, `receive`, `cancel` |
| Sales order | `draft` → `confirmed` → `partially_shipped` → `shipped` → `invoiced`, or `cancelled` | `confirm`, `ship`, `invoice`, `cancel` |
| Work order | `draft` → `confirmed` → `in_progress` → `completed`, or `cancelled` | `confirm`, `start`, `complete`, `cancel` |
| Stock move | `draft` → `done`, or `cancelled` | `complete`, `cancel` |
| Invoice / payment | `draft` → `posted` | `post` |
| MRP run | `pending` → `running` → `completed` \| `failed` | asynchronous |
| Planned order | `planned` → `converted`, or `cancelled` | `convert`, `cancel` |

A partial `receive` or `ship` accepts a `{line_id: quantity}` map and leaves the order in its `partially_*` state; omitting the map fulfils everything open. The order status is rolled up from the line objects mutated inside the service, **not** re-read from `order.lines.all()` — the viewsets prefetch `lines`, so a manager re-read is served from the prefetch cache and reports pre-transition quantities. Pinned by `test_ship_via_api_marks_order_fully_shipped` ([apps/sales/tests.py](../apps/sales/tests.py)) and `test_receive_via_api_marks_order_fully_received` ([apps/purchasing/tests.py](../apps/purchasing/tests.py)).

Every transition writes an audit row through [`core.audit.log_event`](../apps/core/audit.py). A complete order-to-cash plus procure-to-pay plus make-to-stock cycle produces 17 audit rows.

## 7. Validation contract

Business validation lives in serializer `validate()`; flow preconditions live in `services.py` and raise DRF `ValidationError`. Both surface as `400` with a message naming the problem. The following are verified live by [tools/e2e-api-smoke.sh](../tools/e2e-api-smoke.sh):

| Rule | Rejection |
| --- | --- |
| Journal entry debits ≠ credits | `400` |
| Journal entry with no lines | `400` |
| Account made its own parent (or any ancestor cycle) | `400` |
| BOM whose component graph loops back to the parent | `400` |
| Editing a posted invoice | `400` |
| Editing a posted journal entry | `400` |
| Shipping more than a line's open quantity | `400` |
| Shipping without sufficient stock | `400`, `InsufficientStockError` |
| Invoicing an order that is not fully shipped | `400` |
| Posting without a configured account mapping | `400` naming the missing account |
| Any request without a bearer token | `401` |
| A role-gated surface with an insufficient role | `403` |

Account resolution is explicit configuration and never guessed: `ProductCategory` supplies inventory/revenue/COGS, `BusinessPartner` supplies receivable/payable, `Payment` supplies the deposit account, and a missing mapping is a configuration error naming the account.

## 8. API conventions

One `ModelViewSet` per model, registered on a `DefaultRouter` in the app's `urls.py`, included under `/api/<area>/`. Read-only surfaces (`JournalLine`, `AuditLog`, `StockLevel`, `PlannedOrder`) use `ReadOnlyModelViewSet`. State transitions are `@action` endpoints delegating to `services.py`, never named after a bare HTTP verb — `post_to_ledger` with `url_path='post'`, not `def post`.

`DjangoFilterBackend`, `SearchFilter` and `OrderingFilter` plus `PageNumberPagination` at `PAGE_SIZE=20` are configured project-wide, which is why viewsets declare `filterset_fields` and `search_fields` without repeating `filter_backends`. List responses are `{count, next, previous, results}`.

Nested writes (journal lines, invoice line items, order lines, BOM lines) delete and recreate children in `update()`, and only while the parent is draft. Ownership fields are always set server-side and marked read-only — either `perform_create(serializer.save(created_by=self.request.user))` in the view or `validated_data['created_by'] = self.context['request'].user` in the serializer.

Interactive documentation is served at `/swagger/` and `/redoc/`, with the raw schema at `/swagger.json`. `/health/` is an unauthenticated liveness probe returning `{status, app, version, debug}` and touching no models.

## 9. Asynchronous processing

Two workloads run as Celery tasks, and both follow the same contract: the row is created in a pending state, the task is dispatched from the view, and the task flips the row to a terminal state with its payload or an error message.

| Task | Row | States | Dispatched by |
| --- | --- | --- | --- |
| `apps.reports.report_generators.generate_report` | `SavedReport` | `generating` → `completed` \| `failed` | `perform_create` and the `regenerate` action ([apps/reports/views.py](../apps/reports/views.py)) |
| `apps.mrp.engine.run_mrp` | `MRPRun` | `pending` → `running` → `completed` \| `failed` | `perform_create` ([apps/mrp/views.py](../apps/mrp/views.py)) |

Creating the row **must** dispatch the task. `SavedReport.status` defaults to `generating`, so a create path that skips the dispatch returns a healthy-looking `201` and then hangs forever; pinned by `test_create_saved_report_dispatches_the_generator` ([apps/reports/tests.py](../apps/reports/tests.py)).

Task names derive from the module path, so they are `apps.reports.report_generators.generate_report` and `apps.mrp.engine.run_mrp`. Moving an app changes its task names — drain the queue across such a deploy.

`CELERY_TASK_ALWAYS_EAGER=True` in dev settings runs both inline without a worker. The `celery` compose profile sets it `False` on both services so the worker actually receives work.

Report types are `balance_sheet`, `income_statement`, `cash_flow`, `general_ledger`, `trial_balance` and `custom` ([apps/reports/models.py](../apps/reports/models.py)). Adding one means adding a choice to `ReportTemplate.REPORT_TYPES` plus a branch and generator function in [apps/reports/report_generators.py](../apps/reports/report_generators.py).

## 10. Configuration

Settings split `base` / `dev` / `prod`, selected by whether `DJANGO_SETTINGS_MODULE` ends in `.prod`. New configuration goes in `base.py` as `env(...)` with a safe default and must be documented in [.env.example](../.env.example).

| Variable | Default | Effect |
| --- | --- | --- |
| `SECRET_KEY` | insecure placeholder | Must be set in production |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | |
| `DATABASE_URL` | `sqlite:///db.sqlite3` | PostgreSQL in the Docker stack |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker and result backend |
| `CELERY_TASK_ALWAYS_EAGER` | `True` in dev, `False` in base | Run tasks inline |
| `CELERY_TASK_EAGER_PROPAGATES` | mirrors the above | |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | `60` | |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | `7` | |
| `SECURE_SSL_REDIRECT` | `True` in prod | `prod.py` also enables HSTS and secure cookies |
| `CORS_ALLOWED_ORIGINS` | localhost:3000 | |
| `DJANGO_SUPERUSER_*` | `djangoerp`/`djangoerp` | Seeds the admin plus its `UserRole` in Docker |

## 11. Verification gates

A change is done when all of these pass. They are the same gates named in [CLAUDE.md](../CLAUDE.md).

| Gate | Command |
| --- | --- |
| System check | `python manage.py check` |
| No migration drift | `python manage.py makemigrations --check --dry-run` |
| Django test runner | `python manage.py test` |
| pytest | `python -m pytest` |
| End-to-end books balance | `python manage.py demo_erp` |
| Live API conformance | `bash tools/e2e-api-smoke.sh` against a running stack |
| Lint | `flake8 --max-line-length=120 --exclude=.venv,migrations .` and `isort --check-only .` |
| Markdown house style | `python3 tools/unwrap-prose.py --check` |

The demo and the smoke script are the only gates that prove the loops end to end. The demo exercises the services in-process; the smoke script exercises the same loops over HTTP through DRF, which is where serializer, permission and queryset behaviour becomes observable — two defects in this repository were visible only from the HTTP side.
