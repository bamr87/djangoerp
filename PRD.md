# PRD — djangoerp

> Product requirements for [`bamr87/djangoerp`](https://github.com/bamr87/djangoerp).
> Derived from the README, agent docs, config and code in this repo. Anything not
> stated in those sources is marked **TBD**; success metrics are proposals.

## 1. Overview / problem

A Django 6 + Django REST Framework ERP built outward from a double-entry accounting core into a modular enterprise system supporting order-to-cash, procure-to-pay and make-to-stock. The financial core was consolidated from `amrs-project`; supply-chain and planning modules re-implement designs from ERPNext, Odoo, Tryton, InvenTree and frePPLe.

## 2. Target users

- Small/medium businesses or developers needing an API-first ERP backend
- ERP consultants prototyping processes

## 3. Goals and non-goals

**Goals**

- Correct double-entry postings with enforced invariants
- End-to-end O2C, P2P and make-to-stock flows
- A documented JWT-authenticated REST API

**Non-goals**

- A full end-user UI: TBD (not described in README)

## 4. Core features (current)

- Apps: accounts, coa, company, core, journal, invoices, reports, partners, products, inventory, purchasing, sales, manufacturing, mrp
- Multi-warehouse stock ledger with moving-average costing
- BOMs, work orders and an MRP planning engine
- Async financial reports via Celery
- Swagger/ReDoc API docs
- Docker / docker-compose with PostgreSQL

## 5. Requirements

**Functional**

- Every business document posts balanced journal entries per the posting matrix (docs/ARCHITECTURE.md)
- API authenticated via JWT

**Non-functional**

- Python ≥ 3.12, Django 6.1
- Containerized deployment
- Security/compliance requirements (audit, SoD): TBD

## 6. Success metrics (proposed — not defined in sources)

- Test coverage of posting invariants
- API response times: TBD

## 7. Roadmap / open questions

- Multi-currency, tax and localization scope: TBD
- No AGENTS.md in this repo (CLAUDE.md is the agent guide)

## 8. Links

- [README.md](README.md) · [CLAUDE.md](CLAUDE.md) · [CHANGELOG.md](CHANGELOG.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
