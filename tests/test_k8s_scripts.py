"""Kubernetes manifest and isolated command-routing regressions."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "scripts/k8s").glob("*.sh"))


def test_scripts_never_reapply_bare_namespaces():
    """`kubectl create namespace --dry-run | kubectl apply` prunes labels set by
    deploy/local/platform.yaml (iap-apps/routable, ambient), which detached the
    portal and mock IdP routes from the gateway (404) and the mesh."""
    pattern = re.compile(r"create\s+namespace[^\n|]*--dry-run[^\n]*\|\s*kubectl\s+apply")
    offenders = [p.name for p in SCRIPTS if pattern.search(p.read_text())]
    assert offenders == []


def test_portal_namespace_labels_are_declared_where_routes_need_them():
    docs = [d for d in yaml.safe_load_all((ROOT / "deploy/local/platform.yaml").read_text()) if d]
    ns = next(d for d in docs if d["kind"] == "Namespace" and d["metadata"]["name"] == "iap-portal")
    assert ns["metadata"]["labels"]["iap-apps/routable"] == "true"
    assert ns["metadata"]["labels"]["istio.io/dataplane-mode"] == "ambient"

# Execute the real task/script entry points with fake clients. No cluster is
# contacted; an implicit context would hit the fake user's shared context.
@pytest.fixture
def cluster_clients(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.jsonl"
    fake = bindir / "client"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['CLIENT_LOG'], 'a') as f:\n"
        "    f.write(json.dumps([name, *args]) + '\\n')\n"
        "if name == 'minikube' and 'status' in args:\n"
        "    sys.exit(1)\n"
        "if name == 'kubectl' and 'coredns' in args and 'get' in args:\n"
        "    print('# iap-portal-rewrite')\n"
        "elif name == 'kubectl' and 'secret' in args and 'get' in args:\n"
        "    print('dGVzdC1vbmx5')\n"
    )
    fake.chmod(0o755)
    for name in ('kubectl', 'helm', 'minikube', 'istioctl', 'sudo'):
        (bindir / name).symlink_to(fake)
    cache = tmp_path / "cache" / "iap-portal" / "istio-test"
    cache.mkdir(parents=True)
    (cache / "istioctl").symlink_to(fake)
    keys = tmp_path / "keys"
    keys.mkdir()
    for name in ('private.pem', 'public.pem'):
        (keys / name).write_text('fixture only')
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               CLIENT_LOG=str(log), XDG_CACHE_HOME=str(tmp_path / 'cache'),
               ISTIO_VERSION='test', MINIKUBE_PROFILE='isolated-test',
               KEY_DIR=str(keys), KUBECONFIG=str(tmp_path / 'config'))
    pathlib_config = tmp_path / 'config'
    pathlib_config.write_text('current-context: shared-cluster\n')
    return env, log, pathlib_config


def assert_explicit_context(calls, context):
    for name, *args in calls:
        if name == 'sudo':
            assert args[:2] == ['-E', 'kubectl']
            args = args[2:]
            name = 'kubectl'
        if name not in ('kubectl', 'helm', 'istioctl'):
            continue
        flag = '--kube-context' if name == 'helm' else '--context'
        assert flag in args, (name, args)
        assert args[args.index(flag) + 1] == context, (name, args)
        assert 'use-context' not in args


@pytest.mark.parametrize('entry', [
    ['bash', 'scripts/k8s/bootstrap.sh'],
    ['bash', 'scripts/k8s/coredns-patch.sh'],
    ['bash', 'scripts/k8s/gateway-forward.sh'],
    ['task', 'k8s:apps'],
])
def test_local_entrypoints_never_use_active_context(cluster_clients, entry):
    env, log, config = cluster_clients
    if entry[0] == 'task' and not shutil.which('task'):
        pytest.skip('task is required')
    before = config.read_bytes()
    result = subprocess.run(entry, cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls
    assert_explicit_context(calls, 'isolated-test')
    for name, *args in calls:
        if name == 'minikube' and 'start' in args:
            assert '--keep-context' in args
            assert '--cni=calico' in args
    assert config.read_bytes() == before


def test_namespace_prep_requires_explicit_context(cluster_clients):
    env, log, _ = cluster_clients
    result = subprocess.run(['bash', 'scripts/k8s/prep-app-namespace.sh', 'demo'],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert not log.exists()
    result = subprocess.run(['bash', 'scripts/k8s/prep-app-namespace.sh', 'demo', 'chosen-cluster'],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert_explicit_context([json.loads(line) for line in log.read_text().splitlines()],
                            'chosen-cluster')
