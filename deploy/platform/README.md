# Kubernetes platform installation

Kubernetes + Istio is the primary IAP Portal deployment. The portal chart runs the
control plane; app charts attach to the shared Istio gateway. No specific container
registry, cloud, or secrets provider is required.

## Prerequisites

- Kubernetes 1.30+ (ValidatingAdmissionPolicy) with a supported Istio release
  (see Istio's supported releases table for Kubernetes compatibility) and the
  Gateway API CRDs it documents. The supplied namespace labels assume Istio ambient.
  The local stack uses Kubernetes 1.35, Istio 1.31, and Gateway API v1.6.
- A CNI that enforces Kubernetes NetworkPolicy.
- A parent domain such as `apps.example.com`, with `*.apps.example.com` pointing to
  the gateway and a matching TLS certificate.
- Postgres and an application registered with your OIDC provider or Google. Register
  `https://portal.apps.example.com/auth/callback/oidc` (or `/google`) as its callback.
- Docker, kubectl, Helm, and permission to configure the Istio extension provider.

## Generate your installation configuration

From a source checkout with dependencies installed (`uv sync --all-extras`):

```bash
uv run python scripts/configure-platform.py \
  --domain apps.example.com \
  --image YOUR_REGISTRY/iap-portal --tag 0.1.0 \
  --display-name "Company Apps" \
  --output .internal/company-install
```

Use your actual app domain and image repository. The command creates a new directory
and never applies resources or overwrites an existing configuration. It generates
namespace, gateway, authorization, route-admission, and Istio-provider manifests,
plus `portal-values.yaml` and `app-platform-values.yaml`. Add your IdP/admin settings
to the portal values before installing; credentials remain in Kubernetes Secrets.
`--portal-namespace`, `--gateway-namespace`, `--gateway-name`, `--tls-secret`, and
`--trust-domain` keep custom installations consistent across all generated files.
The portal host is `portal.<domain>`; the slug `portal` is reserved.

The examples below show the default namespace layout. Use the generated manifests
and matching namespace values when you customize it. Cluster-wide admission policy
names and the Istio provider are shared: this setup supports one platform per cluster.
Do not install an independent second platform over an existing installation.

## Build the portal

From the repository root:

```bash
docker build -f backend/Dockerfile -t YOUR_REGISTRY/iap-portal:0.1.0 .
docker push YOUR_REGISTRY/iap-portal:0.1.0
```

The image installs the hash-locked dependencies in `backend/requirements.lock`.
No public package or prebuilt project image is assumed to exist.

## Namespaces, credentials, and TLS

```bash
kubectl apply -f deploy/platform/namespaces.yaml
kubectl -n iap-gateway create secret tls iap-apps-wildcard-tls \
  --cert=/path/to/fullchain.pem --key=/path/to/tls.key
```

Use your existing certificate automation to create/rotate this Secret if preferred.
Certificate management is independent of the portal.

Create `portal-config` in namespace `iap-portal` using your chosen secrets workflow.
Its keys are environment variables:

```text
PORTAL_DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@POSTGRES_HOST/portal
PORTAL_SESSION_SECRET=<random value of at least 32 characters>
PORTAL_ADMIN_API_TOKEN=<optional operator token for the CLI, at least 32 random characters>
```

Create `portal-jwt-keys` in the same namespace with `private.pem` and `public.pem`
containing an RSA keypair (2048 bits or more). The portal refuses to start if they
do not match. Keep private credentials out of values files and source control.
Existing Kubernetes Secrets work directly; an external secrets controller is optional.

## Install the portal

Choose providers using `portal.auth.oidc.enabled` and `portal.auth.google.enabled`.
Both default to `false`; enable OIDC, Google, or both. Production requires at
least one. Only configured providers appear on the sign-in page; disabled provider
login and callback URLs return 404. Disabling a provider prevents new sign-ins;
existing sessions retain their normal lifetime and can be revoked separately.

The generic provider uses OpenID Connect discovery and authorization code flow
with PKCE (S256). Set `issuer` to the issuer URL, not the discovery document URL.
Register `/auth/callback/oidc` with your provider. Okta can use this generic path.
SAML and OAuth-only providers are not supported.

`displayName` sets the “Continue with …” button label (default `SSO`). `scopes`
defaults to `[openid, email, profile]` and must include `openid`. Request additional
provider scopes when needed. `tokenEndpointAuthMethod` supports
`client_secret_basic` (default) or `client_secret_post` to match your provider.
The validated ID token must supply `sub`, `email`, and `email_verified: true`.
Configure your provider's claim mappings to supply these claims. Group membership
is managed in the portal, not automatically synchronized from provider claims.

For native deployments, the equivalent environment variables are
`PORTAL_OIDC_ISSUER`, `PORTAL_OIDC_CLIENT_ID`, `PORTAL_OIDC_CLIENT_SECRET`,
`PORTAL_OIDC_DISPLAY_NAME`, `PORTAL_OIDC_SCOPES` (a JSON array), and
`PORTAL_OIDC_TOKEN_ENDPOINT_AUTH_METHOD`.

Provider configuration is grouped under `portal.auth`. OIDC accepts `issuer`,
`clientId`, and `clientSecretRef`; Google accepts `clientId` and `clientSecretRef`
(the Google issuer is fixed). Each secret reference specifies an existing Kubernetes
Secret `name` and `key` in the portal's namespace. The two providers can reference
separate Secrets or different keys in one Secret, including `portal.existingSecret`.
Only enabled providers require these settings. Disabled providers ignore saved
credentials, including values inherited from the shared Secret.

For example, to offer both providers:

```yaml
portal:
  auth:
    oidc:
      enabled: true
      displayName: Company SSO
      scopes: [openid, email, profile]
      issuer: https://idp.example.com
      clientId: YOUR_OIDC_CLIENT_ID
      clientSecretRef:
        name: portal-oauth
        key: oidc-client-secret
    google:
      enabled: true
      clientId: YOUR_GOOGLE_CLIENT_ID
      clientSecretRef:
        name: portal-oauth
        key: google-client-secret
  googleHostedDomains: [example.com]
```

Set either `enabled` to false for a single-provider deployment. Create the referenced
Secret through your chosen secrets workflow before installing. For example, the
Secret above must contain `oidc-client-secret` and `google-client-secret`. No secret
values go into Helm values or rendered manifests. Missing required references fail
chart rendering; missing Secrets or keys prevent the pod from starting.

Register the following redirect URIs in the respective OAuth applications:
`https://portal.<your-domain>/auth/callback/oidc` and
`https://portal.<your-domain>/auth/callback/google`.
Set `portal.googleHostedDomains` or `portal.allowedEmailDomains` to restrict Google
sign-in to your organization.

Create a deployment values file (non-secret example):

```yaml
image:
  repository: YOUR_REGISTRY/iap-portal
  tag: "0.1.0"
  pullSecret: "" # optional registry pull Secret in iap-portal
portal:
  displayName: "Company Apps" # defaults to iap-portal; no image rebuild needed
  baseUrl: https://portal.apps.example.com
  adminEmails: admin@example.com
  auth:
    oidc:
      enabled: true
      issuer: https://idp.example.com
      clientId: YOUR_CLIENT_ID
      clientSecretRef:
        name: portal-oauth
        key: oidc-client-secret
    google:
      enabled: false
  allowedEmailDomains: [example.com]   # optional sign-in admission
  existingSecret: portal-config
  jwtSecret: portal-jwt-keys
httpRoute:
  hostname: portal.apps.example.com
```

```bash
helm upgrade --install portal deploy/platform/portal-chart \
  --namespace iap-portal --values portal-values.yaml --wait
```

Google uses `portal.auth.google.clientId`; set `portal.googleHostedDomains` to your
Workspace domains unless personal Google accounts should be able to sign in.
The chart creates the only `portal` Service (public API on 8090, gateway authorization
on 8091), the portal HTTPRoute, a
ServiceAccount with the `system:auth-delegator` binding used to validate app
registration tokens, and a non-root, read-only pod. The portal applies database
migrations at startup; see [Upgrading](../../docs/UPGRADING.md).

## Connect Istio

Merge the `spec.meshConfig.extensionProviders` entry in `mesh-config-patch.yaml`
into your existing Istio installation configuration. This is installation input,
not a standalone Kubernetes resource to apply with kubectl. Preserve other mesh
settings/providers. The entry points to `portal.iap-portal.svc.cluster.local:8091`
and must not forward `x-forwarded-host`: the portal authorizes the Host the
gateway routes on.

Then apply your domain-adjusted copies of:

```bash
kubectl apply -f deploy/platform/gateway.yaml
kubectl apply -f deploy/platform/auth-policy.yaml
kubectl apply -f deploy/platform/route-admission-policy.yaml
```

`auth-policy.yaml` sends every gateway request except those for the portal host
through `/auth/verify` and denies `/auth/verify` on the portal host. Unknown hosts
are denied by the portal. Do not add an ALLOW policy on the gateway: CUSTOM and
ALLOW policies must both allow a request when both apply. The optional
`waypoint.yaml` is available for ambient deployments needing a waypoint; it is not
a substitute for the CUSTOM policy on the ingress Gateway.

`route-admission-policy.yaml` rejects routes in routable app namespaces unless
every hostname is exactly `<slug>.<domain>` for namespace `iap-app-<slug>`.

## Prepare apps and deployers

For each app, the operator creates the namespace:

```bash
scripts/k8s/prep-app-namespace.sh my-tool YOUR_CLUSTER_CONTEXT   # iap-app-my-tool with required labels
```

No credential is copied into it: the chart's registration Job authenticates with a
projected ServiceAccount token that can register only that app. Give the app's
deployers (a CI identity or team) a namespaced Role such as
[`app-deployer-rbac.yaml`](app-deployer-rbac.yaml). Deployers must not be able to
create or edit Namespace objects, RBAC, ReferenceGrants, or Istio networking
resources; the admission policy and gateway selector rely on that.

Follow [Adding an app](../../docs/ADDING_AN_APP.md). Private images may use
`image.pullSecret`. Runtime app credentials may use `secrets.existingSecret`,
`extraEnvFrom`, or optional provider-neutral External Secrets.

For operator tasks use the CLI with `IAP_PORTAL_URL` and `IAP_PORTAL_TOKEN` (the
operator token), e.g. `iap-portal users set user@example.com revoke-sessions`.

## Key rotation

Create a Secret with the previous public key as `<old-kid>.pem`, set
`portal.jwtPreviousKeysSecret` to it, then switch `portal-jwt-keys` and
`portal.jwtKid` to the new pair. Apps accept both keys. Remove the previous key
after at least the JWT lifetime plus five minutes.

## Verify before exposure

On your actual cluster, confirm at least:

- Gateway and routes are accepted (`kubectl get httproute -A` shows `Accepted=True`
  for the portal and every app); the portal is ready; sign-in works. A route in a
  namespace that lost its `iap-apps/routable=true` label is rejected by the listener
  and its host returns 404 from the gateway. Manage namespace labels in one place
  and do not re-apply label-less Namespace manifests.
- `curl -H 'Host: an-app.apps.example.com' -H 'X-Forwarded-Host: portal.apps.example.com'`
  through the gateway without cookies returns a sign-in redirect, never app content.
- Forged `X-Forwarded-Email` and `Authorization` headers never reach an app (use a
  test app that echoes its request headers), and app requests carry no portal cookies.
- `https://portal.apps.example.com/auth/verify` is denied.
- An HTTPRoute for another app's hostname or for `portal.apps.example.com`, created
  with an app deployer's credentials, is rejected at admission.
- ExternalName Services in routable app namespaces are rejected on create/update;
  remove any pre-existing aliases before exposure. Cross-namespace backendRefs
  must remain unresolved without an operator-managed ReferenceGrant.
- A pod outside the gateway cannot connect to an app pod directly, while gateway
  traffic, WebSockets, and streaming work.
- Scaling the portal to zero denies app requests.
- Access denial, owner approval, grant revocation, sign-out, and admin session
  revocation behave as expected.

Helm rendering and the automated tests in this repository are not proof that the
deployed boundary works on your cluster.

References: [Istio authorization](https://istio.io/latest/docs/concepts/security/),
[Istio ambient and NetworkPolicy](https://istio.io/latest/docs/ambient/usage/networkpolicy/),
[ValidatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/),
[External Secrets](https://external-secrets.io/latest/api/externalsecret/).

## Private authorization listener

The portal pod runs two containers from the same image. Port 8090 serves the UI,
API, JWKS, and registration. Port 8091 serves only authorization checks and health
endpoints. The public app has no `/auth/verify` handler, including when contacted
directly inside the cluster.

The chart's `portal-listeners` Istio L4 ALLOW policy permits 8090 to callers and
8091 only to `authorization.gatewayPrincipal` (default
`cluster.local/ns/iap-gateway/sa/iap-apps-gateway-istio`). Set this value to your real
mesh trust domain, gateway namespace, and gateway ServiceAccount. Keep the portal
namespace enrolled in ambient mode as declared in `namespaces.yaml`; the policy
requires mesh enforcement. Do not add a broader ALLOW policy for the portal pod:
Istio combines ALLOW rules, so it could permit other callers on 8091.

Verify that an app pod cannot reach 8091 using either the Service or PodIP, even
with a valid app session. It must still reach JWKS and registration on 8090, and
normal gateway sign-in must succeed. A wrong gateway principal fails closed.

## Install from a packaged chart

```bash
task package:charts
helm upgrade --install portal dist/charts/iap-portal-0.1.0.tgz \
  --namespace iap-portal \
  --values .internal/company-install/portal-values.yaml --wait
```

Complete the namespace, TLS, Secret, Istio-provider, and gateway steps above first.
The portal chart deploys the control plane; it does not install Istio, Postgres,
DNS, or certificates. This separation lets operators use their existing infrastructure.
Use the matching `iap-app-0.1.0.tgz` with generated `app-platform-values.yaml` and the
app's own values to deploy apps. Prepare app namespaces before installing them.

Packaging produces archives locally; CI retains them as workflow artifacts. Nothing
is automatically published to a registry or deployed by portal CI. To distribute
via OCI after choosing your registry, run `helm push` on each archive. Until images
and charts are published, downstream users must build the portal image and package
the charts from the checkout. The SDK can also be installed from `./sdk`.


The `publish-release` GitHub workflow is manual and skips private repositories.
After making the repository public, align the Python package and chart versions,
create the matching `vX.Y.Z` tag, and dispatch the workflow with `X.Y.Z`. It tests
and publishes an amd64/arm64 portal image to `ghcr.io/<owner>/<repo>:X.Y.Z`
and both charts under `oci://ghcr.io/<owner>/<repo>/charts`. Set the GHCR packages
to public and verify anonymous image pulls and chart installs before announcing
availability. No artifacts have been published as part of repository preparation.
