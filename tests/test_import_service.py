"""
Tests for import/dedup/lifecycle logic.

These use an in-memory SQLite DB instead of Postgres, swapping out
app.database's engine — fast, no Docker required to run these. Note in the
README that integration tests against real Postgres would be the next step
for a production system; this is a deliberate scope choice for the project.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.import_service import import_assets


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


SAMPLE = [
    {"id": "a1", "type": "domain", "value": "example.com",
     "status": "active", "source": "scan", "tags": ["root"], "metadata": {}},
    {"id": "a2", "type": "subdomain", "value": "api.example.com",
     "status": "active", "source": "scan", "tags": ["prod"], "metadata": {}, "parent": "a1"},
]


def test_import_creates_new_assets(db_session):
    result = import_assets(db_session, SAMPLE)
    assert result.created == 2
    assert result.updated == 0
    assert result.skipped == []


def test_reimport_is_idempotent(db_session):
    import_assets(db_session, SAMPLE)
    result = import_assets(db_session, SAMPLE)
    assert result.created == 0
    assert result.updated == 2, "re-importing identical data should update, not duplicate"

    from app.models import Asset
    count = db_session.query(Asset).count()
    assert count == 2, "no duplicate rows should exist after re-import"


def test_stale_asset_returns_to_active_on_resighting(db_session):
    stale_record = {"id": "a3", "type": "subdomain", "value": "old.example.com",
                     "status": "stale", "source": "scan", "tags": [], "metadata": {}}
    import_assets(db_session, [stale_record])

    from app.models import Asset
    asset = db_session.get(Asset, "a3")
    assert asset.status == "stale"

    resighting = {**stale_record, "status": "active"}  # re-seen, source reports active
    import_assets(db_session, [resighting])
    db_session.refresh(asset)
    assert asset.status == "active"


def test_tags_and_metadata_merge_on_conflict(db_session):
    first = {"id": "a4", "type": "service", "value": "443/tcp", "status": "active",
              "source": "scan", "tags": ["scan-tag"], "metadata": {"banner": "nginx"}}
    second = {"id": "a4", "type": "service", "value": "443/tcp", "status": "active",
               "source": "manual", "tags": ["manual-tag"], "metadata": {"banner": "nginx/1.25", "note": "verified"}}
    import_assets(db_session, [first])
    import_assets(db_session, [second])

    from app.models import Asset
    asset = db_session.get(Asset, "a4")
    assert set(asset.tags) == {"scan-tag", "manual-tag"}, "tags should union, not overwrite"
    assert asset.asset_metadata["banner"] == "nginx/1.25", "most recent source wins on key conflict"
    assert asset.asset_metadata["note"] == "verified"


def test_malformed_record_is_skipped_not_fatal(db_session):
    malformed = {"type": "domain", "value": "no-id-here.com", "status": "active",
                 "source": "scan", "tags": [], "metadata": {}}  # missing required "id"
    valid = SAMPLE[0]
    result = import_assets(db_session, [malformed, valid])

    assert result.created == 1, "the one valid record should still be imported"
    assert len(result.skipped) == 1, "the malformed record should be reported, not crash the batch"


def test_invalid_asset_type_is_skipped(db_session):
    bad_type = {"id": "bad1", "type": "not_a_real_type", "value": "x.com",
                "status": "active", "source": "scan", "tags": [], "metadata": {}}
    result = import_assets(db_session, [bad_type])
    assert result.created == 0
    assert len(result.skipped) == 1


def test_relationship_extracted_from_parent_field(db_session):
    import_assets(db_session, SAMPLE)  # a2 has parent=a1
    from app.models import AssetRelationship
    rels = db_session.query(AssetRelationship).all()
    assert any(r.from_asset_id == "a2" and r.to_asset_id == "a1"
               and r.relationship_type == "parent_domain" for r in rels)
