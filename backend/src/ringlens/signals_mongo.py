"""Statistical fraud signals computed inside MongoDB with the aggregation framework.

Every provider-level pipeline ends in $merge into `provider_signals`, a materialized
collection the scoring engine reads. Peer comparison uses $setWindowFields partitioned by
specialty, so a provider is only compared with providers who do the same kind of work.
Claim-level hits are written back to the claims as `flags`, which feeds the partial index.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .datagen.reference import EM_ORDER, LAB_COMPONENTS
from .db import analytics_db, mongo_db

COMPONENTS = [c[0] for c in LAB_COMPONENTS]
WHOLE = {"documents": ["unbounded", "unbounded"]}
LIVE = {"status": {"$ne": "void"}}
IMPOSSIBLE_MINUTES = 16 * 60  # more billed face time in one day than a practitioner can work


def _z(field: str, mean: str, std: str, guard: dict | None = None) -> dict:
    """(x - mean) / std, or 0 when the peer group has no spread (or the guard fails)."""
    cond = {"$gt": [f"${std}", 0]}
    if guard:
        cond = {"$and": [cond, guard]}
    return {"$cond": [cond, {"$round": [{"$divide": [{"$subtract": [f"${field}", f"${mean}"]}, f"${std}"]}, 3]}, 0]}


def _merge(when_matched: str = "merge") -> dict:
    return {"$merge": {"into": "provider_signals", "on": "_id", "whenMatched": when_matched,
                       "whenNotMatched": "insert"}}


def billing_profile_pipeline() -> list[dict]:
    """Billing profile per provider, specialty peer statistics and z-scores.
    Amounts are log-transformed first: billing is heavy-tailed, logs are near-normal."""
    em = {"$filter": {"input": "$line_items", "cond": {"$in": ["$$this.cpt", EM_ORDER]}}}
    top = {"$filter": {"input": "$line_items", "cond": {"$eq": ["$$this.cpt", "99215"]}}}
    return [
        {"$match": LIVE},
        {"$group": {
            "_id": "$rendering_npi",
            "specialty": {"$first": "$rendering_specialty"},
            "claims": {"$sum": 1},
            "total_billed": {"$sum": "$total_charge"},
            "avg_claim": {"$avg": "$total_charge"},
            "log_avg_claim": {"$avg": {"$ln": {"$add": ["$total_charge", 1]}}},
            "patients": {"$addToSet": "$patient_id"},
            "em_lines": {"$sum": {"$size": em}},
            "em_top_lines": {"$sum": {"$size": top}},
            "referred_in": {"$sum": {"$cond": [{"$eq": [{"$type": "$referring_npi"}, "string"]}, 1, 0]}},
            "first_service": {"$min": "$service_date"},
            "last_service": {"$max": "$service_date"},
        }},
        {"$set": {"unique_patients": {"$size": "$patients"},
                  "top_em_ratio": {"$cond": [{"$gt": ["$em_lines", 0]}, {"$divide": ["$em_top_lines", "$em_lines"]}, 0]}}},
        {"$set": {"billed_per_patient": {"$divide": ["$total_billed", "$unique_patients"]},
                  "log_billed_per_patient": {"$ln": {"$add": [{"$divide": ["$total_billed", "$unique_patients"]}, 1]}}}},
        {"$unset": "patients"},
        {"$setWindowFields": {
            "partitionBy": "$specialty",
            "sortBy": {"avg_claim": -1},
            "output": {
                "peer_log_avg_mean": {"$avg": "$log_avg_claim", "window": WHOLE},
                "peer_log_avg_std": {"$stdDevPop": "$log_avg_claim", "window": WHOLE},
                "peer_log_bpp_mean": {"$avg": "$log_billed_per_patient", "window": WHOLE},
                "peer_log_bpp_std": {"$stdDevPop": "$log_billed_per_patient", "window": WHOLE},
                "peer_top_em_mean": {"$avg": "$top_em_ratio", "window": WHOLE},
                "peer_top_em_std": {"$stdDevPop": "$top_em_ratio", "window": WHOLE},
                "peer_avg_claim_median": {"$median": {"input": "$avg_claim", "method": "approximate"}, "window": WHOLE},
                "billing_rank_in_specialty": {"$rank": {}},
                "peers": {"$count": {}, "window": WHOLE},
            },
        }},
        {"$set": {
            "z_avg_claim": _z("log_avg_claim", "peer_log_avg_mean", "peer_log_avg_std"),
            "z_billed_per_patient": _z("log_billed_per_patient", "peer_log_bpp_mean", "peer_log_bpp_std"),
            # E/M mix is only meaningful with enough office visits.
            "z_top_em": _z("top_em_ratio", "peer_top_em_mean", "peer_top_em_std", {"$gte": ["$em_lines", 25]}),
            "computed_at": "$$NOW",
        }},
        _merge("replace"),
    ]


def velocity_pipeline() -> list[dict]:
    """Billing velocity with time-range windows: each day's rolling 7-day claim count
    against the provider's own rolling 90-day rate, plus 'impossible days'."""
    return [
        {"$match": LIVE},
        {"$group": {"_id": {"p": "$rendering_npi", "d": "$service_date"}, "n": {"$sum": 1},
                    "minutes": {"$sum": {"$sum": "$line_items.minutes"}}}},
        {"$setWindowFields": {
            "partitionBy": "$_id.p",
            "sortBy": {"_id.d": 1},
            "output": {
                "rolling7": {"$sum": "$n", "window": {"range": [-6, 0], "unit": "day"}},
                "rolling90": {"$sum": "$n", "window": {"range": [-89, 0], "unit": "day"}},
            },
        }},
        {"$set": {"spike": {"$divide": ["$rolling7", {"$max": [{"$multiply": ["$rolling90", 7 / 90]}, 3]}]}}},
        {"$group": {"_id": "$_id.p", "max_daily_claims": {"$max": "$n"}, "active_days": {"$sum": 1},
                    "max_velocity_spike": {"$max": {"$round": ["$spike", 2]}},
                    "max_daily_minutes": {"$max": "$minutes"},
                    "impossible_days": {"$sum": {"$cond": [{"$gt": ["$minutes", IMPOSSIBLE_MINUTES]}, 1, 0]}}}},
        _merge(),
    ]


def after_death_pipeline() -> list[dict]:
    """Services dated after the patient's death. Starts from the few deceased patients and
    looks up their claims through the {patient_id, service_date} index."""
    return [
        {"$match": {"dod": {"$type": "date"}}},
        {"$lookup": {"from": "claims", "let": {"pid": "$_id", "dod": "$dod"}, "as": "late",
                     "pipeline": [{"$match": {"$expr": {"$and": [{"$eq": ["$patient_id", "$$pid"]},
                                                                {"$gt": ["$service_date", "$$dod"]}]}}},
                                  {"$project": {"rendering_npi": 1}}]}},
        {"$unwind": "$late"},
        {"$group": {"_id": "$late.rendering_npi", "after_death_claims": {"$sum": 1},
                    "after_death_claim_ids": {"$push": "$late._id"}}},
        _merge(),
    ]


def duplicate_pipeline() -> list[dict]:
    """Same provider, patient, date and procedure set billed more than once."""
    return [
        {"$match": LIVE},
        {"$group": {
            "_id": {"p": "$rendering_npi", "pt": "$patient_id", "d": "$service_date",
                    "codes": {"$sortArray": {"input": "$line_items.cpt", "sortBy": 1}}},
            "subs": {"$push": {"id": "$_id", "at": "$submitted_at"}}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$set": {"later": {"$slice": [{"$sortArray": {"input": "$subs", "sortBy": {"at": 1}}}, 1, 100]}}},
        {"$group": {"_id": "$_id.p", "duplicate_claims": {"$sum": {"$subtract": ["$n", 1]}},
                    "duplicate_claim_ids": {"$push": "$later.id"}}},
        {"$set": {"duplicate_claim_ids": {"$reduce": {"input": "$duplicate_claim_ids", "initialValue": [],
                                                       "in": {"$concatArrays": ["$$value", "$$this"]}}}}},
        _merge(),
    ]


def unbundling_pipeline() -> list[dict]:
    """Lab claims billing 3+ panel components separately instead of the panel code."""
    return [
        {"$match": {"line_items.cpt": {"$in": COMPONENTS}, **LIVE}},  # served by the multikey index
        {"$set": {"component_lines": {"$size": {"$filter": {"input": "$line_items",
                                                            "cond": {"$in": ["$$this.cpt", COMPONENTS]}}}}}},
        {"$match": {"component_lines": {"$gte": 3}}},
        {"$group": {"_id": "$rendering_npi", "unbundled_claims": {"$sum": 1},
                    "unbundled_claim_ids": {"$push": "$_id"}}},
        _merge(),
    ]


def shared_identity_pipeline(field: str, out_field: str) -> list[dict]:
    """Providers who share a bank account or a street address with another provider.
    $unwind replaces the array with one element per document, so a copy is kept first."""
    return [
        {"$group": {"_id": f"${field}", "providers": {"$push": "$_id"}}},
        {"$match": {"providers.1": {"$exists": True}}},
        {"$set": {"group": "$providers"}},
        {"$unwind": "$providers"},
        {"$project": {"_id": "$providers",
                      out_field: {"$filter": {"input": "$group", "cond": {"$ne": ["$$this", "$providers"]}}}}},
        _merge(),
    ]


def referral_edges_pipeline() -> list[dict]:
    """Aggregated referral edge list, the $graphLookup target of the traversal benchmark."""
    return [
        {"$match": {"referring_npi": {"$type": "string"}, **LIVE}},
        {"$group": {"_id": {"from": "$referring_npi", "to": "$rendering_npi"}, "count": {"$sum": 1},
                    "amount": {"$sum": "$total_charge"}, "patients": {"$addToSet": "$patient_id"}}},
        {"$project": {"_id": {"$concat": ["$_id.from", ">", "$_id.to"]}, "from": "$_id.from", "to": "$_id.to",
                      "count": 1, "amount": {"$round": ["$amount", 2]}, "patients": {"$size": "$patients"}}},
        {"$out": "referral_edges"},
    ]


def flag_claims(top_em_z: float = 2.5) -> dict:
    """Write claim-level flags back to the system of record (feeds the partial index).
    The CDC change stream filters these flag-only updates out."""
    db = mongo_db()
    db.claims.update_many({"flagged": True}, {"$set": {"flagged": False, "flags": []}})
    counts = {}
    sources = {"duplicate": "duplicate_claim_ids", "unbundling": "unbundled_claim_ids",
               "after_death": "after_death_claim_ids"}
    for flag, field in sources.items():
        ids = [i for s in db.provider_signals.find({field: {"$exists": True}}, {field: 1}) for i in s[field]]
        for i in range(0, len(ids), 5000):
            db.claims.update_many({"_id": {"$in": ids[i:i + 5000]}},
                                  {"$set": {"flagged": True}, "$addToSet": {"flags": flag}})
        counts[flag] = len(ids)
    upcoders = [s["_id"] for s in db.provider_signals.find({"z_top_em": {"$gte": top_em_z}}, {"_id": 1})]
    if upcoders:
        r = db.claims.update_many({"rendering_npi": {"$in": upcoders}, "line_items.cpt": "99215"},
                                  {"$set": {"flagged": True}, "$addToSet": {"flags": "upcoding"}})
        counts["upcoding"] = r.modified_count
    counts["flagged_claims"] = db.claims.count_documents({"flagged": True})
    return counts


def compute_all() -> dict:
    """Run every statistical pipeline; returns timings per pipeline."""
    db, adb = mongo_db(), analytics_db()
    timings = {}

    def run(name, coll, pipeline):
        t0 = time.perf_counter()
        list(coll.aggregate(pipeline, allowDiskUse=True))
        timings[name] = round(time.perf_counter() - t0, 2)

    db.provider_signals.delete_many({})
    run("billing_profile", adb.claims, billing_profile_pipeline())
    run("velocity", adb.claims, velocity_pipeline())
    run("after_death", adb.patients, after_death_pipeline())
    run("duplicates", adb.claims, duplicate_pipeline())
    run("unbundling", adb.claims, unbundling_pipeline())
    run("shared_bank", adb.providers, shared_identity_pipeline("bank_hash", "shares_bank_with"))
    run("shared_address", adb.providers, shared_identity_pipeline("address_key", "shares_address_with"))
    run("referral_edges", adb.claims, referral_edges_pipeline())
    t0 = time.perf_counter()
    flags = flag_claims()
    timings["flag_claims"] = round(time.perf_counter() - t0, 2)
    return {"providers_profiled": db.provider_signals.count_documents({}), "flags": flags, "timings": timings,
            "computed_at": datetime.now(timezone.utc)}


# Shown in the DB Internals "pipeline viewer".
PIPELINES = {
    "billing_profile": billing_profile_pipeline,
    "velocity": velocity_pipeline,
    "after_death": after_death_pipeline,
    "duplicates": duplicate_pipeline,
    "unbundling": unbundling_pipeline,
    "shared_bank": lambda: shared_identity_pipeline("bank_hash", "shares_bank_with"),
    "referral_edges": referral_edges_pipeline,
}
