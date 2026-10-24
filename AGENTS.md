<!-- kit: sdlc v0.1.0 · AGENTS.md is the CANONICAL agent instructions file (decision D4, UPS-AGENT-07/08). -->

# djangoerp

> Modular Django REST ERP: double-entry accounting, inventory, purchasing, sales, manufacturing and MRP planning.

## What this repo is

DjangoERP is a Django 6 + DRF ERP built outward from a double-entry accounting core into order-to-cash, procure-to-pay and make-to-stock processing plus an MRP planning engine. The financial core was consolidated from `amrs-project` (AMRS), the most mature of the fleet's three accounting prototypes (`amrs-project`, `afms`, `amrs-django`); the supply-chain and planning modules re-implement designs from ERPNext, Odoo, Tryton, InvenTree and frePPLe, with no copyleft code ([docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). A change is **done** when `python manage.py check` is clean, `makemigrations --check` reports no drift, `python manage.py test` and `python -m pytest` both pass, `python manage.py demo_erp` still ends balanced, the generated route reference is current, and the nearest `README.md` reflects the change.

## Stack & commands

Python 3.12+ (Django 6 dropped 3.11; the hub's shared CI runs 3.12), Django 6.1, DRF, SimpleJWT, drf-yasg (Swagger/ReDoc), django-filter, Celery + Redis, PostgreSQL in production and SQLite by default in dev. Dependencies are pinned exact in `requirements.txt`; when bumping them, the whole loop below plus `demo_erp` is the acceptance gate.

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

More, as needed:

```bash
isort .                                    # configured in pyproject.toml (line_length=120, first-party apps)
black <files you touched>                  # installed but unconfigured: never reformat whole files
python3 tools/gen_api_routes.py --write    # regenerate docs/reference/api-routes.md (--check to verify)
python3 tools/docs_index.py --write        # regenerate the docs/README.md index
python3 tools/unwrap-prose.py --check      # markdown: one paragraph per line (--write to fix)

# Docker: the app on PostgreSQL + Redis instead of SQLite + eager Celery; the entrypoint migrates on every start
docker compose up                                  # web + postgres + redis, autoreload, no debugger
docker compose --profile celery up                 # ...plus a real Celery worker
DEBUGPY_ENABLE=1 docker compose up -d --wait web   # what VS Code's preLaunchTask runs
docker compose run --rm -e RUN_MIGRATIONS=0 web python manage.py test   # the loop in a container: docs/DOCKER.md
docker compose down -v                             # stop and drop the database volume
```

Docker gotchas (details in [docs/DOCKER.md](docs/DOCKER.md)):

- The entrypoint seeds the admin from `.env` (`DJANGO_SUPERUSER_*`, default `djangoerp`/`djangoerp`) via `docker/bootstrap-admin.py`: superuser plus `UserRole` row, idempotent, never resetting an existing password.
- Ports bind to `127.0.0.1` because an open debugpy port is remote code execution and the stack ships a known password; `BIND_HOST` opts out.
- Debugging attaches (F5, **Docker: Attach to Django**; the `preLaunchTask` blocks on the healthcheck). Debug mode runs `runserver --noreload` on purpose — the autoreloader's child serves requests, so breakpoints would never hit.
- Editing `docker/*.sh` needs `docker compose build`; the source tree is bind-mounted, those scripts are baked in.

## Layout

| Path | Role |
| --- | --- |
| `djangoerp/` | Project package: `settings/` (base/dev/prod), root `urls.py`, `celery.py`, WSGI/ASGI |
| `core/` | `TimestampMixin`, `DocumentSequence` (claim numbers only via `DocumentSequence.next_number(prefix)`), `audit.py` (`log_event`), `demo.py` + `demo_erp`, and `tests.py` (the end-to-end suite) |
| `company/` | `Company` (the legal entity whose books these are), the landing page and `/health/` — kept out of `core/`, which stays infrastructure-only |
| `accounts/` | `UserRole` (admin/accountant/viewer/auditor), `AuditLog`, JWT login, the shared permission classes |
| `coa/`, `journal/` | Chart of accounts (`AccountType`, self-referencing `Account`); the double-entry ledger and `services.post_entry` |
| `invoices/`, `reports/` | AR (`Invoice`, `Payment`, GL posting services); `ReportTemplate`/`SavedReport` and `report_generators.py` (all report math) |
| `partners/`, `products/` | `BusinessPartner` with AR/AP control accounts; `UnitOfMeasure`, `ProductCategory` (GL mappings), `Product` (buy/make, MRP parameters) |
| `inventory/` | `Warehouse`, `StockMove` (append-only stock ledger), `StockLevel` (cached on-hand + moving-average cost) |
| `purchasing/`, `sales/`, `manufacturing/` | Document flows: receipts post Dr Inventory / Cr payable; shipments post Dr COGS / Cr Inventory; work-order completion backflushes at rolled-up cost |
| `mrp/` | `MRPRun`/`PlannedOrder`, `engine.py` (the planner, a Celery task), `services.convert_run` (planned orders → draft POs/WOs) |
| `docs/` | [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) (module map, invariants, posting matrix, MRP algorithm, adopted patterns), [`DOCKER.md`](docs/DOCKER.md), the generated [`reference/`](docs/reference/) and the [index](docs/README.md) |
| `tools/` | `gen_api_routes.py`; `docs_index.py` and `unwrap-prose.py` are vendored from the hub — do not edit |
| `Dockerfile`, `docker-compose.yml`, `docker/`, `.vscode/` | The image (`dev` with debugpy, `prod` with gunicorn), the `web`/`db`/`redis`/`worker` stack, its scripts, and the attach configurations |

Each app owns `models.py`, `migrations/`, `tests.py` and usually `serializers.py`, `views.py`, `urls.py`, `admin.py`; document apps also own a `services.py` holding their state transitions. Each app's `DefaultRouter` is mounted under `/api/<area>/`; every route is listed in the generated [`docs/reference/api-routes.md`](docs/reference/api-routes.md).

## Conventions

- **Backlog of record:** GitHub Issues with the fleet labels. Pick up issues labelled `agent:ready`; never touch `agent:hold` or `human-review`.
- **Definition of Done:** the fleet default pull request template (inherited; none here) plus the done-criteria above.
- **Decisions:** ADRs in `docs/adr/NNNN-slug.md`, indexed in `docs/adr/README.md` — none recorded yet; the first one creates the directory. Add one for anything hard to reverse.
- **Commits:** Conventional Commits (`feat fix docs style refactor test chore perf ci build`). The PR title is the squash subject.
- **Branches:** `feature/`, `fix/`, `docs/`, `refactor/`, `test/` or `chore/` off `main`; open a PR.
- **Changelog:** `CHANGELOG.md` (Keep a Changelog) records notable changes.
- **Never:** push to `main` directly, commit lockfiles or secrets, suppress type errors (`as any`, `@ts-ignore`, `# type: ignore`), leave empty exception handlers, or edit generated files by hand (`docs/reference/api-routes.md`, the `docs/README.md` index span).
- **README-First, README-Last:** read the nearest `README.md` before changing a directory; update it after.
- **Markdown:** one paragraph per line (`markdown-oneline.yml`; fix with `python3 tools/unwrap-prose.py --write`).
- **Python:** 4-space indent, 120-column limit (`.editorconfig`); `black`'s default 88 disagrees, so scope it to files you touched.
- **API surface:** one `ModelViewSet` per model; read-only surfaces (`JournalLine`, `AuditLog`, `StockLevel`, `PlannedOrder`) use `ReadOnlyModelViewSet`. Transitions are `@action` endpoints delegating to `services.py`, never named after an HTTP verb (`post_to_ledger` + `url_path='post'`, not `def post`). After changing a route or view docstring, run `python3 tools/gen_api_routes.py --write` (`core/test_docs_reference.py` fails while it is stale).
- **Permissions:** master data (partners, products, warehouses, BOMs, COA) is writable by any authenticated user, like `coa`; transactional documents (journal, stock moves, orders, MRP, posting actions) require `IsAccountant`, like `journal`.
- **Validation:** business rules in serializer `validate()`, not views or models; flow preconditions (state, stock, account configuration) in `services.py`, raising DRF `ValidationError`. Nested writes (journal, invoice, order and BOM lines) delete and recreate children in `update()`, only in draft.
- **Decimal precision** for new models: quantities `12,3`, unit costs/prices `12,4`, monetary totals 2 dp (ledger lines stay `20,2`). Quantize GL amounts with `Decimal('0.01')` before posting.
- **Ownership is server-side:** set `created_by` in `perform_create` or from `self.context['request'].user`, and keep it read-only. Models with `created_at` + `updated_at` subclass `core.models.TimestampMixin`.
- **Migrations are committed** (a real `0001_initial.py` per app, plus `coa`'s `0002_seed_account_types` data migration). Run `makemigrations --check --dry-run` before committing a model change; treat drift as a CI failure.
- `.github/workflows/ci.yml` is a thin caller of the hub's `standard-ci.yml` — the gate logic is not here, so don't edit it.

## Domain rules that must not be broken

Hard-won in `amrs-project`; never regress them (`amrs-django`'s `reports/generators/balance_sheet.py` shows what happens when you do).

- **Double entry.** `JournalEntrySerializer.validate` rejects unbalanced entries and entries with no lines; every new write path into `JournalEntry` must too. Lines are created and replaced wholesale through the nested `lines` serializer.
- **Only posted entries count.** Every report generator filters `entry__status='posted'`; use the `JournalEntry.status` choices (`draft`/`posted`/`voided`), never new magic strings.
- **Account type codes are load-bearing.** `report_generators.py` hard-codes `AccountType.code` `AS`, `LI`, `EQ`, `RE`, `EX` for normal balance and report sections, and `coa`'s `0002_seed_account_types` seeds exactly these. Don't rename or remove one without updating every generator.
- **Normal balances.** Assets and expenses are debit-normal; liabilities, equity and revenue credit-normal. `calculate_account_balances` applies that signing; the balance sheet and income statement build on it. `generate_trial_balance` deliberately does **not** — it lists raw debit-minus-credit so the columns agree; "fixing" it makes `balanced` permanently false.
- **The balance sheet folds open P&L into equity.** P&L accounts stay open until a closing entry, so `generate_balance_sheet` adds `current_period_earnings` (revenue minus expenses at the report date) to `total_equity` and reports it as its own field; `reports/tests.py::ReportGeneratorTests` pins it.
- **Roles come from `accounts/permissions.py`:** `IsAdmin`, `IsAdminOrSelf`, `IsAccountant` and `IsAuditor` (both admin-implied). They read `request.user.role.role` inside `try/except AttributeError`, so a user with no `UserRole` row is denied, not crashed. Never re-implement role checks inline.
- **A superuser is not an API user.** No signal creates `UserRole`, so a `createsuperuser` account opens `/admin/` but every role check denies it. Create the `UserRole` row with the user (as `docker/bootstrap-admin.py` and `demo_erp` do).
- **Money is `DecimalField`, never `FloatField`.** Ledger amounts (`JournalLine.debit`/`credit`) are `20,2` — wider than the `12,2` elsewhere, leaving headroom for a future EDGAR/XBRL import.
- **The COA is a tree.** `AccountSerializer.validate` walks ancestors to reject cycles; `Account.parent_account` and `Account.account_type` are `PROTECT`.
- **Posted history is immutable.** A posted journal entry can only be voided; a done stock move, posted invoice or posted payment rejects edits. Correct with reversing entries and opposing moves.
- **System postings go through `journal.services.post_entry`:** it enforces balance, quantizes to the cent, numbers from the `JE` sequence and is idempotent per unique `source_reference` (one business event, one entry). Never post with raw ORM calls.
- **Only done stock moves count.** `StockLevel` caches the sum of `'done'` moves, maintained under row locks by `inventory.services.complete_move` and rebuildable with `rebuild_stock_levels()`. Never write `StockLevel` directly.
- **Moving-average costing, no negative stock.** Receipts re-weight `StockLevel.average_cost`; issues leave at the current average and raise `InsufficientStockError` rather than go negative. GL postings use exactly the completed move's snapshotted `total_value`, so stock ledger and GL cannot drift.
- **State transitions live in `services.py`.** `confirm`/`receive`/`ship`/`complete`/`post`/`convert`/`cancel` are the only status writers, each transactional and audit-logged via `core.audit.log_event`; serializers reject edits on non-draft documents.
- **Account mappings are explicit.** Postings resolve accounts from `ProductCategory`, `BusinessPartner` and `Payment.deposit_account` and raise a clear configuration error when one is missing — never guess or hard-code account codes.
- **BOMs cannot loop; one is active per product.** `BillOfMaterialsSerializer` rejects cycles (MRP's low-level-code pass would never terminate) and a partial unique constraint allows one active BOM per product.
- **MRP plans only confirmed reality.** Demand: confirmed sales orders, open work-order components, safety stock. Supply: on-hand plus confirmed purchase/work orders. Drafts, including freshly converted ones, are invisible until confirmed; `OPEN_STATUSES` on the document models stays authoritative.

## Architecture notes

- **Auth is JWT only** (chosen over `amrs-django`'s Token auth): SimpleJWT authentication and an `IsAuthenticated` default, so every endpoint needs a bearer token unless a viewset overrides it. `CustomTokenObtainPairSerializer` adds the profile and role to the login response.
- **Settings are split** into `djangoerp/settings/{base,dev,prod}.py`, picked by whether `DJANGO_SETTINGS_MODULE` ends in `.prod`. `dev` runs Celery eagerly and defaults to SQLite; `prod` adds `SECURE_HSTS_*`/secure-cookie hardening. New configuration goes in `base.py` as `env(...)` with a safe default, documented in `.env.example`.
- **Filtering and paging are global:** `DjangoFilterBackend`, `SearchFilter`, `OrderingFilter` and `PageNumberPagination` (`PAGE_SIZE=20`) are project-wide, so viewsets declare `filterset_fields`/`search_fields` without repeating `filter_backends`.
- **Reports and MRP are async.** `SavedReport` starts `generating`; `generate_report.delay()` dispatches on `template.report_type`, writes `result_data` and ends `completed` or `failed` (`error_message`). A new report type is a `ReportTemplate.REPORT_TYPES` choice plus a branch and generator in `report_generators.py`. `mrp.engine.run_mrp` does the same for `MRPRun` (results in `log`). Dev's `CELERY_TASK_ALWAYS_EAGER=True` runs both without a worker.
- **Audit logging is wired through services.** `core.audit.log_event` writes `accounts.AuditLog` rows (`action='update'`, event name in `details['event']`) for every document transition. There is no generic model-change middleware: master-data CRUD is not audited, only document flows. New transitions must call `log_event`.
- **The demo is executable documentation.** `demo_erp` runs plan → buy → make → ship → bill → collect through the real services and fails if the books don't balance; `core/tests.py::EndToEndERPFlowTests` pins the same flow with exact numbers. If a change breaks either, the change is wrong (or the docs are).
- **EDGAR is not ported yet.** The `edgar_statement`/`edgar_reconciliation` report types and EDGAR account-scoping helpers were stripped with `amrs-project`'s `edgar` app; restore them together with that app, not piecemeal.

## Fleet context

One of ~40 repos managed by the [bamr87/bamr87 dash](https://github.com/bamr87/bamr87) (registry: `_data/projects.yml`; standards: `specs/`). It is vendored there as a git submodule: commit and push here first; the hub only bumps its pointer afterwards. Shared CI, release, schema and agent kits are seeded from the hub's `templates/`; prefer adopting them over hand-rolling equivalents. `amrs-project`, `afms` and `amrs-django` are the repos the financial core came from; see each one's own agent file for what still lives there.

## Standard deviations

none
