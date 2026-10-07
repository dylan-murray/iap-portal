"""Explicitly enabled local UI feedback, never mounted in production."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field

from iap_portal_server.auth.principals import Principal, get_principal, require_admin
from iap_portal_server.config import get_settings
from iap_portal_server.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/dev/annotations", tags=["dev annotations"])


def login_token() -> str:
    return URLSafeTimedSerializer(get_settings().session_secret, salt="dev-login-annotations").dumps("/login")


def login_markup() -> str:
    settings = get_settings()
    if settings.env != "dev" or not settings.enable_dev_annotations:
        return ""
    manifest_path = Path(os.environ.get("PORTAL_STATIC_DIR", "/app/static")) / ".vite/manifest.json"
    if not manifest_path.exists():
        return ""  # No compiled frontend in native backend-only development.
    manifest = json.loads(manifest_path.read_text())
    entry = manifest.get("src/dev-annotator.ts")
    if not entry:
        return ""
    css = set()
    seen = set()

    def collect(item):
        css.update(item.get("css", []))
        for key in item.get("imports", []):
            if key not in seen:
                seen.add(key)
                collect(manifest[key])

    collect(entry)
    links = "".join(f'<link rel="stylesheet" href="/{escape(path)}">' for path in sorted(css))
    return (links + f'<div id="dev-annotation-root" data-token="{escape(login_token())}"></div>'
            + f'<script type="module" src="/{escape(entry["file"])}"></script>')


async def annotation_principal(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    token = request.headers.get("x-dev-annotation-token")
    if token:
        settings = get_settings()
        if settings.env != "dev" or not settings.enable_dev_annotations:
            raise HTTPException(404)
        try:
            scope = URLSafeTimedSerializer(settings.session_secret, salt="dev-login-annotations").loads(token, max_age=43200)
        except BadSignature:
            raise HTTPException(403, "Reload the login page to annotate")
        if scope != "/login":
            raise HTTPException(403)
        if request.method != "GET" and request.headers.get("origin") != settings.portal_origin:
            raise HTTPException(403, "cross-origin request rejected")
        return Principal(kind="dev-annotation")
    return await get_principal(request, db)


class Bounds(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float
    y: float
    width: float = Field(ge=0, le=100000)
    height: float = Field(ge=0, le=100000)


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["element", "area"]
    note: str = Field(min_length=1, max_length=10000)
    path: str = Field(max_length=2048, pattern=r"^/")
    selector: str = Field(min_length=1, max_length=2048)
    text: str = Field(max_length=2000)
    anchor: Bounds
    bounds: Bounds
    viewport: Bounds
    scroll_x: float
    scroll_y: float


def storage(principal: Principal):
    settings = get_settings()
    if settings.env != "dev" or not settings.enable_dev_annotations:
        raise HTTPException(404)
    if principal.kind != "dev-annotation":
        require_admin(principal)
    directory = settings.dev_annotations_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@router.get("")
def list_notes(response: Response, principal: Principal = Depends(annotation_principal)):
    directory = storage(principal)
    response.headers["Cache-Control"] = "no-store"
    notes = [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]
    return [n for n in notes if principal.kind != "dev-annotation" or n["path"] == "/login"]


@router.post("", status_code=201)
def save_note(payload: Annotation, principal: Principal = Depends(annotation_principal)):
    directory = storage(principal)
    if principal.kind == "dev-annotation" and payload.path != "/login":
        raise HTTPException(403, "login-page feedback only")
    if not payload.note.strip():
        raise HTTPException(422, "Write a note before saving")
    record = {
        **payload.model_dump(),
        "id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "author": "local-login-reviewer" if principal.kind == "dev-annotation" else principal.email,
    }
    # Publish complete records atomically, including across multiple workers.
    target = directory / f"{record['id']}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False))
    temporary.replace(target)
    return record


@router.delete("/{note_id}", status_code=204)
def delete_note(note_id: UUID, principal: Principal = Depends(annotation_principal)):
    directory = storage(principal)
    target = directory / f"{note_id}.json"
    if target.exists():
        if principal.kind == "dev-annotation" and json.loads(target.read_text())["path"] != "/login":
            raise HTTPException(403, "login-page feedback only")
        # Retain original evidence privately without returning deleted pins.
        target.replace(target.with_suffix(".deleted"))
