"""Database internals made visible: explain plans, index usage, topology, traversal costs."""
from __future__ import annotations

import time

from .db import cypher, mongo_client, mongo_db
from .settings import get_settings


def _samples() -> dict:
    """Realistic parameter values for the showcase queries, picked from the data."""
    db = mongo_db()
    busiest = next(db.claims.aggregate([{"$match": {"rendering_specialty": "Family Medicine"}},
                                        {"$sortByCount": "$rendering_npi"}, {"$limit": 1}]), {"_id": None})["_id"]
    patient = next(db.claims.aggregate([{"$sortByCount": "$patient_id"}, {"$limit": 1}]), {"_id": None})["_id"]
    return {"npi": busiest, "patient": patient}


SHOWCASE = {
    "provider_timeline": {
        "label": "Provider timeline (ESR compound)", "index": "esr_provider_date_charge",
        "why": "Equality on rendering_npi, sort on service_date, range on total_charge: the ESR order lets "
               "one index seek return rows already sorted, without an in-memory sort.",
        "build": lambda s: ({"rendering_npi": s["npi"], "total_charge": {"$gt": 100}}, {"service_date": -1}, 50)},
    "flagged_queue": {
        "label": "Analyst queue (partial index)", "index": "partial_flagged_queue",
        "why": "Only flagged claims are in this index, so it stays tiny however large the collection grows. "
               "The query must include flagged: true to be eligible.",
        "build": lambda s: ({"flagged": True, "flags": "duplicate"}, {"service_date": -1}, 100)},
    "cpt_lookup": {
        "label": "Procedure lookup (multikey)", "index": "multikey_cpt_date",
        "why": "One index entry per embedded line item, so a query can reach inside the line_items array.",
        "build": lambda s: ({"line_items.cpt": "93306"}, {"service_date": -1}, 100)},
    "patient_timeline": {
        "label": "Patient history (compound)", "index": "esr_patient_date",
        "why": "Every claim of one member, newest first: equality then sort.",
        "build": lambda s: ({"patient_id": s["patient"]}, {"service_date": -1}, 100)},
}
_sample_cache: dict = {}


def _walk(plan: dict, stages: list[str], indexes: list[str]):
    if not isinstance(plan, dict):
        return
    if "stage" in plan:
        stages.append(plan["stage"])
    if "indexName" in plan:
        indexes.append(plan["indexName"])
    for key in ("inputStage", "queryPlan"):
        if key in plan:
            _walk(plan[key], stages, indexes)
    for child in plan.get("inputStages", []) + plan.get("shards", []):
        _walk(child.get("winningPlan", child), stages, indexes)


def explain_query(key: str, collscan: bool = False) -> dict:
    if not _sample_cache:
        _sample_cache.update(_samples())
    spec = SHOWCASE[key]
    flt, sort, limit = spec["build"](_sample_cache)
    cmd = {"find": "claims", "filter": flt, "sort": sort, "limit": limit}
    cmd["hint"] = {"$natural": 1} if collscan else spec["index"]
    t0 = time.perf_counter()
    out = mongo_db().command("explain", cmd, verbosity="executionStats")
    wall = (time.perf_counter() - t0) * 1000
    stats = out["executionStats"]
    stages, indexes = [], []
    _walk(out["queryPlanner"]["winningPlan"], stages, indexes)
    return {"key": key, "label": spec["label"], "why": spec["why"], "filter": flt, "sort": sort,
            "forced_collscan": collscan, "stage": " → ".join(reversed(stages)), "index": indexes[0] if indexes else None,
            "returned": stats["nReturned"], "keys_examined": stats["totalKeysExamined"],
            "docs_examined": stats["totalDocsExamined"], "ms": stats["executionTimeMillis"],
            "wall_ms": round(wall, 1), "server": out.get("serverInfo", {}).get("host")}


def mongo_overview() -> dict:
    db, client = mongo_db(), mongo_client()
    hello = client.admin.command("hello")
    build = client.admin.command("buildInfo")
    info = {"version": build["version"], "topology": "sharded" if hello.get("msg") == "isdbgrid" else "replica_set",
            "set_name": hello.get("setName"), "primary": hello.get("primary"), "hosts": hello.get("hosts", [])}
    try:
        rs = client.admin.command("replSetGetStatus")
        primary_optime = next((m["optimeDate"] for m in rs["members"] if m["stateStr"] == "PRIMARY"), None)
        info["members"] = [{"name": m["name"], "state": m["stateStr"], "health": m.get("health"),
                            "uptime": m.get("uptime"),
                            "lag_s": (primary_optime - m["optimeDate"]).total_seconds()
                            if primary_optime and m.get("optimeDate") else None} for m in rs["members"]]
        oplog = client.local.command("collStats", "oplog.rs")
        info["oplog_mb"] = round(oplog.get("maxSize", 0) / 1e6)
    except Exception:
        info["members"] = []
    collections = []
    for name in ["claims", "providers", "patients", "claims_staging", "provider_signals", "provider_scores",
                 "rings", "referral_edges", "pipeline_metrics", "score_history", "cases", "audit_log"]:
        try:
            st = next(db[name].aggregate([{"$collStats": {"storageStats": {}}}]))["storageStats"]
        except Exception:
            continue
        opts = db[name].options()
        idx_usage = {i["name"]: i["accesses"]["ops"] for i in db[name].aggregate([{"$indexStats": {}}])} \
            if "timeseries" not in opts else {}
        indexes = []
        for idx in db[name].list_indexes():
            kind = []
            if "partialFilterExpression" in idx:
                kind.append("partial")
            if "expireAfterSeconds" in idx:
                kind.append(f"TTL {idx['expireAfterSeconds']}s")
            if "text" in idx["key"].values():
                kind.append("text")
            if any(k.startswith("line_items.") for k in idx["key"]):
                kind.append("multikey")
            if len(idx["key"]) > 1 and "text" not in idx["key"].values():
                kind.append("compound")
            if idx.get("unique"):
                kind.append("unique")
            indexes.append({"name": idx["name"], "keys": dict(idx["key"]), "kind": kind,
                            "partial": idx.get("partialFilterExpression"),
                            "size_mb": round(st.get("indexSizes", {}).get(idx["name"], 0) / 1e6, 2),
                            "ops": idx_usage.get(idx["name"])})
        # $collStats on a time-series collection counts storage buckets, not measurements.
        count = db[name].count_documents({}) if "timeseries" in opts else st.get("count")
        collections.append({"name": name, "count": count, "size_mb": round(st.get("size", 0) / 1e6, 1),
                            "storage_mb": round(st.get("storageSize", 0) / 1e6, 1),
                            "avg_doc_bytes": st.get("avgObjSize"), "validator": "validator" in opts,
                            "timeseries": "timeseries" in opts, "indexes": indexes})
    info["collections"] = collections
    return info


def neo4j_overview() -> dict:
    comp = cypher("CALL dbms.components() YIELD versions, edition RETURN versions[0] AS version, edition")[0]
    counts = cypher("""
        CALL () { MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n }
        RETURN collect({label: label, count: n}) AS nodes""")[0]["nodes"]
    rels = cypher("""
        CALL db.relationshipTypes() YIELD relationshipType AS t
        CALL (t) { MATCH ()-[r]->() WHERE type(r) = t RETURN count(r) AS n }
        RETURN collect({type: t, count: n}) AS rels""")[0]["rels"]
    constraints = cypher("SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties "
                         "RETURN name, type, entityType, labelsOrTypes, properties")
    gds = cypher("RETURN gds.version() AS v")[0]["v"]
    return {**comp, "gds": gds, "nodes": counts, "relationships": rels, "constraints": constraints}


def traversal_benchmark(npi: str | None = None, max_hops: int = 6) -> dict:
    """k-hop referral reachability three ways: Neo4j (index-free adjacency, APOC BFS),
    MongoDB $graphLookup over the edge collection, and application-side joins (one query
    per hop, like a relational engine without recursive queries)."""
    db = mongo_db()
    if npi is None:
        top = next(db.referral_edges.aggregate([{"$group": {"_id": "$from", "n": {"$sum": "$count"}}},
                                                 {"$sort": {"n": -1}}, {"$limit": 1}]), None)
        npi = top["_id"] if top else None
    if npi is None:
        return {"npi": None, "rows": []}
    rows = []
    for k in range(1, max_hops + 1):
        t0 = time.perf_counter()
        neo = cypher("""
            MATCH (a:Provider {npi: $n})
            CALL apoc.path.subgraphNodes(a, {relationshipFilter: 'REFERS_TO>', maxLevel: $k}) YIELD node
            RETURN count(node) - 1 AS reached""", n=npi, k=k)[0]["reached"]
        neo_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        res = next(db.providers.aggregate([
            {"$match": {"_id": npi}},
            {"$graphLookup": {"from": "referral_edges", "startWith": "$_id", "connectFromField": "to",
                              "connectToField": "from", "as": "reach", "maxDepth": k - 1}},
            {"$project": {"reached": {"$size": {"$setDifference": [{"$setUnion": ["$reach.to"]}, [npi]]}}}}]))
        gl_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        seen, frontier, queries = {npi}, [npi], 0
        for _ in range(k):
            nxt = set()
            for i in range(0, len(frontier), 500):
                queries += 1
                for e in db.referral_edges.find({"from": {"$in": frontier[i:i + 500]}}, {"to": 1}):
                    if e["to"] not in seen:
                        nxt.add(e["to"])
            seen |= nxt
            frontier = list(nxt)
            if not frontier:
                break
        app_ms = (time.perf_counter() - t0) * 1000
        rows.append({"hops": k, "reached": neo, "reached_graphlookup": res["reached"], "reached_app": len(seen) - 1,
                     "neo4j_ms": round(neo_ms, 2), "graphlookup_ms": round(gl_ms, 2), "app_join_ms": round(app_ms, 2),
                     "app_round_trips": queries})
    return {"npi": npi, "rows": rows}


def cycles_through(npi: str, max_len: int = 6) -> dict:
    """Closed referral loops through one provider over strong edges: a pattern Cypher
    expresses directly and the document store cannot express natively."""
    s = get_settings()
    t0 = time.perf_counter()
    rows = cypher(f"""
        MATCH p = (a:Provider {{npi: $n}})-[:REFERS_TO*2..{int(max_len)}]->(a)
        WHERE all(r IN relationships(p) WHERE r.count >= $mc)
          AND size(apoc.coll.toSet([x IN nodes(p) | x.npi])) = length(p)
        RETURN [x IN nodes(p) | x.npi] AS loop LIMIT 25""", n=npi, mc=s.strong_min_referrals)
    return {"npi": npi, "loops": [r["loop"] for r in rows], "ms": round((time.perf_counter() - t0) * 1000, 2)}
