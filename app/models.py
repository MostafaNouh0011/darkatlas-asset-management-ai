"""
Domain model: Asset + Relationship.

Design decisions (document these in your README too):
- id is the natural id from the source data (string), not an autoincrement PK.
  The task spec calls for a "stable identifier" and the sample dataset already
  supplies one ("a1", "a2"...) — re-using it is what makes idempotent import
  possible: on re-import we look up by this id, not by guessing equality on
  other fields.
- tags stored as JSON array, metadata as JSON blob. Postgres JSONB would be
  the production choice (indexable, queryable) — using JSON here for
  simplicity within the time box; note this as a stated assumption in README.
- Relationship is a separate table, not foreign keys directly on Asset,
  because asset-to-asset links are many-to-many and the relationship *type*
  (resolves_to, covers, runs_on, etc.) matters as data, not just a link.
"""
from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base


class Asset(Base):
    __tablename__ = "assets"

    id = Column(String, primary_key=True)
    type = Column(String, nullable=False, index=True)       # domain/subdomain/ip_address/service/certificate/technology
    value = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="active", index=True)  # active/stale/archived
    first_seen = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    source = Column(String, nullable=False)                 # import/scan/manual
    tags = Column(JSON, nullable=False, default=list)
    asset_metadata = Column(JSON, nullable=False, default=dict)  # named asset_metadata: "metadata" is reserved on Base


class AssetRelationship(Base):
    __tablename__ = "asset_relationships"
    __table_args__ = (
        UniqueConstraint("from_asset_id", "to_asset_id", "relationship_type", name="uq_relationship"),
    )

    id = Column(String, primary_key=True)  # generated, e.g. f"{from_id}:{to_id}:{type}"
    from_asset_id = Column(String, ForeignKey("assets.id"), nullable=False, index=True)
    to_asset_id = Column(String, ForeignKey("assets.id"), nullable=False, index=True)
    relationship_type = Column(String, nullable=False)  # e.g. "parent_domain", "covers", "resolves_to", "runs_on"
