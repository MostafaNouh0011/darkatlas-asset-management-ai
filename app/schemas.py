"""
Pydantic schemas.

These serve two jobs:
1. Validate API input/output (the normal FastAPI use case).
2. Later, in the LangChain layer (Day 2+), structures like AssetFilter below
   become the "structured output" target you ask the LLM to fill in. The LLM
   never queries the DB directly — it only ever fills in a schema like
   AssetFilter, and your own code applies that filter to real rows. That
   indirection is your main anti-hallucination guardrail.
"""
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, Literal
from datetime import datetime

AssetType = Literal["domain", "subdomain", "ip_address", "service", "certificate", "technology"]
AssetStatus = Literal["active", "stale", "archived"]


class AssetIn(BaseModel):
    """Shape of one record in the bulk import payload.

    Deliberately permissive on optional fields — Section 7 of the task asks us
    to handle malformed/partial records gracefully rather than reject the
    whole batch, so only the fields we truly cannot proceed without
    (id, type, value) are required.
    """
    id: str
    type: AssetType
    value: str
    status: AssetStatus = "active"
    source: str = "import"
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    parent: Optional[str] = None   # subdomain -> domain
    covers: Optional[str] = None   # certificate -> domain/subdomain

    @field_validator("id", "value")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v


class AssetOut(BaseModel):
    """
    Note: `metadata` here is aliased to the ORM attribute `asset_metadata`.
    SQLAlchemy's declarative Base reserves `.metadata` as the class-level
    MetaData object, so the model can't have a column attribute literally
    named `metadata` — it's stored as `asset_metadata` (see app/models.py)
    and re-exposed as `metadata` here for a clean API contract.
    """
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    type: str
    value: str
    status: str
    first_seen: datetime
    last_seen: datetime
    source: str
    tags: list[str]
    metadata: dict = Field(validation_alias="asset_metadata")


class ImportResult(BaseModel):
    created: int
    updated: int
    skipped: list[dict]  # [{"record": {...}, "reason": "..."}]


class AssetFilter(BaseModel):
    """Structured target for the NL-query LangChain chain (built Day 2).

    All fields optional/None-able: the LLM fills in only what the question
    implies. Your own filter logic (not the LLM) then applies this against
    real DB rows.
    """
    type: Optional[AssetType] = None
    status: Optional[AssetStatus] = None
    tag_contains: Optional[str] = None
    value_contains: Optional[str] = None
    expiry_before: Optional[str] = None   # ISO date string, for certificate metadata.expires
    out_of_scope: bool = False            # LLM sets this true if the question isn't about asset data at all
