"""A new operator can generate and package an internally consistent install."""
from pathlib import Path
import subprocess
import sys
import tarfile
import shutil

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def generate(output, *args):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/configure-platform.py'),
        '--domain', 'tools.acme.test', '--image', 'registry.acme.test/portal', '--tag', 'v1.2.3',
        '--output', str(output), *args], text=True, capture_output=True)


@pytest.mark.skipif(shutil.which('helm') is None, reason='Helm required')
def test_custom_installation_is_consistent(tmp_path):
    out = tmp_path / 'install'
    result = generate(out, '--portal-namespace', 'company-portal', '--gateway-namespace', 'company-edge',
                      '--gateway-name', 'company-ingress', '--trust-domain', 'mesh.acme.test',
                      '--display-name', 'Acme Tools', '--tls-secret', 'company-tls')
    assert result.returncode == 0, result.stderr
    values = yaml.safe_load((out / 'portal-values.yaml').read_text())
    assert values['portal']['baseUrl'] == 'https://portal.tools.acme.test'
    assert values['authorization']['gatewayPrincipal'] == 'mesh.acme.test/ns/company-edge/sa/company-ingress-istio'
    gateway = yaml.safe_load((out / 'gateway.yaml').read_text())
    assert gateway['metadata'] == {'name':'company-ingress','namespace':'company-edge'}
    assert gateway['spec']['listeners'][0]['tls']['certificateRefs'][0]['name'] == 'company-tls'
    provider = yaml.safe_load((out / 'mesh-config-patch.yaml').read_text())
    assert provider['spec']['meshConfig']['extensionProviders'][0]['envoyExtAuthzHttp']['service'] == 'portal.company-portal.svc.cluster.local'
    for policy in yaml.safe_load_all((out / 'route-admission-policy.yaml').read_text()):
        if policy['kind']=='ValidatingAdmissionPolicy':
            assert policy['spec']['matchConditions'][0]['expression'] == "request.namespace != 'company-portal'"
    app = yaml.safe_load((out / 'app-platform-values.yaml').read_text())
    assert app['portal']['issuer'] == values['portal']['baseUrl']
    assert app['registration']['portalUrl'] == 'http://portal.company-portal.svc.cluster.local:8090'
    for chart, extra in [('deploy/platform/portal-chart', ['--namespace','company-portal','-f',str(out/'portal-values.yaml')]),
                         ('charts/iap-app', ['-f',str(out/'app-platform-values.yaml'),'--set','slug=demo','--set','image.repository=example/demo'])]:
        subprocess.run(['helm','template','demo',str(ROOT/chart),*extra],check=True,capture_output=True)
    assert generate(out).returncode != 0  # Never overwrite a user's configured files.


@pytest.mark.parametrize('args', [('--domain', "bad'host.example"), ('--domain','localhost'),
    ('--portal-namespace','invalid/name'), ('--gateway-name','x'*58), ('--display-name',' '),
    ('--image','repo/image:tag'), ('--gateway-namespace','iap-portal')])
def test_invalid_configuration_is_rejected_before_writing(tmp_path,args):
    out=tmp_path/'install'
    assert generate(out,*args).returncode != 0
    assert not out.exists()


@pytest.mark.skipif(shutil.which('helm') is None, reason='Helm required')
def test_chart_packages_exclude_local_material(tmp_path):
    result = subprocess.run(['bash',str(ROOT/'scripts/package-charts.sh'),str(tmp_path)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    archives=list(tmp_path.glob('*.tgz'))
    assert {p.name for p in archives} == {'iap-app-0.1.0.tgz', 'iap-portal-0.1.0.tgz'}
    for archive in archives:
        with tarfile.open(archive) as package:
            names=package.getnames()
            assert any(name.endswith('values.schema.json') for name in names)
            assert not any('.internal/' in name or name.endswith(('.pem','.key')) for name in names)
        subprocess.run(['helm','show','chart',str(archive)],check=True,capture_output=True)
