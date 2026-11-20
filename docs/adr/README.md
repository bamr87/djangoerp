<!-- kit: sdlc v0.1.0 · ADR index (UPS-WORK-04, decision D2: law-ai format) -->
# Architecture Decision Records

This directory records decisions for djangoerp that are hard to reverse: the stack, data model, security model, public interfaces, and any relaxation of a gate. Add an ADR in the same PR as the change it justifies.

## Format

- File name: `NNNN-short-kebab-title.md`, where `NNNN` is a four-digit sequence (`0001`, `0002`, …). Never renumber; a skipped number stays skipped.
- Start from [`0000-template.md`](0000-template.md).
- Required sections: **Status**, **Context**, **Decision**, **Consequences**, **Alternatives considered**, **Supersedes / Superseded by**.
- Status values: `proposed`, `accepted`, `superseded`, `deprecated`. A superseded ADR is never deleted; it links forward to the ADR that replaced it.

## Index

| ID | Title | Status |
| --- | --- | --- |
| 0001 | [Record architecture decisions](0001-record-architecture-decisions.md) | accepted |
| 0002 | [Absorb conversion costs into finished goods at work-order completion](0002-absorb-conversion-costs-into-finished-goods.md) | proposed |
| 0003 | [An agent simulation sandbox that drives the ERP through its services](0003-agent-simulation-sandbox.md) | proposed |
