"""Dashboard endpoint — returns the apps the current user can see."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.auth.principals import Principal, get_user_principal
from iap_portal_server.auth.urls import app_origin
from iap_portal_server.db.models import App
from iap_portal_server.db.session import get_db
from iap_portal_server.rbac.checks import user_can_access_app, user_is_app_owner

router = APIRouter(prefix="/api", tags=["dashboard"])


class Tile(BaseModel):
    slug: str
    display_name: str
    description: str | None
    icon_url: str | None
    url: str  # public URL the tile should link to
    can_manage: bool  # True if the user is an owner (or admin) and can reach the manage page


class Me(BaseModel):
    email: str
    name: str | None
    picture_url: str | None
    is_admin: bool


@router.get("/me", response_model=Me)
async def me(principal: Principal = Depends(get_user_principal)) -> Me:
    user = principal.user
    return Me(
        email=user.email,
        name=user.name,
        picture_url=user.picture_url,
        is_admin=user.is_admin,
    )


@router.get("/me/apps", response_model=list[Tile])
async def my_apps(
    principal: Principal = Depends(get_user_principal), db: AsyncSession = Depends(get_db)
) -> list[Tile]:
    user = principal.user

    apps = (
        await db.execute(select(App).where(App.is_enabled.is_(True)).order_by(App.display_name))
    ).scalars().all()

    visible: list[Tile] = []
    for app in apps:
        if await user_can_access_app(db, user, app):
            visible.append(
                Tile(
                    slug=app.slug,
                    display_name=app.display_name,
                    description=app.description,
                    icon_url=app.icon_url,
                    url=app_origin(app.slug),
                    can_manage=await user_is_app_owner(db, user, app),
                )
            )
    return visible
