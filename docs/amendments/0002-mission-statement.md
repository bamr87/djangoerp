<!-- kit: governance v0.1.0 -->
# 0002 — Missions

- **Status:** proposed
- **Date:** 2026-11-16
- **Proposed by:** Grok Bot (agent), at the maintainer's request
- **Ratified by:**
- **Affects:** constitution §1; PRD §1.1; feature DJERP-009
- **Supersedes:** none
- **Related:** [PRD.md](../../PRD.md) §1.1; feature DJERP-009

## Context

Constitution §1 Mission was a product description (or, for a new constitution, had not been written yet), not a three-mission direction statement. The fleet now requires every repo's constitution §1 to be one direction statement followed by exactly three numbered missions, adopted through a numbered amendment the maintainer ratifies by merging. The maintainer asked for those missions to be comprehensive and epic: Django ERP as the ultimate open home for all things enterprise resource planning. Mission 1 follows the maintainer's wording, adapted to this domain: provide data, tools, resources and transparency for all stakeholders, domain practitioners and the general public. The ambition is the direction, not a claim that the work is already finished. The missions are aligned with this repo's existing constitution and PRD.

## Amendment

Replace constitution §1 with the following, retitled **Missions**:

Django ERP's direction is to become the ultimate open home for all things enterprise resource planning — data, tools, resources and transparency, gathered in one place that anyone can inspect and reuse.

1. **Provide data, tools, resources and transparency for all stakeholders, finance and operations professionals, and the general public.** Operators, accountants, developers and anyone studying how an ERP should work are first-class audiences. The aim is a shared, inspectable commons: the same ledger, the same modules, and the same API for everyone.
2. **Grow a modular, API-first ERP outward from a double-entry accounting core, covering order-to-cash, procure-to-pay and make-to-stock, with a ledger that stays auditable.** Chart of accounts, journal entries, invoices and payments, inventory, purchasing, sales, manufacturing and MRP planning should compose as open Django apps rather than a monolith no one can read.
3. **Keep that system open and comparable: Django and Django REST Framework, modular apps, and designs that can be studied against established open ERPs rather than locked behind a vendor.** The financial core, consolidated from earlier work, and supply-chain and planning modules informed by open-source predecessors, should remain something a practitioner can inspect, extend and teach from.

These missions set the course; they do not claim the work is finished. They serve the PRD's scope: a Django 6 and Django REST Framework ERP built outward from a double-entry core into a modular enterprise system.

_Source: [PRD.md](../PRD.md) §1.1._


## Consequences

Agents and reviewers judge fit against these three missions before inventing scope, treating them as the direction of the repo rather than a description of what already ships. The same text is added as PRD §1.1 so the constitution's `_Source:` line stays accurate. The original PRD purpose / overview paragraph is left in place. This PR also aligns the README and AGENTS.md taglines with the missions and links them to constitution §1, and adds feature DJERP-009 to the feature catalog, traced to requirement MR1. Merging this PR ratifies amendment 0002. Until then the record stays `Status: proposed`.
