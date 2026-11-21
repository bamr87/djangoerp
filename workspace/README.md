# workspace — the common app shell

> One shell for every module: sign in once, find any document from the header, move between modules from one navigation, and open any business object to see its lines, lifecycle, the documents before and after it, the journal entries it posted, the stock it moved and its audit trail. Every screen is a client of the public REST API; nothing here decides a business rule ([ADR 0004](../docs/adr/0004-registry-driven-workspace-shell.md)).

## Run it

```bash
python manage.py migrate
python manage.py demo_erp            # or: python manage.py simulate --ticks 30, for a busier set of books
python manage.py runserver           # then open http://localhost:8000/app/
```

Sign in with a user that has a role (`createsuperuser`, then a `UserRole` row in the admin; the Docker stack and `demo_erp` create one). What the navigation lists, which buttons appear and which tabs show data follow the role, computed from the same permission classes the API enforces.

## Routes

| Route | Page |
| --- | --- |
| `/app/` | Home: quick-create buttons, "needs attention" work queues (orders to confirm, ship, invoice; invoices to post or collect; purchase orders to receive; work orders to start; planned orders to convert…), every module with live counts, and, for admins, recent activity |
| `/app/<group>/<resource>/` | A list: search, filters (choices, yes/no, references through a searchable picker, dates), sortable columns, paging; the URL holds the state, so a filtered list is a link |
| `/app/<group>/<resource>/<id>/` | A record: status and lifecycle stepper, actions (Confirm, Ship, Post…) shown only in the statuses and to the roles that allow them, fields with references as links, line tables with totals, and tabs: **Document flow** (the chain from source to settlement and every related object), **Accounting** (each journal entry, line by line, with a balanced check), **Stock** (the moves at their snapshotted cost), **History** (the audit trail; admin only, as in the API), **Data** (the raw API record) |
| `/app/<group>/<resource>/new/`, `…/<id>/edit/` | A form: writable fields from the serializer, reference pickers, a lines editor with running totals; errors from the API land on the field or line cell they belong to. Editing is offered only in the statuses the document allows (drafts) |
| `/app/finance/statements/` | Trial balance, balance sheet, income statement and general ledger from `reports.report_generators` |
| `/console/` | The simulation console ([simulation/README.md](../simulation/README.md)), a page of the same shell |

The header's search (press `/`) looks across every resource the role can read, through each viewset's own search fields, and ranks title matches first.

## How it works

| File | Role |
| --- | --- |
| `registry.py` | The layout: nine groups, 31 resources (columns, lifecycle, line tables, actions and their dialog fields, which statuses are editable), saved views such as Customers and Suppliers, the statements page and twelve work queues. At first use it introspects every serializer and viewset (field types, choices, reference targets, filters, search, ordering, `@action`s, endpoints from the router) and computes a per-role access matrix by running each viewset's permission classes against a fake request for every role |
| `services.py` | The read models: `flow` (per document type: upstream and downstream documents from audit events, source references and foreign keys; its journal entries and stock moves), `search`, `overview` and `statements`. Each section is included only if the role could read it through the owning viewset, and `restricted` names what was withheld |
| `views.py`, `urls.py` | `WorkspaceViewSet` under `/api/workspace/`: `registry/`, `overview/`, `search/?search=`, `statements/?report=…`, `flow/<resource>/<id>/`; and `shell`, the page view for `/app/…` |
| `context_processors.py` | Server-renders the module navigation into shell pages, so it appears before any script runs |
| `templates/workspace/shell.html` | The shell: header, search, user chip, navigation, breadcrumbs, sign-in, toasts; `app.html` and `simulation/console.html` extend it |
| `static/workspace/js/` | `core.js` (DOM helpers, formatting, the JWT client with one refresh), `shell.js` (sign-in, navigation, search; exposes `window.ERPShell` and fires `erp:ready`), `app.js` (router, home, statements), `list.js`, `detail.js`, `form.js`, `widgets.js` (value rendering, stepper, table, reference picker, dialog) |
| `static/workspace/css/app.css` | The design tokens (light and selected dark steps) and every component, shared with the console |

No build step: the scripts are ES modules served as static files. The session is the SimpleJWT pair in `localStorage` (`erp.access`, `erp.refresh`, `erp.user`), so new tabs and the console share it; the registry is cached per tab in `sessionStorage`.

## Adding a resource or a document flow

1. A new viewset fails `workspace/tests.py::RegistryTests::test_every_api_collection_is_described` until `RESOURCES` in `registry.py` gives it a key, group, label and columns. Names in the layout must exist on the serializer, or `test_layout_names_only_fields_and_actions_the_api_has` fails.
2. A transition is an `A(name, label, when=[statuses], fields=[I(...)], confirm=…)` entry whose `name` is the viewset's `@action`; the button posts to it and shows the API's answer.
3. A document that should show its flow sets `flow=True` and gets a builder in `services.py` (`FLOWS`, keyed by model label), linking through the audit events and source references its services write.

## Tests and evidence

`python manage.py test workspace` covers the registry against the API, the access matrix against the permission classes, each flow builder on the `demo_erp` books, the read models by role, and the shell routes. The browser scenario `verify/scenarios/workspace-shell.yml` and the signed-in captures in [`test/evidence/workspace-shell/`](../test/evidence/workspace-shell/README.md) show it working end to end, including order to cash through the screens.
