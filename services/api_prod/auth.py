from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .security import create_access_token, decode_access_token, hash_token, reveal_secret, verify_hub_signature, verify_password, verify_totp


bearer = HTTPBearer(auto_error=False)


class Principal:
    def __init__(self, user: models.User, memberships: list[models.Membership]):
        self.user = user
        self.memberships = memberships
        self.roles = {membership.role for membership in memberships}
        self.organization_ids = {membership.organization_id for membership in memberships}
        self.site_ids = {membership.site_id for membership in memberships if membership.site_id}

    @property
    def primary_org_id(self) -> str:
        return next(iter(self.organization_ids))


def issue_user_token(db: Session, email: str, password: str, mfa_code: str = "") -> str:
    user = db.scalar(select(models.User).where(models.User.email == email.lower().strip(), models.User.status == "active"))
    if not user or not verify_password(password, user.password_hash):
        audit_auth(db, user, "auth.login_failed", "Invalid credentials.")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials.")
    if user.mfa_enabled and not verify_totp(reveal_secret(user.mfa_secret_hash), mfa_code or ""):
        audit_auth(db, user, "auth.login_failed", "Invalid MFA code.")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA verification required.")
    memberships = list(db.scalars(select(models.Membership).where(models.Membership.user_id == user.id)))
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User has no tenant access.")
    org_id = memberships[0].organization_id
    site_ids = sorted({membership.site_id for membership in memberships if membership.site_id})
    roles = sorted({membership.role for membership in memberships})
    token = create_access_token(user.id, org_id, site_ids, roles)
    audit_auth(db, user, "auth.login", "User signed in.")
    return token


def audit_auth(db: Session, user: models.User | None, action: str, detail: str) -> None:
    db.add(
        models.AuditEvent(
            id=f"aud_{__import__('secrets').token_hex(8)}",
            organization_id=user.memberships[0].organization_id if user and user.memberships else None,
            site_id=user.memberships[0].site_id if user and user.memberships else None,
            actor_user_id=user.id if user else None,
            action=action,
            subject_type="user",
            subject_id=user.id if user else "unknown",
            detail=detail,
        )
    )
    db.commit()


def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> Principal:
    if not credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    try:
        claims = decode_access_token(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token.") from exc
    user = db.get(models.User, claims.get("sub"))
    if not user or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is not active.")
    memberships = list(db.scalars(select(models.Membership).where(models.Membership.user_id == user.id)))
    return Principal(user, memberships)


def require_role(*roles: str):
    def dependency(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if not principal.roles.intersection(roles):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role.")
        return principal

    return dependency


def require_site_access(db: Session, principal: Principal, site_id: str) -> models.Site:
    site = db.get(models.Site, site_id)
    if not site or site.organization_id not in principal.organization_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")
    if "org_admin" not in principal.roles and "system_admin" not in principal.roles and principal.site_ids and site_id not in principal.site_ids:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Site access denied.")
    return site


async def current_hub(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    x_hub_id: Annotated[str, Header(alias="X-Hub-Id")],
    x_hub_signature: Annotated[str, Header(alias="X-Hub-Signature")],
) -> models.HubDevice:
    body = await request.body()
    hub = db.get(models.HubDevice, x_hub_id)
    if not hub or hub.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown hub.")
    # The plain secret is only returned at bootstrap. Operators can rotate it by replacing the stored hash.
    if not verify_hub_signature(hub.secret_hash, body, x_hub_signature):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid hub signature.")
    return hub
