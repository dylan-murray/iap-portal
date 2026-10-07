from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.db.models import (
    App,
    AppAccess,
    AppOwner,
    Group,
    GroupMembership,
    User,
)

# Grant is live iff expires_at is unset OR still in the future.
_live = or_(AppAccess.expires_at.is_(None), AppAccess.expires_at > func.now())


async def user_can_access_app(db: AsyncSession, user: User, app: App) -> bool:
    if user.is_admin:
        return True

    owner_exists = (
        await db.execute(
            select(AppOwner.id).where(AppOwner.app_id == app.id, AppOwner.user_id == user.id)
        )
    ).scalar_one_or_none()
    if owner_exists is not None:
        return True

    direct_grant = (
        await db.execute(
            select(AppAccess.id).where(
                AppAccess.app_id == app.id,
                AppAccess.user_id == user.id,
                _live,
            )
        )
    ).scalar_one_or_none()
    if direct_grant is not None:
        return True

    group_grant = (
        await db.execute(
            select(AppAccess.id)
            .join(Group, Group.id == AppAccess.group_id)
            .join(GroupMembership, GroupMembership.group_id == Group.id)
            .where(
                AppAccess.app_id == app.id,
                GroupMembership.user_id == user.id,
                _live,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return group_grant is not None


async def user_groups(db: AsyncSession, user: User) -> list[str]:
    rows = (
        await db.execute(
            select(Group.name)
            .join(GroupMembership, GroupMembership.group_id == Group.id)
            .where(GroupMembership.user_id == user.id)
        )
    ).all()
    return [r[0] for r in rows]


async def user_is_app_owner(db: AsyncSession, user: User, app: App) -> bool:
    if user.is_admin:
        return True
    owner_exists = (
        await db.execute(
            select(AppOwner.id).where(AppOwner.app_id == app.id, AppOwner.user_id == user.id)
        )
    ).scalar_one_or_none()
    return owner_exists is not None
