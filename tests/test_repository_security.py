"""Prevent accidental privilege expansion in contributor-triggered workflows."""

import importlib.util
from pathlib import Path
import sys

import pytest
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_workflows_pin_actions_and_do_not_persist_checkout_credentials():
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        workflow = yaml.safe_load(path.read_text())
        for job in workflow['jobs'].values():
            for step in job.get('steps', []):
                action = step.get('uses', '')
                if action and not action.startswith('./'):
                    assert re.fullmatch(r'[^@]+@[a-f0-9]{40}', action), (path, action)
                if action.startswith('actions/checkout@'):
                    assert step['with']['persist-credentials'] is False, path


def test_contributor_workflows_cannot_publish_or_use_privileged_events():
    for name in ('portal-ci.yml', 'codeql.yml', 'security.yml'):
        workflow = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
        # PyYAML's YAML 1.1 parser reads "on" as True.
        events = workflow.get('on', workflow.get(True))
        assert 'pull_request_target' not in events and 'workflow_run' not in events
        assert workflow['permissions'] == {'contents': 'read'}
        for job in workflow['jobs'].values():
            assert job['runs-on'] == 'ubuntu-latest'
            assert 'environment' not in job
            assert job.get('permissions', {}).get('contents', 'read') == 'read'
            assert job.get('permissions', {}).get('packages', 'read') != 'write'


def load_settings_script():
    spec = importlib.util.spec_from_file_location('repository_settings', ROOT / 'scripts/configure-repository.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_controls_refuse_private_repository_before_mutating(monkeypatch):
    module = load_settings_script()
    calls = []

    def fake_api(path, method='GET', payload=None):
        calls.append((path, method, payload))
        return {'private': True}

    monkeypatch.setattr(module, 'api', fake_api)
    monkeypatch.setattr(sys, 'argv', ['configure-repository', '--repo', 'owner/repo',
                                    '--public-features', '--apply'])
    with pytest.raises(SystemExit) as error:
        module.main()
    assert error.value.code == 2
    assert calls == [('repos/owner/repo', 'GET', None)]


def test_settings_preview_never_calls_github(monkeypatch, capsys):
    module = load_settings_script()

    def unexpected_api(*args, **kwargs):
        pytest.fail('Dry-run must not call GitHub')

    monkeypatch.setattr(module, 'api', unexpected_api)
    monkeypatch.setattr(sys, 'argv', ['configure-repository', '--repo', 'owner/repo'])
    module.main()
    assert '"changes_visibility": false' in capsys.readouterr().out
    for public in (False, True):
        for _, _, payload in module.plan('owner/repo', public):
            assert not payload or not {'private', 'visibility'} & payload.keys()
