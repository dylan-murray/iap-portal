"""Group CRUD + membership management. Admin-only.

Memberships are managed through the portal. Provider group claims are not
automatically synchronized."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.audit import log_event
from iap_portal_server.auth.principals import Principal, get_principal, require_admin
from iap_portal_server.db.models import Group, GroupMembership, User
from iap_portal_server.db.session import get_db

router = APIRouter(prefix="/api/groups", tags=["groups"])


class GroupIn(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
    description: str | None = None


class GroupOut(BaseModel):
    id: int
    name: str
    description: str | None
    member_count: int


class GroupDetail(BaseModel):
    id: int
    name: str
    description: str | None
    members: list[str]  # emails


class MemberIn(BaseModel):
    email: str


@router.get("/names", response_model=list[str])
async def list_group_names(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    """Lightweight list any signed-in user can read — used by the access-grant form
    on the app-manage page so owners (not just admins) can pick a group."""
    if principal.kind == "registration":
        raise HTTPException(403)
    rows = (
        await db.execute(select(Group.name).order_by(Group.name))
    ).all()
    return [r[0] for r in rows]


@router.get("", response_model=list[GroupOut])
async def list_groups(
    principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    rows = (
        await db.execute(
            select(
                Group.id,
                Group.name,
                Group.description,
                func.count(GroupMembership.id),
            )
            .outerjoin(GroupMembership, GroupMembership.group_id == Group.id)
            .group_by(Group.id)
            .order_by(Group.name)
        )
    ).all()
    return [
        GroupOut(id=r[0], name=r[1], description=r[2], member_count=r[3]) for r in rows
    ]


@router.post("", response_model=GroupOut, status_code=201)
async def create_group(
    payload: GroupIn,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    actor = require_admin(principal)
    existing = (
        await db.execute(select(Group).where(Group.name == payload.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"group {payload.name} already exists")

    group = Group(name=payload.name, description=payload.description)
    db.add(group)
    await db.flush()
    await log_event(
        db,
        event_type="group_created",
        actor_email=actor.email,
        detail={"name": payload.name},
    )
    await db.commit()
    return GroupOut(
        id=group.id, name=group.name, description=group.description, member_count=0
    )


@router.get("/{name}", response_model=GroupDetail)
async def get_group(
    name: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    require_admin(principal)
    group = (
        await db.execute(select(Group).where(Group.name == name))
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(404)
    member_rows = (
        await db.execute(
            select(User.email)
            .join(GroupMembership, GroupMembership.user_id == User.id)
            .where(GroupMembership.group_id == group.id)
            .order_by(User.email)
        )
    ).all()
    return GroupDetail(
        id=group.id,
        name=group.name,
        description=group.description,
        members=[r[0] for r in member_rows],
    )


@router.delete("/{name}", status_code=204)
async def delete_group(
    name: str, principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)
):
    actor = require_admin(principal)
    group = (
        await db.execute(select(Group).where(Group.name == name))
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(404)
    # Cascade: drop memberships first (no DB-level cascade configured).
    for m in (
        await db.execute(
            select(GroupMembership).where(GroupMembership.group_id == group.id)
        )
    ).scalars():
        await db.delete(m)
    await db.delete(group)
    await log_event(
        db, event_type="group_deleted", actor_email=actor.email, detail={"name": name}
    )
    await db.commit()


@router.post("/{name}/members", response_model=GroupDetail, status_code=201)
async def add_member(
    name: str,
    payload: MemberIn,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    actor = require_admin(principal)
    group = (
        await db.execute(select(Group).where(Group.name == name))
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(404)
    target = (
        await db.execute(select(User).where(User.email == payload.email.lower()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, f"no user with email {payload.email}")

    existing = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(GroupMembership(user_id=target.id, group_id=group.id))
        await log_event(
            db,
            event_type="group_member_added",
            actor_email=actor.email,
            target_email=target.email,
            detail={"group": name},
        )
    await db.commit()
    return await get_group(name, principal, db)


@router.delete("/{name}/members/{email}", status_code=204)
async def remove_member(
    name: str,
    email: str,
    principal: Principal = Depends(get_principal),
    db: AsyncSession = Depends(get_db),
):
    actor = require_admin(principal)
    group = (
        await db.execute(select(Group).where(Group.name == name))
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(404)
    target = (
        await db.execute(select(User).where(User.email == email.lower()))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, f"no user with email {email}")
    membership = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(404, "user is not a member of this group")
    await db.delete(membership)
    await log_event(
        db,
        event_type="group_member_removed",
        actor_email=actor.email,
        target_email=target.email,
        detail={"group": name},
    )
    await db.commit()
