# ADR 0002 — Absorb conversion costs into finished goods at work-order completion

- **Status**: proposed
- **Date**: 2026-11-20
- **Deciders**: proposed by an AI agent (Claude Code) for the maintainer to ratify by merging
- **Related**: [DJERP-015](../../features/features.yml), PRD FR10, [ADR 0003](0003-agent-simulation-sandbox.md)

## Context

`manufacturing.services.complete_work_order` valued a finished good at exactly the component value it consumed. Labor, machine time and overhead never reached inventory, so unit costs were material costs and every conversion cost was a period expense. The architecture document listed labor and overhead as roadmap. The agent simulation sandbox needs them: its line crews, machines and maintenance are cost drivers, and "what happens to costs if we add a production line" is a question about how conversion cost spreads over units.

The constraint is the ledger invariant that the stock ledger and the general ledger cannot drift: the GL posting for a stock move uses exactly the move's snapshotted `total_value`. Posting an absorption entry separately from the receipt would break it, because the finished good's moving average would not include the absorbed cost.

## Decision

`complete_work_order` takes an optional `conversion_costs`, a list of `{'account': Account, 'amount': Decimal, 'label': str}`. `clean_conversion_costs` validates them before anything moves (non-negative, quantized to the cent, grouped by account, zero amounts dropped). The finished-good receipt is completed with `total_value = component value + conversion total`, which re-weights its moving average. The completion entry debits finished-goods inventory for that receipt value and credits component inventory for the components and each absorption account for its share. Callers that pass nothing get the previous behavior to the cent, so the API, `demo_erp` and `EndToEndERPFlowTests` are unchanged.

The sandbox credits one contra-expense account, `5390 Production costs absorbed into inventory` (type `EX`, credit balance), at a predetermined rate per line: budgeted daily conversion cost over normal-capacity hours. Actual conversion costs are expensed as incurred to `5300`–`5340`, so `5390` against them is the under/over-absorption variance.

## Consequences

**Positive**

- Unit costs carry labor and overhead, so margins by product and the effect of capacity decisions on cost are visible in the books.
- The stock ledger and the GL still agree to the cent; `core/tests.py` and the reconciliation guardrail pin it.
- Normal-capacity rates keep idle capacity out of unit cost (it shows as under-absorption), avoiding the death spiral of rates computed on actual volume.

**Negative**

- The rate is the caller's responsibility; the core does not know about work centers. A future routings/work-center module should own it.
- The absorption account has to be configured by the caller; nothing checks that it is a contra-expense.

## Alternatives considered

- **A separate absorption entry after completion** — rejected: the finished good's stock value would differ from its GL value.
- **Actual-cost absorption (spread each tick's actual conversion cost over units completed)** — rejected for the sandbox: unit costs would swing with volume, and the completion entry would need costs known only at the end of the tick.
- **A work-center model in `manufacturing` with hourly rates** — the right long-term home (ERPNext workstations, Odoo work centers), deferred: it is a larger change to the core than this decision needs.

## Supersedes / Superseded by

- Supersedes: none
- Superseded by: none
