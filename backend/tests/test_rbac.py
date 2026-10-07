"""Controls #1 / dashboard — RBAC across admin, owner, direct grant, group grant, and denial."""

from __future__ import annotations

import pytest

from iap_portal_server.db.models import (
    App,
    AppAccess,
    AppOwner,
    Group,
    GroupMembership,
    User,
)
from iap_portal_server.rbac.checks import user_can_access_app


async def _mk_user(db, email: str, is_admin: bool = False) -> User:
    u = User(email=email, is_admin=is_admin)
    db.add(u)
    await db.flush()
    return u


async def _mk_app(db, slug: str) -> App:
    a = App(
        slug=slug,
        display_name=slug,
        upstream_service=f"{slug}.svc",
        upstream_port=8080,
    )
    db.add(a)
    await db.flush()
    return a


async def _mk_group(db, name: str) -> Group:
    g = Group(name=name)
    db.add(g)
    await db.flush()
    return g


@pytest.mark.asyncio
async def test_admin_can_access_any_app(db):
    user = await _mk_user(db, "admin@example.com", is_admin=True)
    app = await _mk_app(db, "annotation")
    assert await user_can_access_app(db, user, app) is True


@pytest.mark.asyncio
async def test_owner_can_access_own_app(db):
    user = await _mk_user(db, "owner@example.com")
    app = await _mk_app(db, "annotation")
    db.add(AppOwner(app_id=app.id, user_id=user.id))
    await db.flush()
    assert await user_can_access_app(db, user, app) is True


@pytest.mark.asyncio
async def test_direct_user_grant(db):
    user = await _mk_user(db, "alice@example.com")
    granter = await _mk_user(db, "owner@example.com", is_admin=True)
    app = await _mk_app(db, "annotation")
    db.add(
        AppAccess(
            app_id=app.id, user_id=user.id, granted_by_user_id=granter.id
        )
    )
    await db.flush()
    assert await user_can_access_app(db, user, app) is True


@pytest.mark.asyncio
async def test_group_grant(db):
    user = await _mk_user(db, "alice@example.com")
    granter = await _mk_user(db, "owner@example.com", is_admin=True)
    group = await _mk_group(db, "engineering")
    db.add(GroupMembership(user_id=user.id, group_id=group.id))
    app = await _mk_app(db, "annotation")
    db.add(
        AppAccess(
            app_id=app.id, group_id=group.id, granted_by_user_id=granter.id
        )
    )
    await db.flush()
    assert await user_can_access_app(db, user, app) is True


@pytest.mark.asyncio
async def test_user_without_any_grant_is_denied(db):
    user = await _mk_user(db, "bystander@example.com")
    app = await _mk_app(db, "annotation")
    assert await user_can_access_app(db, user, app) is False


@pytest.mark.asyncio
async def test_group_grant_does_not_leak_to_non_members(db):
    member = await _mk_user(db, "member@example.com")
    non_member = await _mk_user(db, "outsider@example.com")
    granter = await _mk_user(db, "owner@example.com", is_admin=True)
    group = await _mk_group(db, "engineering")
    db.add(GroupMembership(user_id=member.id, group_id=group.id))
    app = await _mk_app(db, "annotation")
    db.add(
        AppAccess(
            app_id=app.id, group_id=group.id, granted_by_user_id=granter.id
        )
    )
    await db.flush()
    assert await user_can_access_app(db, member, app) is True
    assert await user_can_access_app(db, non_member, app) is False
