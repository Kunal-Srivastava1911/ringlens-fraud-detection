"""Composite fraud scoring: fuses MongoDB statistical signals with Neo4j structural signals.

Neither source is sufficient alone: a lone upcoder belongs to no ring, while a ring of
individually modest billers looks ordinary claim by claim. Each signal is normalized to
[0, 1] and the default fusion is a noisy-OR,

    score = 1 - prod(1 - w_i * s_i)

so one decisive signal is never diluted by quiet ones and corroborating evidence
accumulates. w_i is the most a single signal can contribute on its own. Every score
carries per-signal attributions (shares in log space) and plain-English reasons.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .db import mongo_db
from .settings import get_settings

DEFAULT_WEIGHTS = {
    # graph (Neo4j)
    "ring": 0.90,
    "centrality": 0.50,
    "shared_patients": 0.65,
    # statistical (MongoDB)
    "billing": 0.50,
    "upcoding": 0.75,
    "velocity": 0.75,
    "phantom": 0.85,
    "claim_integrity": 0.75,
    "shared_identity": 0.60,
}
GRAPH_SIGNALS = {"ring", "centrality", "shared_patients"}
STAT_SIGNALS = set(DEFAULT_WEIGHTS) - GRAPH_SIGNALS
LABELS = {
    "ring": "Referral ring", "centrality": "Network centrality", "shared_patients": "Shared patients",
    "billing": "Peer-relative billing", "upcoding": "Upcoding", "velocity": "Billing velocity",
    "phantom": "Services after death", "claim_integrity": "Duplicates / unbundling",
    "shared_identity": "Shared identity",
}


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ramp(x, start: float, full: float) -> float:
    """0 below `start`, rising linearly to 1 at `full`."""
    return _clip(((x or 0.0) - start) / (full - start))


def signal_vector(stat: dict, graph: dict) -> tuple[dict[str, float], dict[str, str]]:
    """Normalize raw signals to [0, 1] and explain each one that fired."""
    s: dict[str, float] = {}
    why: dict[str, str] = {}

    in_ring = bool(graph.get("ring_id"))
    s["ring"] = graph.get("ring_suspicion", 0.0) if in_ring else 0.0
    if in_ring:
        why["ring"] = (f"Member of referral ring {graph['ring_id']} ({graph.get('ring_size')} providers, "
                       f"suspicion {graph.get('ring_suspicion', 0):.2f})")

    bz = graph.get("betweenness_z", 0.0) or 0.0
    hub = graph.get("ring_hub_share", 0.0) if in_ring else 0.0
    s["centrality"] = max(hub if graph.get("ring_hub") else 0.6 * hub, _ramp(bz, 2.5, 4.5))
    if in_ring and graph.get("ring_hub"):
        why["centrality"] = "Hub of the ring: highest PageRank, referral value funnels here"
    elif s["centrality"] > 0 and bz >= 2.5:
        why["centrality"] = f"Broker position: betweenness {bz:.1f} standard deviations above specialty peers"

    jac = graph.get("shared_patients_jaccard", 0.0) or 0.0
    s["shared_patients"] = _ramp(jac, 0.12, 0.30)
    if s["shared_patients"] > 0:
        why["shared_patients"] = (f"Shares {graph.get('shared_patients')} patients with {graph.get('shares_patients_with')} "
                                  f"with no referral between them (Jaccard {jac:.2f})")

    z_bill = max(stat.get("z_avg_claim", 0) or 0, stat.get("z_billed_per_patient", 0) or 0)
    s["billing"] = _ramp(z_bill, 2.0, 4.0)
    if s["billing"] > 0:
        why["billing"] = f"Bills {z_bill:.1f} standard deviations above {stat.get('specialty')} peers"

    z_em = stat.get("z_top_em", 0) or 0
    s["upcoding"] = _ramp(z_em, 1.5, 3.5)
    if s["upcoding"] > 0:
        why["upcoding"] = (f"{stat.get('top_em_ratio', 0):.0%} of office visits billed at the top level (99215), "
                           f"z = {z_em:.1f} within specialty")

    imp = stat.get("impossible_days", 0) or 0
    spike = stat.get("max_velocity_spike", 0) or 0
    s["velocity"] = max(_clip(imp / 2), 0.5 * _ramp(spike, 4, 12))
    if imp:
        why["velocity"] = (f"{imp} day(s) with more than 16 hours of billed face time "
                           f"(peak {stat.get('max_daily_minutes', 0) / 60:.0f} h)")
    elif s["velocity"] > 0:
        why["velocity"] = f"7-day billing spiked to {spike:.1f}x the provider's own 90-day rate"

    dead = stat.get("after_death_claims", 0) or 0
    s["phantom"] = _clip(dead / 3)
    if dead:
        why["phantom"] = f"{dead} claim(s) for services dated after the patient's death"

    dup, unb = stat.get("duplicate_claims", 0) or 0, stat.get("unbundled_claims", 0) or 0
    s["claim_integrity"] = max(_ramp(dup, 1, 6), _ramp(unb, 0, 15))
    parts = []
    if dup > 1:
        parts.append(f"{dup} duplicate claims (same patient, date and procedures)")
    if unb:
        parts.append(f"{unb} claims unbundle a lab panel into separately billed components")
    if parts and s["claim_integrity"] > 0:
        why["claim_integrity"] = "; ".join(parts)

    bank, addr = stat.get("shares_bank_with") or [], stat.get("shares_address_with") or []
    s["shared_identity"] = 1.0 if bank else (0.25 if addr else 0.0)
    if bank:
        why["shared_identity"] = f"Paid into the same bank account as {len(bank)} other provider(s)"
    elif addr:
        why["shared_identity"] = f"Shares a street address with {len(addr)} other provider(s)"
    return s, why


def fuse(signals: dict[str, float], weights: dict[str, float], only: set[str] | None = None,
         mode: str = "noisy_or") -> float:
    names = [n for n in signals if (only is None or n in only)]
    if mode == "weighted_sum":
        total_w = sum(weights[n] for n in names) or 1.0
        return round(100 * sum(weights[n] * signals[n] for n in names) / total_w, 2)
    remaining = 1.0
    for n in names:
        remaining *= 1.0 - weights[n] * signals[n]
    return round(100.0 * (1.0 - remaining), 2)


def attributions(signals: dict[str, float], weights: dict[str, float], score: float) -> dict[str, float]:
    """Split a noisy-OR score between signals in proportion to -log(1 - w*s)."""
    logs = {n: -math.log(max(1e-6, 1 - min(0.999999, weights[n] * v))) for n, v in signals.items()}
    total = sum(logs.values())
    if total <= 0:
        return {n: 0.0 for n in signals}
    return {n: round(score * v / total, 2) for n, v in logs.items()}


def risk_tier(score: float, threshold: float) -> str:
    if score >= 80:
        return "critical"
    if score >= 65:
        return "high"
    if score >= threshold:
        return "elevated"
    return "low"


def active_config() -> dict:
    cfg = mongo_db().scoring_config.find_one({"_id": "active"}) or {}
    weights = {**DEFAULT_WEIGHTS, **(cfg.get("weights") or {})}
    return {"version": cfg.get("version", 1), "weights": weights, "mode": cfg.get("mode", "noisy_or"),
            "threshold": cfg.get("threshold", get_settings().flag_threshold)}


def score_all(config: dict | None = None) -> dict:
    db = mongo_db()
    cfg = config or active_config()
    w, threshold, mode = cfg["weights"], cfg["threshold"], cfg["mode"]
    stats = {d["_id"]: d for d in db.provider_signals.find({}, {"duplicate_claim_ids": 0, "unbundled_claim_ids": 0,
                                                                 "after_death_claim_ids": 0})}
    graphs = {d["_id"]: d for d in db.graph_signals.find()}
    now = datetime.now(timezone.utc)
    docs = []
    for p in db.providers.find({}, {"name": 1, "specialty": 1, "metro": 1, "kind": 1, "location": 1}):
        npi = p["_id"]
        stat, graph = stats.get(npi, {}), graphs.get(npi, {})
        signals, why = signal_vector(stat, graph)
        score = fuse(signals, w, mode=mode)
        attr = attributions(signals, w, score) if mode == "noisy_or" else {
            n: round(w[n] * v, 3) for n, v in signals.items()}
        reasons = [why[n] for n in sorted(why, key=lambda n: -attr.get(n, 0)) if attr.get(n, 0) > 0.5 or n == "ring"]
        docs.append({
            "_id": npi, "name": p["name"], "specialty": p["specialty"], "metro": p["metro"], "kind": p["kind"],
            "location": p.get("location"), "score": score, "risk": risk_tier(score, threshold),
            "flagged": score >= threshold,
            "score_statistical": fuse(signals, w, STAT_SIGNALS, mode),
            "score_graph": fuse(signals, w, GRAPH_SIGNALS, mode),
            "score_weighted_sum": fuse(signals, w, mode="weighted_sum"),
            "signals": {k: round(v, 3) for k, v in signals.items()}, "attributions": attr,
            "reasons": reasons, "ring_id": graph.get("ring_id"), "community": graph.get("community"),
            "claims": stat.get("claims", 0), "total_billed": round(stat.get("total_billed", 0) or 0, 2),
            "patients": stat.get("unique_patients", 0), "config_version": cfg["version"], "scored_at": now,
        })
    docs.sort(key=lambda d: -d["score"])
    previous = {d["_id"]: d.get("score", 0) for d in db.provider_scores.find({}, {"score": 1})}
    for rank, d in enumerate(docs, 1):
        d["rank"] = rank
        d["score_delta"] = round(d["score"] - previous.get(d["_id"], d["score"]), 2)
    db.provider_scores.delete_many({})
    if docs:
        db.provider_scores.insert_many(docs)
        db.score_history.insert_many([{"ts": now, "npi": d["_id"], "score": d["score"]} for d in docs])
    return {"providers_scored": len(docs), "flagged": sum(d["flagged"] for d in docs),
            "critical": sum(d["risk"] == "critical" for d in docs), "threshold": threshold, "mode": mode}
