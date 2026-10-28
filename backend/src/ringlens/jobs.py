"""Analytics run: MongoDB signals -> cross-store barrier -> Neo4j graph signals -> fusion -> evaluation.

Progress is recorded in the `jobs` collection; the API's live hub streams those updates
to the dashboard over WebSocket (it watches the collection with a change stream).
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timezone

from . import cdc, evaluation, scoring, signals_graph, signals_mongo
from .db import mongo_db

STEPS = ["barrier", "mongo", "graph", "score", "evaluate"]
_lock = threading.Lock()


def running() -> bool:
    return _lock.locked()


def analyze(trigger: str = "manual") -> dict:
    """Run the full analytics pipeline once. Raises RuntimeError if one is already running."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("An analytics run is already in progress")
    db = mongo_db()
    job_id = f"job-{int(time.time() * 1000)}"
    started = time.perf_counter()
    db.jobs.insert_one({"_id": job_id, "kind": "analyze", "trigger": trigger, "status": "running",
                        "step": None, "steps": {}, "started_at": datetime.now(timezone.utc)})

    def mark(step: str, status: str, **extra):
        db.jobs.update_one({"_id": job_id}, {"$set": {"step": step, f"steps.{step}": {"status": status, **extra}}})

    result: dict = {"job_id": job_id}
    try:
        for step in STEPS:
            mark(step, "running")
            t0 = time.perf_counter()
            if step == "barrier":
                # The graph must contain every claim the statistics are about to see.
                result["barrier_ok"] = cdc.wait_for_barrier()
            elif step == "mongo":
                result["mongo"] = signals_mongo.compute_all()
            elif step == "graph":
                result["graph"] = signals_graph.compute_all()
            elif step == "score":
                result["score"] = scoring.score_all()
            elif step == "evaluate":
                ev = evaluation.evaluate()
                result["evaluation"] = {k: {m: v[m] for m in ("average_precision", "precision", "recall", "f1") if m in v}
                                        for k, v in ev["variants"].items()}
                result["rings"] = {k: ev["rings"][k] for k in ("planted", "detected", "recovered", "exact")}
            mark(step, "done", seconds=round(time.perf_counter() - t0, 2))
        result["seconds"] = round(time.perf_counter() - started, 2)
        db.jobs.update_one({"_id": job_id}, {"$set": {
            "status": "done", "finished_at": datetime.now(timezone.utc), "seconds": result["seconds"],
            "summary": {"rings_detected": result["graph"]["rings_detected"], "new_rings": result["graph"]["new_rings"],
                        "flagged": result["score"]["flagged"]}}})
        return result
    except Exception as exc:
        db.jobs.update_one({"_id": job_id}, {"$set": {"status": "failed", "error": repr(exc),
                                                      "trace": traceback.format_exc()[-2000:]}})
        raise
    finally:
        _lock.release()
