"""Release artifacts use locked dependencies."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_portal_image_requirements_match_uv_lock():
    exported = subprocess.check_output(
        ["uv", "export", "--frozen", "--package", "iap-portal-server", "--no-dev",
         "--no-emit-workspace", "--no-emit-project", "-q"],
        cwd=ROOT, text=True,
    )
    committed = (ROOT / "backend/requirements.lock").read_text()
    assert exported.splitlines()[2:] == committed.splitlines()[2:], "run `task deps:export`"


def test_portal_image_installs_hash_locked_dependencies():
    dockerfile = (ROOT / "backend/Dockerfile").read_text()
    assert "--require-hashes -r requirements.lock" in dockerfile
    assert "pip install --no-cache-dir --no-deps ." in dockerfile


def test_distributions_include_apache_license():
    license_text = (ROOT / 'LICENSE').read_text()
    assert 'Apache License' in license_text and 'Version 2.0' in license_text
    for relative in ('sdk/LICENSE', 'backend/LICENSE', 'charts/iap-app/LICENSE',
                     'deploy/platform/portal-chart/LICENSE'):
        assert (ROOT / relative).read_text() == license_text


def test_publishing_is_manual_and_public_only():
    workflow = (ROOT / '.github/workflows/publish-release.yml').read_text()
    assert 'workflow_dispatch:' in workflow
    assert 'if: ${{ !github.event.repository.private }}' in workflow
    assert '\n  push:' not in workflow
    assert 'platforms: linux/amd64,linux/arm64' in workflow
