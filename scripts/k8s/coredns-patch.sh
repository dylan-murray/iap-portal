#!/usr/bin/env bash
# Teach CoreDNS that *.iapportal.test resolves to the Istio gateway Service.
# Without this, the portal pod can't call OIDC discovery on mock-idp — that
# hostname only resolves on the laptop via /etc/hosts.
#
# Uses the `rewrite` plugin to map the query onto the gateway Service's
# in-cluster DNS name, then CoreDNS resolves that normally.
set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-iap-portal}"

MARKER="# iap-portal-rewrite"
# Trailing-dot match: CoreDNS sees queries as FQDNs (e.g. "mock-idp.iapportal.test.").
# Escape dots with \\ so the shell passes a single backslash to CoreDNS.
REWRITE="rewrite name regex ^(.*)\\.iapportal\\.test\\.\$ iap-apps-gateway-istio.iap-gateway.svc.cluster.local. answer auto ${MARKER}"

current=$(kubectl --context "${PROFILE}" -n kube-system get cm coredns -o jsonpath='{.data.Corefile}')

if grep -qF "${MARKER}" <<< "${current}"; then
  echo "coredns already patched"
  exit 0
fi

# Insert the rewrite rule right after the top-level `.:53 {` line.
patched=$(awk -v rule="    ${REWRITE}" '
  /^\.:53 \{/ { print; print rule; next }
  { print }
' <<< "${current}")

kubectl --context "${PROFILE}" -n kube-system create cm coredns \
  --from-literal=Corefile="${patched}" \
  --dry-run=client -o yaml \
  | kubectl --context "${PROFILE}" apply -f -

kubectl --context "${PROFILE}" -n kube-system rollout restart deploy/coredns
kubectl --context "${PROFILE}" -n kube-system rollout status deploy/coredns --timeout=60s
