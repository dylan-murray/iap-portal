#!/usr/bin/env bash
# Bootstrap a minikube cluster with everything iap-portal needs:
#   - minikube (started if not running)
#   - Gateway API v1 CRDs
#   - Istio ambient (ztunnel + CNI + control plane)
#   - waypoint class
#
# Idempotent: re-running is cheap. Safe to invoke from `task k8s:up`.
#
# Prereqs: minikube, kubectl, helm, curl on PATH (istioctl is downloaded per version).

set -euo pipefail

PROFILE="${MINIKUBE_PROFILE:-iap-portal}"
CPUS="${MINIKUBE_CPUS:-4}"
MEMORY="${MINIKUBE_MEMORY:-8192}"
DRIVER="${MINIKUBE_DRIVER:-docker}"
CNI="${MINIKUBE_CNI:-calico}"  # NetworkPolicy enforcement is required for isolation.
# Istio 1.31 supports Kubernetes 1.32-1.36 and documents Gateway API v1.6.0
# (https://istio.io/latest/docs/releases/supported-releases/).
ISTIO_VERSION="${ISTIO_VERSION:-1.31.1}"
GATEWAY_API_VERSION="${GATEWAY_API_VERSION:-v1.6.0}"
KUBERNETES_VERSION="${KUBERNETES_VERSION:-v1.35.1}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing dep: $1"; exit 1; }; }
need minikube
need kubectl
need helm

echo "==> minikube profile=${PROFILE} driver=${DRIVER}"
if ! minikube -p "${PROFILE}" status >/dev/null 2>&1; then
  minikube -p "${PROFILE}" start --keep-context \
    --cpus="${CPUS}" --memory="${MEMORY}" \
    --driver="${DRIVER}" --cni="${CNI}" \
    --kubernetes-version="${KUBERNETES_VERSION}"
fi

echo "==> Gateway API CRDs (${GATEWAY_API_VERSION})"
kubectl --context "${PROFILE}" apply --server-side -f "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"

echo "==> Istio ambient (${ISTIO_VERSION})"
# Use an istioctl matching ISTIO_VERSION from a per-version cache; never replace
# an istioctl already on PATH.
ISTIO_CACHE="${XDG_CACHE_HOME:-${HOME}/.cache}/iap-portal/istio-${ISTIO_VERSION}"
ISTIOCTL="${ISTIO_CACHE}/istioctl"
if [[ ! -x "${ISTIOCTL}" ]]; then
  # Istio's release filenames use `osx` for macOS and amd64/arm64 architectures.
  OS=$(uname -s | tr '[:upper:]' '[:lower:]')
  case "${OS}" in darwin) OS=osx ;; esac
  ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
  TARBALL="istioctl-${ISTIO_VERSION}-${OS}-${ARCH}.tar.gz"
  URL="https://github.com/istio/istio/releases/download/${ISTIO_VERSION}/${TARBALL}"
  echo "downloading istioctl ${ISTIO_VERSION} → ${ISTIO_CACHE}"
  mkdir -p "${ISTIO_CACHE}"
  TMP=$(mktemp -d)
  trap 'rm -rf "${TMP}"' EXIT
  curl -fsSL "${URL}" -o "${TMP}/${TARBALL}"
  curl -fsSL "${URL}.sha256" -o "${TMP}/${TARBALL}.sha256"
  expected=$(awk '{print $1}' "${TMP}/${TARBALL}.sha256")
  actual=$(shasum -a 256 "${TMP}/${TARBALL}" | awk '{print $1}')
  [[ -n "${expected}" && "${expected}" == "${actual}" ]] || { echo "istioctl checksum mismatch" >&2; exit 1; }
  tar -xzf "${TMP}/${TARBALL}" -C "${ISTIO_CACHE}" istioctl
  chmod +x "${ISTIOCTL}"
fi

# Ambient profile: installs istiod + ztunnel DaemonSet + CNI.
# --skip-confirmation so the script is non-interactive.
"${ISTIOCTL}" --context "${PROFILE}" install --set profile=ambient --skip-confirmation

echo "==> waiting for istiod + ztunnel"
kubectl --context "${PROFILE}" -n istio-system rollout status deploy/istiod --timeout=180s
kubectl --context "${PROFILE}" -n istio-system rollout status ds/ztunnel --timeout=180s

echo "==> done. next:  task k8s:platform"
