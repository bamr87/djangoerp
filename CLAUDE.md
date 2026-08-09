# CLAUDE.md

Guidance for AI coding agents (Claude Code, Copilot, Cursor) working in **djangoerp**.

DjangoERP is a Django 6 + Django REST Framework ERP built outward from a double-entry accounting core into a modular enterprise system with end-to-end order-to-cash, procure-to-pay and make-to-stock processing plus an MRP planning engine. The financial core — chart of accounts, journal entries, invoices/payments, and async financial reports — was consolidated here from `amrs-project` (AMRS), the most mature of three overlapping accounting prototypes found across the fleet (`amrs-project`, `afms`, `amrs-django`); the supply-chain/planning modules re-implement patterns from ERPNext, Odoo, Tryton, InvenTree and frePPLe (designs only, no copyleft code — see `docs/ARCHITECTURE.md`). A change is "done" here when `python manage.py check` is clean, `makemigrations --check` reports no drift, `python manage.py test` and `python -m pytest` both pass, `python manage.py demo_erp` still ends balanced, and the nearest `README.md` reflects the change.

## Stack & commands

Python 3.12+ (Django 6 dropped 3.11; the shared hub CI runs 3.12), Django 6.1, DRF, SimpleJWT, drf-yasg (Swagger/ReDoc), django-filter, Celery + Redis, PostgreSQL in production / SQLite by default in dev. Dependencies are pinned exact in `requirements.txt` — when bumping them, the whole verification loop below plus `demo_erp` is the acceptance gate.

```bash
# install dependencies:
python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env

# bootstrap the database — migrations are committed, unlike every source repo this was ported from:
python manage.py migrate
python manage.py createsuperuser

# run the dev server:
python manage.py runserver           # http://localhost:8000/

# run tests (both work out of the box; pytest-django is configured in pyproject.toml):
python manage.py test
python -m pytest

# lint:
flake8 --max-line-length=120 --exclude=.venv,migrations .
isort .                              # configured in pyproject.toml (line_length=120, first-party apps)
black .                              # installed but unconfigured — scope any black run to files you touched
python3 tools/unwrap-prose.py --check # markdown one-paragraph-per-line gate (CI enforces it)

# sanity check:
python manage.py check
python manage.py makemigrations --check --dry-run   # fails if a model change needs a new migration
```

## Layout

| Path | Role |
| --- | --- |
| `djangoerp/` | Project package: `settings/` (base/dev/prod split), root `urls.py`, `celery.py`, WSGI/ASGI |
| `core/` | Shared platform: `TimestampMixin`, `DocumentSequence` (atomic document numbering — always claim numbers via `DocumentSequence.next_number(prefix)`), `core/audit.py` (`log_event`, the audit-trail writer used by every service transition), `core/demo.py` + the `demo_erp` management command, and `core/tests.py` (the end-to-end integration suite) |
| `company/` | `Company` — the legal entity whose books this installation keeps — plus the browser-facing landing page and the `/health/` probe. Deliberately *not* in `core/`: `core` stays infrastructure-only. Its `urls.py` is the DRF router (`/api/company/`); `urls_web.py` owns the site root |
| `accounts/` | `UserRole` (admin/accountant/viewer/auditor, `OneToOne` to `django.contrib.auth.User`) + `AuditLog`, JWT login view, and the shared DRF permission classes |
| `coa/` | Chart of accounts: `AccountType` and the self-referencing `Account` tree |
| `journal/` | `JournalEntry` header + `JournalLine` rows — the double-entry ledger — plus `services.post_entry`, the one programmatic write path for system postings |
| `invoices/` | `Invoice`, `InvoiceLineItem`, `Payment` — AR, with `services.post_invoice`/`post_payment` GL posting |
| `reports/` | `ReportTemplate` + `SavedReport` models, viewsets, and `report_generators.py` (the Celery task and all report math) |
| `partners/` | `BusinessPartner` — customer and/or supplier flags on one row, with AR/AP control-account FKs |
| `products/` | `UnitOfMeasure`, `ProductCategory` (inventory/revenue/COGS account mappings), `Product` (buy/make `procurement_type`, standard cost, lead time, safety stock, lot sizing) |
| `inventory/` | `Warehouse`, `StockMove` (the append-only stock ledger), `StockLevel` (cached on-hand + moving-average cost), `services.py` (`create_move`/`complete_move`/`rebuild_stock_levels`) |
| `purchasing/` | `PurchaseOrder`/`PurchaseOrderLine` and `services.py` (confirm/receive/cancel; receipt posts Dr Inventory / Cr supplier payable) |
| `sales/` | `SalesOrder`/`SalesOrderLine` and `services.py` (confirm/ship/invoice/cancel; shipping posts Dr COGS / Cr Inventory) |
| `manufacturing/` | `BillOfMaterials`/`BOMLine` (cycle-checked), `WorkOrder` and `services.py` (completion backflushes components and receives the finished good at rolled-up cost) |
| `mrp/` | `MRPRun`/`PlannedOrder`, `engine.py` (the planning algorithm, a Celery task) and `services.convert_run` (planned orders → draft POs/WOs) |
| `docs/ARCHITECTURE.md` | Module map, invariants, GL posting matrix, MRP algorithm, adopted open-source patterns + licensing rationale |
| `tools/unwrap-prose.py` | Vendored from the hub; do not edit — it is the markdown CI gate |

Each app owns `apps.py`, `models.py`, `migrations/`, and usually `serializers.py`, `views.py`, `urls.py`, `admin.py`, `tests.py` — unlike the source repos this was ported from, apps here have committed migrations and real tests from the start. Document apps additionally own a `services.py` holding their state transitions.

## URL map

Every app exposes a DRF `DefaultRouter` from its own `urls.py`, included by `djangoerp/urls.py` under an `/api/<area>/` prefix.

| Prefix | Routes |
| --- | --- |
| `/api/auth/` | `login/` (JWT obtain, returns user + role), `refresh/`, `users/`, `roles/`, `audit-logs/` |
| `/api/coa/` | `accounts/`, `accounts/hierarchy/`, `account-types/` |
| `/api/journal/` | `entries/`, `lines/` (read-only) |
| `/api/invoices/` | `invoices/`, `payments/`, both with `{id}/post/` GL posting actions |
| `/api/reports/` | `templates/`, `saved-reports/`, `saved-reports/{id}/regenerate/` |
| `/api/partners/` | `partners/` |
| `/api/products/` | `products/`, `categories/`, `uoms/` |
| `/api/inventory/` | `warehouses/`, `stock-moves/` (+`{id}/complete/`, `{id}/cancel/`), `stock-levels/` (read-only) |
| `/api/purchasing/` | `orders/` (+`{id}/confirm/`, `{id}/receive/`, `{id}/cancel/`) |
| `/api/sales/` | `orders/` (+`{id}/confirm/`, `{id}/ship/`, `{id}/invoice/`, `{id}/cancel/`) |
| `/api/manufacturing/` | `boms/`, `work-orders/` (+`{id}/confirm/`, `{id}/start/`, `{id}/complete/`, `{id}/availability/`, `{id}/cancel/`) |
| `/api/mrp/` | `runs/` (create dispatches the engine; +`{id}/convert/`), `planned-orders/` (read-only +`{id}/cancel/`) |
| `/api/company/` | `companies/` |
| — | `/`, `/health/` (from `company/urls_web.py`), `/admin/`, `/swagger/`, `/redoc/`, `/swagger.json` |

## Domain rules that must not be broken

These were verified and hard-won in `amrs-project`; do not regress them during the port or in later consolidation passes (see amrs-project's own CLAUDE.md and amrs-django's `reports/generators/balance_sheet.py` gap for what happens when they are).

- **Double entry.** `JournalEntrySerializer.validate` rejects entries whose debits and credits differ, and entries with no lines. Any new write path into `JournalEntry` must preserve that; lines are created and replaced wholesale through the nested `lines` serializer.
- **Only posted entries count.** Every report generator filters `entry__status='posted'`. `JournalEntry.status` has real choices (`draft`/`posted`/`voided`) — this was a bare unconstrained `CharField` in `amrs-project`; it was fixed during the port. Keep using the choices rather than inventing new magic strings.
- **Account type codes are load-bearing.** `report_generators.py` hard-codes the two-letter `AccountType.code` values `AS`, `LI`, `EQ`, `RE`, `EX` to decide normal balance and report sections. `coa`'s `0002_seed_account_types` data migration seeds exactly these five on every fresh database — do not remove or rename them without updating every report generator.
- **Normal balances.** Assets and expenses are debit-normal; liabilities, equity, and revenue are credit-normal. `calculate_account_balances` applies that signing, and the balance sheet and income statement build on it. `generate_trial_balance` deliberately does *not*: a trial balance lists raw debit-minus-credit balances so the two columns agree. Do not "fix" it back to normal-balance signing — that pushes credit-normal accounts into the debit column and makes `balanced` permanently false.
- **The balance sheet folds open P&L into equity.** Revenue and expense accounts stay open until a closing entry moves them to retained earnings, so `generate_balance_sheet` adds `current_period_earnings` (revenue minus expenses as of the report date) to `total_equity` and reports it as its own field. `amrs-django`'s rewrite of this report dropped that fold-in and its balance sheet never foots for any book with posted P&L — `reports/tests.py::ReportGeneratorTests` pins this behavior, keep it passing.
- **Roles come from `accounts/permissions.py`.** Reuse `IsAdmin`, `IsAdminOrSelf`, `IsAccountant` (admin implied), `IsAuditor` (admin implied). They all read `request.user.role.role` inside `try/except AttributeError`, so a user with no `UserRole` row is denied rather than crashing. Do not re-implement role checks inline in a viewset.
- **Money is `DecimalField`.** Never `FloatField`. Ledger amounts (`JournalLine.debit`/`credit`) are `max_digits=20, decimal_places=2` — wider than the `12,2` used elsewhere — to leave headroom for large-scale postings (e.g. a future EDGAR/XBRL import, see amrs-project's `edgar` app) without another migration.
- **The COA is a tree.** `AccountSerializer.validate` walks ancestors to reject cycles; `Account.parent_account` is `PROTECT`, as is `Account.account_type`.
- **Posted history is immutable.** A posted journal entry can only be voided (`JournalEntrySerializer` enforces it); a done stock move, posted invoice or posted payment rejects edits. Corrections are reversing entries and opposing moves, never edits.
- **System postings go through `journal.services.post_entry`.** It enforces balance, quantizes to the cent, numbers entries from the `JE` sequence, and is idempotent per `source_reference` (unique column — one business event, one entry). Never create system journal entries with raw ORM calls.
- **Only done stock moves count.** The stock ledger mirrors the journal: `StockMove.status` must be `'done'` to affect anything; `StockLevel` is a cache of the done-move sum, maintained under row locks by `inventory.services.complete_move` and reconstructible via `rebuild_stock_levels()`. Never write `StockLevel` directly.
- **Moving-average costing, no negative stock.** Receipts re-weight `StockLevel.average_cost`; issues leave at the current average and raise `InsufficientStockError` rather than go negative. Completed moves snapshot `unit_cost`/`total_value`, and GL postings use exactly `move.total_value`, so the stock ledger and GL cannot drift apart.
- **State transitions live in each app's `services.py`.** `confirm`/`receive`/`ship`/`complete`/`post`/`convert`/`cancel` are the only status writers, each transactional and audit-logged via `core.audit.log_event`. Views call services from actions; serializers reject edits on non-draft documents.
- **Account mappings are explicit.** GL posting resolves accounts from `ProductCategory` (inventory/revenue/COGS), `BusinessPartner` (receivable/payable) and `Payment.deposit_account`, and raises a clear configuration error when one is missing — never guess or hard-code account codes in services.
- **BOMs cannot loop, and one is active per product.** `BillOfMaterialsSerializer` walks component BOMs to reject cycles (the MRP engine's low-level-code pass would not terminate otherwise) and a partial unique constraint enforces a single active BOM per product.
- **MRP plans only confirmed reality.** Demand is confirmed sales orders (+ open work-order components + safety stock); supply is on-hand plus confirmed purchase/work orders. Draft documents — including ones freshly converted from a run — are invisible until confirmed. Keep `OPEN_STATUSES` on the document models authoritative for this.

## Architecture notes

- **Auth is JWT only.** `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES` is SimpleJWT and `DEFAULT_PERMISSION_CLASSES` is `IsAuthenticated`, so every endpoint requires a bearer token unless a viewset overrides it. `CustomTokenObtainPairSerializer` enriches the login response with the user's profile and role. This was a deliberate choice over `amrs-django`'s Token auth — see the consolidation research this repo's `README.md` roadmap section links back to.
- **Settings are split** `djangoerp/settings/{base,dev,prod}.py`, selected by whether `DJANGO_SETTINGS_MODULE` ends in `.prod` (see `djangoerp/settings/__init__.py`). `dev` runs Celery tasks eagerly and defaults to SQLite; `prod` turns on `SECURE_HSTS_*`/cookie-secure hardening that has no equivalent in `amrs-project`. Add new configuration to `base.py` as `env(...)` with a safe default, and document it in `.env.example`.
- **Filtering and paging are global.** `DjangoFilterBackend`, `SearchFilter`, and `OrderingFilter` plus `PageNumberPagination` at `PAGE_SIZE=20` are configured project-wide, which is why viewsets can declare `filterset_fields` / `search_fields` without repeating `filter_backends`.
- **Reports are async, and so is MRP.** `SavedReport` starts at `status='generating'`; `generate_report.delay(report.id)` in `reports/report_generators.py` dispatches on `template.report_type`, writes the payload into `result_data` as JSON, and flips status to `completed` or `failed` with `error_message`. Add a new report type by adding a choice to `ReportTemplate.REPORT_TYPES` and a branch plus generator function in `report_generators.py`. `mrp.engine.run_mrp` follows the identical pattern for `MRPRun` (`pending`/`running`/`completed`/`failed`, results in `log`). `CELERY_TASK_ALWAYS_EAGER=True` in dev settings means both run synchronously without a worker.
- **Audit logging is wired through services.** `core.audit.log_event` writes `accounts.AuditLog` rows (as `action='update'` with the event name in `details['event']`) from every document transition — confirm, receive, ship, complete, post, convert, cancel. There is still no generic model-change middleware; CRUD on master data is not audited, only document flows. New transitions must call `log_event`.
- **The demo is executable documentation.** `python manage.py demo_erp` runs the whole loop (plan → buy → make → ship → bill → collect) through the real services and fails if the statements don't balance; `core/tests.py::EndToEndERPFlowTests` pins the same flow with exact numbers. If a change breaks either, the change is wrong (or the docs are).
- **EDGAR is not ported yet.** `amrs-project`'s `edgar` app (SEC/XBRL-to-ledger integration) was deliberately excluded from this first consolidation pass — see `README.md`'s roadmap. `reports/models.py` and `report_generators.py` have the `edgar_statement`/`edgar_reconciliation` report type and the EDGAR account-scoping helpers stripped out rather than left as dead code referencing a nonexistent app; restore them together with the `edgar` app itself in a later pass, not piecemeal.

## Migrations

Unlike every repo this was consolidated from, migrations **are** committed here from the start — every app has a real `0001_initial.py` (plus follow-ups: `coa`'s `0002_seed_account_types` data migration, `journal`'s `0002_journalentry_source_reference`, the `invoices` link migrations). Bare `python manage.py makemigrations` (no app labels needed) and `python manage.py migrate` work on a fresh checkout. Run `python manage.py makemigrations --check --dry-run` before committing a model change — CI should be treated as failing if it reports drift, even though the workflow itself is the shared hub one (see below).

## Conventions

- Conventional Commits: `type(scope): description` (`feat`/`fix`/`docs`/`refactor`/`test`/`chore`/`ci`).
- Default branch is `main` — branch from it and open a PR; never push to it directly.
- README-First, README-Last: read the nearest `README.md` before changing a directory, and update it after.
- Don't suppress type errors (`as any`, `@ts-ignore`, `# type: ignore`) or leave empty exception handlers.
- **Markdown is one paragraph per line.** `.github/workflows/markdown-oneline.yml` fails the build on soft-wrapped prose; fix with `python3 tools/unwrap-prose.py --write`.
- 4-space indent and a 120-column soft limit for Python, per `.editorconfig`; `black`'s default 88 columns disagrees with that, so scope any `black` run to files you touched rather than reformatting whole files.
- API surface: one `ModelViewSet` per model, registered on a `DefaultRouter` in the app's `urls.py`. Read-only surfaces (`JournalLine`, `AuditLog`, `StockLevel`, `PlannedOrder`) use `ReadOnlyModelViewSet`. Document state transitions are `@action` endpoints delegating to `services.py` — never name an action after a bare HTTP verb (`post_to_ledger` + `url_path='post'`, not `def post`).
- Permissions: master data (partners, products, warehouses, BOMs, COA) is writable by any authenticated user, matching `coa`; transactional documents (journal, stock moves, purchase/sales/work orders, MRP, posting actions) require `IsAccountant`, matching `journal`.
- Business validation lives in serializer `validate()`, not in views or models; flow preconditions (state, stock, account configuration) live in `services.py` and raise DRF `ValidationError`. Nested writes (journal lines, invoice line items, order lines, BOM lines) delete-and-recreate children in `update()`, and only in draft.
- Decimal precision for new models: quantities `12,3`, unit costs/prices `12,4`, monetary totals 2dp (ledger lines stay `20,2`). Quantize GL amounts to the cent with `Decimal('0.01')` before posting.
- Ownership fields are set server-side: `perform_create(serializer.save(created_by=self.request.user))` in views, or `validated_data['created_by'] = self.context['request'].user` in serializers. Always mark `created_by` read-only.
- New models that have both `created_at` and `updated_at` should subclass `core.models.TimestampMixin` rather than redeclaring the two fields.
- `.github/workflows/ci.yml` is a thin caller into the hub's shared `standard-ci.yml` — the gate logic is not in this repo, so don't try to edit it here.

## Fleet context

This repo is one of ~40 managed by the [bamr87/bamr87 dash](https://github.com/bamr87/bamr87) (registry: `_data/projects.yml`; tiered baseline: `docs/STANDARDS.md`). It is vendored there as a git submodule: commit and push changes **here** first — the hub only bumps its pointer afterwards. Shared CI, release, schema, and agent kits are seeded from the hub's `templates/`; prefer adopting those over hand-rolling equivalents.

`amrs-project`, `afms`, and `amrs-django` are the three repos this one's financial core was consolidated from; see each one's own `CLAUDE.md` for what — if anything — still lives there.
