# Security and release status

IAP Portal is an early identity-aware application portal. This document describes
the trust model, the controls that are implemented, what has and has not been
verified, and known limitations. It is not a certification; obtain an independent
review before relying on it for sensitive data.

## Trust boundary

Browser → gateway (Istio in Kubernetes, Envoy in local Compose) → application.
For every request to an app host the gateway calls the portal's `/auth/verify`
on its private listener (8091), with the original Host, method, and path. The portal answers from its own
configuration and database only; client headers such as `X-Forwarded-Host` are
never used. On success the gateway replaces identity headers, `Authorization`
(a short-lived JWT scoped to that app), and `Cookie` on the upstream request.

Trusted: the portal, its database and signing keys, the identity providers, the
gateway and mesh configuration, and platform operators. App code is *not*
trusted with other apps' users: a compromised app should not be able to act as
its visitors anywhere else. App deployers are trusted only for their own
`iap-app-<slug>` namespace.

## Implemented controls

**Gateway and hosts**
- `/auth/verify` uses only the check request's Host. Hosts are normalized and
  must be exactly `<slug>.<apps domain>`; malformed, unknown, reserved, and the
  portal host are denied. The portal host is excluded from the gateway's CUSTOM
  policy, and public requests for `/auth/verify` on the portal host are denied.
  The public application on 8090 does not mount the authorization router.
- Kubernetes restricts the private listener to the gateway ServiceAccount
  using an Istio L4 policy. It requires the portal pod to be enrolled in the mesh.
  Compose places the authorization service on separate gateway/database networks
  that app containers do not join. A valid session does not bypass this boundary.
- Portal errors or timeouts deny access (`failOpen`/`failure_mode_allow` false).
- Client-supplied `X-Forwarded-Email/User/Name/Groups`, `X-Iap-Portal-App`, and
  `Authorization` never reach an app on an allowed request: they are overwritten
  or removed. The portal strips the same identity headers from its own requests.
- Cross-origin requests and WebSocket handshakes into apps (an `Origin` other than
  the app's own) are denied at the gateway (`PORTAL_ENFORCE_APP_SAME_ORIGIN`).

**Sessions**
- Server-side sessions with absolute lifetimes (`PORTAL_SESSION_TTL_SECONDS`,
  default 12 hours). Cookies hold random tokens; only hashes are stored.
- The portal cookie is host-only on the portal host. Each app gets its own
  host-only session cookie, issued through a single-use, 60-second code and bound
  to that app and to the portal session that created it. Over HTTPS all cookies
  use the `__Host-` prefix, so sibling subdomains cannot set or overwrite them.
- Portal-managed cookies are removed from requests forwarded to apps. An app sees
  neither the portal session nor its own session cookie.
- Sign-out, admin revocation (`POST /api/users/{email}/revoke-sessions`), and
  disabling a user (`/disable`) end portal and app sessions immediately. Grants
  are checked on every request. Admin status follows `PORTAL_ADMIN_EMAILS` on every
  request, so removing an email demotes that user at once.

**Browser requests to the portal**
- Cookie-authenticated `POST/PUT/PATCH/DELETE` require `Origin` equal to the portal
  origin (or `Sec-Fetch-Site: same-origin` when Origin is absent), including
  bodyless requests. Bearer-token API clients are not affected.
- Sign-out is a same-origin POST; a GET from elsewhere shows a confirmation.
- `return_to` and app deep links accept only portal paths and exact app hosts with
  the portal's scheme and port; userinfo, backslashes, control characters, and
  scheme-relative forms are rejected.

**Identity**
- OIDC through Authlib with PKCE, state, and nonce; the ID token is validated by
  Authlib and its issuer checked again. Unverified tokens are never decoded.
- Users are identified by (issuer, subject). `email_verified` must be true.
- An email address is linked to an existing account only if that account has no
  identity yet (owner/grant placeholders). A different subject from the same
  issuer is never linked automatically; cross-provider linking is opt-in
  (`PORTAL_LINK_VERIFIED_EMAIL_ACROSS_ISSUERS`). Admins can unlink identities.
- Optional admission: `PORTAL_ALLOWED_EMAIL_DOMAINS` and
  `PORTAL_GOOGLE_HOSTED_DOMAINS`. Without them any verified account at a
  configured IdP can sign in (and see app names and request access); the portal
  logs a warning when Google is configured this way.

**Tokens and keys**
- Per-app RS256 JWTs (`aud` = slug, `iss` = portal URL, `sub` = portal user id,
  5-minute lifetime). The SDK requires `IAP_PORTAL_APP_SLUG` and
  `IAP_PORTAL_ISSUER`, accepts only RS256 keys from JWKS, and returns 401 for
  missing, invalid, or expired tokens, including on WebSocket handshakes.
- JWKS may publish additional keys for overlapping rotation. Startup checks that
  the signing and published keys match. The SDK refetches JWKS at most every 30
  seconds for unknown key IDs and tolerates short portal outages with cached keys.

**Registration and Kubernetes**
- App registration Jobs use projected ServiceAccount tokens validated with the
  TokenReview API. A token from `iap-app-<slug>` may create or update only that
  app's registry entry and owners. The operator token (`PORTAL_ADMIN_API_TOKEN`)
  is admin-equivalent and is never placed in app namespaces.
- A ValidatingAdmissionPolicy (Kubernetes 1.30+) requires every route in a
  routable app namespace to use exactly `<slug>.<apps domain>`. The gateway
  accepts only HTTPRoutes from namespaces labeled `iap-apps/routable=true`.
  A second admission policy rejects ExternalName Services in app namespaces,
  preventing backend aliases from crossing app boundaries.
- App pods run as non-root with no privilege escalation, dropped capabilities,
  RuntimeDefault seccomp, and no ServiceAccount token. A NetworkPolicy admits only
  the gateway pods (app port and ambient HBONE 15008) and ambient probe traffic;
  an Istio L4 AuthorizationPolicy admits only the gateway's mTLS identity.

**Abuse limits and startup safety**
- Database-backed rate limits shared by all replicas: sign-in per client address,
  failed bearer tokens per address, app sign-in and cookie mutations per user.
  Client addresses come from `X-Forwarded-For` using `PORTAL_TRUSTED_PROXY_HOPS`.
  `/auth/verify` is not rate limited; denial audit events are deduplicated.
- `PORTAL_ENV` defaults to `production`, which refuses to start with plain HTTP,
  sample or short secrets, mismatched keys, or dev login. Dev login requires
  `PORTAL_ENV=dev` and `PORTAL_ENABLE_DEV_LOGIN=true`, and dev mode is limited to
  `localhost`, `*.localhost`, and `*.test` portal URLs. API docs are not served in
  production. The SDK refuses `IAP_PORTAL_DEV` fake identities inside Kubernetes.
- Schema changes use versioned Alembic migrations; see [Upgrading](UPGRADING.md).

## Verification status

Automated tests cover the behaviors above at the unit and HTTP level, the rendered
Helm charts, and consistency between the Istio, Compose, and SDK gateway configs.
The Compose stack has been exercised end to end with a real browser, including
header spoofing, sign-in, cookie scoping, a hostile sibling app, SSE, Streamlit
reconnection, outage behavior, and revocation. On disposable local clusters
(Kubernetes 1.35 with Calico and Istio 1.31 ambient), the documented installation,
route acceptance, route admission policy, workload-identity registration, the
portal's internal-path DENY rule, unauthenticated sign-in redirects, and app
readiness probes under the NetworkPolicy and mesh policy were exercised. Authenticated
Istio flows, upstream header/cookie handling, cross-app replay rejection, revocation,
portal outages, SSE, browser WebSocket protections, and Streamlit expiry/reload
behavior have also been exercised. Both network isolation layers were checked with
fresh connections and a positive control. A production-style HTTPS listener was
checked with a locally trusted test certificate; this does not validate a real
certificate provider, external load balancer, or production IdP.
Run the checks in [Platform installation](../deploy/platform/README.md#verify-before-exposure)
on your cluster and CNI before exposure.

## Known limitations

- **Long-lived connections.** Authorization happens when a connection opens. An
  open Streamlit WebSocket keeps its identity for up to
  `IAP_PORTAL_MAX_CONNECTION_AGE_SECONDS` (chart default one hour); the user is
  then asked to reload. SSE and other streams run until they end. Revocation
  applies to new requests and connections immediately.
- **Sibling cookies.** Apps can still set cookies for the parent domain. Portal
  cookies cannot be overwritten (host-only, `__Host-` over HTTPS), but apps must not
  trust parent-domain cookies set by others. Local HTTP development cannot use the
  `__Host-` prefix.
- **Existing network connections.** NetworkPolicy changes may leave established
  connections intact. Keep the mesh authorization policy enabled and test isolation
  with fresh source identities/connections when changing network rules.
- **Header trust mode.** With `IAP_PORTAL_VERIFY_JWT=false`, an app relies entirely
  on the gateway, NetworkPolicy, and mesh policy. Prefer JWT verification.
- **Deployer permissions.** The tenant boundary assumes deployers hold only
  namespaced permissions like `deploy/platform/app-deployer-rbac.yaml`: no Namespace
  edits, RBAC, ReferenceGrants, or Istio networking resources.
- **Mesh enforcement.** The gateway-only listener depends on the portal remaining
  enrolled in Istio and its ALLOW policy remaining restrictive. Platform operators
  control these resources and the gateway ServiceAccount. Host-network access and
  privileged cluster administrators are outside the app-pod threat model.
- Rate limits are per fixed one-minute window. Audit logs do not record every
  successful gateway authorization.
- The portal image installs hash-locked dependencies from `uv.lock`
  (`backend/requirements.lock`); base images and app images do not. Scan and pin
  the images you deploy.

Fine-grained permissions inside an app remain that application's responsibility.
Do not publish secrets or vulnerability details in a public issue; use the hosting
repository's private security reporting channel once configured.
