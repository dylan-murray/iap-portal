from datetime import datetime
from enum import Enum

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IdentityProvider(str, Enum):
    OIDC = "oidc"
    GOOGLE = "google"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    picture_url: Mapped[str | None] = mapped_column(String(1024))
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Disabled users cannot sign in and all of their sessions stop working.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    identities: Mapped[list["UserIdentity"]] = relationship(back_populates="user")
    group_memberships: Mapped[list["GroupMembership"]] = relationship(back_populates="user")


class UserIdentity(Base):
    """A stable OIDC identity. Looked up by (issuer, subject), never by email.

    `issuer` is NULL only for rows created before issuer tracking; such a row is
    upgraded on the next sign-in with the same provider and subject.
    """

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject", name="uq_identity_issuer_subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[IdentityProvider] = mapped_column(String(16))
    issuer: Mapped[str | None] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(255))
    raw_claims: Mapped[dict] = mapped_column(JSON, default=dict)

    user: Mapped[User] = relationship(back_populates="identities")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500))

    memberships: Mapped[list["GroupMembership"]] = relationship(back_populates="group")


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_group"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="group_memberships")
    group: Mapped[Group] = relationship(back_populates="memberships")


class App(Base):
    """A registered app. Identified by `slug`; reached at <slug>.apps.example.com."""

    __tablename__ = "apps"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(2000))
    icon_url: Mapped[str | None] = mapped_column(String(1024))
    upstream_service: Mapped[str] = mapped_column(String(512))
    upstream_port: Mapped[int] = mapped_column(default=8080)
    health_check_path: Mapped[str] = mapped_column(String(255), default="/healthz")
    is_enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owners: Mapped[list["AppOwner"]] = relationship(
        back_populates="app", cascade="all, delete-orphan"
    )
    access_grants: Mapped[list["AppAccess"]] = relationship(
        back_populates="app", cascade="all, delete-orphan"
    )


class AppOwner(Base):
    __tablename__ = "app_owners"
    __table_args__ = (UniqueConstraint("app_id", "user_id", name="uq_app_owner"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    app: Mapped[App] = relationship(back_populates="owners")


class AppAccess(Base):
    """Grant: user OR group can access an app. One principal per row."""

    __tablename__ = "app_access"

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"))
    # NULL when granted with the operator credential rather than by a user.
    granted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # NULL = permanent. A timestamp means the grant goes inert at/after it —
    # user_can_access_app filters these out soft-style, so the row stays for
    # audit/renewal but stops granting access.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    app: Mapped[App] = relationship(back_populates="access_grants")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_email: Mapped[str | None] = mapped_column(String(320))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    app_slug: Mapped[str | None] = mapped_column(String(63), index=True)
    target_email: Mapped[str | None] = mapped_column(String(320))
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AccessRequest(Base):
    """User-originated request for access to an app. Owners/admins resolve it.
    Approving creates a corresponding AppAccess row for the requester."""

    __tablename__ = "access_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("apps.id"), index=True)
    requester_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_note: Mapped[str | None] = mapped_column(String(1000))


class AuthSession(Base):
    """Server-side session. Cookies carry a random token; only its hash is stored.

    kind="portal": the portal UI session (host-only cookie on the portal host).
    kind="app": one app's session, bound to `app_slug` and to its parent portal
    session; revoking the parent revokes it.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    app_slug: Mapped[str | None] = mapped_column(String(63))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("auth_sessions.id"), index=True)
    provider: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppLoginCode(Base):
    """Single-use, short-lived code that turns a portal session into an app session."""

    __tablename__ = "app_login_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("auth_sessions.id"))
    app_slug: Mapped[str] = mapped_column(String(63))
    return_path: Mapped[str] = mapped_column(String(4096))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimitCounter(Base):
    """Fixed-window counters shared by all portal replicas through the database."""

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
