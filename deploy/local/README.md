# iap-portal on minikube

Full-stack local Kubernetes environment for exercising the real auth chain:
Istio ambient + waypoint + `AuthorizationPolicy` ext_authz + Helm register-Job.
Exercises the Kubernetes/Istio path using local images, mock OIDC, and Kubernetes Secrets.
Requires Kubernetes 1.30+ for the route admission policy. App registration Jobs use
projected ServiceAccount tokens; no admin credential is copied into app namespaces.

## What you get

- Minikube cluster with Istio ambient, Gateway API v1, and a waypoint proxy
- `iap-portal` namespace: portal, Vue UI (served by portal), mock-idp, postgres
- `iap-gateway` namespace: shared `Gateway`, `AuthorizationPolicy` pointed at the portal
- Example apps (`streamlit-example`, `fastapi-sse`) installed via the `iap-app` Helm chart
- Access via `http://<slug>.iapportal.test` through a gateway port-forward

## Prereqs

Installed on PATH: `minikube`, `kubectl`, `helm`, `docker`, `openssl`, `curl`.
The bootstrap script selects Calico (`MINIKUBE_CNI=calico`) for NetworkPolicy
enforcement. Recreate older local profiles that use a CNI without policy enforcement.
The bootstrap script uses Kubernetes 1.35.1, Istio 1.31.1 (ambient), and Gateway API
v1.6.0, a combination Istio lists as supported. It downloads a checksum-verified
`istioctl` for that Istio version into `~/.cache/iap-portal/`; an `istioctl` already
on your PATH is neither used nor replaced. Override with `KUBERNETES_VERSION`,
`ISTIO_VERSION`, and `GATEWAY_API_VERSION`.

All local Kubernetes tasks explicitly target `MINIKUBE_PROFILE` (default
`iap-portal`), regardless of your active kubeconfig context. Bootstrap preserves
the active context. Set `MINIKUBE_PROFILE` consistently for setup, deployment,
port-forwarding, and teardown. For manual `kubectl` commands, pass
`--context "${MINIKUBE_PROFILE:-iap-portal}"` as well. The namespace preparation
script requires a context argument because it is also used for production.

## One-shot

```bash
task k8s:up          # cold-start: ~3-5 min (minikube + istio + CRDs)
task k8s:deploy      # build images, apply platform + portal + apps, pin hosts

# In a second terminal (keeps running):
task k8s:forward     # port-forward gateway to localhost:80 (sudo required)

# In a browser:
open http://portal.iapportal.test
open http://streamlit-example.iapportal.test
```

Log in as `alice` / `x` in the mock-idp. Alice is auto-promoted to admin via
`PORTAL_ADMIN_EMAILS`.

## What each task does

| Task | What it does |
|---|---|
| `k8s:up` | `minikube start`, Gateway API CRDs, `istioctl install --set profile=ambient` |
| `k8s:images` | `docker build` + `minikube image load` for portal, mock-idp, examples |
| `k8s:platform` | Apply MeshConfig patch, namespaces, waypoint, gateway, auth policies, route admission policy |
| `k8s:portal` | Seed JWT keypair + session + operator-token Secrets, deploy postgres + mock-idp + portal |
| `k8s:apps` | Prepare app namespaces (labels only) and `helm upgrade --install` each example app |
| `k8s:hosts` | Add `*.iapportal.test` → `127.0.0.1` to `/etc/hosts` |
| `k8s:forward` | `kubectl port-forward` the gateway Service to `localhost:80` |
| `k8s:deploy` | Runs images + platform + portal + apps + hosts in order |
| `k8s:down` | `minikube delete` (nuke the whole profile) |

## File layout

```
deploy/local/
├── README.md              (this file)
├── mesh-config-patch.yaml  IstioOperator — ext_authz provider
├── platform.yaml           namespaces, gateway, waypoint, auth policies, HTTPRoutes
├── route-admission-policy.yaml  hostname ↔ namespace binding for *.iapportal.test
├── portal.yaml             postgres + mock-idp + portal (ServiceAccount with TokenReview rights)
└── values-local.yaml       shared overlay for the iap-app Helm chart

scripts/k8s/
├── bootstrap.sh            minikube + istio + gateway-api
├── load-images.sh          build + load every image into the minikube VM
├── seed-secrets.sh         generate + apply portal-jwt-keys, portal-session, portal-admin-token (operator CLI only)
├── prep-app-namespace.sh   create iap-app-<slug> with required labels (no credentials)
├── hosts.sh                append *.iapportal.test → 127.0.0.1 to /etc/hosts
└── gateway-forward.sh      port-forward the gateway Service to localhost:80
```

## How this differs from prod

| Concern | Prod | Local |
|---|---|---|
| TLS | cert-manager + DNS-01 wildcard | HTTP only (port 80) |
| Domain | `*.apps.example.com` | `*.iapportal.test` via `/etc/hosts` |
| IdP | Okta / Google | mock-idp (alice/bob) |
| Image registry | Your container registry | `minikube image load` |
| Secrets | Kubernetes Secrets (optional External Secrets) | `secrets.existingSecret` when needed |
| Ingress | Istio Gateway on LB | Gateway + `kubectl port-forward` |

## Authorization listener

The portal pod contains public and authorization containers. The gateway checks
port 8091 using its mesh identity; API/JWKS/registration remain on 8090. The
`portal-listeners` policy in `portal.yaml` denies other workloads on 8091. Keep
the portal namespace enrolled in ambient mode and update the policy principal
if you rename the gateway or change its ServiceAccount.

## Common issues

- **Portal or mock IdP returns 404 from `istio-envoy`**: the route is not attached
  to the gateway. Check `kubectl get httproute -A` shows `Accepted=True` and that
  namespace `iap-portal` still has `iap-apps/routable=true` and
  `istio.io/dataplane-mode=ambient`. Re-applying a bare Namespace with
  `kubectl apply` removes those labels; re-run `kubectl apply -f deploy/local/platform.yaml`.
  (The mock IdP itself has no `/` page; use `/.well-known/openid-configuration`.)

- **`:80 permission denied` on `k8s:forward`**: that's why it sudos. Accept the prompt.
- **Gateway stuck `Unknown`**: wait a minute after `k8s:platform` — Istio needs to reconcile the GatewayClass.
- **Mock-idp redirect loops**: make sure `k8s:forward` is running and `/etc/hosts` points `mock-idp.iapportal.test` at `127.0.0.1`.
- **`/login` returns 500 with `httpx.ConnectError`**: CoreDNS isn't rewriting `*.iapportal.test` for in-cluster pods (the portal can't reach mock-idp for OIDC discovery). Re-run `scripts/k8s/coredns-patch.sh` and verify with `kubectl -n kube-system get cm coredns -o jsonpath='{.data.Corefile}' | grep iap-portal-rewrite`. Docker Desktop exposes the host's `/etc/hosts` to cluster DNS, which resolves `iapportal.test` names to `127.0.0.1` — the rewrite overrides that.

## Teardown

```bash
task k8s:down        # delete the whole minikube profile
```

Or, to keep the cluster but remove just iap-portal state:

```bash
helm uninstall -n iap-app-streamlit-example streamlit-example
helm uninstall -n iap-app-fastapi-sse fastapi-sse
kubectl delete -f deploy/local/portal.yaml
kubectl delete -f deploy/local/platform.yaml
```
