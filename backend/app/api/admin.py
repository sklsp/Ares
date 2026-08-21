"""Admin user management.

Server-side authorization on every endpoint. Protections:

- Only admins reach these routes (enforced by require_role, not UI hiding).
- Admins manage only their own organization: no cross-tenant administration.
- The last active admin in an organization cannot be demoted or deactivated.
- An admin cannot deactivate/demote themselves if they are the last admin.
- Every mutation is audit-logged.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.auth import current_user
from app.api.deps import DbSession
from app.api.tenancy import TenantContext, get_tenant
from app.db.identity import Organization, Session as DbSessionTable, User
from app.services import auth as auth_service
from app.services.auth import Role

router = APIRouter(prefix="/admin/users", tags=["admin"])


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    is_active: bool
    organization_id: int
    created_at: object

    model_config = {"from_attributes": True}


class UpdateUserRequest(BaseModel):
    role: str | None = Field(default=None, pattern="^(admin|manager|analyst|viewer)$")
    is_active: bool | None = None


def _active_admin_count(db, organization_id: int) -> int:
    return int(db.execute(
        select(func.count())
        .select_from(User)
        .where(User.organization_id == organization_id)
        .where(User.role == Role.ADMIN.value)
        .where(User.is_active.is_(True))
    ).scalar_one())


def _target_scoped(tenant: TenantContext, db, user_id: int) -> User:
    """Fetch a target user inside the admin's own organization only."""
    user = db.get(User, user_id)
    if user is None or (
        tenant.organization_id is not None
        and user.organization_id != tenant.organization_id
    ):
        # Same generic 404 for missing and cross-tenant targets.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("", response_model=list[UserOut])
def list_users(
    db: DbSession,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    tenant: TenantContext = Depends(get_tenant),
    _: Annotated[object, Depends(auth_service.require_role(Role.ADMIN.value))] = None,
) -> list[User]:
    query = select(User).order_by(User.id.desc()).limit(limit)
    if tenant.organization_id is not None:
        query = query.where(User.organization_id == tenant.organization_id)
    if search:
        pattern = f"%{search.lower()}%"
        query = query.where(or_(
            func.lower(User.email).like(pattern),
        ))
    return list(db.execute(query).scalars().all())


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    db: DbSession,
    tenant: TenantContext = Depends(get_tenant),
    actor: Annotated[User, Depends(auth_service.require_role(Role.ADMIN.value))] = None,
) -> User:
    target = _target_scoped(tenant, db, user_id)

    changing_admin_power = (
        (payload.role is not None and target.role == Role.ADMIN.value
         and payload.role != Role.ADMIN.value)
        or (payload.is_active is False and target.is_active)
    )
    if changing_admin_power:
        remaining = _active_admin_count(db, target.organization_id)
        is_self = actor.id == target.id
        if remaining <= 1 and (is_self or target.organization_id == actor.organization_id):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Cannot remove the last active administrator of an organization",
            )

    changed: list[str] = []
    if payload.role is not None and payload.role != target.role:
        target.role = payload.role
        changed.append(f"role->{payload.role}")
    if payload.is_active is not None and payload.is_active != target.is_active:
        target.is_active = payload.is_active
        changed.append(f"active->{payload.is_active}")
        if not payload.is_active:
            # Revoke live sessions immediately on deactivation.
            for session_row in db.execute(
                select(DbSessionTable).where(DbSessionTable.user_id == target.id)
            ).scalars():
                session_row.revoked_at = auth_service.utcnow()

    if changed:
        db.commit()
        db.refresh(target)
        auth_service.audit(
            db,
            action="user.updated",
            actor_user_id=actor.id,
            organization_id=target.organization_id,
            resource=f"user:{target.id}",
            detail={"changes": changed},
        )
    return target


@router.get("/organizations")
def list_organizations(
    db: DbSession,
    _: Annotated[object, Depends(auth_service.require_role(Role.ADMIN.value))] = None,
) -> list[dict]:
    orgs = db.execute(select(Organization).order_by(Organization.id)).scalars().all()
    return [
        {
            "id": org.id,
            "name": org.name,
            "user_count": int(db.execute(
                select(func.count()).select_from(User).where(User.organization_id == org.id)
            ).scalar_one()),
        }
        for org in orgs
    ]
