# RelayNorth API and Railway Deployment Guide

This document describes the API surface in the current repository, how to run it locally, and how to deploy the application with Railway.

It also explains an important regional limitation: the current application has logical relay regions, but it does not yet route outbound requests through separate regional workers. Railway currently documents deployment regions in US West, US East, EU West, and Southeast Asia/Singapore; it does not list an India deployment region. See the [Railway regions documentation](https://docs.railway.com/deployments/regions).

## 1. Current architecture

The repository contains one FastAPI application that serves:

- The public RelayNorth HTML site.
- The React admin console from `/admin/`.
- The session and proxy APIs under `/api/v1/`.
- Optional PostgreSQL persistence.
- Optional Redis rate limiting and active-request coordination.

The current request flow is:

```text
Browser
  -> POST /api/v1/sessions
  <- session_id, signed session_token, browse_url
  -> GET /browse?session=...&token=...&url=...
  -> GET /api/v1/proxy/{session_id}?token=...&url=...
  -> approved upstream origin
```

The current proxy is deliberately restricted. Destinations must be in `ALLOWED_PROXY_ORIGINS`, private IPs are blocked, and only form-urlencoded POST requests are accepted.

## 2. Deployment reality for India

### What Railway can do

Railway can deploy the FastAPI service, React admin, PostgreSQL, and Redis. It can also deploy services in its supported regions and provides a `PORT` variable, health checks, service variables, private networking, and deployment logs. See the [Railway build and deploy guide](https://docs.railway.com/build-deploy).

### What Railway cannot currently guarantee

Railway's documented regions do not include an India/Mumbai region. Deploying the current application to Railway's Southeast Asia region means the outbound request will originate from that Railway service, not from an Indian IP.

Railway multi-region replicas are designed for routing incoming users to the nearest replica. They do not implement a user-selected proxy egress region, and Railway does not provide sticky sessions. See [Railway regional deployments](https://docs.railway.com/deployments/optimize-performance).

### Recommended production topology

For an actual India exit IP, use this topology:

```text
User
  |
  v
Railway control plane (Singapore or another supported region)
  |  creates sessions, serves UI, stores metadata
  |
  +--> India relay worker on an India-based VM/provider
  +--> Europe relay worker later
  +--> US relay worker later
```

The control plane should assign a session to a worker and the worker should make the outbound HTTP request. The current `fetch_for_session()` implementation makes the request from the FastAPI process itself, so this worker routing is a future implementation step.

If an India egress IP is not required for the first release, deploy the complete application to Railway Southeast Asia and label it as a Singapore/APAC relay. Do not label it India when the service is not running in India.

## 3. Public HTML routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/` | Public home page and relay form |
| `GET` | `/browse` | Browser shell that loads a relay session |
| `GET` | `/faq` | Public FAQ |
| `GET` | `/privacy` | Privacy page |
| `GET` | `/terms` | Terms page |
| `GET` | `/acceptable-use` | Acceptable-use page |
| `GET` | `/copyright` | Copyright page |
| `GET` | `/status` | Public status page |
| `GET` | `/blog` | Published blog index |
| `GET` | `/blog/{slug}` | Published blog article |
| `GET` | `/admin` or `/admin/` | React admin shell |
| `GET` | `/admin/{path}` | React admin deep-link fallback |

Static assets are mounted at `/static`. Admin build assets are mounted at `/admin/assets`.

## 4. Public API

Base URL:

```text
https://your-domain.example/api/v1
```

### List relay regions

```http
GET /api/v1/regions
```

Returns the currently configured region records.

Example response:

```json
[
  {
    "cell_id": "eu",
    "display_name": "Europe",
    "country": "Europe",
    "health_state": "ready",
    "capacity": 72,
    "egress_ip_pool": ["203.0.113.10"],
    "drain_deadline": null,
    "policy_version": 1
  }
]
```

The `egress_ip_pool` value is currently metadata. It does not cause traffic to leave through that IP.

### Create a browsing session

```http
POST /api/v1/sessions
Content-Type: application/json
```

Request:

```json
{
  "url": "https://approved-origin.example/search?q=flipkart",
  "region": "eu"
}
```

Current allowed region values are `eu`, `us`, and `apac`. An `in` region must be added in code before an India option can be used.

Response:

```json
{
  "session_id": "opaque-session-id",
  "cell_id": "eu",
  "region": "Europe",
  "expires_at": 1780000000,
  "session_token": "signed-token",
  "browse_url": "/browse?session=opaque-session-id&token=signed-token&url=..."
}
```

Possible errors:

| Status | Meaning |
| --- | --- |
| `400` | Invalid or oversized URL |
| `403` | Destination is not allowlisted, is private, or uses a disallowed port |
| `429` | Session creation rate limit reached |
| `503` | Selected region is not ready |

### Read session status

```http
GET /api/v1/sessions/{session_id}/status
```

Response:

```json
{
  "session_id": "opaque-session-id",
  "cell_id": "eu",
  "expires_at": 1780000000,
  "request_count": 4,
  "bytes_out": 18234
}
```

Returns `404` when the session does not exist or has expired.

### Fetch an approved page through the relay

```http
GET /api/v1/proxy/{session_id}?token={session_token}&url={encoded_target_url}
```

The endpoint also accepts `POST`, but only with:

```http
Content-Type: application/x-www-form-urlencoded
```

Current limits include:

- Session lifetime: 15 minutes by default.
- Maximum URL length: 2,048 characters.
- Maximum response size: 2 MB.
- Maximum form body size: 32 KB.
- Maximum concurrent requests per session: 2.
- Maximum requests per session: 120.

Possible errors:

| Status | Meaning |
| --- | --- |
| `401` | Missing or invalid signed session token |
| `403` | Destination or redirect is not allowed |
| `404` | Session not found or expired |
| `413` | Response or form body is too large |
| `415` | POST content type is not form-urlencoded |
| `429` | Session, IP, or concurrency limit reached |
| `502` | Approved origin could not be reached |
| `503` | Pinned relay region is unavailable |

### Submit an abuse report

```http
POST /api/v1/abuse-reports
Content-Type: application/json
```

Request:

```json
{
  "category": "abuse category",
  "description": "A short report description."
}
```

Response:

```json
{
  "report_id": "abuse_0001",
  "status": "received"
}
```

### Read published blog posts

```http
GET /api/v1/blog/posts
GET /api/v1/blog/posts/{slug}
```

The detail endpoint includes sanitized `body_html`. It returns `404` for missing or unpublished posts.

## 5. Admin API

Admin authentication uses an `HttpOnly` cookie named `relay_admin`.

### Login

```http
POST /api/v1/admin/auth/login
Content-Type: application/json
```

Request:

```json
{
  "email": "admin@example.com",
  "password": "replace-this-password"
}
```

The response sets the secure admin cookie when `APP_ENV=production`.

### Logout

```http
POST /api/v1/admin/auth/logout
```

### Current admin identity

```http
GET /api/v1/admin/me
```

Returns `401` without a valid admin cookie.

### Blog management

| Method | Route | Required role |
| --- | --- | --- |
| `GET` | `/api/v1/admin/posts` | owner, editor |
| `POST` | `/api/v1/admin/posts` | owner, editor |
| `PATCH` | `/api/v1/admin/posts/{slug}` | owner, editor |
| `POST` | `/api/v1/admin/posts/{slug}/publish` | owner, editor |
| `POST` | `/api/v1/admin/posts/{slug}/archive` | owner, editor |
| `GET` | `/api/v1/admin/posts/{slug}/revisions` | owner, editor |

Post payload:

```json
{
  "title": "A useful title",
  "slug": "a-useful-title",
  "excerpt": "A short excerpt.",
  "body_markdown": "# Article body",
  "category": "Guides",
  "tags": ["proxy", "security"],
  "seo_title": "",
  "seo_description": "",
  "status": "draft"
}
```

Allowed status values are `draft`, `scheduled`, `published`, and `archived`.

### Region operations

| Method | Route | Required role |
| --- | --- | --- |
| `GET` | `/api/v1/admin/regions` | owner, security |
| `PATCH` | `/api/v1/admin/regions/{cell_id}/state?state=ready` | owner, security |

Allowed region states are `ready`, `draining`, and `unavailable`.

### Operations

| Method | Route | Required role |
| --- | --- | --- |
| `GET` | `/api/v1/admin/abuse-reports` | owner, support, security |
| `GET` | `/api/v1/admin/usage` | owner, support, security |
| `GET` | `/api/v1/admin/audit-events` | owner, security |

### Health check

```http
GET /healthz
```

Returns `200` when the application and configured dependencies are healthy, otherwise `503`.

Use `/healthz` as the Railway healthcheck path. Railway healthchecks expect a `2xx` response before switching traffic to a new deployment. See the [Railway healthcheck documentation](https://docs.railway.com/deployments/healthchecks).

## 6. Local testing

Install dependencies:

```bash
cd /Users/raju/work/proxy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm --prefix admin install
npm --prefix admin run build
```

Start the owned upstream test site:

```bash
uvicorn tests.owned_origin:app --host 127.0.0.1 --port 9001
```

In another terminal, start RelayNorth:

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Open:

- Public site: `http://127.0.0.1:8000/`
- Admin: `http://127.0.0.1:8000/admin/`
- API docs: `http://127.0.0.1:8000/docs`

Run automated tests:

```bash
pytest -q
```

The current fixture intentionally uses an approved local origin. Do not enable arbitrary internet destinations just to make local testing convenient.

## 7. Railway deployment

### 7.1 Create the Railway project

1. Push this repository to GitHub.
2. Create a Railway project from the repository.
3. Add a PostgreSQL service.
4. Add a Redis service.
5. Add the application service from the repository.

Railway provides PostgreSQL and Redis templates and private service networking. See [Railway databases](https://docs.railway.com/databases) and [Railway Redis](https://docs.railway.com/databases/redis).

### 7.2 Build the application with a Dockerfile

The repository currently has no production Dockerfile. Add one at the repository root so Railway builds the React admin and Python API together:

```dockerfile
FROM node:22-bookworm-slim AS admin-build
WORKDIR /build/admin
COPY admin/package*.json ./
RUN npm ci
COPY admin/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
COPY --from=admin-build /build/admin/dist ./admin/dist

ENV PYTHONUNBUFFERED=1

CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```

Railway uses a root `Dockerfile` automatically when one is present. See [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles).

### 7.3 Configure Railway variables

Set these variables on the application service. Do not commit real values to Git.

```env
APP_ENV=production
SECRET_KEY=<generate-a-long-random-secret>
ADMIN_EMAIL=admin@your-domain.example
ADMIN_PASSWORD=<strong-unique-password>

DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}

ALLOW_LOCAL_TEST_ORIGIN=false
ALLOWED_PROXY_ORIGINS=https://approved-origin.your-domain.example
```

Railway supports service reference variables such as `${{Postgres.DATABASE_URL}}` and `${{Redis.REDIS_URL}}`. See [Railway variables](https://docs.railway.com/variables).

For production, do not use:

```env
SECRET_KEY=dev-only-change-me
ADMIN_PASSWORD=change-me-now
ALLOW_LOCAL_TEST_ORIGIN=true
ALLOWED_PROXY_ORIGINS=*
```

### 7.4 Configure the service

In the Railway application service:

- Deploy region: select a supported region, such as Southeast Asia/Singapore.
- Healthcheck path: `/healthz`.
- Port: let Railway provide `PORT`.
- Restart policy: restart on failure.
- Pre-deploy command: `alembic upgrade head` after the database service is ready.
- Generate a Railway domain first, then attach your custom domain.

Railway expects web services to listen on the injected `PORT` variable. A fixed port such as `8000` will fail healthchecks in production unless it is only used as a fallback.

### 7.5 Deploy with the CLI

```bash
npm install -g @railway/cli
railway login
railway link
railway up
```

Railway also supports GitHub-triggered deployments. See the [Railway CLI deploy documentation](https://docs.railway.com/cli/deploy).

### 7.6 Verify the deployment

```bash
export BASE_URL="https://your-service.up.railway.app"

curl -fsS "$BASE_URL/healthz"
curl -fsS "$BASE_URL/api/v1/regions"
curl -I "$BASE_URL/"
curl -I "$BASE_URL/admin/"
```

Check the generated OpenAPI document:

```text
https://your-service.up.railway.app/docs
https://your-service.up.railway.app/openapi.json
```

The first proxy smoke test should use an HTTPS origin listed in `ALLOWED_PROXY_ORIGINS`:

```bash
curl -sS -X POST "$BASE_URL/api/v1/sessions" \
  -H 'content-type: application/json' \
  -d '{"url":"https://approved-origin.your-domain.example/","region":"eu"}'
```

Do not use `http://127.0.0.1:9001` after deploying. That origin exists only inside local development.

## 8. India deployment options

### Option A: Railway control plane plus India worker

This is the recommended Railway-compatible architecture.

1. Deploy the current control plane to Railway Singapore.
2. Deploy a separate relay worker on an India-based VM or provider.
3. Give the worker a private/authenticated API endpoint.
4. Add `in` to the configured region list.
5. Change session creation to assign `in` sessions to the India worker.
6. Move the outbound `httpx` fetch from the control plane into the selected worker.
7. Keep cookies, request limits, response limits, redirect validation, and audit data tied to the session ID.

The worker should not be an unrestricted public proxy. It should accept only authenticated requests from the control plane and enforce the same destination policy.

### Option B: Deploy everything on an India-based provider

If the first release only needs India:

1. Deploy the FastAPI application to an India-based VM or hosting provider.
2. Run PostgreSQL and Redis in the same region or use managed services with low-latency private networking.
3. Set the only ready region to `in` after adding it to the application code.
4. Use a domain with HTTPS.
5. Confirm the outbound IP from an endpoint you own.

This is simpler for an India-only launch, but it does not use Railway for the application service.

### Option C: Railway Singapore as a temporary APAC deployment

If India egress is not required yet, deploy to Railway Southeast Asia and use `apac` as the active region. This validates the UI and API flow while avoiding a false claim that traffic originates in India.

## 9. Required work before true region selection

The following items are not implemented by the current vertical slice:

- An `in` region in `SessionCreate` and the default store.
- A worker-routing interface based on `cell_id`.
- Separate India, EU, and US egress workers.
- Authenticated control-plane-to-worker communication.
- A proxy-friendly path URL that preserves `/search?q=flipkart` across every link and redirect.
- A browser-level test that verifies the selected worker's outbound region.

The current `egress_ip_pool` field is descriptive only. It does not select a network interface or proxy server.

## 10. Production security checklist

- Use HTTPS for the public site, admin, and worker communication.
- Generate a unique `SECRET_KEY`.
- Replace the default admin password.
- Set `ALLOW_LOCAL_TEST_ORIGIN=false`.
- Use a strict HTTPS `ALLOWED_PROXY_ORIGINS` list.
- Keep private IP, metadata endpoint, unsafe scheme, port, response-size, and request-rate protections enabled.
- Do not allow `*` origins without a separate abuse/security review.
- Do not forward client authorization, proxy, or forwarding headers to upstream sites.
- Do not log passwords, session tokens, cookies, or full browsing bodies.
- Keep PostgreSQL and Redis private to the Railway project.
- Configure database backups before storing production content.
- Add continuous uptime monitoring; Railway's deployment healthcheck runs during deployment and is not a continuous monitor. See [Railway healthchecks](https://docs.railway.com/deployments/healthchecks).
- Maintain an abuse-report and takedown process before opening the proxy to arbitrary public destinations.

## 11. Deployment acceptance checklist

```text
[ ] Railway app service is healthy at /healthz
[ ] PostgreSQL and Redis variables resolve
[ ] Admin build is present at /admin/
[ ] Admin login works over HTTPS
[ ] Local test origin is disabled
[ ] Only approved HTTPS origins are accepted
[ ] Session creation works for a ready region
[ ] Session status increments request_count and bytes_out
[ ] Expired sessions return 404
[ ] Unavailable regions reject new sessions
[ ] Redirects are revalidated against the approved origin
[ ] Form-urlencoded POSTs work
[ ] JSON and oversized POSTs are rejected
[ ] Private and metadata destinations are rejected
[ ] Railway logs contain no secrets or browsing bodies
[ ] Backups and monitoring are configured
[ ] India egress is verified separately if an India worker is deployed
```

## 12. Regional egress IP pools

### 12.1 Desired production behavior

The production service can be operated as three regional pools:

```text
Asia / India       10 egress IPs
Europe             10 egress IPs
United States      10 egress IPs
```

When a visitor selects a region, the control plane should assign one healthy egress IP from that region to the new session. The session stays pinned to that IP until it expires. This is important because rotating the IP during a session can invalidate cookies, break redirects, and make the destination treat the browser as a new client.

If “Asia” specifically means India, use the region key `in` and label it “India relay”. Keep `apac` for Singapore or another non-India Asia deployment. The current application only has the logical keys `eu`, `us`, and `apac`; adding an India pool requires the worker-routing changes described below.

### 12.2 Railway is the control plane, not the 30-IP pool

Do not assume that Railway replicas provide 10 selectable public exit IPs. Railway can run the control plane, UI, PostgreSQL, and Redis, but the application must use separately managed relay workers or an egress provider that explicitly supplies the required regional addresses.

Recommended topology:

```text
                         +---------------------------+
User ------------------>| Railway control plane      |
                         | UI + FastAPI + PostgreSQL  |
                         | Redis lease coordinator   |
                         +-------------+-------------+
                                       |
                         authenticated worker requests
                                       |
          +----------------------------+----------------------------+
          |                            |                            |
          v                            v                            v
   India worker pool            Europe worker pool             US worker pool
   in-01 ... in-10              eu-01 ... eu-10                us-01 ... us-10
   10 India egress IPs           10 EU egress IPs                10 US egress IPs
```

Each worker must have a known outbound address in its advertised region. A worker can be a VM, container host, or managed egress endpoint from a provider that permits this use. Verify the provider's acceptable-use policy, reserved-IP behavior, bandwidth limits, and whether the address is shared.

### 12.3 IP pool data model

The existing `RegionModel.egress_ip_pool` field is only descriptive metadata. It does not select a source address for `httpx`, and the current FastAPI process still performs the outbound request itself. For production, replace the JSON list with individually tracked workers/IPs.

Conceptual records:

| Record | Important fields | Purpose |
| --- | --- | --- |
| `relay_workers` | `id`, `region`, `endpoint`, `auth_key_id`, `status`, `last_heartbeat` | A regional worker that can fetch pages |
| `relay_egress_ips` | `id`, `worker_id`, `region`, `address`, `status`, `active_sessions`, `last_check` | One selectable egress address |
| `relay_ip_leases` | `session_id`, `egress_ip_id`, `leased_at`, `expires_at` | Pins one session to one address |
| `relay_sessions` | `session_id`, `region`, `egress_ip_id`, `worker_id` | Auditable session assignment |

Example pool inventory (use real addresses only in the deployment database or secret manager):

```text
region=in   in-01 ... in-10   status=ready|draining|unavailable
region=eu   eu-01 ... eu-10   status=ready|draining|unavailable
region=us   us-01 ... us-10   status=ready|draining|unavailable
```

Never publish the actual egress addresses in the public regions response. Return the region label, readiness, and optionally an approximate available count. The current development endpoint exposes `egress_ip_pool` for inspection; remove or redact that field before a public launch.

### 12.4 Assignment and lease algorithm

The control plane should perform this sequence in one coordinated operation:

1. Validate the requested region and the destination allowlist.
2. Load IPs in that region whose status is `ready` and whose worker heartbeat is current.
3. Acquire a Redis lock/lease for one IP using an atomic `SET ... NX EX` operation. Use a key such as `relay:lease:in:in-04`.
4. Choose the least-loaded healthy IP, with a random tie-breaker or round-robin cursor.
5. Persist the lease and create the session with `egress_ip_id` and `worker_id`.
6. Send the request to the selected worker; the worker makes the outbound request using its assigned egress address.
7. Release the lease when the session expires. A sweeper must also release leases whose TTL has elapsed after a crash.

If all ten IPs in the selected region are leased or unhealthy, return `503` with a safe message such as `Selected relay region is busy`. Do not silently move the user to another region; that would violate the region they selected.

Recommended lease settings:

```text
session TTL:             15 minutes initially
lease TTL:               session TTL + 60 seconds
worker heartbeat:        every 15 seconds
worker unhealthy after:  45 seconds without a heartbeat
draining IP:             no new leases; existing sessions may finish
unavailable IP:          no new leases; terminate or retry later
```

Do not reassign an active session to another egress IP after a worker failure unless the product explicitly accepts cookie and identity changes. The safer behavior is to show a retry message and start a new session.

### 12.5 Worker contract

The worker API must be private or authenticated with mTLS or a short-lived signed worker token. It must not be an unrestricted public forward proxy.

Minimum internal operations:

```http
POST /internal/v1/workers/{worker_id}/heartbeat
POST /internal/v1/relay/fetch
GET  /internal/v1/workers/{worker_id}/egress-check
```

The control plane sends the session ID, approved target URL, request method, safe headers, and request body. The worker returns a bounded response. The worker must enforce the same SSRF, scheme, port, redirect, body-size, response-size, rate, and abuse policies as the control plane.

The egress check should call an endpoint owned by you that records the source address. Do not use a third-party “what is my IP” service as the only production verification. Store the observed address as an operational check and compare it with the configured address before marking the worker ready.

### 12.6 Proposed internal API additions

These routes are a design target; they are not implemented in the current vertical slice.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/regions` | Public region labels and readiness only |
| `POST` | `/internal/v1/worker-register` | Register a worker and its egress metadata |
| `POST` | `/internal/v1/workers/{worker_id}/heartbeat` | Update worker and IP health |
| `POST` | `/internal/v1/leases/acquire` | Atomically lease one IP for a session |
| `POST` | `/internal/v1/leases/{lease_id}/release` | Release an expired/closed lease |
| `POST` | `/internal/v1/relay/fetch` | Fetch through the selected worker |
| `GET` | `/api/v1/sessions/{session_id}/status` | Return selected region and operational counters |

The public session response should expose the selected region and a session ID, not the worker hostname, private endpoint, lease key, or egress address.

### 12.7 Railway deployment runbook for 30 egress IPs

#### A. Deploy the control plane on Railway

1. Create the Railway application service from this repository.
2. Add PostgreSQL and Redis services.
3. Deploy the control plane to a supported region close to the worker/control traffic, such as Singapore for an India-first launch.
4. Set `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, admin credentials, and the strict HTTPS allowlist.
5. Run `alembic upgrade head` as the pre-deploy migration command.
6. Configure `/healthz` as the health check and verify the app listens on Railway's injected `PORT`.

#### B. Provision the regional workers

Provision ten workers or ten provider-backed egress endpoints for each pool:

```text
India:  in-01 ... in-10
Europe: eu-01 ... eu-10
US:     us-01 ... us-10
```

Each worker needs:

- A stable private/control-plane endpoint.
- A known outbound public address.
- A worker identity and secret that is different from every other worker.
- The same destination and response safety policy as the control plane.
- Heartbeat and graceful-drain support.
- Logs that exclude session tokens, cookies, passwords, and browsing bodies.

Deploy the worker software separately from the public Railway service. If the provider only gives one outbound address per VM, use at least 30 VMs. If it provides a managed pool, register each address as a separate `relay_egress_ips` record and confirm that the provider pins requests as documented.

#### C. Register and verify the pool

For every worker:

1. Register its region and egress address through the authenticated internal API.
2. Run the egress check from the worker.
3. Confirm the observed address matches the inventory record.
4. Mark it `ready` only after the heartbeat and allowlist checks succeed.
5. Start with one IP per region, then add the remaining nine after the end-to-end test passes.

#### D. Test assignment before public launch

Use an origin that you own and that returns the observed source IP, for example:

```text
https://relay-test.your-domain.example/echo
```

For each region, create sessions repeatedly and verify:

```text
requested region = assigned region
assigned worker belongs to that region
observed source IP belongs to that region's pool
the same session keeps the same IP
two concurrent sessions can receive different free IPs
the 11th session is queued/rejected when all 10 IP leases are occupied
an unhealthy IP receives no new leases
an expired lease becomes available again
```

Do not use a destination you do not own for this test. The public proxy should remain allowlisted until abuse controls, logging, takedown handling, and the worker boundary have been reviewed.

### 12.8 Local mock-pool test

Real Indian, EU, or US source addresses cannot be reproduced on a laptop by changing a dropdown. Local testing should verify assignment and session pinning with fake worker labels:

```text
asia-mock-01, asia-mock-02 ... asia-mock-10
eu-mock-01,   eu-mock-02   ... eu-mock-10
us-mock-01,   us-mock-02   ... us-mock-10
```

Run the control plane, Redis, the owned origin on port `9001`, and mock workers on separate local ports. The mock worker response should include its worker ID and a fake `egress_ip` label. Assert that the control plane chooses only the requested pool and that the label remains unchanged for the lifetime of a session. A real-region smoke test is required after deployment because local labels cannot prove the public source address.

The current repository does not yet contain the worker service or lease allocator, so the existing `egress_ip_pool` values and `/api/v1/regions` response must not be treated as proof of regional egress. Implement the pool and worker contract before enabling the three-region production selector.

### 12.9 Operational metrics and alerts

Track these metrics by region and IP:

- Ready, draining, unavailable, and heartbeat-stale workers.
- Free, leased, and failed egress IPs.
- Lease acquisition failures and `503` responses by region.
- Requests, bytes, upstream latency, timeout rate, and response-size rejections.
- Destination blocks, abuse reports, and worker authentication failures.
- Sessions whose observed source address does not match the inventory.

Alert when a region has fewer than three ready IPs, when lease acquisition fails repeatedly, when an egress check changes unexpectedly, or when a worker misses three heartbeats. Keep the public status page coarse-grained; do not expose individual addresses or worker endpoints.

### 12.10 Per-IP rate limiting and spam protection

Rate-limit before selecting or leasing an egress IP. Otherwise one client can reserve the entire regional pool with session-creation spam.

The current application already has development limits for session creation and proxy requests, but production traffic may arrive through Railway's edge proxy. The application must resolve the real client IP only from a configured, trusted forwarding boundary. Never accept an arbitrary `X-Forwarded-For` header from the public internet.

Production requirements:

- Use Redis-backed counters shared by all Railway replicas.
- Normalize IPv4 and IPv6 addresses.
- Use an HMAC of the client IP for Redis keys and logs.
- Return `429 Too Many Requests` with `Retry-After`.
- Keep separate counters for client IP, session, active sessions, concurrency, and permalink creation.
- Add a short-lived progressive block only after repeated violations.
- Keep the allowlist and SSRF checks independent of rate limiting.
- Treat Redis as required in production; the in-memory fallback is local-development behavior only.

Suggested starting values:

```env
SESSION_CREATE_LIMIT_PER_IP=10/60s
ACTIVE_SESSION_LIMIT_PER_IP=5
PROXY_REQUEST_LIMIT_PER_IP=60/60s
SESSION_REQUEST_LIMIT=120
SESSION_CONCURRENCY_LIMIT=2
PERMALINK_LIMIT_PER_IP=10/3600s
ABUSE_REPORT_LIMIT_PER_IP=5/3600s
```

Use an atomic Redis Lua script or equivalent transaction for each check-and-increment. A fixed-window key can use this shape:

```text
relaynorth:rate:session-create:ip:{hmac_ip}:{unix_window}
```

Set the expiry when the counter is first created. Release active-session counters when sessions expire, and run a sweeper to repair counters after process crashes. Test that the 11th request from one IP returns `429`, that two Railway replicas share the same bucket, and that an untrusted forwarded header cannot spoof the client identity.
