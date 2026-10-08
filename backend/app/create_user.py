"""Create a user from the command line, for example the first administrator.

    ARES_USER_PASSWORD=... python -m app.create_user admin@example.com --role admin

The public sign-up only creates viewers, so this is how the first admin comes to
exist. The password comes from ARES_USER_PASSWORD or a prompt, never from the
command line, so it stays out of shell history. Running it again for an email
that already exists changes nothing. Run `python -m app.seed` first on a new
database.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.identity import User
from app.services import auth as auth_service
from app.services.auth import Role


def create_user(db: Session, email: str, password: str, role: str) -> tuple[User, bool]:
    """Return (user, created). An existing email is returned untouched."""
    existing = db.execute(select(User).where(User.email == email)).scalars().first()
    if existing is not None:
        return existing, False
    org = auth_service.ensure_default_organization(db)
    user = User(
        organization_id=org.id,
        email=email,
        password_hash=auth_service.hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    auth_service.audit(db, action="user.created_cli", actor_user_id=user.id,
                       organization_id=org.id, resource=f"user:{user.id}",
                       detail={"role": role})
    return user, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a user, for example the first admin")
    parser.add_argument("email")
    parser.add_argument("--role", default=Role.VIEWER.value, choices=[r.value for r in Role])
    args = parser.parse_args(argv)
    if "@" not in args.email:
        print(f"not an email address: {args.email}", file=sys.stderr)
        return 2
    password = os.environ.get("ARES_USER_PASSWORD") or getpass.getpass("Password: ")
    if len(password) < 10:
        print("the password needs at least 10 characters", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        user, created = create_user(db, args.email, password, args.role)
    finally:
        db.close()
    print(f"{'created' if created else 'already exists'}: {user.email} ({user.role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
