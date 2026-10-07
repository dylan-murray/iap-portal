#!/usr/bin/env bash
# Build portal, mock-idp, and example app images locally and load them into
# the minikube VM so Deployments can pull them with IfNotPresent.
set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-iap-portal}"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Tag images with both `:local` and a unique `:local-<ts>` so kubelet can't
# silently reuse a cached layer under IfNotPresent. `k8s:apps` references
# `:local`; manual `kubectl set image ... :local-<ts>` forces a re-pull.
TS=$(date +%s)

build_and_load() {
  local name="$1" ctx="$2" dockerfile="$3"
  echo "==> build ${name}:local (and ${name}:local-${TS})"
  docker build -t "${name}:local" -t "${name}:local-${TS}" -f "${dockerfile}" "${ctx}"
  echo "==> load ${name} → minikube/${PROFILE}"
  minikube -p "${PROFILE}" image load "${name}:local"
  minikube -p "${PROFILE}" image load "${name}:local-${TS}"
}

build_and_load "iap-portal/portal"            "${REPO_ROOT}"              "${REPO_ROOT}/backend/Dockerfile"
build_and_load "iap-portal/mock-idp"          "${REPO_ROOT}/dev/mock-idp" "${REPO_ROOT}/dev/mock-idp/Dockerfile"
build_and_load "iap-portal/streamlit-example" "${REPO_ROOT}"              "${REPO_ROOT}/examples/streamlit-app/Dockerfile"
build_and_load "iap-portal/fastapi-sse"       "${REPO_ROOT}"              "${REPO_ROOT}/examples/fastapi-sse/Dockerfile"

cat <<EOF

Built with timestamp tag: local-${TS}
To force-pull on a running cluster:
  kubectl --context "${PROFILE}" -n iap-portal set image deploy/portal portal=iap-portal/portal:local-${TS}
  kubectl --context "${PROFILE}" -n iap-app-streamlit-example set image deploy/streamlit-example app=iap-portal/streamlit-example:local-${TS}
  kubectl --context "${PROFILE}" -n iap-app-fastapi-sse set image deploy/fastapi-sse app=iap-portal/fastapi-sse:local-${TS}
EOF
