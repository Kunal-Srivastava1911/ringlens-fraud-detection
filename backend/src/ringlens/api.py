"""RingLens REST + WebSocket API.

    ringlens api            (or: uvicorn ringlens.api:app --port 8000)

Endpoints are synchronous (FastAPI runs them in a thread pool); the WebSocket hub is fed by
change streams and intake listeners (see live.py).
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pymongo import DESCENDING
from pymongo.read_concern import ReadConcern

from . import cdc, intake, internals, jobs, scoring, signals_mongo
from .datagen import reference as ref
from .db import MAJORITY, cypher, mongo_client, mongo_db, truth_db
from .live import HUB, jsonable
from .settings import get_settings


@asynccontextmanager
async def lifespan(_app):
    HUB.start(asyncio.get_running_loop())
    yield
    HUB.stop()
    intake.FEED.stop()


app = FastAPI(title="RingLens API", version="1.0.0", lifespan=lifespan,
              default_response_class=JSONResponse)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.monotonic(), value)
    return value


def J(obj):
    return jsonable(obj)


def _truth_labels() -> dict[str, dict]:
    return cached("truth", 5, lambda: {t["_id"]: t for t in truth_db().providers.find()})


# ================================================================ health / overview
@app.get("/api/health")
def health():
    out = {"mongo": False, "neo4j": False}
    try:
        hello = mongo_client().admin.command("hello")
        out["mongo"] = True
        out["mongo_topology"] = "sharded" if hello.get("msg") == "isdbgrid" else f"replica set {hello.get('setName')}"
    except Exception as exc:
        out["mongo_error"] = str(exc)
    try:
        out["gds"] = cypher("RETURN gds.version() AS v")[0]["v"]
        out["neo4j"] = True
    except Exception as exc:
        out["neo4j_error"] = str(exc)
    return out


def _overview():
    db = mongo_db()
    totals = next(db.claims.aggregate([{"$group": {"_id": None, "billed": {"$sum": "$total_charge"}}}]), {"billed": 0})
    scores = list(db.provider_scores.find({}, {"score": 1, "score_statistical": 1, "score_graph": 1, "flagged": 1,
                                                "risk": 1, "total_billed": 1, "name": 1, "specialty": 1, "ring_id": 1,
                                                "reasons": 1, "rank": 1, "metro": 1, "score_delta": 1, "signals": 1}))
    flagged = [s for s in scores if s["flagged"]]
    since = datetime.now(timezone.utc) - timedelta(days=180)
    daily = list(db.claims.aggregate([
        {"$match": {"service_date": {"$gte": since}}},
        {"$group": {"_id": "$service_date", "claims": {"$sum": 1}, "billed": {"$sum": "$total_charge"},
                    "flagged": {"$sum": {"$cond": [{"$eq": ["$flagged", True]}, 1, 0]}}}},
        {"$sort": {"_id": 1}}]))
    hist = [0] * 20
    for s in scores:
        hist[min(19, int(s["score"] // 5))] += 1
    rings = list(db.rings.find({"detected": True, "active": True},
                               {"members": 1, "size": 1, "suspicion": 1, "internal_amount": 1, "metrics": 1,
                                "status": 1, "first_detected_at": 1, "flows": 1, "hub": 1}).sort("suspicion", -1))
    risk_counts: dict[str, int] = {}
    for s in scores:
        risk_counts[s["risk"]] = risk_counts.get(s["risk"], 0) + 1
    specialty: dict[str, dict] = {}
    for s in scores:
        e = specialty.setdefault(s["specialty"], {"specialty": s["specialty"], "providers": 0, "flagged": 0})
        e["providers"] += 1
        e["flagged"] += s["flagged"]
    last_job = db.jobs.find_one({"status": "done"}, sort=[("started_at", -1)])
    return {
        "kpis": {
            "claims": db.claims.estimated_document_count(), "billed": round(totals["billed"], 2),
            "providers": db.providers.estimated_document_count(), "patients": db.patients.estimated_document_count(),
            "flagged_providers": len(flagged), "critical": risk_counts.get("critical", 0),
            "rings": len(rings), "at_risk": round(sum(s["total_billed"] for s in flagged), 2),
            "flagged_claims": db.claims.count_documents({"flagged": True}),
            "ring_amount": round(sum(r["internal_amount"] for r in rings), 2),
        },
        "daily": [{"date": d["_id"].date().isoformat(), "claims": d["claims"], "billed": round(d["billed"], 2),
                   "flagged": d["flagged"]} for d in daily],
        "histogram": [{"bucket": i * 5, "count": c} for i, c in enumerate(hist)],
        "risk_counts": risk_counts,
        "top": sorted(scores, key=lambda s: -s["score"])[:12],
        "rings": rings,
        "quadrant": [{"npi": s["_id"], "stat": s["score_statistical"], "graph": s["score_graph"], "score": s["score"],
                      "name": s["name"], "flagged": s["flagged"]} for s in scores],
        "specialties": sorted(specialty.values(), key=lambda e: -e["flagged"]),
        "last_analysis": last_job,
    }


@app.get("/api/overview")
def overview():
    return J(cached("overview", 3, _overview))


# ================================================================ providers
SORTS = {"score": ("score", -1), "billed": ("total_billed", -1), "claims": ("claims", -1), "name": ("name", 1),
         "delta": ("score_delta", -1)}


@app.get("/api/providers")
def providers(q: str | None = None, specialty: str | None = None, risk: str | None = None,
              ring: bool | None = None, flagged: bool | None = None, metro: str | None = None,
              sort: str = "score", limit: int = Query(200, le=2000), offset: int = 0, reveal: bool = False):
    flt: dict = {}
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        flt["$or"] = [{"_id": {"$regex": "^" + re.escape(q)}}, {"name": rx}, {"specialty": rx}]
    if specialty:
        flt["specialty"] = specialty
    if risk:
        flt["risk"] = risk
    if metro:
        flt["metro"] = metro
    if ring is not None:
        flt["ring_id"] = {"$ne": None} if ring else None
    if flagged is not None:
        flt["flagged"] = flagged
    field, direction = SORTS.get(sort, SORTS["score"])
    db = mongo_db()
    total = db.provider_scores.count_documents(flt)
    rows = list(db.provider_scores.find(flt, {"location": 0}).sort(field, direction).skip(offset).limit(limit))
    cases = {c["_id"]: c["status"] for c in db.cases.find({"_id": {"$in": [r["_id"] for r in rows]}}, {"status": 1})}
    labels = _truth_labels() if reveal else {}
    for r in rows:
        r["case_status"] = cases.get(r["_id"])
        if reveal:
            t = labels.get(r["_id"])
            r["truth"] = t["fraud_types"] if t else []
    return J({"total": total, "rows": rows})


@app.get("/api/providers/{npi}")
def provider_detail(npi: str, reveal: bool = False):
    db = mongo_db()
    p = db.providers.find_one({"_id": npi})
    if not p:
        raise HTTPException(404, "Unknown provider")
    score = db.provider_scores.find_one({"_id": npi})
    stat = db.provider_signals.find_one({"_id": npi}, {"duplicate_claim_ids": 0, "unbundled_claim_ids": 0,
                                                       "after_death_claim_ids": 0})
    graph = db.graph_signals.find_one({"_id": npi})
    spec = p["specialty"]

    # Peer distribution of average claim amount and top-level E/M share within the specialty.
    peers = list(db.provider_signals.find({"specialty": spec}, {"avg_claim": 1, "top_em_ratio": 1, "claims": 1}))
    # Monthly billing vs the specialty's interquartile band ($percentile).
    since = datetime.now(timezone.utc) - timedelta(days=365)
    band = list(db.claims.aggregate([
        {"$match": {"rendering_specialty": spec, "service_date": {"$gte": since}}},
        {"$group": {"_id": {"p": "$rendering_npi", "m": {"$dateTrunc": {"date": "$service_date", "unit": "month"}}},
                    "billed": {"$sum": "$total_charge"}}},
        {"$group": {"_id": "$_id.m", "pct": {"$percentile": {"input": "$billed", "p": [0.25, 0.5, 0.75],
                                                             "method": "approximate"}},
                    "mine": {"$sum": {"$cond": [{"$eq": ["$_id.p", npi]}, "$billed", 0]}}}},
        {"$sort": {"_id": 1}}]))
    em_mine = list(db.claims.aggregate([
        {"$match": {"rendering_npi": npi}}, {"$unwind": "$line_items"},
        {"$match": {"line_items.cpt": {"$in": ref.EM_ORDER}}}, {"$sortByCount": "$line_items.cpt"}]))
    em_peer = cached(f"em:{spec}", 60, lambda: list(db.claims.aggregate([
        {"$match": {"rendering_specialty": spec}}, {"$unwind": "$line_items"},
        {"$match": {"line_items.cpt": {"$in": ref.EM_ORDER}}}, {"$sortByCount": "$line_items.cpt"}])))
    flagged_claims = list(db.claims.find({"rendering_npi": npi, "flagged": True}).sort("service_date", -1).limit(25))
    recent = list(db.claims.find({"rendering_npi": npi}).sort("service_date", -1).limit(25))
    history = list(db.score_history.find({"npi": npi}, {"_id": 0, "ts": 1, "score": 1}).sort("ts", 1).limit(500))
    case = db.cases.find_one({"_id": npi})
    audit = list(db.audit_log.find({"subject": npi}).sort("at", -1).limit(30))
    ring = db.rings.find_one({"_id": graph.get("ring_id")}) if graph and graph.get("ring_id") else None
    out = {"provider": p, "score": score, "statistical": stat, "graph": graph, "ring": ring,
           "peers": {"avg_claim": [x.get("avg_claim") for x in peers], "top_em_ratio": [x.get("top_em_ratio") for x in peers]},
           "billing_band": [{"month": b["_id"].date().isoformat()[:7], "p25": b["pct"][0], "p50": b["pct"][1],
                             "p75": b["pct"][2], "provider": round(b["mine"], 2)} for b in band],
           "em_mix": {"provider": {x["_id"]: x["count"] for x in em_mine}, "peers": {x["_id"]: x["count"] for x in em_peer}},
           "flagged_claims": flagged_claims, "recent_claims": recent, "history": history, "case": case, "audit": audit}
    if reveal:
        out["truth"] = _truth_labels().get(npi)
    return J(out)


@app.get("/api/providers/{npi}/network")
def provider_network(npi: str, limit: int = 40):
    rows = cypher("""
        MATCH (p:Provider {npi: $n})
        CALL (p) {
            MATCH (p)-[r:REFERS_TO]->(o:Provider) RETURN o, r.count AS w, 'out' AS dir, r.amount AS amount
            UNION
            MATCH (o:Provider)-[r:REFERS_TO]->(p) RETURN o, r.count AS w, 'in' AS dir, r.amount AS amount
        }
        WITH p, o, w, dir, amount ORDER BY w DESC LIMIT $lim
        RETURN o.npi AS npi, o.name AS name, o.specialty AS specialty, o.ring_id AS ring_id, w, dir, amount""",
                  n=npi, lim=limit)
    shared = cypher("""
        MATCH (p:Provider {npi: $n})-[s:SHARES_PATIENTS]-(o:Provider)
        RETURN o.npi AS npi, o.name AS name, o.specialty AS specialty, o.ring_id AS ring_id, s.jaccard AS jaccard,
               s.shared AS shared""", n=npi)
    ids = {r["npi"] for r in rows + shared} | {npi}
    scores = {s["_id"]: s for s in mongo_db().provider_scores.find({"_id": {"$in": list(ids)}},
                                                                    {"score": 1, "name": 1, "specialty": 1, "ring_id": 1})}
    nodes = [{"id": i, "name": scores.get(i, {}).get("name"), "specialty": scores.get(i, {}).get("specialty"),
              "score": scores.get(i, {}).get("score", 0), "ring_id": scores.get(i, {}).get("ring_id"),
              "center": i == npi} for i in ids]
    links = [{"source": npi if r["dir"] == "out" else r["npi"], "target": r["npi"] if r["dir"] == "out" else npi,
              "count": r["w"], "amount": r["amount"], "kind": "referral"} for r in rows]
    links += [{"source": npi, "target": s["npi"], "count": s["shared"], "jaccard": s["jaccard"], "kind": "shared"}
              for s in shared]
    return J({"nodes": nodes, "links": links})


# ================================================================ graph
@app.get("/api/graph/galaxy")
def galaxy(min_count: int = 3):
    def build():
        nodes = cypher("""
            MATCH (p:Provider)
            RETURN p.npi AS id, p.specialty AS specialty, p.kind AS kind, p.metro AS metro,
                   p.community AS community, coalesce(p.pagerank, 0.15) AS pagerank, p.ring_id AS ring""")
        links = cypher("""
            MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider) WHERE r.count >= $mc
            RETURN a.npi AS source, b.npi AS target, r.count AS count,
                   (a.ring_id IS NOT NULL AND a.ring_id = b.ring_id) AS ring""", mc=min_count)
        shared = cypher("""
            MATCH (a:Provider)-[s:SHARES_PATIENTS]->(b:Provider) WHERE s.jaccard >= 0.12
            RETURN a.npi AS source, b.npi AS target, s.jaccard AS jaccard""")
        scores = {s["_id"]: s for s in mongo_db().provider_scores.find({}, {"score": 1, "name": 1, "risk": 1})}
        for n in nodes:
            s = scores.get(n["id"], {})
            n["score"], n["name"], n["risk"] = s.get("score", 0), s.get("name"), s.get("risk", "low")
        return {"nodes": nodes, "links": links, "shared": shared}
    return J(cached(f"galaxy:{min_count}", 10, build))


# ================================================================ rings
@app.get("/api/rings")
def rings(include_candidates: bool = False, reveal: bool = False):
    db = mongo_db()
    flt = {"active": True} if include_candidates else {"detected": True, "active": True}
    rows = list(db.rings.find(flt).sort("suspicion", -1))
    if reveal:
        planted = list(truth_db().rings.find())
        for r in rows:
            best = max(((len(set(r["members"]) & set(p["members"])) / len(set(r["members"]) | set(p["members"])), p["_id"])
                        for p in planted), default=(0.0, None))
            r["truth_match"] = {"ring": best[1], "jaccard": round(best[0], 3)}
    return J(rows)


@app.get("/api/rings/{ring_id}")
def ring_detail(ring_id: str, reveal: bool = False):
    db = mongo_db()
    ring = db.rings.find_one({"_id": ring_id})
    if not ring:
        raise HTTPException(404, "Unknown ring")
    members = ring["members"]
    scores = {s["_id"]: s for s in db.provider_scores.find({"_id": {"$in": members}}, {"location": 0})}
    timeline = list(db.claims.aggregate([
        {"$match": {"rendering_npi": {"$in": members}, "referring_npi": {"$in": members}}},
        {"$group": {"_id": {"$dateTrunc": {"date": "$service_date", "unit": "week"}}, "claims": {"$sum": 1},
                    "amount": {"$sum": "$total_charge"}, "patients": {"$addToSet": "$patient_id"}}},
        {"$project": {"claims": 1, "amount": {"$round": ["$amount", 2]}, "patients": {"$size": "$patients"}}},
        {"$sort": {"_id": 1}}]))
    journeys = list(db.claims.aggregate([
        {"$match": {"rendering_npi": {"$in": members}, "referring_npi": {"$in": members}}},
        {"$sort": {"service_date": 1}},
        {"$group": {"_id": "$patient_id", "hops": {"$push": {"from": "$referring_npi", "to": "$rendering_npi",
                                                            "date": "$service_date", "amount": "$total_charge"}}}},
        {"$set": {"n": {"$size": "$hops"}}}, {"$sort": {"n": -1}}, {"$limit": 8}]))
    case = db.cases.find_one({"_id": ring_id})
    audit = list(db.audit_log.find({"subject": ring_id}).sort("at", -1).limit(20))
    out = {"ring": ring, "members": [scores.get(m, {"_id": m}) for m in members],
           "timeline": [{"week": t["_id"].date().isoformat(), **{k: t[k] for k in ("claims", "amount", "patients")}}
                        for t in timeline], "journeys": journeys, "case": case, "audit": audit}
    if reveal:
        planted = list(truth_db().rings.find())
        best = max(((len(set(members) & set(p["members"])) / len(set(members) | set(p["members"])), p)
                    for p in planted), default=(0.0, None), key=lambda x: x[0])
        out["truth"] = {"jaccard": round(best[0], 3), "ring": best[1]} if best[1] else None
    return J(out)


# ================================================================ claims
@app.get("/api/claims")
def claims(npi: str | None = None, patient: str | None = None, flag: str | None = None, flagged: bool = False,
           cpt: str | None = None, referring: str | None = None, before: str | None = None,
           limit: int = Query(50, le=200)):
    flt: dict = {}
    if npi:
        flt["rendering_npi"] = npi
    if patient:
        flt["patient_id"] = patient
    if flagged or flag:
        flt["flagged"] = True  # lets the planner use the partial index
    if flag:
        flt["flags"] = flag
    if cpt:
        flt["line_items.cpt"] = cpt
    if referring:
        flt["referring_npi"] = referring
    if before:  # keyset pagination on (service_date, _id); cursor = "<epoch ms>|<claim id>"
        ms, cid = before.split("|")
        dt = datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
        flt["$or"] = [{"service_date": {"$lt": dt}}, {"service_date": dt, "_id": {"$lt": cid}}]
    rows = list(mongo_db().claims.find(flt).sort([("service_date", DESCENDING), ("_id", DESCENDING)]).limit(limit))
    nxt = f"{int(rows[-1]['service_date'].timestamp() * 1000)}|{rows[-1]['_id']}" if len(rows) == limit else None
    return J({"rows": rows, "next": nxt})


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: str, reveal: bool = False):
    db = mongo_db()
    c = db.claims.find_one({"_id": claim_id})
    if not c:
        raise HTTPException(404, "Unknown claim")
    npis = [c["rendering_npi"], c.get("referring_npi")]
    names = {p["_id"]: p for p in db.providers.find({"_id": {"$in": [n for n in npis if n]}},
                                                    {"name": 1, "specialty": 1})}
    staging = db.claims_staging.find_one({"claim_id": claim_id})
    out = {"claim": c, "providers": names, "patient": db.patients.find_one({"_id": c["patient_id"]}),
           "source": staging}
    if reveal:
        out["truth"] = truth_db().claims.find_one({"_id": claim_id})
    return J(out)


class ClaimSubmission(BaseModel):
    format: Literal["json-a", "json-b", "837P"] = "json-a"
    payer_id: str = "PYR-01"
    payload: dict | str


@app.post("/api/claims")
def submit_claim(body: ClaimSubmission):
    try:
        claim = intake.ingest(body.payload, body.format, body.payer_id, source="api")
        return J({"accepted": True, "claim": claim})
    except intake.ClaimRejected as exc:
        return JSONResponse(status_code=422, content=J({"accepted": False, "error": str(exc),
                                                        "validation": exc.details, "staging_id": exc.staging_id}))


@app.get("/api/claims-sample/{fmt}")
def claim_sample(fmt: Literal["json-a", "json-b", "837P"]):
    """A ready-to-edit payload in the given payer format, built from a real recent claim."""
    c = mongo_db().claims.find_one({"referring_npi": {"$type": "string"}}, sort=[("service_date", -1)])
    canonical = {"patient_id": c["patient_id"], "billing_npi": c["billing_npi"], "rendering_npi": c["rendering_npi"],
                 "referring_npi": c.get("referring_npi"), "service_date": datetime.now(timezone.utc).date().isoformat(),
                 "place_of_service": c.get("place_of_service", "11"), "diagnosis_codes": c["diagnosis_codes"],
                 "line_items": [{k: li[k] for k in ("cpt", "units", "charge") if k in li} for li in c["line_items"]]}
    payer = {"json-a": "PYR-01", "json-b": "PYR-03", "837P": "PYR-02"}[fmt]
    if fmt == "837P":
        from . import edi837
        payload = edi837.render({**canonical, "payer_id": payer})
    elif fmt == "json-a":
        payload = intake.to_json_a(canonical)
    else:
        payload = intake.to_json_b(canonical)
    return {"format": fmt, "payer_id": payer, "payload": payload}


# ================================================================ cases (ACID inside MongoDB)
CASE_STATES = ["open", "investigating", "confirmed_fraud", "cleared"]


class CaseTransition(BaseModel):
    status: Literal["open", "investigating", "confirmed_fraud", "cleared"]
    note: str = ""
    analyst: str = Field("Analyst", max_length=60)


def _transition(session, subject: str, kind: str, body: CaseTransition, now: datetime):
    db = mongo_db()
    prev = db.cases.find_one({"_id": subject}, session=session)
    db.cases.update_one({"_id": subject}, {"$set": {"status": body.status, "kind": kind, "updated_at": now,
                                                    "analyst": body.analyst},
                                           "$setOnInsert": {"opened_at": now}}, upsert=True, session=session)
    db.audit_log.insert_one({"subject": subject, "kind": kind, "from": prev["status"] if prev else None,
                             "to": body.status, "note": body.note, "analyst": body.analyst, "at": now}, session=session)


@app.post("/api/cases/{npi}")
def transition_case(npi: str, body: CaseTransition):
    if not mongo_db().providers.find_one({"_id": npi}, {"_id": 1}):
        raise HTTPException(404, "Unknown provider")
    now = datetime.now(timezone.utc)
    with mongo_client().start_session() as s:
        s.with_transaction(lambda sess: _transition(sess, npi, "provider", body, now),
                           read_concern=ReadConcern("snapshot"), write_concern=MAJORITY)
    return J(mongo_db().cases.find_one({"_id": npi}))


@app.post("/api/rings/{ring_id}/decision")
def ring_decision(ring_id: str, body: CaseTransition):
    """Confirming or clearing a ring updates the ring, every member's case and the audit
    log in ONE multi-document transaction: all of it commits, or none of it does."""
    db = mongo_db()
    ring = db.rings.find_one({"_id": ring_id})
    if not ring:
        raise HTTPException(404, "Unknown ring")
    now = datetime.now(timezone.utc)

    def txn(sess):
        db.rings.update_one({"_id": ring_id}, {"$set": {"status": body.status, "decided_at": now}}, session=sess)
        _transition(sess, ring_id, "ring", body, now)
        for m in ring["members"]:
            _transition(sess, m, "provider", CaseTransition(status=body.status, analyst=body.analyst,
                                                            note=f"Ring {ring_id}: {body.note}".strip(": ")), now)

    t0 = time.perf_counter()
    with mongo_client().start_session() as s:
        s.with_transaction(txn, read_concern=ReadConcern("snapshot"), write_concern=MAJORITY)
    return J({"ring": ring_id, "status": body.status, "documents_written": 2 + 2 * len(ring["members"]) + 1,
              "transaction_ms": round((time.perf_counter() - t0) * 1000, 1)})


@app.get("/api/cases")
def case_list():
    db = mongo_db()
    rows = list(db.cases.find().sort("updated_at", -1).limit(300))
    return J({"rows": rows, "counts": {s: sum(r["status"] == s for r in rows) for s in CASE_STATES}})


# ================================================================ pipeline
@app.get("/api/pipeline/status")
def pipeline_status():
    def build():
        db = mongo_db()
        rep = cdc.lag_report()
        since = datetime.now(timezone.utc) - timedelta(minutes=15)
        metrics = list(db.pipeline_metrics.find({"ts": {"$gte": since}}, {"_id": 0}).sort("ts", 1))
        hour = datetime.now(timezone.utc) - timedelta(hours=1)
        staging = {r["_id"]: r["n"] for r in db.claims_staging.aggregate([
            {"$match": {"received_at": {"$gte": hour}}}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}])}
        rejects = list(db.claims_staging.find({"status": "rejected"}, {"payload": 0}).sort("received_at", -1).limit(8))
        st = cdc.state()
        return {"report": rep, "control": cdc.control(), "metrics": metrics, "staging": staging,
                "recent_rejections": rejects, "feed": intake.FEED.status(), "last_batch": st.get("last_batch"),
                "restarts_at": st.get("restarts_at"), "crashed_at": st.get("crashed_at"),
                "crash_events_in_flight": st.get("crash_events_in_flight"), "pid": st.get("pid"),
                "staging_total": db.claims_staging.estimated_document_count(), "job_running": jobs.running()}
    return J(cached("pipeline", 0.8, build))


@app.post("/api/pipeline/{action}")
def pipeline_action(action: Literal["pause", "resume", "crash"]):
    update = {"pause": {"paused": True}, "resume": {"paused": False}, "crash": {"crash": True, "paused": False}}[action]
    mongo_db().pipeline_control.update_one({"_id": "neo4j"}, {"$set": update}, upsert=True)
    return J(cdc.control())


# ================================================================ simulation
class FeedRequest(BaseModel):
    rate: float = Field(10, ge=0.5, le=200)


@app.post("/api/simulate/feed")
def start_feed(body: FeedRequest):
    intake.FEED.start(body.rate)
    intake.FEED.rate = body.rate
    return J(intake.FEED.status())


@app.delete("/api/simulate/feed")
def stop_feed():
    intake.FEED.stop()
    return J(intake.FEED.status())


class RingRequest(BaseModel):
    size: int = Field(5, ge=3, le=8)
    camouflage: bool = True
    metro: str | None = None
    analyze: bool = True


@app.post("/api/simulate/ring")
def simulate_ring(body: RingRequest):
    try:
        result = intake.plant_ring(body.size, body.camouflage, body.metro)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.analyze and not jobs.running():
        threading.Thread(target=_safe_analyze, args=("ring-planter",), daemon=True).start()
        result["analysis"] = "started"
    return J(result)


def _safe_analyze(trigger: str):
    try:
        jobs.analyze(trigger)
    except Exception:
        pass
    _cache.clear()


@app.post("/api/jobs/analyze")
def start_analyze():
    if jobs.running():
        raise HTTPException(409, "An analytics run is already in progress")
    threading.Thread(target=_safe_analyze, args=("dashboard",), daemon=True).start()
    return {"started": True}


@app.get("/api/jobs")
def job_list():
    return J(list(mongo_db().jobs.find({}, {"trace": 0}).sort("started_at", -1).limit(15)))


# ================================================================ evaluation
@app.get("/api/evaluation")
def get_evaluation():
    db = mongo_db()
    ev = db.evaluation_runs.find_one({"_id": "latest"})
    labels = _truth_labels()
    quadrant = [{"npi": s["_id"], "name": s["name"], "stat": s["score_statistical"], "graph": s["score_graph"],
                 "score": s["score"], "truth": labels.get(s["_id"], {}).get("fraud_types", [])}
                for s in db.provider_scores.find({}, {"name": 1, "score": 1, "score_statistical": 1, "score_graph": 1})]
    bench, seen = [], set()
    for b in db.bench_runs.find({}, {"per_seed": 0}).sort("finished_at", -1):
        if b["difficulty"] not in seen:  # latest run per difficulty
            seen.add(b["difficulty"])
            bench.append(b)
    return J({"evaluation": ev, "config": scoring.active_config(), "defaults": scoring.DEFAULT_WEIGHTS,
              "labels": scoring.LABELS, "graph_signals": sorted(scoring.GRAPH_SIGNALS), "quadrant": quadrant,
              "bench": bench})


class ScoringConfig(BaseModel):
    weights: dict[str, float] | None = None
    mode: Literal["noisy_or", "weighted_sum"] | None = None
    threshold: float | None = Field(None, ge=1, le=99)
    reset: bool = False


@app.put("/api/scoring/config")
def put_config(body: ScoringConfig):
    db = mongo_db()
    cur = scoring.active_config()
    if body.reset:
        new = {"weights": scoring.DEFAULT_WEIGHTS, "mode": "noisy_or", "threshold": get_settings().flag_threshold}
    else:
        weights = {**cur["weights"], **{k: max(0.0, min(0.99, v)) for k, v in (body.weights or {}).items()
                                        if k in scoring.DEFAULT_WEIGHTS}}
        new = {"weights": weights, "mode": body.mode or cur["mode"], "threshold": body.threshold or cur["threshold"]}
    db.scoring_config.update_one({"_id": "active"}, {"$set": {**new, "updated_at": datetime.now(timezone.utc)},
                                                     "$inc": {"version": 1}}, upsert=True)
    scoring.score_all()
    _cache.clear()
    return get_evaluation()


# ================================================================ internals
@app.get("/api/internals/mongo")
def internals_mongo():
    return J(cached("int-mongo", 3, internals.mongo_overview))


@app.get("/api/internals/neo4j")
def internals_neo4j():
    return J(cached("int-neo4j", 5, internals.neo4j_overview))


@app.get("/api/internals/explain")
def internals_explain():
    return J([{"key": k, "label": v["label"], "why": v["why"]} for k, v in internals.SHOWCASE.items()])


@app.post("/api/internals/explain/{key}")
def run_explain(key: str):
    if key not in internals.SHOWCASE:
        raise HTTPException(404, "Unknown query")
    return J({"indexed": internals.explain_query(key, False), "collscan": internals.explain_query(key, True)})


@app.post("/api/internals/traversal")
def run_traversal(npi: str | None = None, max_hops: int = Query(6, ge=1, le=8)):
    return J(internals.traversal_benchmark(npi, max_hops))


@app.get("/api/internals/cycles/{npi}")
def run_cycles(npi: str):
    return J(internals.cycles_through(npi))


@app.get("/api/internals/pipelines")
def pipeline_definitions():
    return J({name: fn() for name, fn in signals_mongo.PIPELINES.items()})


@app.get("/api/meta")
def meta():
    return J({"specialties": list(ref.SPECIALTIES), "metros": list(ref.METROS),
              "payers": {k: {"name": v[0], "format": v[1]} for k, v in ref.PAYERS.items()},
              "dataset": truth_db().meta.find_one({"_id": "meta"}), "threshold": scoring.active_config()["threshold"]})


# ================================================================ websocket
@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue()
    HUB.clients.add(q)
    try:
        await ws.send_text('{"type":"hello","data":{}}')
        while True:
            msg = await q.get()
            await ws.send_text(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        HUB.clients.discard(q)


# ================================================================ built frontend (production)
DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
