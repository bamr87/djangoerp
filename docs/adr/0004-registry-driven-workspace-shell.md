# ADR 0004 — One registry-driven workspace shell for every module

- **Status**: proposed
- **Date**: 2026-11-21
- **Deciders**: proposed by an AI agent (Claude Code) for the maintainer to ratify by merging
- **Related**: [DJERP-016 to DJERP-018](../../features/features.yml), PRD FR11–FR13, [workspace/README.md](../../workspace/README.md), [ADR 0003](0003-agent-simulation-sandbox.md)

## Context

DjangoERP had a REST API for every module, the Django admin for staff, and one purpose-built page (the simulation console). There was no way for an accountant, buyer or planner to work in the modules: no common navigation, no list or detail screens, no way to follow a sales order to its shipment, invoice and journal entries, and nothing that showed what a document did to the ledger or the stock. The request was a common app shell with a consistent navigational framework in which the transactional details and business objects can be seen.

Four forces shape the answer. The constitution's invariants live in the services and serializers, so the screens must never become a second place where business rules are decided. Roles are enforced per viewset by the shared permission classes, so the screens must show each role exactly what the API would let it read or change, and no more. Thirty-one resources across nine modules is too many to hand-build and keep in step with the API by hand. And the repository has no JavaScript toolchain: CI runs Python only, and the hub's standards prefer adopting what exists over adding a build.

## Decision

- **A `workspace` app owns the shell.** `/app/` and every path under it serve one template (`workspace/templates/workspace/app.html`, extending `shell.html`); a History-API router in `workspace/static/workspace/js/app.js` renders the page. The shell (header, global search, user chip, role-filtered module navigation, breadcrumbs, toasts and the one JWT sign-in) is in `shell.js`, and the simulation console at `/console/` is now a page of the same shell: it waits for the `erp:ready` event and calls the API through `window.ERPShell`.
- **The registry describes the API instead of restating it.** `workspace/registry.py` holds only layout (groups, labels, columns, lifecycles, line tables, actions and their dialog fields). Everything else is introspected at startup: each field's type, choices, reference target and writability from the viewset's serializer; filters, search and ordering from the viewset; endpoints from the router (`reverse('<basename>-list')`); and a per-role access matrix computed by running each viewset's own permission classes against a fake request for every role. `GET /api/workspace/registry/` serves the result. `workspace/tests.py` fails when the registry names a field or action the API lacks, misses a router collection, or a nav route does not resolve.
- **Every screen is a client of the public API.** Lists, details, forms and actions call the same endpoints anyone can; transitions are the documents' `@action` endpoints; validation errors come back from the serializers and are shown on the fields and line cells. The UI hides what a role cannot do, but the server remains the only enforcement.
- **Cross-document read models are server-side and permission-filtered.** `workspace/services.py` builds a document's flow (upstream and downstream documents, its journal entries and stock moves, its audit events) from audit events, source references and foreign keys; `search`, `overview` and `statements` sit beside it. Each section is included only if the requesting role could read it through the owning viewset (`registry.permitted`), and the response lists what was withheld so the screen can say why.
- **No build step.** Vanilla ES modules, one stylesheet of design tokens (light and dark), served by Django's static files. The session is the SimpleJWT pair in `localStorage`, so a new tab or the console shares the sign-in.

## Consequences

**Positive**

- One navigation, one sign-in and one look across every module, the console included; a new viewset shows up in the registry check until it is given a place.
- What a role sees matches the API by construction, because both come from the same permission classes; the tests compare them role by role.
- Any document can be followed end to end (order → shipment → invoice → payment, with each journal entry and stock move) without a new endpoint per document type.
- Nothing to install or build; the page works from `runserver`, Docker and CI alike.

**Negative**

- Tokens in `localStorage` are readable by any script on the origin, so an XSS bug would expose them. The pages insert server strings only as text (`textContent`, never `innerHTML`), load no third-party scripts besides the vendored feedback widget, and the access token expires after an hour by default (`JWT_ACCESS_TOKEN_LIFETIME_MINUTES`); an `HttpOnly` cookie session would need CSRF handling on the API and is left for a later decision.
- The registry's layout entries must be kept up to date when a serializer gains a field a screen should show; the introspection catches stale names, not missing columns.
- Hand-written modules without a type checker or bundler: the tests cover the server side and the scenario plus evidence bundle cover the screens, but there are no JavaScript unit tests.
- The flow builder knows each document type's links (audit event names, `source_reference` prefixes); a new document flow needs a builder in `workspace/services.py`.

## Alternatives considered

- **The Django admin, customised** — staff-only, model-centric rather than document-centric, and it writes through `ModelAdmin` rather than the services, so transitions and their postings would bypass the rules the API enforces.
- **A React or Vue single-page app with a bundler** — a Node toolchain in CI and the Docker image, a second dependency tree to pin and audit, and the same API underneath; nothing the screens need requires it.
- **Server-rendered Django templates per module** — a second set of views and permission checks beside the API, which is exactly the duplication the constitution's "inspectable API for everyone" argues against.
- **Hand-written screen definitions without introspection** — thirty-one resources of field lists, choices and permissions copied from the serializers would drift silently.

## Supersedes / Superseded by

- Supersedes: none
- Superseded by: none
