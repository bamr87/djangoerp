# Docker and debugging

> How to run DjangoERP in Docker against PostgreSQL and Redis, which ports it publishes and why they stay on loopback, and how to attach the VS Code debugger to the web, worker and one-shot containers.

The compose stack runs the app against PostgreSQL and Redis rather than the SQLite/eager-Celery defaults, so local development matches production topology. Nothing needs to be installed beyond Docker.

```bash
docker compose up                      # web + postgres + redis, autoreload on
docker compose --profile celery up     # ...plus a real Celery worker
docker compose down -v                 # stop and drop the database volume
```

Migrations are applied by the container entrypoint on every start, so `http://localhost:8000/` serves the landing page as soon as `up` returns.

## The admin account

The entrypoint seeds an admin account from `.env` (copy `.env.example` if you have not already) — `djangoerp` / `djangoerp` by default, good for both `http://localhost:8000/admin/` and `POST /api/auth/login/`. It creates the `accounts.UserRole` row alongside the superuser, which `manage.py createsuperuser` does not: every DRF permission class reads `request.user.role.role`, so a superuser without that row can open the admin but is denied by `IsAdmin`, `IsAccountant` and `IsAuditor` on every API endpoint. Existing accounts are never touched — rotate the password with `docker compose exec web python manage.py changepassword djangoerp`, not by editing `.env`.

## Ports

Published ports bind to `127.0.0.1`, because an open debugpy port is remote code execution and this stack ships a known admin password. Set `BIND_HOST=0.0.0.0` in `.env` if you deliberately want it reachable from your network.

| Host port | Service |
| --- | --- |
| `8000` | Django (`/`, `/admin/`, `/api/…`, `/swagger/`, `/health/`) |
| `5678` | debugpy — Django |
| `5679` | debugpy — Celery worker (`celery` profile) |
| `5680` | debugpy — one-shot commands (tests, `demo_erp`) |
| `5433` | PostgreSQL (off `5432` so it never collides with a local instance) |
| `6380` | Redis (off `6379`, same reason) |

## The verification loop in the container

```bash
docker compose exec web sh -c 'python manage.py check && python manage.py makemigrations --check --dry-run'
docker compose run --rm -e RUN_MIGRATIONS=0 web python manage.py test
docker compose run --rm -e RUN_MIGRATIONS=0 web python -m pytest
docker compose exec web python manage.py demo_erp
```

The `docker: test`, `docker: pytest`, `docker: check` and `docker: demo_erp` tasks in [`.vscode/tasks.json`](../.vscode/tasks.json) run the same commands.

## Debugging in VS Code

Press **F5** and pick **Docker: Attach to Django**. The launch configuration's `preLaunchTask` builds and starts the stack with the debug adapter enabled (`DEBUGPY_ENABLE=1 docker compose up -d --wait web`) and blocks on the compose healthcheck, so attaching cannot race startup — a cold machine to a live breakpoint is one keystroke.

Configurations are defined in [`.vscode/launch.json`](../.vscode/launch.json), the compose plumbing they call in [`.vscode/tasks.json`](../.vscode/tasks.json):

| Configuration | Attaches to |
| --- | --- |
| `Docker: Attach to Django` | The `web` container — views, serializers, `services.py` |
| `Docker: Attach to Celery worker` | The `celery` profile worker — `mrp/engine.py`, `reports/report_generators.py` as real async tasks |
| `Docker: Attach to one-shot command` | A `docker: debug …` task (tests, pytest, `demo_erp`), which waits for you to attach before running |
| `Docker: Django + Celery worker` | Both long-running containers at once |
| `Local: …` | The plain virtualenv workflow, for when Docker is overkill |

Two behaviours worth knowing. Debugging runs Django with `--noreload`, because Django's autoreloader forks a child to serve requests while the debugger stays attached to the parent — so breakpoints would silently never hit. Use the **docker: restart web** task to pick up code changes during a debug session, or plain `docker compose up` when you want autoreload and no debugger. Separately, dev settings run Celery tasks eagerly inside the web process, so report generation and MRP runs are hit by the Django debugger by default; the `celery` profile flips that off and dispatches them to the worker for real.

Editing `docker/*.sh` needs `docker compose build`: those scripts are baked into the image, unlike the source tree, which is bind-mounted.
