"""Integration tests against the running databases (scripts\\db.ps1 start, data seeded).
They write a few uniquely-identified claims and remove them again."""
from datetime import datetime, timezone

import pytest
from pymongo.errors import WriteError

from ringlens import cdc, intake
from ringlens.datagen.ids import ulid
from ringlens.db import cypher, mongo_db
from ringlens.internals import SHOWCASE, explain_query

pytestmark = pytest.mark.integration


def _ready() -> bool:
    try:
        return mongo_db().claims.estimated_document_count() > 0 and bool(cypher("RETURN 1 AS ok"))
    except Exception:
        return False


if not _ready():
    pytest.skip("databases not running or not seeded", allow_module_level=True)


def _two_providers():
    a, b = list(mongo_db().providers.find({}, {"specialty": 1}).limit(2))
    return a, b


def _claim(rendering, referring=None):
    now = datetime.now(timezone.utc)
    return {"_id": "CLM-" + ulid(int(now.timestamp() * 1000)), "payer": {"id": "PYR-01", "format": "json-a"},
            "rendering_npi": rendering["_id"], "billing_npi": rendering["_id"],
            "referring_npi": referring["_id"] if referring else None, "rendering_specialty": rendering["specialty"],
            "patient_id": "MBR-00000001", "service_date": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "submitted_at": now, "place_of_service": "11", "diagnosis_codes": ["I10"],
            "line_items": [{"line": 1, "cpt": "99213", "units": 1, "charge": 92.0}], "total_charge": 92.0,
            "status": "submitted"}


def _edges(claim_id):
    return cypher("""
        OPTIONAL MATCH (a)-[r:REFERRED {claim_id: $c}]->(b)
        WITH collect(CASE WHEN r IS NULL THEN NULL ELSE [a.npi, b.npi] END) AS refs
        OPTIONAL MATCH ()-[t:TREATED {claim_id: $c}]->()
        RETURN refs, count(t) AS treated""", c=claim_id)[0]


@pytest.mark.parametrize("mutate, field", [
    (lambda c: c["line_items"][0].update(units=0), "units"),
    (lambda c: c.update(rendering_npi="12345"), "rendering_npi"),
    (lambda c: c.update(status="approved-by-me"), "status"),
    (lambda c: c.update(line_items=[]), "line_items"),
    (lambda c: c.update(diagnosis_codes=["not-a-code"]), "diagnosis_codes"),
])
def test_schema_validation_rejects_bad_claims(mutate, field):
    a, _ = _two_providers()
    bad = _claim(a)
    mutate(bad)
    with pytest.raises(WriteError) as exc:
        mongo_db().claims.insert_one(bad)
    assert field in str(exc.value.details)


def test_intake_explains_validation_errors():
    a, _ = _two_providers()
    payload = intake.to_json_a({"patient_id": "MBR-00000001", "billing_npi": "12345", "rendering_npi": "12345",
                                "service_date": "2026-10-01", "diagnosis_codes": ["I10"],
                                "line_items": [{"cpt": "99213", "units": 0, "charge": 92.0}]})
    with pytest.raises(intake.ClaimRejected) as exc:
        intake.ingest(payload, "json-a", "PYR-01", source="test")
    paths = {p["path"] for p in exc.value.details["problems"]}
    assert "rendering_npi" in paths and "line_items[0].units" in paths


def test_cdc_upsert_is_idempotent_and_follows_updates_and_deletes():
    a, b = _two_providers()
    c = _claim(b, referring=a)
    row = cdc.claim_row(c)
    try:
        for _ in range(3):  # the same event delivered three times
            cdc.apply_claims([row])
        e = _edges(c["_id"])
        assert e["treated"] == 1 and e["refs"] == [[a["_id"], b["_id"]]]
        # An update that changes the referrer must move the edge, not duplicate it.
        cdc.apply_claims([{**row, "referring_npi": b["_id"], "rendering_npi": a["_id"]}])
        assert _edges(c["_id"])["refs"] == [[b["_id"], a["_id"]]]
        cdc.apply_deletes([c["_id"]])
        e = _edges(c["_id"])
        assert e["treated"] == 0 and e["refs"] == []
    finally:
        cdc.apply_deletes([c["_id"]])


def test_no_duplicate_edges_in_the_graph():
    assert cdc.lag_report()["duplicate_edges"] == 0


@pytest.mark.parametrize("key", list(SHOWCASE))
def test_showcase_queries_use_their_index(key):
    indexed, scan = explain_query(key), explain_query(key, collscan=True)
    assert "IXSCAN" in indexed["stage"] and indexed["index"] == SHOWCASE[key]["index"]
    assert "COLLSCAN" in scan["stage"]
    assert indexed["docs_examined"] < scan["docs_examined"]
