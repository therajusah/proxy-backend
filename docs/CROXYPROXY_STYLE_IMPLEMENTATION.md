# CroxyProxy-style RelayNorth Implementation Plan

This document describes the public behavior observed on [CroxyProxy](https://www.croxyproxy.com/) and turns that behavior into an implementation plan for RelayNorth. It is a functional analysis, not a copy of CroxyProxy's code, branding, assets, or protected name.

The implementation should keep RelayNorth's existing allowlist, short-lived sessions, region/IP pool, SSRF protection, and abuse controls. Do not make the service an unrestricted public forward proxy without a separate security and legal review.

## 1. Observed user flow

### 1.1 Landing page

The public page provides:

- One input that accepts a URL or a search query.
- A `Go!` action.
- Quick links for common destinations.
- A share/permalink action after a destination is opened.
- A browser-like page wrapper instead of sending the user to the destination's original hostname.

CroxyProxy describes this as indirect browsing: the browser connects to the proxy, the proxy downloads the external resource, and the proxy forwards the response back to the browser. Its public description also says that one page can be opened without routing the whole device through the proxy. See [CroxyProxy's home page](https://www.croxyproxy.com/) and [terms of use](https://www.croxyproxy.com/terms).

### 1.2 Launch and proxy URL

When a quick link is opened, the site first displays a short `Proxy is launching...` state. The browser then lands on a proxy server URL similar to:

```text
https://<relay-host>/<target-path>?__cpo=<encoded-target>
```

During inspection, a Google quick link opened on a CroxyProxy relay host with a URL like:

```text
https://51.159.107.232/?__cpo=aHR0cHM6Ly93d3cuZ29vZ2xlLmNvbQ
```

The `__cpo` value is URL-safe Base64-looking data containing the destination URL. This is an observation of the public URL shape, not a recommendation to use an unsigned Base64 value in RelayNorth. Base64 is reversible and must not be treated as authorization.

### 1.3 Browser wrapper

The proxied page keeps a small service-owned header containing:

- A home link back to the proxy landing page.
- A read-only display of the current target URL.
- A premium/service link.
- A `Permalink` action.

The main page area then displays the fetched destination. Resource and navigation links continue to use the relay host. For example, links on the proxied Google consent page remained on the relay host and carried another encoded target in the query string.

### 1.4 Permalink behavior

The public UI reports that a copied permalink is temporary; during inspection it reported a three-hour lifetime. A RelayNorth permalink should use an explicit signed, expiring token and should never include the user's upstream cookies, authorization headers, or POST body.

### 1.5 Service limits and privacy boundary

CroxyProxy's public terms explain that fetched resources may be modified, that rerouting all subresources is not guaranteed, and that the service does not promise complete anonymity. Its privacy policy describes access logs, cookies, and a limited retention goal. RelayNorth should publish equivalent, accurate limitations before launch rather than promising anonymity or perfect page compatibility. See [CroxyProxy's privacy policy](https://www.croxyproxy.com/privacy).

## 2. RelayNorth target architecture

```text
Browser
  |
  | 1. POST /api/v1/sessions {url or query, region}
  v
Railway control plane
  |-- validates URL/search input
  |-- creates signed session
  |-- leases one IP from the selected region
  |-- returns relay URL
  |
  | 2. GET /r/{session}/{target-path}?__cpo={signed-target}
  v
Regional relay worker
  |-- validates session and signed target
  |-- fetches upstream through the leased egress IP
  |-- rewrites supported response links and cookies
  |-- returns bounded response
  v
Approved upstream origin
```

Railway remains the control plane. The actual outbound request must be made by the worker that owns the selected regional egress IP. The 30-IP pool design is documented in [API_AND_RAILWAY_DEPLOYMENT.md](./API_AND_RAILWAY_DEPLOYMENT.md#12-regional-egress-ip-pools).

## 3. URL and search handling

### 3.1 Normalize input

The landing form should accept either:

```text
https://example.com/search?q=flipkart
```

or a search query such as:

```text
flipkart
```

Normalization rules:

1. Trim whitespace and reject empty or oversized input.
2. If the value has an allowed `http` or `https` scheme, parse it as a URL.
3. If the value has no scheme and matches a hostname, prepend `https://`.
4. Otherwise create a URL against the configured search provider, for example:
   `https://www.bing.com/search?q=<encoded-query>`.
5. Validate the resulting origin against `ALLOWED_PROXY_ORIGINS` in the current development build.
6. Reject private, loopback, link-local, metadata, unsupported-scheme, and disallowed-port destinations.

The search provider must be configurable. Do not silently send user input to an unexpected third party, and display the resulting target in the relay header.

### 3.2 Use signed targets, not plain Base64

Use an opaque signed token such as:

```text
__cpo=<base64url(payload)>.<base64url(signature)>
```

Suggested payload:

```json
{
  "sid": "opaque-session-id",
  "target": "https://approved-origin.example/search?q=flipkart",
  "region": "in",
  "exp": 1780000000,
  "version": 1
}
```

The server must verify the signature, expiry, session ID, target allowlist, and selected region on every request. The token should be short-lived and should not expose internal worker IDs or IP addresses.

Recommended RelayNorth URL shape:

```text
https://relay.example/r/{session_id}/{target_path}?{target_query}&__cpo={signed_token}
```

The mirrored path makes the browser behave like it is visiting the target page, while the signed token remains the source of truth. Do not trust the mirrored path or query by themselves.

## 4. Response rewriting pipeline

The worker should process only responses that are safe and supported for rewriting.

### 4.1 HTML

For `text/html` responses:

- Rewrite absolute and relative `a[href]` links.
- Rewrite `img[src]`, `script[src]`, `link[href]`, `iframe[src]`, `video[src]`, `audio[src]`, and `source[src]` where supported.
- Rewrite `form[action]` and preserve the method and supported form body.
- Rewrite `meta[http-equiv="refresh"]` URLs.
- Preserve fragments locally; fragments are not sent to the server.
- Keep external links on the relay host when they point to an allowed target.
- Reject or leave unsupported schemes such as `javascript:`, `data:`, `file:`, and `mailto:` according to policy.

Use an HTML parser and sanitizer. Do not perform blind string replacement on HTML or JavaScript source.

### 4.2 CSS

For `text/css` responses:

- Rewrite `url(...)` references that resolve to an allowed upstream resource.
- Preserve data URLs only when their size and MIME type are allowed.
- Keep CSS imports inside the same session and target policy.

### 4.3 Redirects

For upstream `3xx` responses:

1. Resolve the `Location` header against the current target URL.
2. Validate the destination with the same SSRF and allowlist checks.
3. Return a relay URL for the validated destination.
4. Reject redirects to private networks, unsupported schemes, or unapproved origins.

Never forward an upstream `Location` header directly if it would send the browser to the destination's real hostname.

### 4.4 Cookies

Cookies must be isolated per RelayNorth session:

- Store upstream cookie state server-side, keyed by session and upstream origin.
- Do not set the upstream domain on the browser cookie.
- Set only an opaque RelayNorth session cookie or token on the relay domain.
- Strip or rewrite `Domain`, `Path`, `Secure`, and `SameSite` attributes deliberately.
- Never forward cookies between sessions or between unrelated upstream origins.
- Expire all session cookie state when the relay session expires.

Do not place upstream cookie values in a shareable permalink.

### 4.5 Headers and bodies

Strip hop-by-hop headers and control forwarding explicitly. In particular, do not forward the user's `Authorization`, `Proxy-Authorization`, or arbitrary `Forwarded` headers to the upstream origin. Bound request bodies, response bodies, decompression, redirects, and timeouts.

Start with `GET` and form-urlencoded `POST`. Add streaming, range requests, WebSockets, and uploads only as separate reviewed features because each requires additional limits and rewriting rules.

## 5. Relay API changes

The current API creates a session and then uses `/api/v1/proxy/{session_id}`. To provide a CroxyProxy-style browser URL, add a relay route while retaining the API for testing:

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/sessions` | Normalize target, choose region, lease an egress IP |
| `GET` | `/r/{session_id}/{path}` | Fetch a target through the browser-friendly relay URL |
| `POST` | `/r/{session_id}/{path}` | Submit supported forms through the relay |
| `GET` | `/api/v1/sessions/{session_id}/status` | Return session and region status without exposing the egress IP |
| `POST` | `/api/v1/sessions/{session_id}/permalink` | Create a short-lived share token |
| `POST` | `/internal/v1/relay/fetch` | Control-plane-to-worker fetch operation |
| `POST` | `/internal/v1/workers/{worker_id}/heartbeat` | Worker health and egress verification |

Example session response:

```json
{
  "session_id": "opaque-session-id",
  "region": "India",
  "expires_at": 1780000000,
  "relay_url": "/r/opaque-session-id/search?q=flipkart&__cpo=signed-token"
}
```

The public response must not expose the worker endpoint, lease key, source IP, shared secret, or upstream cookie jar.

## 6. Browser wrapper implementation

Replace the current iframe-oriented browser shell with a normal relay document flow in phases:

### Phase 1: safe text-first pages

- Return rewritten HTML directly from `/r/{session}/{path}`.
- Render a small RelayNorth header above the rewritten content.
- Support links, images, stylesheets, redirects, cookies, and form-urlencoded forms.
- Keep the target origin and session status in the header.
- Provide a new-session link and an expiring permalink action.

### Phase 2: richer content

- Add response streaming and HTTP range support for owned test media.
- Support more form encodings after adding body-size and content-type controls.
- Improve CSS and JavaScript resource rewriting based on failing integration tests.

### Phase 3: regional scale

- Route every session request to its leased worker.
- Add ten IPs per region and verify source address from an owned echo origin.
- Add worker draining, lease cleanup, and region-level capacity responses.

Do not inject arbitrary JavaScript into upstream pages to “fix” navigation. Use server-side rewriting first and isolate any required browser shim behind a strict content policy.

## 7. Data model additions

The current `RegionModel.egress_ip_pool` JSON field is not enough for individual assignment. Add normalized records:

```text
relay_workers
  id, region, endpoint, status, last_heartbeat, auth_key_id

relay_egress_ips
  id, worker_id, region, address, status, active_sessions, last_check

relay_ip_leases
  id, session_id, egress_ip_id, leased_at, expires_at

relay_sessions
  session_id, region, worker_id, egress_ip_id, target_origin, expires_at
```

Use PostgreSQL for durable records and Redis for atomic short-lived lease locks. The session must be pinned to one IP; the selected IP must never be inferred from a client-supplied URL parameter.

## 8. Permalink design

Create a permalink only for a target and session policy, not for private browser state:

```json
{
  "target": "https://approved-origin.example/page",
  "policy_version": 1,
  "region": "eu",
  "exp": 1780000000,
  "nonce": "random-value"
}
```

Recommended behavior:

- Default lifetime: three hours, configurable.
- No upstream cookies, authorization headers, or POST data.
- A permalink opens a fresh session or requires the original session, depending on the privacy mode.
- Revoke on abuse reports or destination policy changes.
- Rate-limit creation and access.

For the safer default, a permalink should create a new session and not share the original user's upstream cookie jar.

## 9. Local implementation and test plan

Use the existing owned origin on port `9001` and add deterministic fixtures for:

```text
/links       relative, absolute, fragment, and external links
/redirect    validated and blocked redirects
/cookies     set, update, and expire cookies
/forms       GET and form-urlencoded POST
/assets      CSS, image, script, and media references
/large       response-size limit
/echo        observed worker/egress identity
```

Run mock workers with labels rather than fake public IPs:

```text
in-mock-01 ... in-mock-10
eu-mock-01 ... eu-mock-10
us-mock-01 ... us-mock-10
```

Test that:

1. A URL creates a session and returns a relay URL.
2. A search query is normalized to the configured search provider.
3. A relay page stays on the RelayNorth hostname.
4. Relative links, forms, redirects, and cookies stay inside the same session.
5. An unapproved origin, private address, metadata address, and unsafe scheme are rejected.
6. A session remains pinned to one mock worker/IP label.
7. A permalink expires and does not contain cookie state.
8. Ten concurrent leases can be acquired and the eleventh receives a controlled busy response.
9. A drained or heartbeat-stale worker receives no new sessions.
10. The real deployed workers pass the same tests using an owned source-IP echo endpoint.

Example smoke test shape:

```bash
BASE_URL=http://127.0.0.1:8000

curl -sS -X POST "$BASE_URL/api/v1/sessions" \
  -H 'content-type: application/json' \
  -d '{"url":"http://127.0.0.1:9001/links","region":"in"}'
```

The current application does not yet implement the `in` region, worker fetch contract, normalized IP leases, or `/r/{session}/{path}` route. Those are implementation tasks, not available behavior today.

## 10. Security and operational requirements

- Keep the destination allowlist enabled during development and initial production.
- Block loopback, private, link-local, cloud metadata, unsafe schemes, and disallowed ports.
- Revalidate every rewritten link and redirect; never trust a client-provided encoded target.
- Use short-lived signed tokens and rotate signing secrets through Railway variables.
- Authenticate workers with mTLS or per-worker signed credentials.
- Do not expose actual IP-pool inventory in the public regions endpoint.
- Limit sessions, concurrent requests, response size, uploads, and bandwidth.
- Do not log full URLs when they may contain secrets or personal data; redact query values where appropriate.
- Provide abuse reporting, takedown handling, rate limits, and a published acceptable-use policy.
- Monitor egress-IP mismatch, worker heartbeat failures, lease exhaustion, upstream timeouts, and blocked destinations.
- Publish accurate privacy, retention, and anonymity limitations before opening access beyond owned origins.

### 10.1 Per-client-IP anti-spam limits

Rate limiting must happen before a request leases a regional egress IP. Otherwise a single abusive client can consume all ten IPs in a region even when the upstream requests are later rejected.

Use Redis in production so limits are shared by every Railway application replica. The current development store has an in-memory fallback; that fallback is acceptable for local testing only. Production should mark Redis as required or fail closed for session creation when the distributed limiter is unavailable.

#### Determine the real client address safely

The control plane may sit behind Railway's edge proxy. Do not blindly trust a user-supplied `X-Forwarded-For` value. Configure the application with the exact trusted proxy behavior for the deployment and only accept forwarded client-address headers from that trusted boundary.

```text
trusted proxy -> validated forwarded client IP -> normalized IP address
direct local request -> request.client.host
untrusted forwarded header -> ignore
```

Normalize IPv4 and IPv6 addresses with the standard library, then use an HMAC of the address for Redis keys and operational logs. Do not store raw client IPs in rate-limit keys or application logs unless the published privacy policy and retention rules allow it.

#### Recommended starting limits

These are starting values, not guarantees. Tune them using abuse metrics and legitimate shared-network traffic:

| Bucket | Starting limit | Key | Response |
| --- | --- | --- | --- |
| Session creation | 10 per minute per client IP | `rl:session-create:ip:{ip_hash}:{window}` | `429` + `Retry-After` |
| Active sessions | 5 active sessions per client IP | `rl:active-sessions:ip:{ip_hash}` | `429` |
| Proxy requests | 60 per minute per client IP | `rl:proxy:ip:{ip_hash}:{window}` | `429` |
| One session | 120 requests per session | `rl:proxy:session:{session_id}:{window}` | `429` |
| Concurrent requests | 2 per session | `rl:concurrency:session:{session_id}` | `429` |
| Permalinks | 10 per hour per client IP | `rl:permalink:ip:{ip_hash}:{window}` | `429` |
| Abuse reports | 5 per hour per client IP | `rl:abuse:ip:{ip_hash}:{window}` | `429` |

Use a short burst limiter in front of the longer window, for example a token bucket allowing 5 session-create requests in 10 seconds and 10 per minute. Return a generic message; do not reveal which internal IP, worker, or lease was selected.

#### Atomic Redis operation

Use an atomic Redis Lua script or an equivalent transaction for check-and-increment. A plain `GET` followed by `INCR` can race when multiple requests arrive at the same time.

```text
key = rl:session-create:ip:{hmac(client_ip)}:{unix_window}
count = INCR key
if count == 1: EXPIRE key 61
if count > limit: reject with 429
else: continue to validation and IP-lease acquisition
```

The active-session counter must be incremented only after a session is successfully created and decremented on expiry/release. A background sweeper must repair counters after crashes. Keep the per-session and per-IP limits separate so one browser cannot evade the IP limit by creating many sessions.

#### Progressive abuse response

After repeated violations, add a short-lived HMAC-keyed block such as `block:ip:{ip_hash}`. Start with a few minutes and increase only for continued abuse. Do not permanently block an address based on one burst because mobile carriers, schools, offices, and VPNs can place many legitimate users behind one public IP.

For high-confidence abuse, combine signals instead of relying only on IP:

- Session creation rate.
- Active sessions and concurrent requests.
- Destination and path repetition.
- Response bytes and bandwidth.
- Failed validation and blocked-destination attempts.
- Worker and region capacity consumed.

Use a CAPTCHA, authenticated quota, or a manual review path only after the soft limits are exceeded. Never use a CAPTCHA as a replacement for SSRF protection or an allowlist.

#### Required tests

Before public launch, verify:

1. The 11th session-create request from one client IP receives `429`.
2. Two Railway replicas share the same Redis counter.
3. Requests from two different client IPs use separate buckets.
4. A forwarded header supplied directly by an untrusted client cannot spoof the limiter key.
5. `Retry-After` is present on `429` responses.
6. Expired active-session counters are released.
7. Rate limiting runs before an egress-IP lease is acquired.
8. Redis outage is visible and does not silently disable production limits.

## 11. Implementation order

1. Add target normalization and signed target tokens.
2. Add `/r/{session}/{path}` with HTML link and redirect rewriting.
3. Add session-scoped cookie storage and header filtering.
4. Add normalized worker/IP/lease tables and Redis lease coordination.
5. Add mock workers and owned-origin fixtures.
6. Deploy one India, one EU, and one US worker for smoke testing.
7. Add the remaining seven workers per region after source-IP and failover tests pass.
8. Add permalink creation with three-hour expiry.
9. Add streaming/range support only after text-first flows are stable.
10. Perform security, abuse, privacy, and legal review before enabling broader destinations.
