# DjangoERP feature set

What the system actually does today, what implements it, and what pins it. Status is evidence-based: **Verified** means exercised end to end over HTTP against a clean PostgreSQL build with a real Celery worker by [tools/e2e-api-smoke.sh](../tools/e2e-api-smoke.sh); **Tested** means pinned by the automated suite but not part of the live smoke path; **Partial** and **Absent** are gaps with their upgrade path named.

The normative contract for each of these is [SPEC.md](SPEC.md); the design rationale is [ARCHITECTURE.md](ARCHITECTURE.md).

## Verification baseline

| Measure | Value |
| --- | --- |
| Automated tests | 98, passing under both `manage.py test` and `pytest` |
| Apps | 14 under [apps/](../apps/), plus the `djangoerp` project package |
| Models | 25 |
| Live smoke assertions | 42, all passing on a clean build |
| Books | Trial balance foots, balance sheet balances, income statement ties |

The reference cycle the smoke test drives: 25,000 opening capital, a 5-unit widget sale at 250, MRP-planned component buys, one work order, one shipment, one invoice and one payment. It ends with debits 27,300 = credits 27,300, assets 26,950 = liabilities 1,050 + equity 25,900 (including 900 current-period earnings), revenue 1,250 − expenses 350 = net income 900, and a second MRP run that plans nothing because the first cycle satisfied all demand.

## 1. Accounting core

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| Chart of accounts as a self-referencing tree | Verified | [apps/coa/models.py](../apps/coa/models.py) | `apps/coa/tests.py` (8 tests) |
| Cycle rejection on account parenting | Verified | `AccountSerializer.validate` | smoke: self-parent → `400` |
| Five seeded account types (`AS`/`LI`/`EQ`/`RE`/`EX`) | Verified | `coa/migrations/0002_seed_account_types` | present on every fresh database |
| Account hierarchy endpoint | Tested | `/api/coa/accounts/hierarchy/` | `apps/coa/tests.py` |
| Double-entry journal with balance enforcement | Verified | [apps/journal/serializers.py](../apps/journal/serializers.py) | smoke: unbalanced and empty entries → `400` |
| Single programmatic posting path, idempotent | Verified | [`post_entry`](../apps/journal/services.py) | `apps/journal/tests.py` (8 tests) |
| Posted-entry immutability (void only) | Verified | `JournalEntrySerializer` | smoke: edit posted entry → `400` |
| Draft / posted / voided lifecycle | Tested | `JournalEntry.status` choices | `apps/journal/tests.py` |
| Customer invoices with line items | Verified | [apps/invoices/models.py](../apps/invoices/models.py) | `apps/invoices/tests.py` (8 tests) |
| Invoice posting (Dr receivable / Cr revenue) | Verified | [`post_invoice`](../apps/invoices/services.py) | smoke: `JE-00004` |
| Payment posting (Dr deposit / Cr receivable) | Verified | [`post_payment`](../apps/invoices/services.py) | smoke: `JE-00005` |
| Posted invoice/payment immutability | Verified | `InvoiceSerializer` | smoke: edit posted invoice → `400` |
| Accounts payable as a first-class document | Absent | supplier settlement is a manual journal entry today | vendor-bill three-way match is roadmap |

## 2. Master data

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| Single legal entity with currency and tax id | Verified | [apps/company/models.py](../apps/company/models.py) | `apps/company/tests.py` (12 tests) |
| Partner that is customer and/or supplier | Verified | [apps/partners/models.py](../apps/partners/models.py) | `apps/partners/tests.py` |
| AR/AP control accounts on the partner | Verified | `receivable_account` / `payable_account` | used by every posting |
| Units of measure | Verified | [apps/products/models.py](../apps/products/models.py) | `apps/products/tests.py` |
| Product categories carrying GL mappings | Verified | `ProductCategory` | resolves inventory/revenue/COGS on every post |
| Products with buy/make procurement and MRP parameters | Verified | `Product` | lead time, safety stock, lot sizing all exercised |
| UoM conversion between units | Absent | one UoM per product | ERPNext `conversion_factor` idiom |
| Product variants | Absent | deliberate — the largest accidental complexity in comparable systems | — |

## 3. Inventory

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| Append-only stock ledger | Verified | [apps/inventory/models.py](../apps/inventory/models.py) | `apps/inventory/tests.py` (7 tests) |
| Only `done` moves count | Verified | `StockMove.status` | smoke: 6 completed moves |
| Cached on-hand per product/warehouse | Verified | `StockLevel` under row locks | smoke: FRAME 10 @ 40, WHEEL 20 @ 15, WIDGET 5 @ 70 |
| Moving-average costing | Verified | [`complete_move`](../apps/inventory/services.py) | receipts re-weight, issues leave at average |
| No negative stock | Tested | `InsufficientStockError` | `apps/inventory/tests.py` |
| Cache rebuildable from the ledger | Tested | `rebuild_stock_levels()` | `apps/inventory/tests.py` |
| Multi-warehouse | Verified | `Warehouse` FK throughout | smoke uses one, model supports many |
| Stock reservations | Absent | — | Odoo `reserved_quantity` |
| Lot / serial tracking, per-location bins | Absent | — | roadmap |

## 4. Procure to pay

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| Purchase orders with lines | Verified | [apps/purchasing/models.py](../apps/purchasing/models.py) | `apps/purchasing/tests.py` (9 tests) |
| Confirm transition | Verified | [`confirm_purchase_order`](../apps/purchasing/services.py) | smoke |
| Goods receipt posting Dr Inventory / Cr AP | Verified | `receive_purchase_order` | smoke: `GRN-00001`, 2 moves |
| Partial receipt with line-level counters | Verified | `{line_id: quantity}` map | `test_partial_receipt_via_api_stays_partially_received` |
| Correct status roll-up through the API | Verified | fixed; see section 8 | `test_receive_via_api_marks_order_fully_received` |
| Over-receipt rejection | Tested | service precondition | `apps/purchasing/tests.py` |
| Cancel guarded after receipt | Tested | `cancel_purchase_order` | `apps/purchasing/tests.py` |
| Vendor bill matched against the GRN accrual | Absent | — | classic GR/IR three-way match |

## 5. Order to cash

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| Sales orders with lines | Verified | [apps/sales/models.py](../apps/sales/models.py) | `apps/sales/tests.py` (8 tests) |
| Confirm transition, becomes MRP demand | Verified | [`confirm_sales_order`](../apps/sales/services.py) | smoke |
| Shipment posting Dr COGS / Cr Inventory at moving average | Verified | `ship_sales_order` | smoke: `SHP-00001`, `JE-00003` |
| Partial shipment with open-quantity tracking | Verified | `{line_id: quantity}` map | `test_partial_shipment_via_api_stays_partially_shipped` |
| Correct status roll-up through the API | Verified | fixed; see section 8 | `test_ship_via_api_marks_order_fully_shipped` |
| Invoice generated from a fully shipped order | Verified | `create_invoice_from_order` | smoke: `INV-00001` at 1,250 |
| Revenue recognised at invoice, not shipment | Verified | deliberate split | shipment posts COGS only |
| Duplicate invoicing blocked | Tested | service precondition | `apps/sales/tests.py` |
| Credit notes | Absent | — | roadmap |

## 6. Manufacturing

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| Multi-level bills of material | Verified | [apps/manufacturing/models.py](../apps/manufacturing/models.py) | `apps/manufacturing/tests.py` (6 tests) |
| BOM cycle rejection | Verified | `BillOfMaterialsSerializer.validate` | smoke: cyclic BOM → `400` |
| One active BOM per product | Verified | `UniqueConstraint: one_active_bom_per_product` | database-enforced |
| Work order confirm / start / complete | Verified | [apps/manufacturing/services.py](../apps/manufacturing/services.py) | smoke |
| Component availability preview | Verified | `component_availability` | smoke: FRAME 5/15, WHEEL 10/30 sufficient |
| Backflush on completion at rolled-up cost | Verified | `complete_work_order` | smoke: 2 issues, FG received at 70 |
| Routings, work centres, finite capacity | Absent | — | frePPLe |
| WIP staging warehouse, job cards | Absent | — | ERPNext |
| BOM snapshot on the work order | Absent | live BOM is read at completion | roadmap |

## 7. MRP planning

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| Regenerative level-by-level MRP I | Verified | [apps/mrp/engine.py](../apps/mrp/engine.py) | `apps/mrp/tests.py` (7 tests) |
| Low-level code computation over the BOM graph | Verified | `engine.py` | each product netted once |
| Demand from confirmed orders, WO components, safety stock | Verified | `engine.py` | smoke: 5 planned orders |
| Supply from on-hand plus confirmed PO/WO | Verified | `engine.py` | drafts invisible until confirmed |
| Lot sizing (min order qty, order multiple) | Verified | `engine.py` | smoke: rounded buys |
| Lead-time offsetting with expedite flagging | Verified | `engine.py` | smoke: clamped releases flagged |
| Pegging back to the demand source | Verified | `PlannedOrder.demand_reference` | smoke: buys pegged to `SO-00001` |
| Conversion to draft POs grouped per supplier and draft WOs | Verified | [`convert_run`](../apps/mrp/services.py) | smoke: 1 PO + 1 WO from 5 proposals |
| Regenerative correctness (satisfied demand plans nothing) | Verified | second run | smoke: `planned_order_count = 0` |
| Runs asynchronously via Celery | Verified | `run_mrp` task | smoke: dispatched to a real worker |
| Finite capacity scheduling | Absent | infinite capacity assumed | frePPLe |

## 8. Reporting

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| Trial balance | Verified | [apps/reports/report_generators.py](../apps/reports/report_generators.py) | smoke: 27,300 = 27,300, `balanced: true` |
| Balance sheet with current-period earnings folded into equity | Verified | `generate_balance_sheet` | smoke: balances; `ReportGeneratorTests` pins the fold-in |
| Income statement | Verified | `generate_income_statement` | smoke: 1,250 − 350 = 900 |
| General ledger | Verified | `generate_general_ledger` | smoke |
| Cash flow | Verified | generator branch | smoke |
| Custom report type | Partial | choice exists, generator is a stub branch | — |
| Asynchronous generation with status and error capture | Verified | `SavedReport` + Celery | `test_create_saved_report_dispatches_the_generator` |
| Decimal-safe JSON persistence | Tested | `DjangoJSONEncoder` on the JSONFields | `test_regenerate_actually_saves_decimal_result_data` |
| Row scoping to the creator unless admin/accountant | Tested | `SavedReportViewSet.get_queryset` | `apps/reports/tests.py` (8 tests) |
| EDGAR / XBRL statement import and reconciliation | Absent | excluded from this consolidation pass | README roadmap |

## 9. Platform

| Capability | Status | Implementation | Pinned by |
| --- | --- | --- | --- |
| JWT authentication with profile and role in the response | Verified | [apps/accounts/serializers.py](../apps/accounts/serializers.py) | smoke: login returns role `admin` |
| Unauthenticated rejection on every API surface | Verified | project-wide `IsAuthenticated` | smoke: `401` |
| Role-gated transactional surfaces | Verified | [apps/accounts/permissions.py](../apps/accounts/permissions.py) | `viewer` receives `403` on 5 write surfaces |
| Ledger-write actions gated even where the viewset is open | Verified | `get_permissions` in [apps/invoices/views.py](../apps/invoices/views.py) | `viewer` receives `403` on invoice `post` |
| `auditor` role wired to endpoints | Absent | `IsAuditor` exists but no viewset references it | see SPEC 2.4 |
| `viewer` able to read transactional documents | Partial | `403` on entries/orders/moves, `200` on lines/levels/invoices | see SPEC 2.4 |
| Atomic document numbering | Verified | [`DocumentSequence`](../apps/core/models.py) | 10 prefixes, no collisions across the smoke run |
| Audit trail on every state transition | Verified | [`log_event`](../apps/core/audit.py) | smoke: 17 rows across 12 event types |
| Audit trail on master-data CRUD | Absent | only document flows are audited | no generic model-change middleware |
| Filtering, search, ordering, pagination project-wide | Verified | `REST_FRAMEWORK` settings | every list response is paginated |
| OpenAPI schema, Swagger and ReDoc | Tested | drf-yasg at `/swagger/`, `/redoc/` | schema builds without error |
| Health probe | Verified | [apps/company/views.py](../apps/company/views.py) | compose healthcheck depends on it |
| Committed migrations, no drift | Verified | every app has real migrations | `makemigrations --check` clean |
| Docker stack on PostgreSQL, Redis and a real Celery worker | Verified | [docker-compose.yml](../docker-compose.yml) | clean `--no-cache` build exercised |
| Attach-based debugging | Tested | [.vscode/launch.json](../.vscode/launch.json) | debugpy on 5678/5679/5680 |

## 10. Defects found and fixed by live exercise

Three defects were found by driving the API over HTTP that the 94-test suite did not catch, because every existing test called the service functions directly with plain model instances.

| Defect | Cause | Fix | Regression test |
| --- | --- | --- | --- |
| A fully shipped sales order stayed `partially_shipped` and could not be invoiced | The status roll-up re-read `order.lines.all()`, which is served from the viewset's prefetch cache and reports pre-shipment quantities | Roll up from the line objects the service just mutated, [apps/sales/services.py](../apps/sales/services.py) | `test_ship_via_api_marks_order_fully_shipped` |
| A fully received purchase order stayed `partially_received` | Same cause, [apps/purchasing/views.py](../apps/purchasing/views.py) prefetches `lines` too | Same fix, [apps/purchasing/services.py](../apps/purchasing/services.py) | `test_receive_via_api_marks_order_fully_received` |
| Every report created through the API hung at `generating` forever | `SavedReportViewSet` had no `perform_create`, so the row persisted at its default status and the Celery task was never dispatched | Added `perform_create` dispatching `generate_report`, mirroring `MRPRunViewSet`, [apps/reports/views.py](../apps/reports/views.py) | `test_create_saved_report_dispatches_the_generator` |

The common lesson is recorded in [SPEC.md](SPEC.md#11-verification-gates): service-level tests and HTTP-level tests prove different things, and this codebase needs both. The pre-existing `test_create_saved_report` asserted only `201`, which a create path that never dispatches passes perfectly.

## 11. Known gaps, ranked

1. The `viewer` role cannot read the transactional documents its name implies, while `auditor` is unwired entirely — the read side of the role model is unfinished (SPEC 2.4).
2. Accounts payable has no document of its own; supplier settlement is a manual journal entry, so there is no three-way match against the GRN accrual.
3. Master-data CRUD is unaudited — only document transitions write `AuditLog` rows.
4. The `custom` report type is a choice without a real generator.
5. UoM conversion, product variants, lot/serial tracking, stock reservations and per-location bins are all absent by deliberate decision, each with a named upgrade path in [ARCHITECTURE.md](ARCHITECTURE.md#deliberate-exclusions-roadmap).
6. EDGAR/XBRL ingestion awaits a later consolidation pass and should be restored together with its app rather than piecemeal.
