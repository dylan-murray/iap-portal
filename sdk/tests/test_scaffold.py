from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from iap_portal.cli import app
from iap_portal.auth.core import user_from_request


@pytest.mark.parametrize("framework", ["streamlit", "fastapi", "flask", "gradio"])
def test_scaffold_has_portable_workflow_and_local_identity(tmp_path, framework, monkeypatch):
    result = CliRunner().invoke(app, [
        "init", "demo-app", "--framework", framework, "--dir", str(tmp_path),
        "--platform-repo", "example/iap-portal",
    ])
    assert result.exit_code == 0, result.output
    spec = yaml.safe_load((tmp_path / "iap-app.yaml").read_text())
    assert spec["slug"] == "demo-app"
    assert spec["secrets"] == {"existingSecret": ""}
    workflow = (tmp_path / ".github/workflows/deploy.yml").read_text()
    assert "example/iap-portal/.github/workflows/deploy-iap-app.yml@main" in workflow
    assert "${{ vars.IAP_BASE_IMAGE }}" in workflow
    assert "{{SLUG}}" not in workflow
    assert "FROM ${BASE_IMAGE}" in (tmp_path / "Dockerfile").read_text()
    assert "from iap_portal" in (tmp_path / "app.py").read_text()
    if framework in {"fastapi", "flask"}:
        assert spec["entrypoint"] == "app:app"
    monkeypatch.setenv("IAP_PORTAL_DEV", "1")
    monkeypatch.setenv("IAP_PORTAL_DEV_EMAIL", "demo@example.com")
    assert user_from_request({}).email == "demo@example.com"


def test_full_dev_requires_source_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("IAP_PORTAL_SOURCE", raising=False)
    monkeypatch.setattr("iap_portal.cli.shutil.which", lambda _: "/usr/bin/docker")
    Path("iap-app.yaml").write_text("slug: demo-app\nframework: fastapi\n")
    result = CliRunner().invoke(app, ["dev", "--full"])
    assert result.exit_code == 1
    assert "IAP_PORTAL_SOURCE" in result.output
