"""`iap-portal` CLI — the primary interface for app developers."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
from typing import Optional

import typer
import yaml
from rich import print
from rich.table import Table

from iap_portal.client import Portal

app = typer.Typer(help="IAP Portal platform CLI", no_args_is_help=True)


SUPPORTED_FRAMEWORKS = ("streamlit", "fastapi", "flask", "gradio")


# --------------------------------------------------------------------------- #
# init
# --------------------------------------------------------------------------- #
@app.command()
def init(
    slug: str = typer.Argument(..., help="App slug, e.g. 'annotation'"),
    framework: str = typer.Option("streamlit", "--framework", "-f"),
    directory: str = typer.Option(".", "--dir", "-d"),
    platform_repo: str = typer.Option("YOUR_ORG/iap-portal", "--platform-repo", help="GitHub owner/repo hosting IAP Portal"),
):
    """Scaffold a new app in the current (or given) directory."""
    if framework not in SUPPORTED_FRAMEWORKS:
        raise typer.BadParameter(f"framework must be one of {SUPPORTED_FRAMEWORKS}")

    target = pathlib.Path(directory).resolve()
    target.mkdir(parents=True, exist_ok=True)

    templates_dir = pathlib.Path(__file__).parent / "templates" / framework
    if not templates_dir.exists():
        raise typer.BadParameter(f"no templates for {framework} yet")

    for src in templates_dir.rglob("*"):
        if src.is_dir() or "__pycache__" in src.parts or src.suffix in {".pyc", ".pyo"}:
            continue
        rel = src.relative_to(templates_dir)
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        content = src.read_text()
        content = content.replace("{{SLUG}}", slug).replace("{{FRAMEWORK}}", framework).replace("{{PLATFORM_REPO}}", platform_repo)
        dst.write_text(content)

    print(f"[green]✓[/green] scaffolded [bold]{slug}[/bold] ({framework}) in {target}")
    print("\nNext steps:")
    print("  1. Edit app.py")
    print("  2. iap-portal dev              # native local run")
    print("  3. Configure iap-app.yaml and GitHub deployment variables/secrets (see the platform docs)")
    if platform_repo == "YOUR_ORG/iap-portal":
        print("  Set the platform repository in .github/workflows/deploy.yml before pushing.")


# --------------------------------------------------------------------------- #
# apply / list / access
# --------------------------------------------------------------------------- #
@app.command()
def apply(
    file: str = typer.Argument("iap-app.yaml"),
    url: Optional[str] = typer.Option(None, "--url", help="Portal URL override"),
):
    """Register or update an app in the portal from an iap-app.yaml."""
    path = pathlib.Path(file)
    if not path.exists():
        raise typer.BadParameter(f"{file} not found")
    spec = yaml.safe_load(path.read_text())

    portal = Portal.from_env()
    if url:
        portal.base_url = url

    registration = spec.get("registration", {}) or {}
    health = spec.get("healthCheck", {}) or {}
    result = portal.register_app(
        slug=spec["slug"],
        display_name=spec.get("displayName", spec["slug"]),
        description=spec.get("description"),
        icon_url=registration.get("iconUrl"),
        upstream_service=spec.get("upstream_service")
        or f"{spec['slug']}.iap-app-{spec['slug']}.svc.cluster.local",
        upstream_port=spec.get("port", 8080),
        health_check_path=health.get("path", "/healthz"),
        owners=registration.get("owners", []),
    )
    print(f"[green]✓[/green] {result['slug']} registered (id={result['id']})")


@app.command("list")
def list_apps():
    """List apps registered in the portal."""
    portal = Portal.from_env()
    apps_ = portal.list_apps()
    table = Table("slug", "display name", "enabled", "owners")
    for a in apps_:
        table.add_row(
            a["slug"],
            a["display_name"],
            "✓" if a["is_enabled"] else "✗",
            ", ".join(a.get("owners", [])),
        )
    print(table)


access = typer.Typer(help="Manage app access grants")
app.add_typer(access, name="access")


@access.command("grant")
def access_grant(
    email_or_group: str = typer.Argument(..., help="user@example.com or group:engineering"),
    app_slug: str = typer.Option(..., "--app"),
):
    portal = Portal.from_env()
    if email_or_group.startswith("group:"):
        result = portal.grant_access(app_slug, group=email_or_group.removeprefix("group:"))
    else:
        result = portal.grant_access(app_slug, email=email_or_group)
    print(f"[green]✓[/green] granted {email_or_group} → {app_slug} (id={result['id']})")


users = typer.Typer(help="Operator user controls (requires the operator token)")
app.add_typer(users, name="users")

USER_ACTIONS = ("revoke-sessions", "disable", "enable", "unlink-identities")


@users.command("set")
def users_set(
    email: str = typer.Argument(..., help="user@example.com"),
    action: str = typer.Argument(..., help="revoke-sessions | disable | enable | unlink-identities"),
):
    """Sign a user out everywhere, disable/enable them, or unlink their identities."""
    if action not in USER_ACTIONS:
        raise typer.BadParameter(f"action must be one of {USER_ACTIONS}")
    result = Portal.from_env().user_action(email, action)
    state = "disabled" if result["disabled"] else "active"
    print(f"[green]✓[/green] {result['email']}: {action} ({state}, identities={result['identities']})")


# --------------------------------------------------------------------------- #
# dev  — the everyday local loop
# --------------------------------------------------------------------------- #
@app.command()
def dev(
    port: int = typer.Option(8080, "--port", "-p"),
    as_email: str = typer.Option("you@example.com", "--as", help="Email for fake dev user"),
    groups: str = typer.Option("engineering", "--groups", "-g", help="Comma-separated groups"),
    name: str = typer.Option("You", "--name"),
    docker: bool = typer.Option(False, "--docker", help="Build and run the app's Dockerfile"),
    full: bool = typer.Option(
        False, "--full", help="docker-compose: Envoy + portal + mock IdP + your app"
    ),
):
    """Run your app locally.

    Default is native (no Docker): runs streamlit/uvicorn/gunicorn/python based on
    iap-app.yaml, injects a fake signed-in user, hot-reloads, <1s startup.

    Use --docker to build and run the app's Dockerfile (pre-deploy sanity check).
    Use --full to boot the whole auth stack via docker-compose (rare; only when
    debugging ext_authz / JWT / headers).
    """
    spec = _load_spec_or_die()
    slug = spec["slug"]

    if full:
        _run_full(slug, port)
        return
    if docker:
        _run_docker(slug, port, as_email, name, groups)
        return

    _run_native(spec, port, as_email, name, groups)


def _load_spec_or_die() -> dict:
    here = pathlib.Path.cwd()
    yml = here / "iap-app.yaml"
    if not yml.exists():
        print("[red]✗[/red] iap-app.yaml not found in current directory.")
        print("    Run this from your app's repo, or `iap-portal init <slug>` first.")
        raise typer.Exit(1)
    try:
        spec = yaml.safe_load(yml.read_text()) or {}
    except Exception as e:
        print(f"[red]✗[/red] iap-app.yaml is invalid: {e}")
        raise typer.Exit(1)
    if "framework" not in spec or "slug" not in spec:
        print("[red]✗[/red] iap-app.yaml must declare 'slug' and 'framework'")
        raise typer.Exit(1)
    return spec


def _dev_env(port: int, email: str, name: str, groups: str, slug: str) -> dict:
    env = os.environ.copy()
    env.update({
        "PORT": str(port),
        "IAP_PORTAL_DEV": "1",
        "IAP_PORTAL_DEV_EMAIL": email,
        "IAP_PORTAL_DEV_NAME": name,
        "IAP_PORTAL_DEV_GROUPS": groups,
        "IAP_PORTAL_APP_SLUG": slug,
    })
    return env


def _run_native(spec: dict, port: int, email: str, name: str, groups: str) -> None:
    framework = spec["framework"]
    entry = spec.get("entrypoint", "app.py")
    slug = spec["slug"]
    env = _dev_env(port, email, name, groups, slug)

    cmd: list[str]
    if framework == "streamlit":
        if shutil.which("streamlit") is None:
            _die("streamlit not installed. `pip install streamlit` first.")
        cmd = [
            "streamlit", "run", entry,
            "--server.address=0.0.0.0", f"--server.port={port}",
            "--server.headless=true", "--browser.gatherUsageStats=false",
        ]
    elif framework == "fastapi":
        if shutil.which("uvicorn") is None:
            _die("uvicorn not installed. `pip install 'uvicorn[standard]'` first.")
        cmd = ["uvicorn", entry, "--host=0.0.0.0", f"--port={port}", "--reload"]
    elif framework == "flask":
        env["FLASK_APP"] = entry
        env["FLASK_DEBUG"] = "1"
        cmd = [sys.executable, "-m", "flask", "run", "--host=0.0.0.0", f"--port={port}"]
    elif framework == "gradio":
        env["GRADIO_SERVER_NAME"] = "0.0.0.0"
        env["GRADIO_SERVER_PORT"] = str(port)
        cmd = [sys.executable, entry]
    else:
        _die(f"unknown framework '{framework}'")

    _print_banner(slug, port, email, groups, mode="native")
    try:
        subprocess.run(cmd, env=env, check=False)
    except KeyboardInterrupt:
        pass


def _run_docker(slug: str, port: int, email: str, name: str, groups: str) -> None:
    if shutil.which("docker") is None:
        _die("docker is required for `--docker` mode.")
    image = f"iap-portal-dev-{slug}:local"
    print(f"[cyan]→[/cyan] building {image}")
    subprocess.run(["docker", "build", "-t", image, "."], check=True)

    _print_banner(slug, port, email, groups, mode="docker")
    subprocess.run(
        [
            "docker", "run", "--rm", "-it",
            "-p", f"{port}:{port}",
            "-e", "IAP_PORTAL_DEV=1",
            "-e", f"IAP_PORTAL_DEV_EMAIL={email}",
            "-e", f"IAP_PORTAL_DEV_NAME={name}",
            "-e", f"IAP_PORTAL_DEV_GROUPS={groups}",
            "-e", f"IAP_PORTAL_APP_SLUG={slug}",
            "-e", f"PORT={port}",
            image,
        ],
        check=False,
    )


def _run_full(slug: str, port: int) -> None:
    if shutil.which("docker") is None:
        _die("docker is required for `--full` mode.")
    compose = pathlib.Path(__file__).parent / "dev" / "docker-compose.yml"
    if not compose.exists():
        _die(f"dev/docker-compose.yml missing in SDK install at {compose}")

    source = os.environ.get("IAP_PORTAL_SOURCE")
    if not source or not (pathlib.Path(source) / "backend/Dockerfile").is_file():
        _die("Set IAP_PORTAL_SOURCE to an IAP Portal checkout for --full mode.")
    source_path = pathlib.Path(source).resolve()
    if not all((source_path / "backend/.dev-keys" / name).is_file() for name in ("private.pem", "public.pem")):
        _die("Run `task dev:keys` in IAP_PORTAL_SOURCE before --full mode.")
    env = os.environ.copy()
    env.update({
        "IAP_PORTAL_SOURCE": str(source_path),
        "IAP_PORTAL_DEV_APP_SLUG": slug,
        "IAP_PORTAL_DEV_APP_PATH": str(pathlib.Path.cwd()),
    })
    _print_banner(slug, 8090, "(real login via mock IdP)", "", mode="full")
    subprocess.run(
        ["docker", "compose", "-f", str(compose), "up", "--build"],
        env=env,
        check=False,
    )


def _print_banner(slug: str, port: int, email: str, groups: str, mode: str) -> None:
    url_host = f"{slug}.iapportal.test" if mode == "full" else "localhost"
    print(f"\n[bold cyan]iap-portal dev[/bold cyan]  ({mode} mode)")
    print(f"  app      : [bold]{slug}[/bold]")
    print(f"  url      : http://{url_host}:{port}")
    print(f"  user     : {email}" + (f"  groups={groups}" if groups else ""))
    print("  verify   : IAP_PORTAL_DEV=1 (fake user, no JWT check)")
    print()


def _die(msg: str) -> None:
    print(f"[red]✗[/red] {msg}")
    raise typer.Exit(1)


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
@app.command()
def doctor():
    """Pre-flight check that everything is in shape before pushing."""
    problems: list[str] = []
    warnings: list[str] = []
    here = pathlib.Path.cwd()

    if not (here / "Dockerfile").exists():
        problems.append("Dockerfile missing")
    if not (here / "iap-app.yaml").exists():
        problems.append("iap-app.yaml missing")
    else:
        try:
            spec = yaml.safe_load((here / "iap-app.yaml").read_text()) or {}
            for required in ("slug", "framework"):
                if required not in spec:
                    problems.append(f"iap-app.yaml missing '{required}'")
            framework = spec.get("framework")
            if framework and framework not in SUPPORTED_FRAMEWORKS:
                warnings.append(
                    f"framework '{framework}' is not a first-class template — "
                    f"you'll need a custom Dockerfile"
                )
            if framework in SUPPORTED_FRAMEWORKS and shutil.which(framework) is None:
                if framework == "fastapi" and shutil.which("uvicorn") is None:
                    warnings.append("uvicorn not on PATH — `pip install 'uvicorn[standard]'`")
                elif framework == "flask":
                    pass
                else:
                    warnings.append(f"{framework} not on PATH — `iap-portal dev` won't work natively")
        except Exception as e:
            problems.append(f"iap-app.yaml invalid: {e}")

    if not (here / ".github" / "workflows" / "deploy.yml").exists():
        warnings.append(".github/workflows/deploy.yml missing — deploy won't run on push")

    for p in problems:
        print(f"[red]✗[/red] {p}")
    for w in warnings:
        print(f"[yellow]![/yellow] {w}")

    if problems:
        raise typer.Exit(1)
    if not warnings:
        print("[green]✓[/green] everything looks good")


if __name__ == "__main__":
    app()
