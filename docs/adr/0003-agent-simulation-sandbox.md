# ADR 0003 — An agent simulation sandbox that drives the ERP through its services

- **Status**: proposed
- **Date**: 2026-11-20
- **Deciders**: proposed by an AI agent (Claude Code) for the maintainer to ratify by merging
- **Related**: [DJERP-011 to DJERP-014](../../features/features.yml), PRD FR5–FR9, [simulation/README.md](../../simulation/README.md)

## Context

The request was an ERP that simulates a time-scaled, non-deterministic economy driven by small, low-cost AI agents acting as resources at every level (direct and indirect labor, overhead, management, machines, maintenance), with the financial reporting backbone as the guardrail, a console that shows everything live, and controls for what-ifs such as adding a production line. Four forces shape it: the books must stay true (the constitution's invariants are non-negotiable), an AI agent must never be able to corrupt them, runs must be explainable and comparable, and the system must work offline and for free when no model is available.

## Decision

- **A new `simulation` app, writing only through services.** Agents call the same service functions as the API, each as its own Django user, so every existing invariant, idempotency key and audit event applies to them unchanged. The app owns agent configuration, decisions, capacity (`ProductionLine`, `ProductionJob`), the payables subledger and analytics snapshots; it adds one optional parameter to the core ([ADR 0002](0002-absorb-conversion-costs-into-finished-goods.md)).
- **Sandbox-only.** `settings.SIMULATION_SANDBOX` (on in dev, off in prod) gates every write path. One scenario per database, because the books are shared.
- **One transaction per tick, books checked at the end.** The controller compiles the trial balance and balance sheet (the report generators on review ticks and on each batch's last tick, a one-query identity otherwise) and reconciles stock, receivables, payables and fixed assets against the GL. An unbalanced tick rolls back and halts the scenario.
- **Agents as policies reviewed by brains.** Each agent acts every tick on a policy (prices, safety stock, shifts, absorption rates...) and revises it at a cadence. A review sends an observation, a heuristic proposal and a bounded field spec to a brain: the heuristic brain accepts the proposal; the Claude brain calls `claude-haiku-5-5` (configurable) through the official `anthropic` SDK with structured outputs at low effort. Answers are clamped to the field bounds, failures fall back to the proposal with the reason recorded, calls are budgeted per tick and each call's cost is posted to `6300 AI agent compute`.
- **Common random numbers.** Every random draw is seeded by (scenario seed, tick, agent, purpose), so runs replay and what-if arms share shocks.
- **What-ifs as rolled-back forks.** An experiment runs a baseline and a variant from the current state inside transactions that always roll back, keeping only the comparison.
- **A dependency-free console.** Server-rendered shell, vanilla JavaScript and SVG over the JWT API — no build step, and the same API any other client can use.

## Consequences

**Positive**

- An AI agent can only choose bounded numbers and options; what reaches the ledger is a service call with the ERP's own validation, so a model's mistake is a bad business decision, never a broken book.
- Every number in the console traces from a decision to a document to journal lines.
- Experiments are reproducible and isolate the effect of a change.
- The heuristic driver needs no key or network, so tests and CI exercise the whole economy.

**Negative**

- `anthropic` becomes a runtime dependency (imported only when a Claude brain runs).
- A tick costs about a quarter of a second on SQLite, so a 30-tick what-if takes about 15 seconds in dev, where Celery runs eagerly; production should run experiments on a worker.
- One scenario per database: comparing independent economies needs separate databases.
- The engine models one plant and warehouse, one currency, whole-order shipments and goods-receipt-accrual payables (see the README's limits).

## Alternatives considered

- **A separate simulator posting raw ORM rows or journal entries** — rejected: it would bypass the invariants this repo exists to keep.
- **LLM agents emitting free-form tool calls against the API** — rejected for now: costlier, slower and harder to bound; bounded policy reviews give the model the economically meaningful choices at a fraction of the calls.
- **Multiple scenarios per database with tagged postings** — rejected: the report generators aggregate the whole ledger, so tagging would mean forking every generator.
- **Raw HTTP to the Messages API** — rejected in favor of the official SDK (typed errors, retries, credential resolution).
- **A React/Vite console** — deferred: the README roadmap ports `amrs-django`'s React frontend; the console stays a plain client until then.

## Supersedes / Superseded by

- Supersedes: none
- Superseded by: none
