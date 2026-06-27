from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_api_key
from app.schemas import ImportResult
from app.services.import_service import import_assets

router = APIRouter(prefix="/import", tags=["import"])


@router.post("", response_model=ImportResult, dependencies=[Depends(require_api_key)])
def bulk_import(records: list[dict], db: Session = Depends(get_db)):
    """
    Bulk import endpoint. Accepts the sample dataset (a JSON list of asset
    records) directly — see Appendix A of the task doc for the shape.

    Idempotent: re-posting the same list will not create duplicates; it
    updates last_seen and merges tags/metadata on existing assets instead.
    Malformed individual records are skipped (and reported) rather than
    failing the whole batch.
    """
    return import_assets(db, records)
