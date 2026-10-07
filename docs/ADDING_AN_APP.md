# Adding an app

IAP Portal keeps the Kubernetes deployment path short: build a container, deploy it
with the app chart, and register it with the portal. Istio handles sign-in and app
access. The Python SDK is optional when the application does not need user identity.

## Scaffold and develop

Install from the platform checkout; the renamed package is not assumed published:

```bash
uv tool install ./sdk
iap-portal init my-tool --framework streamlit --dir my-tool \
  --platform-repo YOUR_ORG/iap-portal
cd my-tool
pip install streamlit
iap-portal dev
```

Framework choices are `streamlit`, `gradio`, `fastapi`, and `flask`. Native development
uses a fake user; `iap-portal dev --as alice@example.com --groups engineering` changes it.
Install the selected framework and its runner locally before starting.

## Build framework base images

From the platform repository root:

```bash
docker build -f base-images/python/Dockerfile -t iap-base/python:local .
docker build -f base-images/streamlit/Dockerfile -t iap-base/streamlit:local .
```

The foundation installs the SDK from this checkout. Framework Dockerfiles accept
`--build-arg BASE=YOUR_REGISTRY/iap-base/python:TAG`. App Dockerfiles accept
`--build-arg BASE_IMAGE=YOUR_REGISTRY/iap-base/streamlit:TAG`. This supports any
registry without relying on a public SDK release.

The optional `build-base-images` workflow publishes foundation/framework/GPU images
on manual dispatch. Supply a lowercase image prefix you own. It defaults to GHCR
credentials from `GITHUB_TOKEN`; other registries use `REGISTRY_USERNAME` and
`REGISTRY_PASSWORD` secrets. Make images public or provision pull credentials.

## Configure and deploy

Edit the scaffold's `iap-app.yaml`:

```yaml
slug: my-tool
displayName: My tool
framework: streamlit
entrypoint: app.py
port: 8080
domain: apps.example.com
image:
  repository: YOUR_REGISTRY/my-tool
  tag: "0.1.0"
  pullSecret: ""
registration:
  portalUrl: http://portal.iap-portal.svc.cluster.local:8090
  owners: [owner@example.com]
secrets:
  existingSecret: "" # optional Secret already in iap-app-my-tool
```

`registration.portalUrl` defaults to the in-cluster portal Service; `portal.issuer`
defaults to `https://portal.<domain>`. Have the platform operator prepare the
`iap-app-my-tool` namespace and your deploy permissions, as described in
[Platform installation](../deploy/platform/README.md#prepare-apps-and-deployers).
No portal credential is needed in the namespace: the registration Job proves its
identity with a short-lived Kubernetes ServiceAccount token, which lets it create
or update only `my-tool`. Owners listed here can manage the app after their first
sign-in; grant users/groups access in the portal after registration.

```bash
docker build --build-arg BASE_IMAGE=YOUR_REGISTRY/iap-base/streamlit:TAG \
  -t YOUR_REGISTRY/my-tool:0.1.0 .
docker push YOUR_REGISTRY/my-tool:0.1.0
helm upgrade --install my-tool /path/to/iap-portal/charts/iap-app \
  --namespace iap-app-my-tool --values iap-app.yaml --wait
```

The chart creates a ClusterIP Service, Deployment, ServiceAccounts, HTTPRoute,
NetworkPolicy, Istio AuthorizationPolicy, and optional registration Job. It does not
install Istio or expose the application on a separate public service. The HTTPRoute
hostname must be `my-tool.<domain>`; the platform's admission policy rejects others.

Pods run as uid 10001 (the framework base images' user) without privilege
escalation, capabilities, or a Kubernetes API token. If your image needs another
user, override `podSecurityContext` and `securityContext`. Only the gateway can
reach the app. Verify identity with the SDK (`IAP_PORTAL_VERIFY_JWT=true`, the
default) rather than trusting headers.

Streamlit apps keep the identity of their WebSocket connection. After
`portal.maxConnectionAgeSeconds` (default 3600) the SDK asks the user to reload,
which re-checks their session and access. Other frameworks verify a fresh token
on every request.

## Optional GitHub Actions deployment

The generated workflow points to the platform repo supplied to `init`. Replace
`YOUR_ORG/iap-portal` if you kept the placeholder. Configure these app-repo variables:

- `IAP_IMAGE_REPOSITORY`: full application image repository without tag.
- `IAP_BASE_IMAGE`: full published framework image, including tag.
- `IAP_REGISTRY`: registry hostname (defaults to `ghcr.io`).
- `IAP_DEPLOY_RUNNER`: optional JSON labels, e.g. `["self-hosted","k8s"]` for a
  private cluster; defaults to `["ubuntu-latest"]`.

Supply `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`, and `KUBECONFIG` secrets. The last
is raw kubeconfig for a deployment identity restricted to the prepared namespace.
The runner must reach the cluster API. The workflow checks out the platform chart,
builds/pushes the app, and waits for Helm deployment and registration. It neither
provisions namespaces nor copies the portal's admin credential.

For releases, pin the workflow reference and `platform-ref` input to the same
reviewed commit. See [GitHub reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).

## Optional external secrets

Existing Secrets need no controller. If you already run External Secrets Operator,
configure `secrets.external.enabled`, `storeRef`, and provider-specific `data` or
`dataFrom` mappings. The chart does not assume a particular secrets backend:

```yaml
secrets:
  external:
    enabled: true
    storeRef: {kind: ClusterSecretStore, name: team-secrets}
    data:
      - secretKey: API_KEY
        remoteRef: {key: my-tool/api-key}
```
