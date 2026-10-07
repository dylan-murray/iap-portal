#!/usr/bin/env bash
# Operator step: create an app namespace with the labels Istio and the gateway
# selector require. Idempotent — safe to re-run.
#
# Usage:  scripts/k8s/prep-app-namespace.sh <slug> <context>
#
# No credential is copied into the namespace. The app chart's registration Job
# uses a projected ServiceAccount token that the portal validates with
# TokenReview and that can only register the app named by this namespace.
# Grant the app's deployers a namespaced Role in iap-app-<slug> only; they must
# not be able to create or edit Namespace objects.

set -euo pipefail

SLUG="${1:?usage: prep-app-namespace.sh <slug> <context>}"
if [[ ! "${SLUG}" =~ ^[a-z][a-z0-9-]{0,53}[a-z0-9]$ ]] || [[ "${SLUG}" == "portal" ]]; then
  echo "invalid or reserved slug: ${SLUG}" >&2
  exit 1
fi
CONTEXT="${2:?usage: prep-app-namespace.sh <slug> <context>}"
NS="iap-app-${SLUG}"

# Create only if missing (re-applying a bare Namespace can prune labels).
kubectl --context "${CONTEXT}" get namespace "${NS}" >/dev/null 2>&1 || kubectl --context "${CONTEXT}" create namespace "${NS}"
kubectl --context "${CONTEXT}" label --overwrite ns "${NS}" \
  istio.io/dataplane-mode=ambient \
  iap-apps/routable=true \
  app.kubernetes.io/managed-by=iap-portal \
  iap-apps/slug="${SLUG}"

echo "namespace ${NS} ready"
