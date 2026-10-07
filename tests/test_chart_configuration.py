"""Render charts and check the assembled platform configuration; no live cluster needed."""
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="Helm not installed")
PLATFORM = ROOT / "deploy/platform"
LOCAL = ROOT / "deploy/local"


def _docs(text):
    return [doc for doc in yaml.safe_load_all(text) if doc]


def render(*args):
    text = subprocess.check_output([
        "helm", "template", "demo", str(ROOT / "charts/iap-app"),
        "--set", "slug=demo-app", "--set", "image.repository=example/demo", *args,
    ], text=True)
    return _docs(text)


def render_portal(*args):
    text = subprocess.check_output([
        "helm", "template", "portal", str(PLATFORM / "portal-chart"), "--namespace", "iap-portal",
        "--set", "image.repository=example/portal", *args,
    ], text=True)
    return _docs(text)


def kind(docs, name):
    return [d for d in docs if d["kind"] == name]


def one(docs, name, resource_name=None):
    matches = kind(docs, name)
    if resource_name is not None:
        matches = [d for d in matches if d["metadata"]["name"] == resource_name]
    [doc] = matches
    return doc


def load(path):
    return _docs(path.read_text())


# --- iap-app chart -------------------------------------------------------------------


def test_default_chart_needs_no_registry_secret_or_external_secrets():
    docs = render()
    assert not kind(docs, "ExternalSecret")
    pod = one(docs, "Deployment")["spec"]["template"]["spec"]
    assert "imagePullSecrets" not in pod
    assert "envFrom" not in pod["containers"][0]
    job = one(docs, "Job")
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == "python:3.12-slim"
    env = {e["name"]: e["value"] for e in pod["containers"][0]["env"]}
    assert env["IAP_PORTAL_APP_SLUG"] == "demo-app"
    assert env["IAP_PORTAL_ISSUER"] == "https://portal.apps.example.com"
    assert env["IAP_PORTAL_VERIFY_JWT"] == "true"


def test_existing_secret_and_private_image():
    docs = render("--set", "secrets.existingSecret=my-runtime", "--set", "image.pullSecret=registry-login")
    pod = one(docs, "Deployment")["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "registry-login"}]
    assert pod["containers"][0]["envFrom"] == [{"secretRef": {"name": "my-runtime"}}]


def test_optional_external_secret_accepts_provider_neutral_mapping():
    docs = render("--set", "secrets.external.enabled=true", "--set", "secrets.external.storeRef.name=team-store",
                  "--set", "secrets.external.data[0].secretKey=API_KEY",
                  "--set", "secrets.external.data[0].remoteRef.key=tool/api-key")
    secret = one(docs, "ExternalSecret")
    assert secret["spec"]["secretStoreRef"]["name"] == "team-store"
    assert secret["spec"]["data"][0] == {"secretKey": "API_KEY", "remoteRef": {"key": "tool/api-key"}}


def test_registration_uses_projected_workload_identity_not_a_shared_secret():
    docs = render()
    text = yaml.safe_dump_all(docs)
    assert "portal-admin-token" not in text and "secretKeyRef" not in text
    job = one(docs, "Job")["spec"]["template"]["spec"]
    assert job["serviceAccountName"] == "iap-portal-registration"
    assert job["automountServiceAccountToken"] is False
    [source] = job["volumes"][0]["projected"]["sources"]
    token = source["serviceAccountToken"]
    assert token["audience"] == "iap-portal" and token["expirationSeconds"] <= 3600
    assert "http://portal.iap-portal.svc.cluster.local:8090" in job["containers"][0]["args"][0]
    accounts = {sa["metadata"]["name"]: sa for sa in kind(docs, "ServiceAccount")}
    assert accounts["iap-portal-registration"]["metadata"]["namespace"] == "iap-app-demo-app"


def test_workload_security_defaults():
    docs = render()
    pod = one(docs, "Deployment")["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    container = pod["containers"][0]["securityContext"]
    assert container["allowPrivilegeEscalation"] is False and container["capabilities"] == {"drop": ["ALL"]}
    app_sa = next(sa for sa in kind(docs, "ServiceAccount") if sa["metadata"]["name"] == "demo-app")
    assert app_sa["automountServiceAccountToken"] is False
    job = one(docs, "Job")["spec"]["template"]["spec"]
    assert job["securityContext"]["runAsNonRoot"] is True
    assert job["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True


def test_network_policy_admits_only_gateway_pods_including_ambient_hbone():
    policy = one(render(), "NetworkPolicy")["spec"]
    gateway_rule, probe_rule = policy["ingress"]
    [peer] = gateway_rule["from"]
    assert peer["namespaceSelector"]["matchLabels"] == {"kubernetes.io/metadata.name": "iap-gateway"}
    assert peer["podSelector"]["matchLabels"] == {"gateway.networking.k8s.io/gateway-name": "iap-apps-gateway"}
    assert {p["port"] for p in gateway_rule["ports"]} == {8090, 15008}
    assert probe_rule == {"from": [{"ipBlock": {"cidr": "169.254.7.127/32"}}], "ports": [{"port": 8090, "protocol": "TCP"}]}
    plain = one(render("--set", "networkPolicy.ambient=false"), "NetworkPolicy")["spec"]
    assert [p["port"] for p in plain["ingress"][0]["ports"]] == [8090]


def test_mesh_authorization_allows_only_the_gateway_identity():
    policy = one(render(), "AuthorizationPolicy")["spec"]
    assert policy["action"] == "ALLOW"
    assert policy["rules"] == [{"from": [{"source": {"principals": [
        "cluster.local/ns/iap-gateway/sa/iap-apps-gateway-istio"]}}]}]
    assert not kind(render("--set", "mesh.authorizationPolicy=false"), "AuthorizationPolicy")


def test_route_hostname_matches_namespace_binding():
    route = one(render(), "HTTPRoute")
    assert route["metadata"]["namespace"] == "iap-app-demo-app"
    assert route["spec"]["hostnames"] == ["demo-app.apps.example.com"]


@pytest.mark.parametrize("args", [
    ("--set", "env.IAP_PORTAL_DEV=1"),
    ("--set", "slug=portal"),
    ("--set", "slug=Bad_Slug"),
])
def test_chart_refuses_unsafe_values(args):
    with pytest.raises(subprocess.CalledProcessError):
        render(*args)


# --- Portal chart and assembled platform ---------------------------------------------------


def test_portal_service_is_owned_once_and_ports_agree():
    portal = render_portal()
    platform_docs = [d for path in sorted(PLATFORM.glob("*.yaml")) for d in load(path)]
    services = [d for d in portal + platform_docs if d["kind"] == "Service"]
    assert [s["metadata"]["name"] for s in services] == ["portal"]
    service_port = next(p for p in services[0]["spec"]["ports"] if p["port"] == 8090)
    container = one(portal, "Deployment")["spec"]["template"]["spec"]["containers"][0]
    [container_port] = container["ports"]
    assert service_port["port"] == container_port["containerPort"] == 8090
    route = one(portal, "HTTPRoute")
    assert route["spec"]["rules"][0]["backendRefs"] == [{"name": "portal", "port": 8090}]
    provider = one(load(PLATFORM / "mesh-config-patch.yaml"), "IstioOperator")
    [ext] = provider["spec"]["meshConfig"]["extensionProviders"]
    assert ext["envoyExtAuthzHttp"]["port"] == 8091
    assert ext["envoyExtAuthzHttp"]["service"] == "portal.iap-portal.svc.cluster.local"
    assert not (PLATFORM / "portal-httproute.yaml").exists()


def test_portal_runs_restricted_with_token_review_rights():
    portal = render_portal()
    pod = one(portal, "Deployment")["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "iap-portal"
    assert pod["securityContext"]["runAsNonRoot"] is True
    container = pod["containers"][0]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["PORTAL_ENV"] == "production"
    assert env["PORTAL_KUBERNETES_REGISTRATION_ENABLED"] == "true"
    assert "PORTAL_COOKIE_DOMAIN" not in env and "PORTAL_ALLOWED_RETURN_TO_HOSTS" not in env
    binding = one(portal, "ClusterRoleBinding")
    assert binding["roleRef"]["name"] == "system:auth-delegator"
    assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "iap-portal", "namespace": "iap-portal"}]
    disabled = render_portal("--set", "kubernetesRegistration.enabled=false")
    assert not kind(disabled, "ClusterRoleBinding")
    assert one(disabled, "ServiceAccount")["automountServiceAccountToken"] is False


@pytest.mark.parametrize("directory", [PLATFORM, LOCAL])
def test_ext_authz_provider_trusts_only_the_check_authority(directory):
    [ext] = one(load(directory / "mesh-config-patch.yaml"), "IstioOperator")["spec"]["meshConfig"]["extensionProviders"]
    http = ext["envoyExtAuthzHttp"]
    assert http["pathPrefix"] == "/auth/verify"
    assert not {"x-forwarded-host", "x-forwarded-proto", "x-original-uri"} & set(http["includeRequestHeadersInCheck"])
    assert {"cookie", "origin"} <= set(http["includeRequestHeadersInCheck"])
    assert {"cookie", "authorization", "x-forwarded-email"} <= set(http["headersToUpstreamOnAllow"])
    assert {"location", "set-cookie"} <= set(http["headersToDownstreamOnDeny"])
    assert http.get("failOpen", False) is False


@pytest.mark.parametrize("path,portal_host,port", [
    (PLATFORM / "auth-policy.yaml", "portal.apps.example.com", "443"),
    (LOCAL / "platform.yaml", "portal.iapportal.test", "80"),
])
def test_custom_policy_excludes_only_the_portal_and_denies_internal_paths(path, portal_host, port):
    policies = {p["metadata"]["name"]: p["spec"] for p in load(path) if p["kind"] == "AuthorizationPolicy"}
    custom = policies["require-portal-auth"]
    assert custom["action"] == "CUSTOM"
    [rule] = custom["rules"]
    [operation] = [t["operation"] for t in rule["to"]]
    assert "hosts" not in operation  # every other host, including unknown ones, is checked
    assert portal_host in operation["notHosts"]
    deny = policies["deny-portal-internal-paths"]
    assert deny["action"] == "DENY"
    deny_op = deny["rules"][0]["to"][0]["operation"]
    assert set(deny_op["paths"]) == {"/auth/verify", "/auth/verify/*"}
    # Scoped to the HTTP listener port, so Istio does not apply it to TCP traffic.
    assert deny_op["ports"] == [port]


@pytest.mark.parametrize("path", [PLATFORM / "gateway.yaml", LOCAL / "platform.yaml"])
def test_gateway_accepts_only_httproutes_from_routable_namespaces(path):
    gateway = next(d for d in load(path) if d["kind"] == "Gateway" and d["metadata"]["name"] == "iap-apps-gateway")
    [listener] = gateway["spec"]["listeners"]
    assert listener["allowedRoutes"]["kinds"] == [{"kind": "HTTPRoute"}]
    assert listener["allowedRoutes"]["namespaces"]["selector"]["matchLabels"] == {"iap-apps/routable": "true"}


@pytest.mark.parametrize("path,domain", [
    (PLATFORM / "route-admission-policy.yaml", "apps.example.com"),
    (LOCAL / "route-admission-policy.yaml", "iapportal.test"),
])
def test_route_admission_policy_is_bound_and_denies(path, domain):
    docs = load(path)
    policy = one(docs, "ValidatingAdmissionPolicy", "iap-portal-route-hostnames")
    binding = one(docs, "ValidatingAdmissionPolicyBinding", "iap-portal-route-hostnames")
    assert binding["spec"] == {"policyName": policy["metadata"]["name"], "validationActions": ["Deny"]}
    assert policy["spec"]["failurePolicy"] == "Fail"
    resources = policy["spec"]["matchConstraints"]["resourceRules"][0]["resources"]
    assert {"httproutes", "grpcroutes"} <= set(resources)
    variables = {v["name"]: v["expression"] for v in policy["spec"]["variables"]}
    assert variables["domain"] == f"'{domain}'"


def test_local_and_compose_gateways_match_production_check_headers():
    [ext] = one(load(PLATFORM / "mesh-config-patch.yaml"), "IstioOperator")["spec"]["meshConfig"]["extensionProviders"]
    istio = ext["envoyExtAuthzHttp"]
    for envoy_path in (ROOT / "dev/envoy.yaml", ROOT / "sdk/src/iap_portal/dev/envoy.yaml"):
        config = yaml.safe_load(envoy_path.read_text())
        hcm = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"]
        authz = next(f for f in hcm["http_filters"] if f["name"] == "envoy.filters.http.ext_authz")["typed_config"]
        service = authz["http_service"]
        allowed = {p["exact"] for p in service["authorization_request"]["allowed_headers"]["patterns"]}
        assert allowed == set(istio["includeRequestHeadersInCheck"]), envoy_path
        upstream = {p["exact"] for p in service["authorization_response"]["allowed_upstream_headers"]["patterns"]}
        assert upstream == set(istio["headersToUpstreamOnAllow"]), envoy_path
        assert authz["failure_mode_allow"] is False
        portal_vhost = next(v for v in hcm["route_config"]["virtual_hosts"] if v["name"] == "portal")
        assert portal_vhost["routes"][0]["typed_per_filter_config"]["envoy.filters.http.ext_authz"]["disabled"] is True


@pytest.mark.parametrize("gateway_path,policy_path,admission_path,portal_host", [
    (PLATFORM / "gateway.yaml", PLATFORM / "auth-policy.yaml", PLATFORM / "route-admission-policy.yaml",
     "portal.apps.example.com"),
    (LOCAL / "platform.yaml", LOCAL / "platform.yaml", LOCAL / "route-admission-policy.yaml",
     "portal.iapportal.test"),
])
def test_policies_match_the_gateway_listener(gateway_path, policy_path, admission_path, portal_host):
    """Port-, host-, and domain-scoped policies only work if they agree with the listener.

    A DENY rule scoped to the wrong port, or a notHosts entry for the wrong port,
    would silently stop matching. Changing the listener must change these too.
    """
    gateway = next(d for d in load(gateway_path) if d["kind"] == "Gateway" and d["metadata"]["name"] == "iap-apps-gateway")
    [listener] = gateway["spec"]["listeners"]
    port = str(listener["port"])
    domain = listener["hostname"].removeprefix("*.")
    policies = {p["metadata"]["name"]: p["spec"] for p in load(policy_path) if p["kind"] == "AuthorizationPolicy"}
    deny = policies["deny-portal-internal-paths"]["rules"][0]["to"][0]["operation"]
    assert deny["ports"] == [port]
    assert set(deny["hosts"]) == {portal_host, f"{portal_host}:{port}"}
    custom = policies["require-portal-auth"]["rules"][0]["to"][0]["operation"]
    assert {portal_host, f"{portal_host}:{port}"} <= set(custom["notHosts"])
    assert portal_host == f"portal.{domain}"
    variables = {v["name"]: v["expression"] for v in one(load(admission_path), "ValidatingAdmissionPolicy", "iap-portal-route-hostnames")["spec"]["variables"]}
    assert variables["domain"] == f"'{domain}'"


def test_production_routes_attach_to_the_https_listener():
    [listener] = one(load(PLATFORM / "gateway.yaml"), "Gateway")["spec"]["listeners"]
    assert (listener["name"], listener["protocol"], listener["port"]) == ("https", "HTTPS", 443)
    assert listener["tls"]["mode"] == "Terminate"
    portal_route = one(render_portal(), "HTTPRoute")
    assert portal_route["spec"]["parentRefs"][0]["sectionName"] == listener["name"]
    assert portal_route["spec"]["hostnames"] == ["portal.apps.example.com"]
    app_route = one(render(), "HTTPRoute")
    assert app_route["spec"]["parentRefs"][0]["sectionName"] == listener["name"]


def test_app_service_aliases_are_rejected_on_create_and_update():
    for path in ("deploy/local/route-admission-policy.yaml", "deploy/platform/route-admission-policy.yaml"):
        docs = list(yaml.safe_load_all((ROOT / path).read_text()))
        policy = next(d for d in docs if d["kind"] == "ValidatingAdmissionPolicy"
                      and d["metadata"]["name"] == "iap-portal-service-backends")
        spec = policy["spec"]
        assert spec["failurePolicy"] == "Fail"
        rule = spec["matchConstraints"]["resourceRules"][0]
        assert set(rule["operations"]) == {"CREATE", "UPDATE"}
        assert rule["resources"] == ["services"]
        assert spec["validations"][0]["expression"] == (
            "!has(object.spec.type) || object.spec.type != 'ExternalName'"
        )
        binding = next(d for d in docs if d["kind"] == "ValidatingAdmissionPolicyBinding"
                       and d["metadata"]["name"] == "iap-portal-service-backends")
        assert binding["spec"] == {"policyName": "iap-portal-service-backends", "validationActions": ["Deny"]}


@pytest.mark.parametrize("local", [False, True])
def test_authorization_listener_is_separate_and_gateway_only(local):
    docs = load(LOCAL / "portal.yaml") if local else render_portal()
    deploy = one([d for d in docs if d["metadata"]["name"] == "portal"], "Deployment")
    containers = {c["name"]: c for c in deploy["spec"]["template"]["spec"]["containers"]}
    assert set(containers) == {"portal", "authorization"}
    assert containers["authorization"]["ports"][0]["containerPort"] == 8091
    assert "iap_portal_server.main:create_authorization_app" in containers["authorization"]["args"]
    assert containers["portal"]["env"] == containers["authorization"]["env"]
    service = one([d for d in docs if d["metadata"]["name"] == "portal"], "Service")
    targets = {p["name"]: p["containerPort"] for c in containers.values() for p in c["ports"]}
    assert {p["port"]: targets[p["targetPort"]] for p in service["spec"]["ports"]} == {8090: 8090, 8091: 8091}
    policy = one(docs, "AuthorizationPolicy", "portal-listeners")["spec"]
    assert policy["selector"] == {"matchLabels": {"app": "portal"}}
    assert policy["action"] == "ALLOW"
    assert policy["rules"] == [
        {"to": [{"operation": {"ports": ["8090"]}}]},
        {"from": [{"source": {"principals": ["cluster.local/ns/iap-gateway/sa/iap-apps-gateway-istio"]}}],
         "to": [{"operation": {"ports": ["8091"]}}]},
    ]


def test_authorization_gateway_identity_can_be_configured():
    docs = render_portal("--set", "authorization.gatewayPrincipal=custom.local/ns/ingress/sa/gateway")
    policy = one(docs, "AuthorizationPolicy", "portal-listeners")
    assert policy["spec"]["rules"][1]["from"][0]["source"]["principals"] == ["custom.local/ns/ingress/sa/gateway"]


@pytest.mark.parametrize("path", [ROOT / "docker-compose.yml", ROOT / "sdk/src/iap_portal/dev/docker-compose.yml"])
def test_compose_authorization_has_no_app_network(path):
    config = yaml.safe_load(path.read_text())
    services = config["services"]
    assert services["authorization"]["networks"] == ["authorization", "authorization-db"]
    assert services["envoy"]["networks"] == ["default", "authorization"]
    assert services["db"]["networks"] == ["default", "authorization-db"]
    assert all(config["networks"][n]["internal"] for n in ("authorization", "authorization-db"))
    assert "iap_portal_server.main:create_authorization_app" in services["authorization"]["command"]


def test_portal_display_name_is_runtime_configuration():
    name = "Acme: Research & Apps"
    docs = render_portal('--set-string', f'portal.displayName={name}')
    deployment = one(docs, 'Deployment', 'portal')
    for container in deployment['spec']['template']['spec']['containers']:
        env = {entry['name']: entry.get('value') for entry in container['env']}
        assert env['PORTAL_DISPLAY_NAME'] == name
    default = one(render_portal(), 'Deployment', 'portal')
    assert next(e['value'] for e in default['spec']['template']['spec']['containers'][0]['env'] if e['name'] == 'PORTAL_DISPLAY_NAME') == 'iap-portal'


@pytest.mark.parametrize('okta,google', [(True, False), (False, True), (True, True), (False, False)])
def test_portal_auth_provider_selection(okta, google):
    docs = render_portal(
        '--set', f'portal.auth.okta.enabled={str(okta).lower()}',
        '--set', f'portal.auth.google.enabled={str(google).lower()}',
        '--set', 'portal.auth.okta.issuer=https://company.okta.com/oauth2/default',
        '--set', 'portal.auth.okta.clientId=okta-client',
        '--set', 'portal.auth.google.clientId=google-client',
        '--set', 'portal.auth.okta.clientSecretRef.name=okta-oauth',
        '--set', 'portal.auth.okta.clientSecretRef.key=okta-secret',
        '--set', 'portal.auth.google.clientSecretRef.name=google-oauth',
        '--set', 'portal.auth.google.clientSecretRef.key=google-secret',
    )
    for container in one(docs, 'Deployment')['spec']['template']['spec']['containers']:
        env = {item['name']: item.get('value') for item in container['env']}
        assert env['PORTAL_OKTA_ISSUER'] == ('https://company.okta.com/oauth2/default' if okta else '')
        assert env['PORTAL_OKTA_CLIENT_ID'] == ('okta-client' if okta else '')
        assert env['PORTAL_GOOGLE_CLIENT_ID'] == ('google-client' if google else '')
        entries = {item['name']: item for item in container['env']}
        for provider, enabled in [('okta', okta), ('google', google)]:
            entry = entries[f'PORTAL_{provider.upper()}_CLIENT_SECRET']
            if enabled:
                assert entry['valueFrom'] == {'secretKeyRef': {'name': f'{provider}-oauth', 'key': f'{provider}-secret'}}
                assert 'value' not in entry
            else:
                assert entry['value'] == ''
                assert 'valueFrom' not in entry


@pytest.mark.parametrize('provider', ['okta', 'google'])
def test_enabled_provider_requires_configuration(provider):
    result = subprocess.run([
        'helm', 'template', 'portal', str(PLATFORM / 'portal-chart'),
        '--set', 'image.repository=example/portal',
        '--set', f'portal.auth.{provider}.enabled=true',
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert f'is required when {provider} is enabled' in result.stderr


@pytest.mark.parametrize('provider', ['okta', 'google'])
@pytest.mark.parametrize('missing', ['name', 'key'])
def test_oauth_secret_reference_is_required(provider, missing):
    result = subprocess.run([
        'helm', 'template', 'portal', str(PLATFORM / 'portal-chart'),
        '--set', 'image.repository=example/portal',
        '--set', f'portal.auth.{provider}.enabled=true',
        '--set', 'portal.auth.okta.issuer=https://company.okta.com/oauth2/default',
        '--set', f'portal.auth.{provider}.clientId=fixture',
        '--set', f'portal.auth.{provider}.clientSecretRef.name=oauth-credentials',
        '--set', f'portal.auth.{provider}.clientSecretRef.key=client-secret',
        '--set-string', f'portal.auth.{provider}.clientSecretRef.{missing}=',
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert f'portal.auth.{provider}.clientSecretRef.{missing} is required' in result.stderr


@pytest.mark.parametrize('length,valid', [(55, True), (56, False)])
def test_app_slug_fits_prefixed_namespace(length, valid):
    result = subprocess.run([
        'helm', 'template', 'demo', str(ROOT / 'charts/iap-app'),
        '--set', 'image.repository=example/demo', '--set', 'slug=' + 'a' * length,
    ], capture_output=True, text=True)
    assert (result.returncode == 0) == valid
    if valid:
        assert len(one(_docs(result.stdout), 'Deployment')['metadata']['namespace']) == 63
