"""Generate consistent, non-secret installation files for a new platform."""
from __future__ import annotations

import argparse
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def dns(value: str) -> str:
    if len(value) > 253 or not all(LABEL.fullmatch(part) for part in value.split('.')):
        raise argparse.ArgumentTypeError('use a lowercase DNS name')
    return value


def label(value: str) -> str:
    if not LABEL.fullmatch(value):
        raise argparse.ArgumentTypeError('use a lowercase Kubernetes name, at most 63 characters')
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain', required=True, type=dns, help='parent app domain, e.g. apps.example.com')
    parser.add_argument('--image', required=True, help='portal image repository, without tag')
    parser.add_argument('--tag', required=True, help='immutable portal image tag')
    parser.add_argument('--display-name', default='iap-portal')
    parser.add_argument('--portal-namespace', default='iap-portal', type=label)
    parser.add_argument('--gateway-namespace', default='iap-gateway', type=label)
    parser.add_argument('--gateway-name', default='iap-apps-gateway', type=label)
    parser.add_argument('--tls-secret', default='iap-apps-wildcard-tls', type=label)
    parser.add_argument('--trust-domain', default='cluster.local', type=dns)
    parser.add_argument('--output', required=True, type=Path, help='new directory for generated files')
    args = parser.parse_args()
    if '.' not in args.domain or len('portal.' + args.domain) > 253:
        parser.error('--domain must have at least two labels and leave room for portal.<domain>')
    if args.portal_namespace == args.gateway_namespace:
        parser.error('portal and gateway namespaces must be distinct')
    if len(args.gateway_name) > 57:
        parser.error('gateway name must leave room for the generated -istio ServiceAccount suffix')
    if not 1 <= len(args.display_name.strip()) <= 100:
        parser.error('display name must contain 1–100 characters')
    if not args.image or any(c.isspace() for c in args.image) or '://' in args.image or '@' in args.image:
        parser.error('--image must be an image repository without a digest or URL scheme')
    if ':' in args.image.rsplit('/', 1)[-1]:
        parser.error('pass the image tag separately using --tag')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', args.tag):
        parser.error('invalid image tag')
    if args.output.exists():
        parser.error("output directory already exists; choose a new directory to preserve your configuration")
    args.output.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'deploy/platform'
    for filename in ('namespaces.yaml', 'gateway.yaml', 'auth-policy.yaml',
                     'route-admission-policy.yaml', 'mesh-config-patch.yaml'):
        text = (source / filename).read_text()
        text = text.replace('apps.example.com', args.domain)
        text = text.replace('namespace: iap-portal', f'namespace: {args.portal_namespace}')
        text = text.replace('name: iap-portal\n', f'name: {args.portal_namespace}\n')
        text = text.replace("'iap-portal'", f"'{args.portal_namespace}'")
        text = text.replace('other than iap-portal', f'other than {args.portal_namespace}')
        text = text.replace('portal.iap-portal.svc', f'portal.{args.portal_namespace}.svc')
        text = text.replace('iap-gateway', args.gateway_namespace)
        text = text.replace('iap-apps-gateway', args.gateway_name)
        text = text.replace('iap-apps-wildcard-tls', args.tls_secret)
        (args.output / filename).write_text(text)
    origin = f'https://portal.{args.domain}'
    service = f'http://portal.{args.portal_namespace}.svc.cluster.local:8090'
    values = {
        'image': {'repository': args.image, 'tag': args.tag},
        'portal': {'displayName': args.display_name.strip(), 'baseUrl': origin,
                   'appsDomain': args.domain},
        'httpRoute': {'hostname': f'portal.{args.domain}', 'gateway': {
            'name': args.gateway_name, 'namespace': args.gateway_namespace, 'sectionName': 'https'}},
        'authorization': {'gatewayPrincipal': f'{args.trust_domain}/ns/{args.gateway_namespace}/sa/{args.gateway_name}-istio'},
    }
    app_values = {
        'domain': args.domain,
        'gateway': {'name': args.gateway_name, 'namespace': args.gateway_namespace,
                    'serviceAccount': f'{args.gateway_name}-istio', 'sectionName': 'https'},
        'portal': {'issuer': origin, 'jwksUrl': service + '/.well-known/jwks.json'},
        'registration': {'portalUrl': service},
        'mesh': {'trustDomain': args.trust_domain},
    }
    for filename, value in [('portal-values.yaml', values), ('app-platform-values.yaml', app_values)]:
        (args.output / filename).write_text(yaml.safe_dump(value, sort_keys=False))
    print(f'Generated {args.output}. No cluster changes made.')
    print('Add IdP/admin settings to portal-values.yaml; create database, signing-key, and IdP Secrets.')
    print('Apply namespaces.yaml and create the TLS Secret in the gateway namespace.')
    print('Merge mesh-config-patch.yaml into your existing Istio configuration; preserve other providers.')
    print('Apply gateway.yaml, auth-policy.yaml, and route-admission-policy.yaml.')
    print(f'Install the portal chart with --namespace {args.portal_namespace} -f {args.output}/portal-values.yaml.')
    print('Use app-platform-values.yaml alongside each app values file.')


if __name__ == '__main__':
    main()
