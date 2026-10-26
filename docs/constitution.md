<!-- kit: governance v0.1.0 · seeded from bamr87/bamr87 templates/governance/. Owned by this repo after seeding;
     change it ONLY through an amendment record in docs/amendments/ (see § 8). Validate: python3 <hub>/tools/governance_lint.py check . -->
# djangoerp — Constitution

> The rules every change to this repo must satisfy, whoever makes it — a human or an AI agent.
> Agents read this before [CLAUDE.md](../CLAUDE.md) conventions, the [PRD](../PRD.md), or the
> [feature catalog](../features/features.yml). Ratified by merged PR; amended only per § 8.

## 1. Mission

A Django 6 + Django REST Framework ERP built outward from a double-entry accounting core into a modular enterprise system supporting order-to-cash, procure-to-pay and make-to-stock. The financial core was consolidated from `amrs-project`; supply-chain and planning modules re-implement designs from ERPNext, Odoo, Tryton, InvenTree and frePPLe.

_Source: [PRD.md](../PRD.md)._

## 2. Invariants (non-negotiable)

These rules are lifted from the repo's own agent guide; breaking one is grounds to reject a PR.

- Double entry. Any new write path into `JournalEntry` must preserve that; lines are created and replaced wholesale through the nested `lines` serializer. _(CLAUDE.md)_
- Only posted entries count. _(CLAUDE.md)_
- Account type codes are load-bearing. `coa`'s `0002_seed_account_types` data migration seeds exactly these five on every fresh database — do not remove or rename them without updating every report generator. _(CLAUDE.md)_
- Normal balances. Do not "fix" it back to normal-balance signing — that pushes credit-normal accounts into the debit column and makes `balanced` permanently false. _(CLAUDE.md)_
- The balance sheet folds open P&L into equity. `amrs-django`'s rewrite of this report dropped that fold-in and its balance sheet never foots for any book with posted P&L — `reports/tests.py::ReportGeneratorTests` pins this behavior, keep it passing. _(CLAUDE.md)_
- Roles come from `accounts/permissions.py`. Do not re-implement role checks inline in a viewset. _(CLAUDE.md)_
- A superuser is not an API user. _(CLAUDE.md)_
- Money is `DecimalField`. Never `FloatField`. _(CLAUDE.md)_
- The COA is a tree. _(CLAUDE.md)_
- Posted history is immutable. Corrections are reversing entries and opposing moves, never edits. _(CLAUDE.md)_
- Secrets (tokens, API keys) are never printed, logged or committed. _(fleet rule)_
- `CHANGELOG.md`, when present, is not hand-edited once release-please owns it. _(fleet rule)_

## 3. Authority and precedence

When two documents disagree, the higher one wins and the lower one is fixed in the same PR:

1. This constitution (and its ratified amendments in [docs/amendments/](amendments/))
2. [CLAUDE.md](../CLAUDE.md) — how to work here (commands, conventions, Definition of Done)
3. The PRD ([`PRD.md`](../PRD.md)) — what the product must do (requirement ids `FRn` / `NFRn`)
4. [features/features.yml](../features/features.yml) — what it does today (stable feature ids)
5. ADRs in `docs/adr/` (none yet) and specs — why and how a specific change was made
6. Code, tests, generated files

## 4. Roles: humans and AI agents

| Actor | May | May not |
| --- | --- | --- |
| Maintainer (human) | Ratify amendments, PRD changes and releases by merging PRs; change settings, secrets, branch protection | — |
| AI agent | Read this file first; open PRs on a branch with Conventional Commit titles; update the feature catalog, PRD block and docs in the same PR as the change; propose amendments | Merge its own PRs; push to the default branch; force-push; change repo settings or branch protection; print or commit secrets; invent features or requirements (unknown = **TBD**) |

## 5. How the artifacts relate

| Artifact | Answers | Owned by | Rule |
| --- | --- | --- | --- |
| `docs/constitution.md` | What must always hold | Maintainer | Changes only by amendment (§ 8) |
| `docs/amendments/NNNN-*.md` | How and why the rules changed | Proposer → maintainer | Append-only; ratified by merged PR |
| `AGENTS.md` / `CLAUDE.md` | How to work here | Repo | Must link this file; `CLAUDE.md` imports `@AGENTS.md` |
| PRD | What the product must do | Maintainer | Requirements carry stable ids (`FRn`, `NFRn`) |
| `features/features.yml` | What the product does, where, proven how | Repo | Every user-facing feature has an id, `status`, location and `prd:` refs |
| PRD "Feature catalog" block | PRD → feature trace | Generated | `governance_lint.py prd-sync`; never hand-edit |
| `docs/adr/NNNN-*.md` | Why a hard-to-reverse decision was made | Proposer | One ADR per decision; an amendment may cite one |
| `CHANGELOG.md` | What shipped | release-please | Generated from Conventional Commits; never hand-edit |

## 6. Change process

| Change | Required in the same PR |
| --- | --- |
| Typo, docs fix, refactor with no user-visible effect | Conventional Commit title |
| New or changed user-facing feature | Entry in `features/features.yml` (`status`, `link`/`docs`, `prd:`), regenerated PRD block |
| New or changed requirement | PRD edit with a new `FRn`/`NFRn` id (ids are never reused), feature `prd:` refs updated |
| Hard-to-reverse technical decision | ADR in `docs/adr/` |
| Change to this constitution | Amendment record in `docs/amendments/` (§ 8) |

## 7. Quality gates

Every PR passes the checks the repo's guide names:

- `python manage.py test` _(CLAUDE.md)_
- `python -m pytest` _(CLAUDE.md)_
- `python3 tools/unwrap-prose.py --check # markdown one-paragraph-per-line gate (CI enforces it)` _(CLAUDE.md)_
- `python manage.py check` _(CLAUDE.md)_
- `python manage.py makemigrations --check --dry-run` _(CLAUDE.md)_
- `docker compose exec web sh -c 'python manage.py check && python manage.py makemigrations --check --dry-run'` _(CLAUDE.md)_
- `python3 <bamr87/bamr87>/tools/governance_lint.py check .` (feature catalog ↔ PRD, constitution, amendments)

## 8. Amendments

This constitution changes only through a numbered record in [docs/amendments/](amendments/) (`NNNN-kebab-slug.md`, copied from [0000-template.md](amendments/0000-template.md)):

1. **Propose** — open a PR that adds the record (`Status: proposed`) *and* the edit to this file it describes. Name what it affects: constitution sections, PRD requirement ids, feature ids.
2. **Review** — the maintainer reviews; an agent never approves or merges its own amendment.
3. **Ratify** — merging the PR ratifies it. The record says `Status: ratified` and `Ratified by:` the PR. A rejected or withdrawn PR keeps its record with that status; numbers are never reused.
4. **Supersede** — a later amendment may replace an earlier one; mark the old record `superseded by NNNN`.

Records are append-only history: fix a ratified rule with a new amendment, never by editing an old record. [0001](amendments/0001-ratify-constitution.md) ratifies this document.
