#!/usr/bin/env bash
# Port-forward the Istio gateway Service to localhost:80.
# Runs until Ctrl-C. Requires sudo to bind :80.
set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-iap-portal}"

NS=iap-gateway
# The Gateway resource creates a Service named `<gateway-name>-istio`.
SVC=iap-apps-gateway-istio

echo "Forwarding svc/${SVC} 80 → localhost:80 (sudo required to bind :80)"
exec sudo -E kubectl --context "${PROFILE}" -n "${NS}" port-forward "svc/${SVC}" 80:80
