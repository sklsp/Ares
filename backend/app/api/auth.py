"""Identity endpoints: register, login, logout, me, audit trail."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession
from app.config import settings
from app.db.identity import AuditLog, User
from app.observability.metrics import inc
from app.services import auth as auth_service
from app.services.auth import Role
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10, max_length=200)
    role: str = Field(default="viewer", pattern="^(admin|manager|analyst|viewer)$")


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_hours: int


class MeResponse(BaseModel):
    id: int
    email: str
    role: str
    organization_id: int

    model_config = {"from_attributes": True}


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def current_user(request: Request, db: DbSession) -> User:
    """Resolve the caller: session token first, then the shared API key.

    Raises 401 when neither credential is valid or when neither is configured.
    """
    token = _bearer_token(request)
    if token:
        user = auth_service.resolve_session(db, token)
        if user is not None:
            request.state.user = user
            return user

    api_key = request.headers.get("X-API-Key")
    if settings.api_key and api_key and hmac_compare(api_key, settings.api_key):
        return MachineUser()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: provide a Bearer session token"
        + (" or X-API-Key" if settings.api_key else " (no API_KEY configured)"),
    )


def hmac_compare(candidate: str, expected: str) -> bool:
    import hmac as hmac_module

    return hmac_module.compare_digest(candidate, expected)


class MachineUser:
    """Synthetic principal for trusted machine clients using the shared key."""

    id = None
    email = "machine@internal"
    role = Role.ADMIN.value
    organization_id = None
    is_active = True


def require_role(required: str):
    """Dependency factory enforcing a minimum role server-side."""

    def dependency(user: Annotated[User, Depends(current_user)]) -> User:
        if not auth_service.role_at_least(user.role, required):
            inc("authz_denied_total", required_role=required)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {required} role",
            )
        return user

    return dependency


# --- endpoints ----------------------------------------------------------
@router.post("/register", response_model=MeResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: DbSession) -> User:
    existing = db.execute(select(User).where(User.email == payload.email)).scalars().first()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    org = auth_service.ensure_default_organization(db)
    user = User(
        organization_id=org.id,
        email=payload.email,
        password_hash=auth_service.hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    auth_service.audit(db, action="user.registered", actor_user_id=user.id,
                       organization_id=org.id, resource=f"user:{user.id}")
    return user


@router.post("/login", response_model=TokenResponse,
             dependencies=[Depends(rate_limit(
                 limit=settings.rate_limit_login_per_minute))])
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    user = db.execute(select(User).where(User.email == payload.email)).scalars().first()
    correlation = getattr(request.state, "correlation_id", None)
    if user is None or not user.is_active or not auth_service.verify_password(
        payload.password, user.password_hash
    ):
        inc("login_total", outcome="failed")
        auth_service.audit(db, action="login.failed", resource=f"email:{payload.email}",
                           outcome="denied", correlation_id=correlation)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    token, session_row = auth_service.create_session(db, user)
    inc("login_total", outcome="success")
    auth_service.audit(db, action="login.success", actor_user_id=user.id,
                       organization_id=user.organization_id,
                       resource=f"session:{session_row.id}", correlation_id=correlation)
    return TokenResponse(access_token=token,
                         expires_in_hours=int(auth_service.SESSION_TTL.total_seconds() // 3600))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, db: DbSession) -> None:
    token = _bearer_token(request)
    if token:
        auth_service.revoke_session(db, token)
        user = getattr(request.state, "user", None)
        auth_service.audit(db, action="logout", actor_user_id=getattr(user, "id", None))


@router.get("/me", response_model=MeResponse)
def me(user: Annotated[User, Depends(current_user)]) -> dict:
    return {
        "id": user.id or 0,
        "email": user.email,
        "role": user.role,
        "organization_id": user.organization_id or 0,
    }


@router.get("/audit")
def list_audit(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    user: Annotated[User, Depends(require_role(Role.MANAGER.value))] = None,
) -> list[dict]:
    rows = db.execute(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    ).scalars().all()
    return [
        {
            "id": row.id,
            "actor_user_id": row.actor_user_id,
            "organization_id": row.organization_id,
            "action": row.action,
            "resource": row.resource,
            "outcome": row.outcome,
            "correlation_id": row.correlation_id,
            "created_at": row.created_at,
        }
        for row in rows
    ]
