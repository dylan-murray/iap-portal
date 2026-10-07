# Local development

From the platform checkout, `task dev` runs Postgres/mock OIDC plus native backend
and frontend processes. `task up:full` builds the bundled apps and runs the complete
Compose stack. Both use development credentials and must remain local: they set
`PORTAL_ENV=dev`, which the portal only accepts for `localhost`, `*.localhost`, and
`*.test` URLs. The identity-free `/dev/login` endpoint additionally requires
`PORTAL_ENABLE_DEV_LOGIN=true`.

To test the Helm charts and Istio routing on a local Kubernetes cluster, use
the [Minikube walkthrough](../deploy/local/README.md). Use the workflows below
for Compose, hot reload, and SDK development.

If ports 8090, 8081, or 5433 are taken, set `IAP_PORTAL_GATEWAY_PORT`,
`IAP_PORTAL_IDP_PORT`, and `IAP_PORTAL_DB_PORT` for `docker compose`, and browse to
the portal on the gateway port you chose.

The mock IdP accepts only registered callback URLs. For custom local URLs, set
`MOCK_IDP_REDIRECT_URIS` to a comma-separated list of exact callback URLs.
Compose configures the gateway-port callback automatically. The defaults support
native port 8088, Compose port 8090, and the documented Minikube hostname.

The inner loop for an app developer working on an app is one command: `iap-portal dev`.
Three modes, picked with flags — default is native and fast.

## TL;DR

```bash
cd my-tool
iap-portal dev                            # native — streamlit/uvicorn/gunicorn direct, <1s start
iap-portal dev --as alice@example.com        # switch identity (test role-based logic)
iap-portal dev --docker                   # build & run your Dockerfile
iap-portal dev --full                     # full auth chain (Envoy + portal + mock IdP)
```

`current_user()` returns a real, typed `User` object in every mode.

---

## Mode 1: native (default)

What runs: the framework command from your `iap-app.yaml`, directly on your machine.

| framework | command iap-portal runs |
|---|---|
| streamlit | `streamlit run <entry> --server.port=$PORT` |
| fastapi   | `uvicorn <entry> --reload --port=$PORT` |
| flask     | `flask run --debug --port=$PORT` |
| gradio    | `python <entry>` (with `GRADIO_SERVER_PORT=$PORT`) |

`current_user()` returns a fake user because `IAP_PORTAL_DEV=1` is set. No JWT, no
portal required:

```python
User(
    email="you@example.com",
    name="You",
    groups=["engineering"],
    user_id="dev-user",
)
```

Override any of those:

```bash
iap-portal dev --as alice@example.com --groups engineering,admins --name "Alice"
```

**Use this mode for 95% of work.** Edits save → hot reload → browser. Debugger and
`breakpoint()` just work. No Docker, no IdP, no portal.

---

## Mode 2: `--docker`

What runs: `docker build -t iap-portal-dev-<slug>:local . && docker run -p $PORT:$PORT ...`

Same fake-user injection as native mode, but your app runs inside the actual
Dockerfile you'll ship. Use this **before pushing** to catch:

- Missing system packages (apt libs you didn't list)
- Python version drift between your venv and the base image
- Dockerfile typos / layering bugs

Slower (30–60s rebuild on code changes), so it's not the everyday loop.

---

## Mode 3: `--full`

What runs: `docker compose up` with Envoy + portal + mock OIDC + your app container.

Use this **only when debugging auth itself**:
- Does my JWT verification actually work?
- Do the headers ext_authz injects reach my app?
- Does my app break when a user has unexpected groups?

One-time setup — add to `/etc/hosts`:

```
127.0.0.1   portal.iapportal.test my-tool.iapportal.test mock-idp.iapportal.test
```

Build your framework base images first (see [Adding an app](ADDING_AN_APP.md)).
Run `task dev:keys` in the platform checkout, then:

```bash
export IAP_PORTAL_SOURCE=/absolute/path/to/iap-portal
iap-portal dev --full
# → open http://my-tool.iapportal.test:8090
# → gets 302 to http://portal.iapportal.test:8090/login
# → click "Continue with SSO" → pick a test user from the mock IdP
# → redirected back with a real session cookie
# → Envoy sub-requests /auth/verify on every request
# → portal mints a real JWT with real kid, signed with the dev key
# → your app verifies it via JWKS
```

The SDK stack binds the gateway to 8090 and the mock IdP to 8081; do not run it
alongside the root Compose stack on those ports. The app container listens on 8080
in full mode (`--port` applies only to native/docker modes). Sign in as `alice`, then
register your app from its directory in a second terminal:

```bash
export IAP_PORTAL_URL=http://portal.iapportal.test:8090
export IAP_PORTAL_TOKEN=dev-admin-token-change-me-in-prod   # local operator token
iap-portal apply
```

Local stacks use plain HTTP, so cookies cannot use the `__Host-` prefix that
protects them from sibling subdomains in HTTPS deployments.

Set `registration.owners: [alice@example.com]` in `iap-app.yaml` for this local fixture.

This exercises local Envoy. Validate the Kubernetes/Istio boundary separately on your cluster.

---

## When to pick which

| You're doing... | Mode |
|---|---|
| Writing features, iterating on UI | native |
| Verifying access-control logic against different groups | native, switch with `--as` |
| Pre-deploy sanity check before pushing | `--docker` |
| Debugging "why isn't my user showing up?" or "why is my JWT invalid?" | `--full` |
| Adding a system dep (ffmpeg, libgl1) to your Dockerfile | `--docker` |

---

## Pre-flight

```bash
iap-portal doctor
```

Checks: `iap-app.yaml` present and valid, `Dockerfile` present, `.github/workflows/deploy.yml`
present, required framework binary on `PATH`.

---

## What gets injected in each mode

| Env var | native / docker | --full |
|---|---|---|
| `IAP_PORTAL_DEV` | `1` (fake user) | — (real verification) |
| `IAP_PORTAL_DEV_EMAIL` / `_NAME` / `_GROUPS` | from `--as` / `--groups` / `--name` | — |
| `IAP_PORTAL_APP_SLUG` | from `iap-app.yaml` | from `iap-app.yaml` |
| `IAP_PORTAL_ISSUER` | unused | `http://portal.iapportal.test:8090` |
| `IAP_PORTAL_JWKS_URL` | unused | `http://portal:8090/.well-known/jwks.json` |
| `IAP_PORTAL_VERIFY_JWT` | unused (dev short-circuits) | `true` |

This is exactly the shape of the env in production, minus the fake-user knobs.
The SDK refuses `IAP_PORTAL_DEV` when it detects Kubernetes.

### Container authorization isolation

Full Compose stacks run a separate `authorization` service on port 8091. Only
Envoy shares its authorization network; its database connection uses another
private network. Apps keep using `portal:8090` for JWKS and registration, and
cannot call the private listener directly. Do not publish its port or join app
containers to either private network. Native backend-only development serves the
public app; use the full stack to exercise gateway authorization.
