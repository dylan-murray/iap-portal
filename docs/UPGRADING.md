# Upgrading

## Security hardening release (sessions, gateway boundary, registration)

This release changes how sessions, the gateway check, and app registration work.
Existing data (users, apps, owners, grants, groups, access requests, audit events)
is preserved. Everyone signs in again after the upgrade.

### Database

Schema changes are versioned with Alembic. By default the portal migrates at
startup (`PORTAL_AUTO_MIGRATE=true`), serialized across replicas with a Postgres
advisory lock. To migrate separately, set `PORTAL_AUTO_MIGRATE=false` and run:

```bash
python -m iap_portal_server.db.migrate
```

Databases created by earlier releases (no `alembic_version` table) are checked
against the previous schema and adopted automatically. A database that does not
match is left untouched and startup fails with the differences; back it up and
migrate it manually. Back up the database before upgrading in any case.

Changes: `users.disabled_at`; `user_identities.issuer` (existing identities are
upgraded on their next sign-in) with uniqueness on (issuer, subject);
`app_access.granted_by_user_id` may be NULL for operator grants; new
`auth_sessions`, `app_login_codes`, and `rate_limit_counters` tables.

### Portal settings

| Setting | Change |
| --- | --- |
| `PORTAL_ENV` | Defaults to `production`. Set `dev` explicitly for local HTTP stacks. `.env` files are no longer loaded implicitly. |
| `PORTAL_ENABLE_DEV_LOGIN` | New. `/dev/login` requires this and `PORTAL_ENV=dev`. |
| `PORTAL_COOKIE_DOMAIN` | Removed (ignored with a warning). Portal cookies are host-only; apps get app-scoped sessions. |
| `PORTAL_ALLOWED_RETURN_TO_HOSTS` | Removed (ignored with a warning). Redirects are limited to the portal and `<slug>.<apps domain>`. |
| `PORTAL_RATE_LIMIT_VERIFY_PER_SECOND` | Removed. |
| `PORTAL_APPS_DOMAIN` | New, optional. Defaults to the portal host without its first label. |
| `PORTAL_SESSION_TTL_SECONDS`, `PORTAL_APP_SESSION_TTL_SECONDS` | New. Absolute lifetimes, default 12 hours. |
| `PORTAL_ALLOWED_EMAIL_DOMAINS`, `PORTAL_GOOGLE_HOSTED_DOMAINS` | New. Sign-in admission (JSON lists). Empty admits any verified account. |
| `PORTAL_LINK_VERIFIED_EMAIL_ACROSS_ISSUERS` | New, default false. Users who signed in with both Okta and Google under the same email previously shared an account; with the default, only the first identity now links. Enable this for trusted provider pairs, or unlink with the API. |
| `PORTAL_KUBERNETES_REGISTRATION_ENABLED` and related | New. TokenReview-based app registration. |
| `PORTAL_TRUSTED_PROXY_HOPS` | New, default 1 (the gateway). |
| `PORTAL_JWT_ADDITIONAL_PUBLIC_KEYS_DIR` | New. Extra verification keys for rotation. |
| `PORTAL_ADMIN_API_TOKEN` | Now an operator-only credential: at least 32 random characters in production. Do not copy it into app namespaces. |

Production startup fails on unsafe configuration: plain HTTP, short or sample
secrets, missing or mismatched JWT keys, no identity provider, or dev login.

### Identity and admin behavior

- Accounts are matched by (issuer, subject). A new subject from the same issuer
  with an existing email is rejected rather than linked; unlink the old identity
  (`DELETE /api/users/{email}/identities` or `iap-portal users set <email>
  unlink-identities`) to re-link deliberately.
- `email_verified` must be present and true.
- Admin status is re-evaluated from `PORTAL_ADMIN_EMAILS` on every request.
- JWT `sub` is now the portal's numeric user id; use the `email` claim for email.

### Gateway (Istio)

- Update the ext_authz provider from `deploy/platform/mesh-config-patch.yaml`:
  `x-forwarded-host` and `x-forwarded-proto` are no longer sent; `origin` and
  `x-forwarded-for` are; `cookie` is added to `headersToUpstreamOnAllow`.
- Replace `auth-policy.yaml`: CUSTOM now applies to every host except the portal,
  and internal portal paths are denied. The DENY rule is scoped to the HTTPS
  listener port (443); adjust `ports` if your listener differs.
- The gateway listener accepts only HTTPRoutes.
- Apply `route-admission-policy.yaml` (Kubernetes 1.30+).
- `portal-httproute.yaml` is gone: the portal chart now owns the portal HTTPRoute
  and the only `portal` Service (port 8090). Delete the old standalone HTTPRoute
  and Service before upgrading the chart if you applied them.

### Local Kubernetes stack

`scripts/k8s/bootstrap.sh` now defaults to Kubernetes 1.35.1, Istio 1.31.1, and
Gateway API v1.6.0 (the previous Istio 1.24 is end of life) and downloads a
matching `istioctl` into `~/.cache/iap-portal/`. Recreate existing local clusters
(`task k8s:down`, then `task k8s:up`). `seed-secrets.sh` and
`prep-app-namespace.sh` no longer re-apply Namespace objects, which previously
removed the portal namespace's gateway and ambient labels (portal 404s).

### Portal chart

`portal.cookieDomain` and `portal.allowedReturnToHosts` are removed. New values:
`portal.appsDomain`, `portal.allowedEmailDomains`, `portal.googleHostedDomains`,
`portal.linkVerifiedEmailAcrossIssuers`, `portal.trustedProxyHops`,
`portal.jwtPreviousKeysSecret`, `kubernetesRegistration.*`, `serviceAccount.name`,
and `httpRoute.*`. The chart creates a ClusterRoleBinding to
`system:auth-delegator` for TokenReview. The pod runs as uid 10001 with a
read-only root filesystem.

### App chart and apps

- Registration uses a projected ServiceAccount token. Remove `portal-admin-token`
  Secrets from app namespaces; `registration.tokenSecretName/Key` are gone.
  `registration.portalUrl` defaults to the in-cluster portal Service.
- Apps must set `IAP_PORTAL_ISSUER` (the chart does, from `portal.issuer` or
  `https://portal.<domain>`); JWT verification fails closed without it.
- Pods run as uid 10001 without a ServiceAccount token. Images that need another
  user can override `podSecurityContext`/`securityContext`.
- The NetworkPolicy admits only the gateway pods; an Istio AuthorizationPolicy
  admits only the gateway identity (`mesh.authorizationPolicy`).
- Streamlit sessions ask users to reload after `portal.maxConnectionAgeSeconds`.
- `IAP_PORTAL_DEV` in `env` is rejected by the chart and refused by the SDK
  inside Kubernetes.

## Namespace-local application backends

Re-apply `deploy/platform/route-admission-policy.yaml` (or its local variant).
It now also rejects ExternalName Services in routable app namespaces. Route
hostname ownership alone does not prevent a DNS alias from targeting another app.
Before exposing an existing deployment, inspect and remove or replace any such
aliases with namespace-local Services. Admission is not retroactive: existing
aliases keep working until removed. Keep deployers from managing EndpointSlices,
Endpoints, ReferenceGrants, and Istio networking resources.

Local bootstrap now defaults to Calico to enforce NetworkPolicy. Existing profiles
are not converted automatically; recreate the local profile to change its CNI.

## Gateway-only authorization listener

Upgrade the portal image, portal chart/local portal manifest, and Istio extension
provider together. Authorization checks move from port 8090 to 8091. The new image
removes `/auth/verify` from the public application; an old provider pointing to
8090 will deny app requests until updated. Plan a maintenance window or staged
rollout that keeps compatible portal/provider versions together.

The chart adds an authorization container and a `portal-listeners` L4 policy.
Configure `authorization.gatewayPrincipal` if the gateway identity or trust domain
differs from the default. Apply the policy before exposing the private Service
port, retain portal namespace mesh enrollment, and remove any pre-existing broader
ALLOW policies that would admit other callers to 8091. JWKS, API, and registration
URLs stay on 8090. Account for the second container's resource requests.

Recreate Compose services with the updated configuration; both repository and SDK
stacks add a separate authorization service and private networks. Do not attach
application containers to those networks. Custom deployments must start the private
ASGI app with `uvicorn iap_portal_server.main:create_authorization_app --factory
--host 0.0.0.0 --port 8091` and enforce equivalent gateway-only access.


## Explicit Helm authentication providers (before generic OIDC)

For current releases, also apply [Generic OIDC provider](#generic-oidc-provider) below.

Set `portal.auth.okta.enabled: true` and/or `portal.auth.google.enabled: true`
when upgrading the portal chart. Both default to false. Existing issuer/client ID
values alone no longer enable a provider in Helm deployments. Move `portal.oktaIssuer`
to `portal.auth.okta.issuer`, `portal.oktaClientId` to `portal.auth.okta.clientId`,
and `portal.googleClientId` to `portal.auth.google.clientId`. Set each enabled
provider's `clientSecretRef.name` and `clientSecretRef.key`. To reuse the existing
shared Secret, set its name and the existing `PORTAL_OKTA_CLIENT_SECRET` or
`PORTAL_GOOGLE_CLIENT_SECRET` key. No credential rotation is required for this move.
Production startup rejects a configuration with neither provider enabled. Compose and direct environment-based
deployments continue selecting providers through their issuer/client ID settings.


## App identifier rename

App specs are now named `iap-app.yaml`, framework images launch through
`iap-app-start`, and entrypoint overrides are `IAP_APP_SPEC` and
`IAP_APP_FRAMEWORK`. Regenerate or update app scaffolds and rebuild app images.
The app chart is `charts/iap-app`, and the reusable workflow is
`.github/workflows/deploy-iap-app.yml`.

New installs use `iap-app-<slug>` namespaces, `iap-apps/slug` and
`iap-apps/routable` labels, and the `iap-gateway` namespace with
`iap-apps-gateway` and `iap-apps-waypoint`. Namespace and gateway identities
cannot be renamed in place. Existing installations need a coordinated migration:
back up the database and app volumes, provision the new namespaces and gateway,
recreate app Secrets/volume data and deploy apps there, and update registration,
route-admission policies, mesh/DNS routing, and gateway authorization principals
together. Registration must update each app's upstream address before cutover.
Verify authentication and app isolation before removing the previous resources.
A plain Helm upgrade is not a namespace migration.


## Generic OIDC provider

The configurable provider is now `oidc`. Google remains a separate option.
Before upgrading:

- Rename `portal.auth.okta` to `portal.auth.oidc` in Helm values. Old chart keys
  are rejected. Existing client-secret references can keep their current Secret
  names and keys.
- Rename `PORTAL_OKTA_ISSUER`, `PORTAL_OKTA_CLIENT_ID`, and
  `PORTAL_OKTA_CLIENT_SECRET` to their `PORTAL_OIDC_*` equivalents for native
  deployments. The old environment variables are no longer used.
- Register `/auth/callback/oidc` instead of `/auth/callback/okta` with your IdP.
  In-flight sign-ins using the old route must restart.
- Set `portal.auth.oidc.displayName` (default `SSO`) and `scopes` (default
  `[openid, email, profile]`). The old implicit `groups` scope is no longer
  requested. Add it explicitly if your provider requires it. Provider group
  claims are not automatically imported into portal memberships.

Migration `0003` renames existing `okta` identity and session provider fields to
`oidc`, preserving their users, issuer/subject bindings, sessions, and grants.
Keep the same issuer when upgrading an existing provider. Switching issuers is
an identity migration, not a label change. Back up the database before upgrading
and finish the migration before serving traffic with the new version.
