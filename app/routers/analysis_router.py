from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.services import analysis_service as svc

router = APIRouter(prefix="/analyze", tags=["analysis"])


class NLQueryRequest(BaseModel):
    question: str


class RiskRequest(BaseModel):
    asset_id: str


class EnrichRequest(BaseModel):
    asset_id: str


class ReportRequest(BaseModel):
    type: str | None = None
    status: str | None = None
    tag_contains: str | None = None
    value_contains: str | None = None


@router.post("/query")
def query(req: NLQueryRequest, db: Session = Depends(get_db)):
    """Feature 1: natural-language asset query."""
    return svc.nl_query(db, req.question)


@router.post("/risk")
def risk(req: RiskRequest, db: Session = Depends(get_db)):
    """Feature 2: risk scoring & summarization for one asset."""
    return svc.risk_summary(db, req.asset_id)


@router.post("/enrich")
def enrich(req: EnrichRequest, db: Session = Depends(get_db)):
    """Feature 3: automated enrichment & categorization."""
    return svc.enrich_asset(db, req.asset_id)


@router.post("/report")
def report(req: ReportRequest, db: Session = Depends(get_db)):
    """Feature 4: natural-language report generation over dataset or a filtered subset."""
    from app.schemas import AssetFilter
    f = AssetFilter(**req.model_dump()) if any(req.model_dump().values()) else None
    return svc.generate_report(db, f)
