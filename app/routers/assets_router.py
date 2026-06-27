from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db
from app.models import Asset
from app.schemas import AssetOut

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("", response_model=list[AssetOut])
def list_assets(
    type: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    value_contains: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """
    List endpoint with filtering + pagination. Sane default limit (50, capped
    at 200) so a large inventory doesn't return everything at once.

    Note: tag filtering happens in Python because `tags` is a JSON column,
    not natively indexable for "contains" queries the way a normalized
    tags table would be. Fine at this dataset's scale; called out in the
    README as a known limitation/assumption.
    """
    stmt = select(Asset)
    if type:
        stmt = stmt.where(Asset.type == type)
    if status:
        stmt = stmt.where(Asset.status == status)
    if value_contains:
        stmt = stmt.where(Asset.value.contains(value_contains))

    if tag:
        rows = db.execute(stmt).scalars().all()
        rows = [r for r in rows if tag in r.tags]
        return rows[offset: offset + limit]

    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().all()
    return rows


@router.get("/{asset_id}", response_model=AssetOut)
def get_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset
