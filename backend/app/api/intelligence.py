"""Market intelligence jobs and evidence-backed opportunity endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.auth import current_user
from app.api.deps import DbSession
from app.api.tenancy import TenantContext, get_tenant, scoped_or_404
from app.config import settings
from app.db.models import ExternalProduct, ExternalStore, Opportunity, OpportunityEvidence, ResearchJob
from app.schemas.intelligence import OpportunityOut, ResearchJobOut, ResearchJobRequest
from app.services import intelligence as intelligence_service
from app.services.intelligence_jobs import cancel_job, create_job
from app.services.rate_limit import rate_limit

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.post("/jobs", response_model=ResearchJobOut, status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(rate_limit(
                 limit=settings.rate_limit_research_per_minute))])
def start_job(payload: ResearchJobRequest, db: DbSession,
              tenant: TenantContext = Depends(get_tenant)) -> ResearchJob:
    job, _created = create_job(db, payload.objective, payload.query, payload.start_urls,
                               organization_id=tenant.organization_id)
    return job


@router.get("/jobs", response_model=list[ResearchJobOut])
def list_jobs(db: DbSession, limit: int = Query(default=20, ge=1, le=100),
              tenant: TenantContext = Depends(get_tenant)) -> list[ResearchJob]:
    query = tenant.scoped(select(ResearchJob).order_by(ResearchJob.id.desc()).limit(limit))
    return list(db.execute(query).scalars().all())


@router.get("/jobs/{job_id}", response_model=ResearchJobOut)
def get_job(job_id: int, db: DbSession,
            tenant: TenantContext = Depends(get_tenant)) -> ResearchJob:
    return scoped_or_404(tenant, db, ResearchJob, job_id)


@router.post("/jobs/{job_id}/cancel", response_model=ResearchJobOut)
def stop_job(job_id: int, db: DbSession,
             tenant: TenantContext = Depends(get_tenant)) -> ResearchJob:
    job = scoped_or_404(tenant, db, ResearchJob, job_id)
    return cancel_job(db, job)


@router.get("/stores")
def list_stores(db: DbSession, limit: int = Query(default=50, ge=1, le=200),
                tenant: TenantContext = Depends(get_tenant)) -> list[dict]:
    query = tenant.scoped(
        select(ExternalStore).order_by(ExternalStore.last_crawled_at.desc()).limit(limit)
    )
    stores = db.execute(query).scalars().all()
    # One grouped query instead of one COUNT per store (avoids N+1).
    counts = dict(
        db.execute(
            select(ExternalProduct.store_id, func.count(ExternalProduct.id)).group_by(
                ExternalProduct.store_id
            )
        ).all()
    )
    return [
        {
            "id": store.id,
            "domain": store.domain,
            "name": store.name,
            "niche": store.niche,
            "platform": store.platform,
            "country": store.country,
            "crawl_status": store.crawl_status,
            "product_count": counts.get(store.id, 0),
            "last_crawled_at": store.last_crawled_at,
        }
        for store in stores
    ]


@router.get("/products")
def list_external_products(
    db: DbSession,
    search: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    stmt = select(ExternalProduct).order_by(ExternalProduct.last_seen_at.desc()).limit(limit)
    if search:
        stmt = stmt.where(ExternalProduct.normalized_name.contains(search.lower()))
    products = db.execute(stmt).scalars().all()
    return [
        {
            "id": product.id,
            "store_id": product.store_id,
            "source_url": product.source_url,
            "name": product.name,
            "brand": product.brand,
            "category": product.category,
            "price": product.price,
            "currency": product.currency,
            "availability": product.availability,
            "confidence": product.confidence,
            "first_seen_at": product.first_seen_at,
            "last_seen_at": product.last_seen_at,
        }
        for product in products
    ]


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(db: DbSession, limit: int = Query(default=50, ge=1, le=200), kind: str | None = None,
                       tenant: TenantContext = Depends(get_tenant)) -> list[Opportunity]:
    query = tenant.scoped(select(Opportunity).order_by(Opportunity.score.desc()).limit(limit))
    if kind:
        query = query.where(Opportunity.type == kind)
    return list(db.execute(query).scalars().all())


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityOut)
def get_opportunity(opportunity_id: int, db: DbSession,
                    tenant: TenantContext = Depends(get_tenant)) -> Opportunity:
    return scoped_or_404(tenant, db, Opportunity, opportunity_id)


@router.get("/opportunities/{opportunity_id}/evidence")
def evidence(opportunity_id: int, db: DbSession,
             tenant: TenantContext = Depends(get_tenant)) -> list[dict]:
    scoped_or_404(tenant, db, Opportunity, opportunity_id)
    rows = db.execute(
        select(OpportunityEvidence).where(
            OpportunityEvidence.opportunity_id == opportunity_id
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "source_url": row.source_url,
            "source_domain": row.source_domain,
            "claim": row.claim,
            "extraction_method": row.extraction_method,
            "observed_value": row.observed_value,
            "confidence": row.confidence,
            "captured_at": row.captured_at,
        }
        for row in rows
    ]
