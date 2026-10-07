#!/usr/bin/env bash
# Pin *.iapportal.test → 127.0.0.1 in /etc/hosts so the browser resolves
# minikube-hosted apps through the gateway port-forward on localhost.
set -euo pipefail

ENTRY='127.0.0.1 portal.iapportal.test streamlit-example.iapportal.test fastapi-sse.iapportal.test mock-idp.iapportal.test'

if grep -q 'portal\.iapportal\.test' /etc/hosts; then
  echo "/etc/hosts already has *.iapportal.test entries"
  exit 0
fi

echo "Adding *.iapportal.test → 127.0.0.1 (sudo required, one-time)"
echo "${ENTRY}" | sudo tee -a /etc/hosts > /dev/null
echo "/etc/hosts updated."
