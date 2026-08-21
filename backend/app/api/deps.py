"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.base import get_db
from app.integrations.base import EcommerceProvider
from app.integrations.mock_provider import MockEcommerceProvider
from app.llm.base import LLMProvider
from app.llm.factory import build_llm_provider

DbSession = Annotated[Session, Depends(get_db)]


def get_provider(db: DbSession) -> EcommerceProvider:
    """Swap this one function to point the whole API at a real store."""
    return MockEcommerceProvider(db)


def get_llm() -> LLMProvider:
    return build_llm_provider()


Provider = Annotated[EcommerceProvider, Depends(get_provider)]
LLM = Annotated[LLMProvider, Depends(get_llm)]
