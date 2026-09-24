# proxy-backend

RelayNorth control plane — the FastAPI API, restricted proxy endpoint, and the
optional regional worker. Split out from the RelayNorth monorepo so it can be
deployed on Railway as its own service. The public site and admin console live in
[`proxy-frontend`](https://github.com/therajusah/proxy-frontend) and
[`proxy-admin`](https://github.com/therajusah/proxy-admin).

## What's here

- `backend/` — FastAPI app (`backend.app.main:app`): sessions, proxy, blog, admin APIs.
- `worker/` — optional regional egress worker (`worker.main:app`).
- `alembic/`, `alembic.ini` — database migrations.
- `tests/` — pytest suite.
- `Dockerfile`, `Procfile` — deployment entrypoints.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn backend.app.main:app --reload --port 8000
# tests
pytest -q
```

With no `DATABASE_URL`/`REDIS_URL` reachable the app runs on its in-memory
fallback. `GET /healthz` returns 200 with `"degraded": true` in that mode.

## Deploy on Railway (Docker)

Railway auto-detects the `Dockerfile` and builds it — no extra config needed.

1. **New Project → Deploy from GitHub repo →** `therajusah/proxy-backend`.
2. Railway builds the `Dockerfile` and starts it. The container listens on
   `$PORT` (injected by Railway) via the `CMD`.
3. Add a **PostgreSQL** and a **Redis** plugin (required for multiple replicas —
   see the monorepo `docs/DEPLOYMENT.md` §7).
4. Set service **Variables**:

   | Variable | Value |
   |----------|-------|
   | `APP_ENV` | `production` |
   | `SECRET_KEY` | a long random secret |
   | `ADMIN_EMAIL` / `ADMIN_PASSWORD` | real operator credentials |
   | `DATABASE_URL` | from the Postgres plugin (`postgresql+psycopg://…`) |
   | `REDIS_URL` | from the Redis plugin |
   | `ALLOWED_PROXY_ORIGINS` | reviewed HTTPS origins (never `*`) |
   | `ALLOW_LOCAL_TEST_ORIGIN` | `false` |
   | `ALLOWED_APP_ORIGINS` | the deployed **frontend** and **admin** URLs, comma-separated (enables CORS so those services can call this API) |
   | `FORWARDED_ALLOW_IPS` | `*` (Railway terminates TLS at its edge) |

5. Run migrations once: `alembic -x sqlalchemy.url="$DATABASE_URL" upgrade head`
   (or rely on the app's startup `create_all`).

> This service is **API-only** when deployed alone: the static mounts for the
> public site and admin console are skipped when those directories are absent, so
> `/`, `/blog`, and `/admin/` are served by the other two services instead. Point
> those services at this API's URL and list their origins in `ALLOWED_APP_ORIGINS`.

The same image can run a worker by setting `APP_MODULE=worker.main:app`.
