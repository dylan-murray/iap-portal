"""Apply reviewed repository settings. Never changes repository visibility.

Dry-run by default. Public-only controls require --public-features and an already
public repository. Requires gh authentication with repository administration rights.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def api(path, method='GET', payload=None):
    command = ['gh', 'api', path, '--method', method]
    if payload is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def plan(repo, public=False):
    base = f'repos/{repo}'
    steps = [
        (base, 'PATCH', {'delete_branch_on_merge': True, 'allow_auto_merge': False,
                        'allow_merge_commit': True, 'allow_squash_merge': True,
                        'allow_rebase_merge': False, 'has_wiki': False, 'has_projects': False}),
        (base + '/actions/permissions', 'PUT', {'enabled': True, 'allowed_actions': 'selected',
                                              'sha_pinning_required': True}),
        (base + '/actions/permissions/selected-actions', 'PUT', {
            'github_owned_allowed': True, 'verified_allowed': False,
            'patterns_allowed': ['astral-sh/setup-uv@*', 'azure/setup-helm@*',
                                 'azure/setup-kubectl@*', 'docker/setup-qemu-action@*',
                                 'docker/setup-buildx-action@*', 'docker/login-action@*',
                                 'docker/build-push-action@*']}),
        (base + '/actions/permissions/workflow', 'PUT', {
            'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False}),
        (base + '/vulnerability-alerts', 'PUT', None),
        (base + '/automated-security-fixes', 'PUT', None),
    ]
    if public:
        steps += [
            (base + '/actions/permissions/fork-pr-contributor-approval', 'PUT', {
                'approval_policy': 'all_external_contributors'}),
            (base, 'PATCH', {'security_and_analysis': {
                'secret_scanning': {'status': 'enabled'},
                'secret_scanning_push_protection': {'status': 'enabled'}}}),
            (base + '/private-vulnerability-reporting', 'PUT', None),
        ]
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, help='OWNER/REPO')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--public-features', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo):
        parser.error('--repo must be OWNER/REPO')
    steps = plan(args.repo, args.public_features)
    rules = [json.loads(p.read_text()) for p in sorted((ROOT / '.github/repository-rules').glob('*.json'))]
    if not args.apply:
        print(json.dumps({'settings': steps, 'public_rules': rules,
                          'release_environment': 'Owner review, protected branches only',
                          'changes_visibility': False}, indent=2))
        return
    base = f'repos/{args.repo}'
    metadata = api(base)
    if args.public_features and metadata['private']:
        parser.error('Public controls require an already-public repository. Visibility was not changed.')
    for path, method, payload in steps:
        api(path, method, payload)
        print(f'Applied {method} {path}')
    if args.public_features:
        existing = {rule['name']: rule['id'] for rule in api(base + '/rulesets')}
        for rule in rules:
            rule_id = existing.get(rule['name'])
            path = base + '/rulesets' + (f'/{rule_id}' if rule_id else '')
            api(path, 'PUT' if rule_id else 'POST', rule)
            print('Applied ruleset: ' + rule['name'])
        api(base + '/environments/release', 'PUT', {
            'prevent_self_review': False,
            'reviewers': [{'type': 'User', 'id': metadata['owner']['id']}],
            'deployment_branch_policy': {'protected_branches': True, 'custom_branch_policies': False}})
        print('Applied release environment: owner approval and protected branches only')
    else:
        print('Public rulesets, fork approval, secret scanning and release protection remain pending.')


if __name__ == '__main__':
    main()
