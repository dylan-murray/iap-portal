"""User lifecycle for admins and the operator token.

- revoke-sessions: sign the user out of the portal and every app now.
- disable / enable: a disabled user cannot sign in and all sessions stop working.
- identities (DELETE): unlink sign-in identities so the next verified sign-in with
  this email claims the account. Use it to deliberately re-link an account after
  an identity provider change; it also revokes sessions.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.audit import log_event
from iap_portal_server.auth.principals import Principal, get_principal, require_admin
from iap_portal_server.auth.session import revoke_user_sessions, utcnow
from iap_portal_server.db.models import User, UserIdentity
from iap_portal_server.db.session import get_db

router = APIRouter(prefix="/api/users", tags=["users"])


class UserStatus(BaseModel):
    email: str
    disabled: bool
    identities: int


async def _target(db: AsyncSession, email: str) -> User:
    user = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, f"no user with email {email}")
    return user


async def _status(db: AsyncSession, user: User) -> UserStatus:
    count = len(
        (await db.execute(select(UserIdentity.id).where(UserIdentity.user_id == user.id))).all()
    )
    return UserStatus(email=user.email, disabled=user.disabled_at is not None, identities=count)


@router.get("/{email}", response_model=UserStatus)
async def get_user(
    email: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    return await _status(db, await _target(db, email))


@router.post("/{email}/revoke-sessions", response_model=UserStatus)
async def revoke_sessions(
    email: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    user = await _target(db, email)
    await revoke_user_sessions(db, user.id)
    await log_event(db, event_type="sessions_revoked", actor_email=principal.email, target_email=user.email)
    return await _status(db, user)


@router.post("/{email}/disable", response_model=UserStatus)
async def disable_user(
    email: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    user = await _target(db, email)
    if user.disabled_at is None:
        user.disabled_at = utcnow()
    await revoke_user_sessions(db, user.id)
    await log_event(db, event_type="user_disabled", actor_email=principal.email, target_email=user.email)
    return await _status(db, user)


@router.post("/{email}/enable", response_model=UserStatus)
async def enable_user(
    email: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    user = await _target(db, email)
    user.disabled_at = None
    await log_event(db, event_type="user_enabled", actor_email=principal.email, target_email=user.email)
    return await _status(db, user)


@router.delete("/{email}/identities", response_model=UserStatus)
async def unlink_identities(
    email: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    user = await _target(db, email)
    await db.execute(delete(UserIdentity).where(UserIdentity.user_id == user.id))
    await revoke_user_sessions(db, user.id)
    await log_event(
        db, event_type="identities_unlinked", actor_email=principal.email, target_email=user.email
    )
    return await _status(db, user)
