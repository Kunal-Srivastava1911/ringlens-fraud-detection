"""MongoDB system-of-record design: collections, JSON Schema validators and indexes.

Modelling decisions
-------------------
* A claim EMBEDS its line items: they are always read with the claim, bounded in number
  and never exist on their own, so embedding keeps the read atomic and join-free.
* A claim REFERENCES providers (by NPI) and patients (by member id): they are shared by
  many claims and mutable, so embedding them would cause update anomalies and push
  documents towards the 16 MB limit.
* The rendering provider's specialty is copied onto the claim (the "extended reference"
  pattern). It almost never changes and lets peer-group aggregations skip a $lookup.
* Validators run at write time, so malformed claims never enter the system of record.
"""
import logging

from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.errors import CollectionInvalid, OperationFailure

from .db import mongo_db, truth_db

log = logging.getLogger("ringlens.schema")

CLAIM_STATUSES = ["submitted", "adjudicated", "paid", "denied", "void"]
NUMBER = ["double", "int", "long", "decimal"]
NPI = {"bsonType": "string", "pattern": "^[12][0-9]{9}$"}

CLAIM_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "Healthcare claim",
        "required": ["_id", "payer", "rendering_npi", "billing_npi", "patient_id", "service_date",
                     "submitted_at", "line_items", "total_charge", "status"],
        "properties": {
            "_id": {"bsonType": "string", "pattern": "^CLM-[0-9A-HJKMNP-TV-Z]{26}$",
                    "description": "CLM- followed by a ULID"},
            "payer": {"bsonType": "object", "required": ["id", "format"],
                      "properties": {"id": {"bsonType": "string", "pattern": "^PYR-[0-9]{2}$"},
                                     "format": {"enum": ["json-a", "json-b", "837P"]}}},
            "rendering_npi": NPI,
            "billing_npi": NPI,
            "referring_npi": {"oneOf": [NPI, {"bsonType": "null"}]},
            "rendering_specialty": {"bsonType": "string"},
            "patient_id": {"bsonType": "string", "pattern": "^MBR-[0-9]{8}$"},
            "service_date": {"bsonType": "date"},
            "submitted_at": {"bsonType": "date"},
            "place_of_service": {"bsonType": "string", "pattern": "^[0-9]{2}$"},
            "diagnosis_codes": {"bsonType": "array", "minItems": 1, "maxItems": 12,
                                "items": {"bsonType": "string", "pattern": "^[A-Z][0-9][0-9A-Z](\\.[0-9A-Z]{1,4})?$"}},
            "line_items": {
                "bsonType": "array", "minItems": 1, "maxItems": 50,
                "items": {
                    "bsonType": "object",
                    "required": ["line", "cpt", "units", "charge"],
                    "properties": {
                        "line": {"bsonType": "int", "minimum": 1},
                        "cpt": {"bsonType": "string", "pattern": "^[0-9A-Z][0-9]{3}[0-9A-Z]$"},
                        "modifiers": {"bsonType": "array", "maxItems": 4, "items": {"bsonType": "string"}},
                        "units": {"bsonType": "int", "minimum": 1, "maximum": 99},
                        "charge": {"bsonType": NUMBER, "minimum": 0},
                        "minutes": {"bsonType": NUMBER, "minimum": 0},
                    },
                },
            },
            "total_charge": {"bsonType": NUMBER, "minimum": 0},
            "status": {"enum": CLAIM_STATUSES},
            "flagged": {"bsonType": "bool"},
            "flags": {"bsonType": "array", "items": {"bsonType": "string"}},
        },
    }
}

PROVIDER_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "name", "specialty", "kind", "metro", "address", "bank_hash"],
        "properties": {
            "_id": NPI,
            "name": {"bsonType": "string", "minLength": 2},
            "specialty": {"bsonType": "string"},
            "kind": {"enum": ["primary", "specialist", "ancillary"]},
            "address": {"bsonType": "object", "required": ["line1", "city", "state", "zip"]},
            "bank_hash": {"bsonType": "string", "pattern": "^[0-9a-f]{16}$"},
            "location": {"bsonType": "object"},
        },
    }
}

PATIENT_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "name", "dob", "sex", "metro"],
        "properties": {
            "_id": {"bsonType": "string", "pattern": "^MBR-[0-9]{8}$"},
            "sex": {"enum": ["F", "M", "X"]},
            "dob": {"bsonType": "date"},
            "dod": {"bsonType": ["date", "null"]},
        },
    }
}

VALIDATORS = {"claims": CLAIM_SCHEMA, "providers": PROVIDER_SCHEMA, "patients": PATIENT_SCHEMA}

INDEXES = {
    "claims": [
        # ESR rule (Equality, Sort, Range): "claims of provider X, newest first, above $N".
        IndexModel([("rendering_npi", ASCENDING), ("service_date", DESCENDING), ("total_charge", ASCENDING)],
                   name="esr_provider_date_charge"),
        IndexModel([("patient_id", ASCENDING), ("service_date", DESCENDING)], name="esr_patient_date"),
        # Multikey: one index entry per embedded line item.
        IndexModel([("line_items.cpt", ASCENDING), ("service_date", DESCENDING)], name="multikey_cpt_date"),
        # Partial: only the small set of flagged claims is indexed, so the analyst queue
        # stays tiny however large the collection grows.
        IndexModel([("flags", ASCENDING), ("service_date", DESCENDING)],
                   partialFilterExpression={"flagged": True}, name="partial_flagged_queue"),
        IndexModel([("referring_npi", ASCENDING), ("rendering_npi", ASCENDING)],
                   partialFilterExpression={"referring_npi": {"$type": "string"}}, name="partial_referrals"),
        IndexModel([("rendering_specialty", ASCENDING), ("service_date", ASCENDING)], name="specialty_date"),
        IndexModel([("submitted_at", DESCENDING)], name="submitted_at"),
        # Keyset pagination of the claims explorer: (service_date, _id) descending.
        IndexModel([("service_date", DESCENDING), ("_id", DESCENDING)], name="keyset_service_date"),
    ],
    "providers": [
        IndexModel([("specialty", ASCENDING)], name="specialty"),
        IndexModel([("bank_hash", ASCENDING)], name="bank_hash"),
        IndexModel([("address_key", ASCENDING)], name="address_key"),
        IndexModel([("metro", ASCENDING), ("kind", ASCENDING)], name="metro_kind"),
        IndexModel([("name", "text"), ("specialty", "text")], name="text_search"),
    ],
    "patients": [IndexModel([("metro", ASCENDING)], name="metro")],
    # The raw payer feed lands here first; the TTL index expires it after 24 h.
    "claims_staging": [IndexModel([("received_at", ASCENDING)], expireAfterSeconds=86400, name="ttl_received_at")],
    "provider_scores": [
        IndexModel([("score", DESCENDING)], name="score_desc"),
        IndexModel([("specialty", ASCENDING), ("score", DESCENDING)], name="specialty_score"),
        IndexModel([("score", DESCENDING)], partialFilterExpression={"flagged": True}, name="partial_flagged_providers"),
    ],
    "referral_edges": [IndexModel([("from", ASCENDING)], name="from"), IndexModel([("to", ASCENDING)], name="to")],
    "rings": [IndexModel([("suspicion", DESCENDING)], name="suspicion_desc")],
    "cases": [IndexModel([("status", ASCENDING), ("updated_at", DESCENDING)], name="status_updated")],
    "audit_log": [IndexModel([("subject", ASCENDING), ("at", DESCENDING)], name="subject_at")],
}

TIMESERIES = {
    # CDC lag/throughput samples, kept for 3 days.
    "pipeline_metrics": {"timeField": "ts", "metaField": "component", "granularity": "seconds", "ttl": 3 * 86400},
    # One sample per provider per scoring run.
    "score_history": {"timeField": "ts", "metaField": "npi", "granularity": "minutes", "ttl": None},
}


def create_schema(drop: bool = False) -> dict:
    db = mongo_db()
    if drop:
        for name in db.list_collection_names():
            # The CDC worker's control channel survives a reseed; system.views goes away
            # with the time-series collections it describes.
            if name != "pipeline_control" and not name.startswith("system."):
                db.drop_collection(name)
        truth = truth_db()
        for name in truth.list_collection_names():
            truth.drop_collection(name)
    existing = set(db.list_collection_names())

    for name, validator in VALIDATORS.items():
        opts = {"validator": validator, "validationLevel": "strict", "validationAction": "error"}
        if name in existing:
            db.command("collMod", name, **opts)
        else:
            db.create_collection(name, **opts)
        # Pre-images let the CDC consumer see what a deleted document contained. Shared tiers
        # (MongoDB Atlas free) may refuse the option; the worker then works from claim ids alone.
        try:
            db.command("collMod", name, changeStreamPreAndPostImages={"enabled": True})
        except OperationFailure as exc:
            log.warning("pre-images unavailable on %s: %s", name, exc)

    for name, ts in TIMESERIES.items():
        if name not in existing:
            opts = {"timeseries": {k: ts[k] for k in ("timeField", "metaField", "granularity")}}
            if ts["ttl"]:
                opts["expireAfterSeconds"] = ts["ttl"]
            try:
                db.create_collection(name, **opts)
            except CollectionInvalid:
                pass

    for name, indexes in INDEXES.items():
        try:
            db[name].create_indexes(indexes)
        except OperationFailure as exc:  # options changed between versions: rebuild
            if exc.code in (85, 86):
                db[name].drop_indexes()
                db[name].create_indexes(indexes)
            else:
                raise
    db.scoring_config.update_one({"_id": "active"}, {"$setOnInsert": _default_scoring_config()}, upsert=True)
    db.pipeline_control.update_one({"_id": "neo4j"}, {"$setOnInsert": {"paused": False, "crash": False}},
                                   upsert=True)
    return {"collections": sorted(db.list_collection_names())}


def _default_scoring_config() -> dict:
    from .scoring import DEFAULT_WEIGHTS

    return {"version": 1, "weights": DEFAULT_WEIGHTS, "mode": "noisy_or", "updated_at": None}
