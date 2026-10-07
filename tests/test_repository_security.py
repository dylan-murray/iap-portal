"""Prevent accidental privilege expansion in contributor-triggered workflows."""

from pathlib import Path
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
