# simulation — the agent simulation sandbox

> A time-stepped economy driven by small AI agents through DjangoERP's own services, with the double-entry books as the guardrail: every tick is compiled into a balanced, reconciled set of statements, and a browser console shows all of it live.

## What it is

A sandboxed manufacturer — product line A (widgets) and product line B (gadgets), made from four purchased materials on production lines — whose every actor is an agent. Customers place orders, suppliers quote and deliver, the sales manager prices and ships, the buyer runs MRP, the planner schedules jobs on finite capacity, line crews and machines produce (and break down), maintenance services the lines, the plant manager sets shifts, and the controller posts payroll, overheads and depreciation, pays suppliers, borrows on the revolver and checks the books. None of them writes a model directly: they call the same services the API uses (`sales.services`, `purchasing.services`, `manufacturing.services`, `mrp`, `invoices.services`, `journal.services.post_entry`), each as its own Django user, so the audit trail names the agent behind every transition.

The economic forces are non-deterministic but reproducible: demand is a Poisson draw on base demand × season × trend × e^sentiment × (price ÷ competitor price)^elasticity × customer loyalty × a log-normal shock; supplier prices random-walk with inflation; deliveries slip on a reliability draw; crews vary in productivity and absence; machines wear and break down with a hazard that rises with wear. Every draw is seeded by (scenario seed, tick, agent, purpose), so a run replays exactly and a what-if variant sees the same shocks as its baseline.

## Run it

```bash
python manage.py migrate
python manage.py simulate --ticks 30                    # bootstrap the economy if needed, run 30 days
python manage.py simulate --ticks 60 --set market_growth_pct=40 --set demand_base.A-WIDGET=14
python manage.py simulate --what-if add_line:A --horizon 30
python manage.py runserver                              # then open http://localhost:8000/console/
```

`simulate` prints a KPI line per tick and the statements at the end, and exits non-zero if the books or any subledger fail to reconcile. The console needs a user with a role (`createsuperuser`, then a `UserRole` row in the admin; the Docker stack seeds one): any role can read, the accountant and admin roles can run the clock and change controls.

Agents only act where `SIMULATION_SANDBOX` is on. `djangoerp/settings/dev.py` turns it on, production leaves it off, and every write path checks it, so simulated agents never post into real books. The books of a database are shared, so it holds one scenario; a separate economy needs its own database (`DATABASE_URL=sqlite:///sandbox.sqlite3`). A scenario started on a database that already has books (the `demo_erp` company, say) continues them; its reconciliations measure the sandbox's own movement from the moment it started.

## The console (`/console/`)

The console is a page of the common workspace shell ([workspace/README.md](../workspace/README.md)): one sign-in for it and every module, the module navigation at the side, and the header's search. Any document number in the console's traces and feeds can also be opened in the workspace, whose document pages show the same journal entries and stock moves from the document's side.

| Tab | What it shows |
| --- | --- |
| Cockpit | Net income since start, KPI tiles with 30-tick sparklines, eight trend charts (P&L flows, cash and working capital, actual vs absorbed conversion cost, units, line utilization, unit cost, prices, market indices), the guardrails, the controller's commentary and the live decision feed. Play, step 1/7/30 ticks. |
| Financials | Management income statement (absorption costing), balance sheet, trial balance and income statement cross-check from `reports.report_generators`, cost by driver with the agent role behind each, unit economics, margin by product line, reconciliations; click any account for its general ledger. |
| Operations | Production lines (shifts, utilization, absorption rate, wear, book value) with shift and retire controls, commissioning, open jobs, stock at moving average, documents by status for order-to-cash, procure-to-pay and make-to-stock. |
| Agents | The roster as resources (cost per day, GL account, brain, review cadence, Claude calls, AI cost, current policy) and the decision log, filterable; each decision shows its observation, the baseline proposal, the decision and the documents it produced. Any document reference opens a trace: the decisions that touched it and its journal entries, line by line. |
| Controls | Every parameter in [`parameters.py`](parameters.py) with its bounds, the agent roles that read it and the accounts it moves; changes apply from the next tick and land in the audit trail. Driver (heuristic or Claude), model and tick length. |
| What-if lab | Presets ("Add a production line for product line A", demand growth, a second shift, labor rate, inflation, price cuts, retiring a line) or a custom change set, run as a rolled-back fork: headline, baseline-vs-variant table, cumulative net income and cash, unit cost and utilization. |
| System map | The architecture, every agent role (cadence, policy, actions, services, accounts, parameters), the posting matrix with live counts, the invariants with live status, the chart of accounts and the API. |

## The financial model

The sandbox adds its accounts to the chart (codes the demo company already uses are shared): `1210` finished goods, `1500`/`1590` machinery and accumulated depreciation, `2400` revolver, `4100`/`4200` and `5100`/`5200` sales and cost of goods sold per product line, `5300` direct labor, `5310` indirect labor, `5320` variable overhead, `5330` depreciation, `5340` maintenance, `5390` production costs absorbed, `6100` management, `6200` facility, `6300` AI agent compute, `6400` interest, `6500` loss on disposal.

Product cost is absorption costing at normal capacity ([ADR 0002](../docs/adr/0002-absorb-conversion-costs-into-finished-goods.md)). The controller sets each line's rate as budgeted daily conversion cost (crew wages with the late-shift premium, supervision, depreciation, energy at normal hours, maintenance) over normal-capacity hours. A completed job absorbs standard hours × rate + consumables into finished-goods value through `complete_work_order(conversion_costs=...)`, crediting `5390`. Actual costs post to `5300`–`5340` every tick whether the line is busy or idle, so `5390` against them is the under/over-absorption variance: idle capacity shows up as a period cost, not as inflated unit cost. Each what-if reports both the product unit cost and the fully loaded cost per unit shipped.

| Event | Debit | Credit | By |
| --- | --- | --- | --- |
| Goods receipt | Inventory 1200 | Accounts payable 2100 | supplier → `receive_purchase_order` |
| Production | Finished goods 1210 | Inventory 1200, absorbed 5390 | crew → `complete_work_order` |
| Shipment | COGS 5100/5200 | Finished goods 1210 | sales manager → `ship_sales_order` |
| Invoice | Receivables 1100 | Sales 4100/4200 | sales manager → `post_invoice` |
| Payment | Cash 1000 | Receivables 1100 | customer → `post_payment` |
| Period costs | 5300–5340, 6100–6400 | Cash 1000 | controller → `post_entry` |
| Depreciation | 5330 | 1590 | controller → `post_entry` |
| Supplier payment | Accounts payable 2100 | Cash 1000 | controller → `post_entry` |
| Revolver | Cash / 2400 | 2400 / Cash | controller → `post_entry` |
| Capex / retirement | Machinery 1500 / 1590 + 6500 | Cash 1000 / 1500 | plant manager → `world.commission_line` / `decommission_line` |

## Guardrails

After every tick the controller compiles the books. The trial balance and balance sheet come from the one-query ledger identity on most ticks and from `reports.report_generators` (the same code the reports API runs) every seventh tick and on the last tick of every batch. Four reconciliations run every tick: done stock-move value = inventory accounts, posted invoices − posted payments = receivables, open goods-receipt accruals (`SupplierPayable`) = 2100, line capex = 1500. A tick that unbalances the trial balance or balance sheet is rolled back and halts the scenario until someone resumes it; a reconciliation difference is recorded as a controller exception and shown red in the console.

## Claude agents

With the `llm` driver (or `brain: llm` on one agent), each policy review — market sentiment and competitor prices, customer loyalty and payment timing, supplier price and reliability, prices, safety stock, job sequencing, maintenance threshold, shifts and capex, absorption rates and cash buffer — goes to a small Claude model, `claude-haiku-5-5` by default (`SIMULATION_AGENT_MODEL`), through the official `anthropic` SDK with structured outputs at low effort. The model sees the observation and the heuristic's proposal and answers with bounded numbers or choices plus a rationale. [`brains.py`](brains.py) clamps every value to its field's bounds and falls back to the proposal on an API error, a refusal, truncation or invalid JSON, saying why in the decision log. At most `llm_max_calls_per_tick` reviews per tick go to Claude; each call's token cost (at list price) is posted to `6300 AI agent compute`, so the agents are resources on the books like any other. It needs `ANTHROPIC_API_KEY` (or an `ant auth login` profile) on the server; without one, every review falls back and the decision log says so. What-ifs default to heuristic agents so their comparisons are reproducible.

## API

Mounted at `/api/simulation/` ([route reference](../docs/reference/api-routes.md)): `scenarios` (create, update controls, `advance`, `resume`, `dashboard`, `financials`, `ledger`, `trace`, `design`), `lines` (commission, shifts and crew, `retire`), `agents` (brain, model, cost, cadence, policy), `decisions` and `snapshots` (read-only), `experiments` (create runs the what-if). Reads need any role; writes need the accountant role.

## Files

| File | Role |
| --- | --- |
| `models.py` | `Scenario`, `ProductionLine`, `Agent`, `AgentDecision`, `ProductionJob`, `SupplierPayable`, `TickSnapshot`, `Experiment` |
| `parameters.py` | The parameter catalogue: bounds, units, the roles that read each and the accounts it moves |
| `world.py` | Bootstrap: chart of accounts, products and BOMs, partners, lines, agents, opening books; line commissioning and retirement; absorption rates |
| `engine.py` | The tick: agents in business-day order, every write through a service in a savepoint, KPI compilation, guardrails |
| `brains.py` | Heuristic and Claude brains, field specs, structured-output schema, clamping and fallback |
| `finance.py` | Ledger aggregates, P&L and position views, cost drivers, unit economics, reconciliations and guardrails |
| `experiments.py` | What-ifs: change validation, rolled-back arms, comparison |
| `catalog.py` | The system map as data |
| `services.py` | Transitions and read models for the API, console and command |
| `management/commands/simulate.py` | The command line |
| `templates/`, `static/` | The console (no build step; vanilla JavaScript and SVG) |

## Limits

The economy is one plant, one warehouse and one currency; a line makes one product family; work orders complete all at once (no WIP account); capacity is machine hours, not a routing; customers ship complete orders only; supplier bills are the goods-receipt accrual (no three-way match). Scenario state is analytics (`TickSnapshot` stores floats) — the journal stays the book of record.
