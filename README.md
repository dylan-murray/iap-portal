# IAP Portal

Ship internal apps with SSO, access management, and a team portal built in.

IAP Portal gives teams one place to discover and share Streamlit, Gradio,
FastAPI, Flask, and custom web apps. Engineers ship the app; the gateway handles
sign-in and access checks. App owners manage access requests, user and group grants,
and expiration from the portal.

**Kubernetes with Istio is the primary deployment.** Docker Compose provides a
local stack with Envoy, Postgres, a mock identity provider, and example apps.
Use your own container registry, Postgres instance, certificate provider, and
Kubernetes Secrets. External Secrets Operator is optional.

This is an early project being prepared for an open-source release. Read
[Security](docs/SECURITY.md) for the trust model, implemented controls, and known
limitations, and [Upgrading](docs/UPGRADING.md) before updating an existing install.
A package or image on a public registry is not required: examples and base images
build the SDK directly from this checkout.

## How it works

```text
Browser → Istio gateway → application
               │              ↑
               │ ext_authz    │ verified identity headers + per-app JWT
               ↓              │
          IAP Portal ─────────┘
          OIDC • access grants • app registry • access requests
               │
            Postgres
```

The gateway handles HTTP, WebSockets, and streaming. The portal handles identity
and app-level authorization. Each app gets its own session cookie and app-scoped
token; apps never receive the portal session. An optional Python SDK gives applications a typed
user object and verifies the portal's signed JWTs. Apps remain responsible for
permissions inside their own application.

## Try it locally

Install Docker, [uv](https://docs.astral.sh/uv/), Node.js 20+, and
[Task](https://taskfile.dev/installation/), then:

```bash
task up:full
```

This builds the stack, generates local signing keys, and adds local hostnames
(the hosts-file step requires sudo). Open `http://portal.iapportal.test:8090`
and sign in through the mock Okta flow as `alice`. Local identities use
`example.com`; the mock identity provider is for development only.

Register the bundled apps from another terminal with the local operator token:

```bash
export IAP_PORTAL_URL=http://portal.iapportal.test:8090
export IAP_PORTAL_TOKEN=dev-admin-token-change-me-in-prod
uv run iap-portal apply examples/streamlit-app/iap-app.yaml
uv run iap-portal apply examples/fastapi-sse/iap-app.yaml
```

The example owner is `alice@example.com`. See [Local development](docs/LOCAL_DEV.md)
for native hot reload, SDK development, and Compose details.

## Deploy to Kubernetes

Requirements: Kubernetes 1.30+, Istio with Gateway API support (ambient for the
supplied namespace configuration), a NetworkPolicy-capable CNI, wildcard DNS/TLS,
Postgres, and an Okta or Google OIDC application.

Follow [Platform installation](deploy/platform/README.md). It covers the portal
Helm chart, Istio authorization provider, gateway, TLS Secret, and app namespaces.
For a local Kubernetes cluster, follow [Minikube](deploy/local/README.md).

Generate matching installation files with `scripts/configure-platform.py` and build
installable chart archives with `task package:charts`. Companies can set
`portal.displayName` (default `iap-portal`) without rebuilding the image.
The install guide covers custom domains, namespaces, registries, and Secrets.

## Ship an app

Install the SDK from this checkout, then scaffold:

```bash
uv tool install ./sdk
iap-portal init annotation --framework streamlit --dir annotation \
  --platform-repo YOUR_ORG/iap-portal
cd annotation
iap-portal dev
```

Replace `YOUR_ORG/iap-portal` with your actual platform repository. Before deploying,
configure the app's domain, owners, image, and workflow credentials as described in
[Adding an app](docs/ADDING_AN_APP.md). App deployment is Helm-based; GitHub Actions
is a convenience, not a requirement.

## Repository

| Path | Purpose |
| --- | --- |
| `backend/` | FastAPI portal: OIDC, app registry, authorization, access requests |
| `frontend/` | Vue portal and administration UI |
| `sdk/` | `iap-portal` CLI and `iap_portal` Python helpers |
| `charts/iap-app/` | App deployment, routing, isolation, optional registration |
| `deploy/platform/` | Production installation and portal Helm chart |
| `deploy/local/` | Minikube stack with mock identity provider |
| `base-images/` | Optional framework and GPU images, built from source |
| `examples/` | Streamlit and FastAPI streaming examples |

Run `task test` for Python tests, `task check:portability` for packaging/configuration
checks, and `cd frontend && npm ci && npm run build` for the frontend.
See [Contributing](CONTRIBUTING.md).

## License

Licensed under the [Apache License 2.0](LICENSE).
