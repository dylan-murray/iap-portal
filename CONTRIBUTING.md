# Contributing

IAP Portal is for teams shipping internal apps. Kubernetes + Istio is the primary
platform; Docker Compose supports local development. Keep registries, secrets
providers, and deployment credentials configurable.

```bash
uv sync --all-extras --locked
task test
task check:portability
cd frontend
npm ci
npm run build
```

Python packages live under `backend/src/iap_portal_server` and `sdk/src/iap_portal`.
Schema changes need an Alembic revision in `backend/src/iap_portal_server/migrations`
(`tests` check that migrations match the models). After changing backend
dependencies, run `task deps:export` to refresh the portal image lock.
The SDK, CLI, templates, charts, and examples must agree on identity headers and
configuration names. Test generated apps as well as the platform itself.

Use example.com or reserved .test domains for fixtures. Never commit private keys,
real credentials, or local agent state. See [Security](docs/SECURITY.md) for known
release gaps. Tests passing is not evidence of a live cluster security review.

Open a pull request for changes. CI checks Python 3.10–3.14, the frontend build,
Helm charts, Compose configuration, and PostgreSQL migrations and session behavior.
Security workflows scan code and dependency changes. Workflows use pinned Actions
and read-only tokens; publishing runs separately with release approval.

The PostgreSQL integration test skips unless `IAP_TEST_POSTGRES_URL` points to a
disposable PostgreSQL database using a `postgresql+asyncpg://` URL. It creates and
removes an isolated schema; CI supplies a PostgreSQL 16 service automatically.

Repository maintainers can follow [repository controls](docs/repository-controls.md)
for the public-launch settings and verification steps.
