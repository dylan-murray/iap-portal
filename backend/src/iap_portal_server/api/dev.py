"""Dev-only session bypass: arbitrary identity, no IdP.

Registered only when PORTAL_ENV=dev AND PORTAL_ENABLE_DEV_LOGIN=true; startup
refuses the flag in any other environment, and dev mode itself is limited to
localhost/*.test portal URLs.

    curl -X POST http://localhost:8088/dev/login \
         -H 'Content-Type: application/json' \
         -d '{"email":"alice@example.com","groups":["engineering","admins"]}' \
         -c cookies.txt

Creates a server-side portal session so you can iterate on the UI without the
OIDC roundtrip. In prod this module is not imported.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.auth.session import create_portal_session, set_portal_cookie
from iap_portal_server.config import get_settings
from iap_portal_server.db.models import Group, GroupMembership, User
from iap_portal_server.db.session import get_db

router = APIRouter(prefix="/dev", tags=["dev"])


class DevLoginIn(BaseModel):
    email: EmailStr
    name: str | None = None
    groups: list[str] = []
    is_admin: bool = False


@router.post("/login")
async def dev_login(payload: DevLoginIn, db: AsyncSession = Depends(get_db)):
    if not get_settings().dev_login_enabled:
        raise HTTPException(404)

    email = payload.email.lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(email=email, name=payload.name, is_admin=payload.is_admin)
        db.add(user)
        await db.flush()
    else:
        if payload.name:
            user.name = payload.name
        if payload.is_admin:
            user.is_admin = True

    # Reconcile group memberships to the requested set.
    for group_name in payload.groups:
        group = (
            await db.execute(select(Group).where(Group.name == group_name))
        ).scalar_one_or_none()
        if group is None:
            group = Group(name=group_name)
            db.add(group)
            await db.flush()
        exists = (
            await db.execute(
                select(GroupMembership.id).where(
                    GroupMembership.user_id == user.id,
                    GroupMembership.group_id == group.id,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            db.add(GroupMembership(user_id=user.id, group_id=group.id))

    token, session = await create_portal_session(db, user, "dev")
    await db.commit()

    resp = JSONResponse({"email": email, "user_id": user.id, "groups": payload.groups})
    set_portal_cookie(resp, token, session.expires_at)
    return resp
