"""Change data capture: MongoDB change streams -> Neo4j referral graph.

* The graph is a DERIVED view of the authoritative claims in MongoDB.
* Snapshot handoff: record the cluster time, bulk-copy the claims, then open the change
  stream AT that time, so writes made during the copy are replayed, not lost.
* The stream is filtered on the server: updates touching only detector-owned fields
  (flags) are dropped before they ever reach the worker.
* Events are applied in micro-batches with idempotent UNWIND statements keyed on the
  claim id. The resume token is saved only after Neo4j commits, so delivery is
  at-least-once and every change takes effect exactly once.
* A token older than the oplog (ChangeStreamHistoryLost) triggers a fresh snapshot.
* MongoDB stays the strongly consistent source of truth; the graph lags by the pipeline
  latency, which is measured per batch and stored in a time-series collection.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections import OrderedDict
from datetime import datetime, timezone

from pymongo.errors import OperationFailure, PyMongoError

from .db import cypher, cypher_autocommit, mongo_db
from .settings import get_settings

log = logging.getLogger("ringlens.cdc")
STATE_ID = "neo4j"
WATCHED = ["claims", "providers"]
DETECTOR_FIELDS = ["flags", "flagged"]

SCHEMA = [
    "CREATE CONSTRAINT provider_npi IF NOT EXISTS FOR (p:Provider) REQUIRE p.npi IS UNIQUE",
    "CREATE CONSTRAINT patient_id IF NOT EXISTS FOR (p:Patient) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT bank_hash IF NOT EXISTS FOR (b:BankAccount) REQUIRE b.hash IS UNIQUE",
    "CREATE CONSTRAINT address_key IF NOT EXISTS FOR (a:Address) REQUIRE a.key IS UNIQUE",
    # Relationship uniqueness on the claim id makes the upserts idempotent by construction.
    "CREATE CONSTRAINT referred_claim IF NOT EXISTS FOR ()-[r:REFERRED]-() REQUIRE r.claim_id IS UNIQUE",
    "CREATE CONSTRAINT treated_claim IF NOT EXISTS FOR ()-[r:TREATED]-() REQUIRE r.claim_id IS UNIQUE",
    "CREATE INDEX provider_ring IF NOT EXISTS FOR (p:Provider) ON (p.ring_id)",
]

UPSERT_PROVIDERS = """
UNWIND $rows AS row
MERGE (p:Provider {npi: row.npi})
SET p.name = row.name, p.specialty = row.specialty, p.kind = row.kind, p.metro = row.metro,
    p.lat = row.lat, p.lon = row.lon
WITH p, row
OPTIONAL MATCH (p)-[old:PAID_TO|LOCATED_AT]->()
DELETE old
WITH DISTINCT p, row
MERGE (b:BankAccount {hash: row.bank_hash})
MERGE (a:Address {key: row.address_key})
MERGE (p)-[:PAID_TO]->(b)
MERGE (p)-[:LOCATED_AT]->(a)
"""

# Removing a claim's edges first makes an update that changes its providers or patient
# correct, and makes a redelivered event harmless.
DELETE_CLAIMS = """
UNWIND $ids AS cid
OPTIONAL MATCH ()-[r:REFERRED {claim_id: cid}]->()
DELETE r
WITH DISTINCT cid
OPTIONAL MATCH ()-[t:TREATED {claim_id: cid}]->()
DELETE t
"""

CREATE_CLAIMS = """
UNWIND $rows AS row
MERGE (prov:Provider {npi: row.rendering_npi})
MERGE (pat:Patient {id: row.patient_id})
CREATE (prov)-[:TREATED {claim_id: row.claim_id, date: row.date, amount: row.amount}]->(pat)
WITH row, prov
WHERE row.referring_npi IS NOT NULL
MERGE (ref:Provider {npi: row.referring_npi})
CREATE (ref)-[:REFERRED {claim_id: row.claim_id, date: row.date, amount: row.amount,
                         patient_id: row.patient_id}]->(prov)
"""


def provider_row(doc: dict) -> dict:
    lon, lat = (doc.get("location") or {}).get("coordinates", [None, None])
    return {"npi": doc["_id"], "name": doc["name"], "specialty": doc["specialty"], "kind": doc["kind"],
            "metro": doc["metro"], "lat": lat, "lon": lon, "bank_hash": doc["bank_hash"],
            "address_key": doc.get("address_key") or f"{doc['address']['line1']}|{doc['address']['zip']}".upper()}


def claim_row(doc: dict) -> dict:
    return {"claim_id": doc["_id"], "rendering_npi": doc["rendering_npi"], "referring_npi": doc.get("referring_npi"),
            "patient_id": doc["patient_id"], "date": doc["service_date"].date().isoformat(),
            "amount": float(doc["total_charge"])}


def ensure_graph_schema():
    for stmt in SCHEMA:
        try:
            cypher(stmt)
        except Exception as exc:  # relationship uniqueness needs 5.7+; fall back to an index
            log.warning("schema statement failed (%s); falling back", exc)
            if "REFERRED" in stmt:
                cypher("CREATE INDEX referred_claim_idx IF NOT EXISTS FOR ()-[r:REFERRED]-() ON (r.claim_id)")
            if "TREATED" in stmt:
                cypher("CREATE INDEX treated_claim_idx IF NOT EXISTS FOR ()-[r:TREATED]-() ON (r.claim_id)")


def wipe_graph():
    cypher_autocommit("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 20000 ROWS")


def apply_claims(rows: list[dict], fresh: bool = False):
    """Apply claim upserts. `fresh` skips the edge cleanup when loading an empty graph."""
    for i in range(0, len(rows), 2500):
        chunk = rows[i:i + 2500]
        if not fresh:
            cypher(DELETE_CLAIMS, ids=[r["claim_id"] for r in chunk])
        cypher(CREATE_CLAIMS, rows=chunk)


def apply_deletes(claim_ids: list[str]):
    for i in range(0, len(claim_ids), 2500):
        cypher(DELETE_CLAIMS, ids=claim_ids[i:i + 2500])


# ---------------------------------------------------------------- state
def state() -> dict:
    return mongo_db().cdc_offsets.find_one({"_id": STATE_ID}) or {}


def save_state(**fields):
    fields["updated_at"] = datetime.now(timezone.utc)
    inc = {"events_applied": fields.pop("applied")} if "applied" in fields else {}
    update = {"$set": fields}
    if inc:
        update["$inc"] = inc
    mongo_db().cdc_offsets.update_one({"_id": STATE_ID}, update, upsert=True)


def control() -> dict:
    return mongo_db().pipeline_control.find_one({"_id": STATE_ID}) or {}


def snapshot(epoch) -> dict:
    """Initial load. The cluster time is captured BEFORE reading, so the change stream
    opened afterwards replays anything written while the snapshot was running."""
    db = mongo_db()
    started = time.perf_counter()
    op_time = db.command("ping")["operationTime"]
    save_state(state="snapshotting", resume_token=None, epoch=epoch, events_applied=0)
    wipe_graph()
    ensure_graph_schema()
    providers = [provider_row(d) for d in db.providers.find({})]
    for i in range(0, len(providers), 2000):
        cypher(UPSERT_PROVIDERS, rows=providers[i:i + 2000])
    n, batch = 0, []
    projection = {"rendering_npi": 1, "referring_npi": 1, "patient_id": 1, "service_date": 1, "total_charge": 1}
    for doc in db.claims.find({"status": {"$ne": "void"}}, projection).batch_size(10000):
        batch.append(claim_row(doc))
        if len(batch) >= 10000:
            apply_claims(batch, fresh=True)
            n += len(batch)
            batch = []
            save_state(snapshot_progress=n)
    apply_claims(batch, fresh=True)
    n += len(batch)
    stats = {"providers": len(providers), "claims": n, "seconds": round(time.perf_counter() - started, 1),
             "completed_at": datetime.now(timezone.utc)}
    save_state(state="streaming", start_at_operation_time=op_time, snapshot=stats, snapshot_progress=n)
    log.info("snapshot done: %s", stats)
    return stats


def _stream_pipeline() -> list[dict]:
    """Server-side filter: drop updates whose changed fields are all detector-owned."""
    changed_non_detector = {"$size": {"$filter": {
        "input": {"$objectToArray": {"$ifNull": ["$updateDescription.updatedFields", {}]}},
        "cond": {"$not": [{"$in": [{"$arrayElemAt": [{"$split": ["$$this.k", "."]}, 0]}, DETECTOR_FIELDS]}]},
    }}}
    return [{"$match": {
        "ns.coll": {"$in": WATCHED},
        "operationType": {"$in": ["insert", "update", "replace", "delete"]},
        "$or": [{"operationType": {"$ne": "update"}}, {"$expr": {"$gt": [changed_non_detector, 0]}}],
    }}]


def open_stream(st: dict):
    kwargs = {"full_document": "updateLookup", "full_document_before_change": "whenAvailable",
              "max_await_time_ms": 100}
    if st.get("resume_token"):
        kwargs["resume_after"] = st["resume_token"]
    elif st.get("start_at_operation_time"):
        kwargs["start_at_operation_time"] = st["start_at_operation_time"]
    return mongo_db().watch(_stream_pipeline(), **kwargs)


class Batch:
    """Collapses events per document so only each claim's final state is applied."""

    def __init__(self):
        self.claims: OrderedDict[str, dict | None] = OrderedDict()
        self.providers: OrderedDict[str, dict] = OrderedDict()
        self.events = 0
        self.lags_ms: list[float] = []
        self.opened = time.monotonic()

    def add(self, change: dict):
        self.events += 1
        wall = change.get("wallTime")
        if wall is not None:
            if wall.tzinfo is None:
                wall = wall.replace(tzinfo=timezone.utc)
            self.lags_ms.append((datetime.now(timezone.utc) - wall).total_seconds() * 1000)
        coll, op = change["ns"]["coll"], change["operationType"]
        key = change["documentKey"]["_id"]
        doc = change.get("fullDocument")
        if coll == "providers":
            if doc is not None:
                self.providers[key] = provider_row(doc)
            return
        self.claims.pop(key, None)
        if op == "delete" or doc is None or doc.get("status") == "void":
            self.claims[key] = None
        else:
            self.claims[key] = claim_row(doc)

    def apply(self):
        if self.providers:
            cypher(UPSERT_PROVIDERS, rows=list(self.providers.values()))
        deletes = [k for k, v in self.claims.items() if v is None]
        upserts = [v for v in self.claims.values() if v is not None]
        if deletes:
            apply_deletes(deletes)
        if upserts:
            apply_claims(upserts)


def _record_metrics(batch: Batch, apply_ms: float):
    if not batch.events:
        return
    lags = sorted(batch.lags_ms) or [0.0]
    mongo_db().pipeline_metrics.insert_one({
        "ts": datetime.now(timezone.utc), "component": "cdc", "events": batch.events,
        "claims": len(batch.claims), "lag_ms_avg": round(sum(lags) / len(lags), 1),
        "lag_ms_max": round(lags[-1], 1), "apply_ms": round(apply_ms, 1)})


class Worker:
    """Single CDC consumer. Run under `supervise()` so a crash is followed by a restart."""

    def __init__(self):
        self.s = get_settings()
        self._ctl, self._ctl_at = {}, 0.0

    def ctl(self) -> dict:
        if time.monotonic() - self._ctl_at > 0.5:
            self._ctl, self._ctl_at = control(), time.monotonic()
        return self._ctl

    def run(self):
        log.info("CDC worker started (pid %s)", os.getpid())
        while True:
            ctl = self.ctl()
            if ctl.get("seeding") or not ctl.get("epoch"):
                save_state(state="waiting-for-data", heartbeat=datetime.now(timezone.utc))
                time.sleep(1)
                continue
            st = state()
            if st.get("epoch") != ctl["epoch"] or not (st.get("resume_token") or st.get("start_at_operation_time")):
                snapshot(ctl["epoch"])
                continue
            if ctl.get("paused"):
                save_state(state="paused", heartbeat=datetime.now(timezone.utc))
                time.sleep(0.5)
                continue
            try:
                self.stream(st)
            except OperationFailure as exc:
                if exc.code == 286:  # ChangeStreamHistoryLost: the token fell off the oplog
                    log.warning("resume token too old; re-snapshotting")
                    save_state(resume_token=None, start_at_operation_time=None, epoch=None)
                else:
                    raise

    def stream(self, st: dict):
        epoch = st["epoch"]
        save_state(state="streaming", pid=os.getpid())
        with open_stream(st) as stream:
            batch = Batch()
            last_beat = 0.0
            while True:
                change = stream.try_next()
                if change is not None:
                    batch.add(change)
                age_ms = (time.monotonic() - batch.opened) * 1000
                if batch.events and (batch.events >= self.s.cdc_batch_events or age_ms >= self.s.cdc_batch_ms
                                     or change is None):
                    self.flush(stream, batch)
                    batch = Batch()
                elif change is None and not batch.events:
                    batch.opened = time.monotonic()
                    if time.monotonic() - last_beat > 0.5:
                        # Idle: everything committed before `seen` has been consumed if one
                        # more poll comes back empty, so the barrier may advance to it.
                        seen = mongo_db().command("ping")["operationTime"]
                        nxt = stream.try_next()
                        if nxt is None:
                            save_state(resume_token=stream.resume_token, last_cluster_time=seen,
                                       heartbeat=datetime.now(timezone.utc), state="streaming")
                        else:
                            batch.add(nxt)
                        last_beat = time.monotonic()
                ctl = self.ctl()
                if ctl.get("crash") and batch.events == 0:
                    self.crash_mid_batch(stream)
                if ctl.get("paused") or ctl.get("seeding") or ctl.get("epoch") != epoch:
                    if batch.events:
                        self.flush(stream, batch)
                    return

    def flush(self, stream, batch: Batch):
        t0 = time.perf_counter()
        batch.apply()
        apply_ms = (time.perf_counter() - t0) * 1000
        cluster_time = mongo_db().command("ping")["operationTime"]
        lags = batch.lags_ms or [0.0]
        # The token is saved only after Neo4j has committed: at-least-once delivery.
        save_state(resume_token=stream.resume_token, applied=batch.events, last_applied_at=datetime.now(timezone.utc),
                   last_cluster_time=cluster_time, heartbeat=datetime.now(timezone.utc), state="streaming",
                   last_batch={"events": batch.events, "claims": len(batch.claims), "apply_ms": round(apply_ms, 1),
                               "lag_ms_avg": round(sum(lags) / len(lags), 1), "lag_ms_max": round(max(lags), 1)})
        _record_metrics(batch, apply_ms)

    def crash_mid_batch(self, stream):
        """Demo switch: apply the next batch to Neo4j, then die BEFORE saving the token.
        The restarted worker receives those events again and the idempotent upserts
        absorb them, so no edge is duplicated."""
        batch, deadline = Batch(), time.monotonic() + 3
        while time.monotonic() < deadline and batch.events < 200:
            change = stream.try_next()
            if change is not None:
                batch.add(change)
        batch.apply()
        mongo_db().pipeline_control.update_one({"_id": STATE_ID}, {"$set": {"crash": False}})
        save_state(state="crashed", crashed_at=datetime.now(timezone.utc), crash_events_in_flight=batch.events)
        log.error("simulated crash with %d applied-but-unacknowledged events", batch.events)
        os._exit(3)


def run_worker():
    while True:
        try:
            Worker().run()
        except PyMongoError as exc:  # transient database trouble: back off and resume
            log.warning("CDC error: %s; retrying in 2 s", exc)
            time.sleep(2)


def supervise():
    """Keep a worker process alive, restarting it after a crash (like a container restart policy)."""
    while True:
        started = time.monotonic()
        code = subprocess.call([sys.executable, "-m", "ringlens.cli", "cdc", "--child"])
        log.warning("CDC worker exited with code %s; restarting", code)
        try:
            save_state(restarts_at=datetime.now(timezone.utc), last_exit_code=code)
        except PyMongoError:
            pass
        time.sleep(1 if time.monotonic() - started > 5 else 3)


def lag_report() -> dict:
    """Consistency check between the stores: claims vs TREATED edges, referral claims vs REFERRED edges."""
    db = mongo_db()
    st = state()
    graph = cypher("""
        CALL () { MATCH ()-[t:TREATED]->() RETURN count(t) AS treated }
        CALL () { MATCH ()-[r:REFERRED]->() RETURN count(r) AS referred }
        RETURN treated, referred""")[0]
    claims = db.claims.count_documents({"status": {"$ne": "void"}})
    referrals = db.claims.count_documents({"referring_npi": {"$type": "string"}, "status": {"$ne": "void"}})
    duplicates = cypher("""
        MATCH ()-[r:REFERRED]->() WITH r.claim_id AS c, count(*) AS n WHERE n > 1 RETURN count(c) AS dup""")[0]["dup"]
    return {"state": st.get("state"), "events_applied": st.get("events_applied", 0),
            "last_applied_at": st.get("last_applied_at"), "heartbeat": st.get("heartbeat"),
            "snapshot": st.get("snapshot"), "snapshot_progress": st.get("snapshot_progress"),
            "resume_token": (st.get("resume_token") or {}).get("_data"),
            "mongo_claims": claims, "graph_treated": graph["treated"],
            "mongo_referrals": referrals, "graph_referred": graph["referred"],
            "duplicate_edges": duplicates,
            "in_sync": claims == graph["treated"] and referrals == graph["referred"]}


def wait_for_barrier(timeout: float = 120.0) -> bool:
    """Cross-store barrier: wait until the worker has applied everything MongoDB had
    committed when this call started, so graph analytics sees every claim statistics see."""
    target = mongo_db().command("ping")["operationTime"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = state()
        lct = st.get("last_cluster_time")
        if st.get("state") == "streaming" and lct is not None and lct >= target:
            return True
        time.sleep(0.25)
    return False
