#!/usr/bin/env bash
# Package only distributable chart sources; never include local notes or secrets.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-${root}/dist/charts}"
mkdir -p "$out"
python3 "$root/scripts/check_portability.py"
helm lint "$root/charts/iap-app" --set slug=demo --set image.repository=example/demo
helm lint "$root/deploy/platform/portal-chart" --set image.repository=example/portal
helm package "$root/charts/iap-app" --destination "$out"
helm package "$root/deploy/platform/portal-chart" --destination "$out"
