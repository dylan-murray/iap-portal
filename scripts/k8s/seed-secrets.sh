#!/usr/bin/env bash
# Generate local JWT keypair + session secret + portal admin token and load
# them into the iap-portal namespace as Secrets. Idempotent.
set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-iap-portal}"

NS=iap-portal
KEY_DIR="${KEY_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)/backend/.dev-keys}"

# Create only if missing. Never `kubectl apply` a bare Namespace here: the
# three-way merge would delete the labels deploy/local/platform.yaml sets
# (iap-apps/routable, istio.io/dataplane-mode), detaching the portal's routes
# from the gateway and the namespace from the mesh.
kubectl --context "${PROFILE}" get namespace "${NS}" >/dev/null 2>&1 || kubectl --context "${PROFILE}" create namespace "${NS}"

if [[ ! -f "${KEY_DIR}/private.pem" ]]; then
  echo "generating JWT keypair → ${KEY_DIR}"
  mkdir -p "${KEY_DIR}"
  openssl genrsa -out "${KEY_DIR}/private.pem" 2048 >/dev/null 2>&1
  openssl rsa -in "${KEY_DIR}/private.pem" -pubout -out "${KEY_DIR}/public.pem" >/dev/null 2>&1
fi

kubectl --context "${PROFILE}" -n "${NS}" create secret generic portal-jwt-keys \
  --from-file=private.pem="${KEY_DIR}/private.pem" \
  --from-file=public.pem="${KEY_DIR}/public.pem" \
  --dry-run=client -o yaml | kubectl --context "${PROFILE}" apply -f -

# Preserve existing values across re-runs. Pod env vars from secretKeyRef are
# read at pod start; rotating the token here without restarting the portal
# would leave the pod using the OLD value while consumers (register Job) get
# the NEW one — instant 401s.
existing_or_new() {
  local name="$1" key="$2"
  local cur
  cur=$(kubectl --context "${PROFILE}" -n "${NS}" get secret "${name}" -o jsonpath="{.data.${key}}" 2>/dev/null | base64 -d 2>/dev/null || true)
  if [[ -n "${cur}" ]]; then echo "${cur}"; else openssl rand -hex 32; fi
}

SESSION_SECRET="${PORTAL_SESSION_SECRET:-$(existing_or_new portal-session secret)}"
kubectl --context "${PROFILE}" -n "${NS}" create secret generic portal-session \
  --from-literal=secret="${SESSION_SECRET}" \
  --dry-run=client -o yaml | kubectl --context "${PROFILE}" apply -f -

ADMIN_TOKEN="${PORTAL_ADMIN_TOKEN:-$(existing_or_new portal-admin-token token)}"
kubectl --context "${PROFILE}" -n "${NS}" create secret generic portal-admin-token \
  --from-literal=token="${ADMIN_TOKEN}" \
  --dry-run=client -o yaml | kubectl --context "${PROFILE}" apply -f -

echo "secrets seeded in ns/${NS}"
