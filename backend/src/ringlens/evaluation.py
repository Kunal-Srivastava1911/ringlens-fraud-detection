"""Detection quality against the planted ground truth.

Provider level: precision/recall/F1 at the flag threshold, average precision (area under
the precision-recall curve), precision@k for analyst capacity, recall per fraud type and an
ablation that removes one signal at a time. Ring level: recovery of each planted ring by
member Jaccard. Claim level: precision/recall of each claim flag.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .db import mongo_db, truth_db
from .scoring import DEFAULT_WEIGHTS, GRAPH_SIGNALS, active_config, fuse

VARIANTS = {"fused": "score", "statistical": "score_statistical", "graph": "score_graph",
            "weighted_sum": "score_weighted_sum"}


def _prf(predicted: set, actual: set) -> dict:
    tp = len(predicted & actual)
    p = tp / len(predicted) if predicted else 0.0
    r = tp / len(actual) if actual else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "fp": len(predicted - actual), "fn": len(actual - predicted),
            "precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3)}


def average_precision(ranked: list[tuple[str, float]], actual: set) -> float:
    """Mean precision at the rank of each true positive (ties broken by order)."""
    hits, total = 0, 0.0
    for i, (npi, _) in enumerate(ranked, 1):
        if npi in actual:
            hits += 1
            total += hits / i
    return round(total / len(actual), 4) if actual else 0.0


def pr_curve(scored: list[tuple[str, float]], actual: set) -> list[dict]:
    points = []
    for t in range(0, 101, 2):
        pred = {n for n, s in scored if s >= t}
        if not pred:
            continue
        tp = len(pred & actual)
        points.append({"threshold": t, "precision": round(tp / len(pred), 3),
                       "recall": round(tp / len(actual), 3) if actual else 0.0})
    return points


def evaluate(store: bool = True) -> dict:
    db, truth = mongo_db(), truth_db()
    labels = {t["_id"]: t for t in truth.providers.find()}
    actual = set(labels)
    scores = list(db.provider_scores.find({}, {"signals": 1, "score": 1, "score_statistical": 1, "score_graph": 1,
                                                "score_weighted_sum": 1}))
    cfg = active_config()
    th = cfg["threshold"]

    variants = {}
    for name, field in VARIANTS.items():
        ranked = sorted(((s["_id"], s[field]) for s in scores), key=lambda x: -x[1])
        entry = {"average_precision": average_precision(ranked, actual),
                 "precision_at": {str(k): round(len({n for n, _ in ranked[:k]} & actual) / k, 3) for k in (25, 50)},
                 "pr_curve": pr_curve(ranked, actual)}
        if name != "weighted_sum":
            entry.update(_prf({n for n, s in ranked if s >= th}, actual))
        variants[name] = entry

    flagged = {s["_id"] for s in scores if s["score"] >= th}
    by_type: dict[str, dict] = {}
    for npi, t in labels.items():
        for ft in t["fraud_types"]:
            e = by_type.setdefault(ft, {"planted": 0, "detected": 0, "stat_detected": 0, "graph_detected": 0})
            e["planted"] += 1
            e["detected"] += npi in flagged
    stat_flag = {s["_id"] for s in scores if s["score_statistical"] >= th}
    graph_flag = {s["_id"] for s in scores if s["score_graph"] >= th}
    for npi, t in labels.items():
        for ft in t["fraud_types"]:
            by_type[ft]["stat_detected"] += npi in stat_flag
            by_type[ft]["graph_detected"] += npi in graph_flag
    for e in by_type.values():
        e["recall"] = round(e["detected"] / e["planted"], 3)

    # Ablation: drop one signal, re-fuse in memory, compare average precision.
    base_ap = variants["fused"]["average_precision"]
    ablation = []
    for sig in DEFAULT_WEIGHTS:
        keep = set(DEFAULT_WEIGHTS) - {sig}
        ranked = sorted(((s["_id"], fuse(s["signals"], cfg["weights"], keep)) for s in scores), key=lambda x: -x[1])
        ap = average_precision(ranked, actual)
        ablation.append({"signal": sig, "group": "graph" if sig in GRAPH_SIGNALS else "statistical",
                         "average_precision": ap, "delta": round(ap - base_ap, 4)})
    ablation.sort(key=lambda a: a["delta"])

    detected = [r for r in db.rings.find({"detected": True, "active": True})]
    planted = list(truth.rings.find())
    ring_rows = []
    for pr in planted:
        pm = set(pr["members"])
        best, best_id = 0.0, None
        for d in detected:
            j = len(pm & set(d["members"])) / len(pm | set(d["members"]))
            if j > best:
                best, best_id = j, d["_id"]
        ring_rows.append({"ring_id": pr["_id"], "size": len(pm), "camouflaged": pr.get("camouflaged"),
                          "source": pr.get("source", "seed"), "matched": best_id, "jaccard": round(best, 3),
                          "recovered": best >= 0.5, "exact": best >= 0.999})
    true_members = set().union(*(set(r["members"]) for r in planted)) if planted else set()
    found_members = set().union(*(set(r["members"]) for r in detected)) if detected else set()
    false_rings = [d["_id"] for d in detected
                   if not any(len(set(d["members"]) & set(p["members"])) for p in planted)]

    claim_truth = {c["_id"]: c["type"] for c in truth.claims.find({"type": {"$in": ["duplicate", "unbundling",
                                                                                    "upcoding", "phantom_billing"]}})}
    claim_eval = {}
    mapping = {"duplicate": "duplicate", "unbundling": "unbundling", "upcoding": "upcoding",
               "after_death": "phantom_billing"}
    for flag, ttype in mapping.items():
        flagged_ids = {c["_id"] for c in db.claims.find({"flagged": True, "flags": flag}, {"_id": 1})}
        true_ids = {k for k, v in claim_truth.items() if v == ttype}
        tp = len(flagged_ids & true_ids)
        claim_eval[flag] = {"flagged": len(flagged_ids), "precision": round(tp / len(flagged_ids), 3) if flagged_ids else None,
                            "recall": round(tp / len(true_ids), 3) if true_ids and flag != "after_death" else None}

    meta = truth.meta.find_one({"_id": "meta"}) or {}
    report = {
        "_id": "latest", "evaluated_at": datetime.now(timezone.utc), "threshold": th,
        "config_version": cfg["version"], "mode": cfg["mode"],
        "dataset": {k: meta.get(k) for k in ("seed", "profile", "difficulty", "claims", "providers")},
        "providers": len(scores), "fraudulent_providers": len(actual),
        "variants": variants, "by_type": by_type, "ablation": ablation,
        "rings": {"planted": len(planted), "detected": len(detected),
                  "recovered": sum(r["recovered"] for r in ring_rows), "exact": sum(r["exact"] for r in ring_rows),
                  "false_rings": false_rings, "detail": ring_rows,
                  "members": _prf(found_members, true_members)},
        "claims": claim_eval,
    }
    if store:
        db.evaluation_runs.replace_one({"_id": "latest"}, report, upsert=True)
    return report
