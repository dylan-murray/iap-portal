"""Map verified OIDC claims to a portal user.

Rules, in order:
1. An identity with the same (issuer, subject) signs in as its user. Legacy
   identities recorded before issuer tracking match on (provider, subject).
2. Otherwise, a user row with this email and *no* identities is claimed. Such rows
   are placeholders created by owner registration or grants ("invite by email").
3. Otherwise, an email that belongs to a user with an identity is only linked when
   PORTAL_LINK_VERIFIED_EMAIL_ACROSS_ISSUERS=true and all of that user's
   identities come from other issuers. A different subject from the same issuer
   (a recycled or reassigned account) is never linked automatically.
4. Otherwise a new user is created.
Admission (email domain, Google hosted domain) is checked before any of this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iap_portal_server.config import Settings
from iap_portal_server.db.models import IdentityProvider, User, UserIdentity


class LoginRejected(Exception):
    def __init__(self, status: int, message: str, reason: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.reason = reason


@dataclass(frozen=True)
class VerifiedClaims:
    provider: str
    issuer: str
    subject: str
    email: str
    name: str | None
    picture: str | None
    raw: dict[str, Any]


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def verified_claims(
    provider: str, issuer: str, claims: dict[str, Any], settings: Settings
) -> VerifiedClaims:
    """Enforce required claims and sign-in admission policy."""
    subject = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    if not isinstance(subject, str) or not subject or not issuer:
        raise LoginRejected(400, "The identity provider returned no stable subject.", "missing_sub")
    if not email or "@" not in email:
        raise LoginRejected(400, "The identity provider returned no email address.", "missing_email")
    if not _is_true(claims.get("email_verified")):
        raise LoginRejected(403, "Your email address is not verified by the identity provider.", "email_unverified")
    domain = email.rsplit("@", 1)[1]
    if settings.allowed_email_domains and domain not in settings.allowed_email_domains:
        raise LoginRejected(403, "This email domain is not allowed to sign in.", "domain_not_allowed")
    if provider == "google" and settings.google_hosted_domains:
        if str(claims.get("hd") or "").lower() not in settings.google_hosted_domains:
            raise LoginRejected(403, "This Google account is not in an allowed Workspace domain.", "hd_not_allowed")
    return VerifiedClaims(
        provider=provider,
        issuer=issuer,
        subject=subject,
        email=email,
        name=claims.get("name"),
        picture=claims.get("picture"),
        raw=dict(claims),
    )


async def _find_identity(db: AsyncSession, c: VerifiedClaims) -> UserIdentity | None:
    identity = (
        await db.execute(
            select(UserIdentity).where(
                UserIdentity.issuer == c.issuer, UserIdentity.subject == c.subject
            )
        )
    ).scalar_one_or_none()
    if identity is not None:
        return identity
    legacy = (
        await db.execute(
            select(UserIdentity).where(
                UserIdentity.issuer.is_(None),
                UserIdentity.provider == c.provider,
                UserIdentity.subject == c.subject,
            )
        )
    ).scalar_one_or_none()
    if legacy is not None:
        legacy.issuer = c.issuer
    return legacy


def _new_identity(user: User, c: VerifiedClaims) -> UserIdentity:
    return UserIdentity(
        user_id=user.id,
        provider=IdentityProvider(c.provider),
        issuer=c.issuer,
        subject=c.subject,
        raw_claims=c.raw,
    )


async def resolve_user(
    db: AsyncSession, c: VerifiedClaims, settings: Settings
) -> tuple[User, str]:
    """Return (user, how) where how is existing|claimed|linked|created."""
    identity = await _find_identity(db, c)
    if identity is not None:
        user = (await db.execute(select(User).where(User.id == identity.user_id))).scalar_one()
        if user.email != c.email:
            taken = (
                await db.execute(select(User.id).where(User.email == c.email, User.id != user.id))
            ).scalar_one_or_none()
            if taken is not None:
                raise LoginRejected(
                    409,
                    "Your identity provider now reports an email that belongs to another "
                    "portal account. Ask an administrator to resolve it.",
                    "email_conflict",
                )
            user.email = c.email
        identity.raw_claims = c.raw
        _update_profile(user, c)
        return user, "existing"

    user = (await db.execute(select(User).where(User.email == c.email))).scalar_one_or_none()
    if user is None:
        user = User(email=c.email, is_admin=False)
        db.add(user)
        await db.flush()
        db.add(_new_identity(user, c))
        _update_profile(user, c)
        return user, "created"

    existing = (
        await db.execute(select(UserIdentity).where(UserIdentity.user_id == user.id))
    ).scalars().all()
    if not existing:
        how = "claimed"
    elif settings.link_verified_email_across_issuers and all(
        i.issuer not in (None, c.issuer) for i in existing
    ):
        how = "linked"
    else:
        raise LoginRejected(
            409,
            "This email already belongs to a portal account with a different sign-in "
            "identity. Ask an administrator to link or reset it.",
            "identity_mismatch",
        )
    db.add(_new_identity(user, c))
    _update_profile(user, c)
    return user, how


def _update_profile(user: User, c: VerifiedClaims) -> None:
    if c.name:
        user.name = c.name
    if c.picture:
        user.picture_url = c.picture
