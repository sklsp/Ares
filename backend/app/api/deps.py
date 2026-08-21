"""Shared FastAPI dependencies."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db.base import get_db
from app.integrations.base import EcommerceProvider
from app.integrations.mock_provider import MockEcommerceProvider
from app.llm.base import LLMProvider
from app.llm.factory import build_llm_provider

DbSession = Annotated[Session, Depends(get_db)]


def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Enforce a shared API key when API_KEY is configured.

    Local development stays open by default; production deployments set
    API_KEY and every request must present it via the X-API-Key header.
    Comparison is constant-time to avoid timing oracles.
    """
    expected = settings.api_key
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-API-Key header is required",
        )


def get_provider(db: DbSession) -> EcommerceProvider:
    """Swap this one function to point the whole API at a real store."""
    return MockEcommerceProvider(db)


def get_llm() -> LLMProvider:
    return build_llm_provider()


Provider = Annotated[EcommerceProvider, Depends(get_provider)]
LLM = Annotated[LLMProvider, Depends(get_llm)]
