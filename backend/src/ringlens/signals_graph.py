"""Structural fraud signals computed in Neo4j with the Graph Data Science library.

* REFERS_TO aggregates the per-claim REFERRED edges into one weighted edge per pair.
* Strongly connected components on the STRONG referral graph (edges repeated often,
  across several patients) isolate closed referral loops: the structural definition of
  a ring. One-off referrals are ignored as ordinary noise.
* Louvain surfaces dense clusters; PageRank finds where value is funnelled; betweenness
  finds brokers bridging clusters.
* Node similarity over the bipartite Provider-TREATED->Patient graph finds pairs that
  share an implausible set of patients WITHOUT any referral edge between them.
* WCC over Provider-PAID_TO->BankAccount finds identity clusters.
* Ring analysis scores each candidate loop on how patients actually move through it, which
  separates rings from legitimate co-managing specialists who also refer in a cycle.

GDS is called through Cypher, so every algorithm is visible as a query. Results are
written back to the Provider nodes and materialized into MongoDB (`graph_signals`,
`rings`) for the scoring engine and the dashboard.
"""
from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, timezone

import numpy as np

from .db import cypher, cypher_autocommit, mongo_db
from .settings import get_settings

PROJECTIONS = ["rl_strong", "rl_referrals", "rl_referrals_u", "rl_bipartite", "rl_identity"]

ALGORITHMS = {
    "scc": "CALL gds.scc.write('rl_strong', {writeProperty: 'scc'}) YIELD componentCount RETURN componentCount",
    "louvain": ("CALL gds.louvain.write('rl_referrals_u', {writeProperty: 'community', "
                "relationshipWeightProperty: 'weight'}) YIELD communityCount, modularity "
                "RETURN communityCount, modularity"),
    "pagerank": ("CALL gds.pageRank.write('rl_referrals', {writeProperty: 'pagerank', "
                 "relationshipWeightProperty: 'weight'}) YIELD ranIterations RETURN ranIterations"),
    "betweenness": ("CALL gds.betweenness.write('rl_referrals', {writeProperty: 'betweenness'}) "
                    "YIELD centralityDistribution RETURN centralityDistribution.max AS max"),
}


def _drop_projections():
    for name in PROJECTIONS:
        cypher("CALL gds.graph.drop($name, false) YIELD graphName RETURN graphName", name=name)


def build_refers_to() -> dict:
    cypher_autocommit("MATCH ()-[x:REFERS_TO|SHARES_PATIENTS]->() CALL (x) { DELETE x } IN TRANSACTIONS OF 20000 ROWS")
    return cypher("""
        MATCH (a:Provider)-[r:REFERRED]->(b:Provider)
        WITH a, b, count(r) AS c, sum(r.amount) AS amt, count(DISTINCT r.patient_id) AS pts
        CREATE (a)-[:REFERS_TO {count: c, amount: round(amt, 2), patients: pts}]->(b)
        RETURN count(*) AS edges""")[0]


def project_graphs() -> dict:
    s = get_settings()
    _drop_projections()
    out = {}
    out["strong"] = cypher("""
        MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
        WHERE r.count >= $mc AND r.patients >= $mp
        WITH gds.graph.project('rl_strong', a, b, {relationshipProperties: {weight: toFloat(r.count)}}) AS g
        RETURN g.nodeCount AS nodes, g.relationshipCount AS rels""", mc=s.strong_min_referrals,
                           mp=s.strong_min_patients)[0]
    out["referrals"] = cypher("""
        MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
        WITH gds.graph.project('rl_referrals', a, b, {relationshipProperties: {weight: toFloat(r.count)}}) AS g
        RETURN g.nodeCount AS nodes, g.relationshipCount AS rels""")[0]
    cypher("""
        MATCH (a:Provider)-[r:REFERS_TO]->(b:Provider)
        WITH gds.graph.project('rl_referrals_u', a, b, {relationshipProperties: {weight: toFloat(r.count)}},
                               {undirectedRelationshipTypes: ['*']}) AS g
        RETURN g.nodeCount AS nodes""")
    out["bipartite"] = cypher("""
        MATCH (p:Provider)-[t:TREATED]->(pt:Patient)
        WITH p, pt, count(t) AS w
        WITH gds.graph.project('rl_bipartite', p, pt, {sourceNodeLabels: ['Provider'], targetNodeLabels: ['Patient'],
                               relationshipProperties: {weight: toFloat(w)}}) AS g
        RETURN g.nodeCount AS nodes, g.relationshipCount AS rels""")[0]
    out["identity"] = cypher("""
        MATCH (p:Provider)-[:PAID_TO]->(b:BankAccount)
        WITH gds.graph.project('rl_identity', p, b, {}, {undirectedRelationshipTypes: ['*']}) AS g
        RETURN g.nodeCount AS nodes, g.relationshipCount AS rels""")[0]
    return out


def run_algorithms() -> dict:
    cypher("MATCH (p:Provider) REMOVE p.scc, p.community, p.pagerank, p.betweenness, p.ring_id, p.ring_suspicion")
    timings = {}
    for name, query in ALGORITHMS.items():
        t0 = time.perf_counter()
        cypher(query)
        timings[name] = round(time.perf_counter() - t0, 3)
    return timings


def shared_patients(cutoff: float = 0.08) -> list[dict]:
    """Provider pairs with heavily overlapping patient sets and NO referral edge between them."""
    pairs = cypher("""
        CALL gds.nodeSimilarity.stream('rl_bipartite', {topK: 3, similarityCutoff: $cutoff, degreeCutoff: 15,
                                                         similarityMetric: 'JACCARD'})
        YIELD node1, node2, similarity
        WITH gds.util.asNode(node1) AS a, gds.util.asNode(node2) AS b, similarity
        WHERE a.npi < b.npi AND NOT EXISTS { (a)-[:REFERS_TO]-(b) }
        MERGE (a)-[s:SHARES_PATIENTS]->(b)
        SET s.jaccard = similarity
        RETURN a.npi AS a, b.npi AS b, similarity AS jaccard""", cutoff=cutoff)
    shared_counts = cypher("""
        MATCH (a:Provider)-[s:SHARES_PATIENTS]->(b:Provider)
        CALL (a, b) { MATCH (a)-[:TREATED]->(pt:Patient)<-[:TREATED]-(b) RETURN count(DISTINCT pt) AS shared }
        SET s.shared = shared
        RETURN a.npi AS a, b.npi AS b, shared""")
    counts = {(r["a"], r["b"]): r["shared"] for r in shared_counts}
    for p in pairs:
        p["shared"] = counts.get((p["a"], p["b"]), 0)
    return pairs


def identity_clusters() -> dict[str, int]:
    rows = cypher("""
        CALL gds.wcc.stream('rl_identity') YIELD nodeId, componentId
        WITH gds.util.asNode(nodeId) AS n, componentId WHERE n:Provider
        WITH componentId, collect(n.npi) AS members WHERE size(members) > 1
        RETURN members""")
    out = {}
    for r in rows:
        for m in r["members"]:
            out[m] = len(r["members"])
    return out


# ------------------------------------------------------------------ ring analysis
def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def analyse_candidate(members: list[str]) -> dict:
    """Score one closed referral loop on how patients actually move through it."""
    edges = cypher("""
        MATCH (a:Provider)-[r:REFERRED]->(b:Provider)
        WHERE a.npi IN $m AND b.npi IN $m
        RETURN a.npi AS src, b.npi AS dst, r.patient_id AS patient, r.date AS date, r.amount AS amount""", m=members)
    volume = cypher("""
        MATCH (a:Provider)-[r:REFERS_TO]-(:Provider) WHERE a.npi IN $m
        RETURN sum(r.count) AS touching""", m=members)[0]["touching"] or 0
    info = cypher("""
        MATCH (p:Provider) WHERE p.npi IN $m
        OPTIONAL MATCH (p)-[:PAID_TO]->(b:BankAccount)<-[:PAID_TO]-(o:Provider) WHERE o.npi IN $m
        OPTIONAL MATCH (p)-[:LOCATED_AT]->(ad:Address)<-[:LOCATED_AT]-(o2:Provider) WHERE o2.npi IN $m
        RETURN p.npi AS npi, p.name AS name, p.specialty AS specialty, p.metro AS metro, p.pagerank AS pagerank,
               count(DISTINCT o) AS bank_peers, count(DISTINCT o2) AS address_peers""", m=members)

    n = len(members)
    internal = len(edges)
    by_patient: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    pair_count: dict[tuple[str, str], int] = defaultdict(int)
    pair_amount: dict[tuple[str, str], float] = defaultdict(float)
    for e in edges:
        by_patient[e["patient"]].append((e["date"], e["src"], e["dst"]))
        pair_count[(e["src"], e["dst"])] += 1
        pair_amount[(e["src"], e["dst"])] += e["amount"] or 0.0
    touched = [len({x for _, s, d in hops for x in (s, d)}) for hops in by_patient.values()]
    recirculation = sum(t >= 3 for t in touched) / len(touched) if touched else 0.0
    referrals_per_patient = internal / len(by_patient) if by_patient else 0.0
    gaps = []
    for hops in by_patient.values():
        days = sorted(date.fromisoformat(h[0]) for h in hops)
        gaps += [(b - a).days for a, b in zip(days, days[1:], strict=False)]
    median_gap = statistics.median(gaps) if gaps else None
    compactness = 0.0 if median_gap is None else _clip(1 - median_gap / 60)
    # Internal edges counted once per endpoint in `volume`, so compare like with like.
    internal_share = (2 * internal / volume) if volume else 0.0
    density = len(pair_count) / (n * (n - 1)) if n > 1 else 0.0
    reciprocal = sum(1 for (a, b) in pair_count if (b, a) in pair_count) / len(pair_count) if pair_count else 0.0
    identity = sum(1 for r in info if r["bank_peers"] > 0) / n
    address = sum(1 for r in info if r["address_peers"] > 0) / n
    pr = {r["npi"]: r["pagerank"] or 0.0 for r in info}
    total_pr = sum(pr.values()) or 1.0
    hub = max(pr, key=pr.get) if pr else members[0]

    suspicion = (0.45 * recirculation + 0.15 * _clip(referrals_per_patient / 4) + 0.15 * compactness
                 + 0.15 * _clip(internal_share / 0.3) + 0.10 * _clip(identity + 0.5 * address))
    # Order the members around the loop for display: follow the heaviest outgoing edge.
    order, current = [hub], hub
    while len(order) < n:
        nxt = sorted(((c, d) for (s, d), c in pair_count.items() if s == current and d not in order), reverse=True)
        current = nxt[0][1] if nxt else next(m for m in members if m not in order)
        order.append(current)
    dates = sorted(e["date"] for e in edges)
    return {
        "members": order, "size": n, "hub": hub, "hub_share": round(pr.get(hub, 0) / total_pr, 3),
        "metrics": {"recirculation": round(recirculation, 3), "referrals_per_patient": round(referrals_per_patient, 2),
                    "median_gap_days": median_gap, "compactness": round(compactness, 3),
                    "internal_share": round(internal_share, 3), "density": round(density, 3),
                    "reciprocity": round(reciprocal, 3), "shared_bank": round(identity, 3),
                    "shared_address": round(address, 3)},
        "suspicion": round(suspicion, 3),
        "internal_referrals": internal, "patients": len(by_patient),
        "internal_amount": round(sum(e["amount"] or 0 for e in edges), 2),
        "flows": [{"source": s, "target": d, "count": c, "amount": round(pair_amount[(s, d)], 2)}
                  for (s, d), c in sorted(pair_count.items(), key=lambda kv: -kv[1])],
        "first_activity": dates[0] if dates else None, "last_activity": dates[-1] if dates else None,
        "member_info": [{"npi": r["npi"], "name": r["name"], "specialty": r["specialty"], "metro": r["metro"],
                         "pagerank": r["pagerank"], "shares_bank": r["bank_peers"] > 0,
                         "shares_address": r["address_peers"] > 0} for r in info],
    }


def detect_rings() -> list[dict]:
    """SCCs of 3+ providers are candidates; each is analysed and given a stable id."""
    s = get_settings()
    comps = cypher("""
        MATCH (p:Provider) WHERE p.scc IS NOT NULL
        WITH p.scc AS c, collect(p.npi) AS members WHERE size(members) >= 3
        RETURN c, members ORDER BY size(members) DESC""")
    candidates = [analyse_candidate(c["members"]) for c in comps]

    db = mongo_db()
    previous = list(db.rings.find({}))
    now = datetime.now(timezone.utc)
    used, out = set(), []
    for cand in candidates:
        mset = set(cand["members"])
        best, best_j = None, 0.0
        for prev in previous:
            if prev["_id"] in used:
                continue
            j = len(mset & set(prev["members"])) / len(mset | set(prev["members"]))
            if j > best_j:
                best, best_j = prev, j
        detected = cand["suspicion"] >= s.ring_threshold
        if best is not None and best_j >= 0.5:
            used.add(best["_id"])
            ring_id = best["_id"]
            first_seen = best.get("first_detected_at") or (now if detected else None)
            status = best.get("status", "new")
            is_new = detected and not best.get("detected")
        else:
            seq = db.counters.find_one_and_update({"_id": "ring"}, {"$inc": {"seq": 1}}, upsert=True,
                                                  return_document=True)["seq"]
            ring_id = f"RING-{seq:04d}"
            first_seen = now if detected else None
            status, is_new = "new", detected
        out.append({"_id": ring_id, **cand, "detected": detected, "active": True, "status": status,
                    "is_new": is_new, "first_detected_at": first_seen, "analysed_at": now})
    stale = [p["_id"] for p in previous if p["_id"] not in used]
    if stale:
        db.rings.update_many({"_id": {"$in": stale}}, {"$set": {"active": False, "detected": False, "is_new": False}})
    for ring in out:
        db.rings.replace_one({"_id": ring["_id"]}, ring, upsert=True)
        if ring["detected"]:
            cypher("MATCH (p:Provider) WHERE p.npi IN $m SET p.ring_id = $r, p.ring_suspicion = $s",
                   m=ring["members"], r=ring["_id"], s=ring["suspicion"])
    return out


# ------------------------------------------------------------------ export
def export_signals(rings: list[dict], pairs: list[dict], identity: dict[str, int]) -> int:
    rows = cypher("""
        MATCH (p:Provider)
        OPTIONAL MATCH (p)-[o:REFERS_TO]->() WITH p, sum(o.count) AS out_refs, count(o) AS out_degree
        OPTIONAL MATCH ()-[i:REFERS_TO]->(p) WITH p, out_refs, out_degree, sum(i.count) AS in_refs, count(i) AS in_degree
        RETURN p.npi AS npi, p.specialty AS specialty, p.scc AS scc, p.community AS community,
               coalesce(p.pagerank, 0.0) AS pagerank, coalesce(p.betweenness, 0.0) AS betweenness,
               out_refs, in_refs, out_degree, in_degree""")
    community_size: dict = defaultdict(int)
    for r in rows:
        community_size[r["community"]] += 1
    # Betweenness relative to specialty peers (log scale): specialists naturally sit between
    # PCPs and ancillary services, so only an outlier within the specialty is suspicious.
    by_spec: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_spec[r["specialty"]].append(math.log1p(r["betweenness"]))
    spec_stats = {k: (float(np.mean(v)), float(np.std(v))) for k, v in by_spec.items()}

    member_ring: dict[str, dict] = {}
    for ring in rings:
        if ring["detected"]:
            top = max((m["pagerank"] or 0) for m in ring["member_info"]) or 1.0
            for m in ring["member_info"]:
                member_ring[m["npi"]] = {"ring_id": ring["_id"], "ring_suspicion": ring["suspicion"],
                                         "ring_size": ring["size"], "ring_hub": ring["hub"] == m["npi"],
                                         "ring_hub_share": round((m["pagerank"] or 0) / top, 3)}
    best_pair: dict[str, dict] = {}
    for p in pairs:
        for me, other in ((p["a"], p["b"]), (p["b"], p["a"])):
            if p["jaccard"] > best_pair.get(me, {}).get("jaccard", 0):
                best_pair[me] = {"jaccard": round(p["jaccard"], 3), "shared": p["shared"], "with": other}

    now = datetime.now(timezone.utc)
    docs = []
    for r in rows:
        mean, std = spec_stats[r["specialty"]]
        bz = (math.log1p(r["betweenness"]) - mean) / std if std > 0 else 0.0
        bp = best_pair.get(r["npi"], {})
        docs.append({
            "_id": r["npi"], "specialty": r["specialty"], "scc": r["scc"], "community": r["community"],
            "community_size": community_size[r["community"]], "pagerank": round(r["pagerank"], 4),
            "betweenness": round(r["betweenness"], 1), "betweenness_z": round(bz, 3),
            "out_refs": r["out_refs"], "in_refs": r["in_refs"], "out_degree": r["out_degree"],
            "in_degree": r["in_degree"], **member_ring.get(r["npi"], {"ring_id": None}),
            "shared_patients_jaccard": bp.get("jaccard", 0.0), "shared_patients": bp.get("shared", 0),
            "shares_patients_with": bp.get("with"), "identity_cluster": identity.get(r["npi"], 1),
            "computed_at": now,
        })
    db = mongo_db()
    db.graph_signals.delete_many({})
    if docs:
        db.graph_signals.insert_many(docs)
    return len(docs)


def compute_all(progress=None) -> dict:
    step = progress or (lambda *_: None)
    t0 = time.perf_counter()
    step("graph.refers_to")
    agg = build_refers_to()
    step("graph.project")
    projections = project_graphs()
    step("graph.algorithms")
    timings = run_algorithms()
    step("graph.similarity")
    t1 = time.perf_counter()
    pairs = shared_patients()
    timings["node_similarity"] = round(time.perf_counter() - t1, 3)
    identity = identity_clusters()
    step("graph.rings")
    t1 = time.perf_counter()
    rings = detect_rings()
    timings["ring_analysis"] = round(time.perf_counter() - t1, 3)
    exported = export_signals(rings, pairs, identity)
    _drop_projections()
    return {"refers_to_edges": agg["edges"], "projections": projections, "timings": timings,
            "candidates": len(rings), "rings_detected": sum(r["detected"] for r in rings),
            "new_rings": [r["_id"] for r in rings if r["is_new"]], "shared_patient_pairs": len(pairs),
            "providers_exported": exported, "seconds": round(time.perf_counter() - t0, 2)}
