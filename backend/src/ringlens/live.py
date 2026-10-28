"""Live event hub for the dashboard.

The hub is itself a change-stream consumer: it watches the collections the analytics and
the CDC worker write to (jobs, rings, cdc_offsets, pipeline_control), so the UI updates
straight from the database with no separate message bus. Intake events (claims accepted
or rejected) come from the in-process intake listeners.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime

from bson import ObjectId, Timestamp

from . import intake
from .db import mongo_db

log = logging.getLogger("ringlens.live")


def jsonable(obj):
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, Timestamp):
        return {"t": obj.time, "i": obj.inc}
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


class Hub:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()
        self._stop = threading.Event()

    def start(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        intake.subscribe(lambda kind, data: self.publish(kind, data))
        threading.Thread(target=self._watch, daemon=True, name="live-hub").start()

    def stop(self):
        self._stop.set()

    def publish(self, kind: str, data: dict):
        if not self.loop or not self.clients:
            return
        msg = json.dumps({"type": kind, "at": time.time(), "data": jsonable(data)})
        for q in list(self.clients):
            self.loop.call_soon_threadsafe(_offer, q, msg)

    def _watch(self):
        pipeline = [{"$match": {"ns.coll": {"$in": ["jobs", "rings", "cdc_offsets", "pipeline_control"]},
                                "operationType": {"$in": ["insert", "update", "replace"]}}}]
        while not self._stop.is_set():
            try:
                with mongo_db().watch(pipeline, full_document="updateLookup", max_await_time_ms=500) as stream:
                    while not self._stop.is_set():
                        change = stream.try_next()
                        if change is not None:
                            self._route(change)
            except Exception as exc:  # database restarting, reseed in progress, ...
                log.warning("live hub stream error: %s", exc)
                time.sleep(2)

    def _route(self, change: dict):
        coll = change["ns"]["coll"]
        doc = change.get("fullDocument") or {}
        updated = set((change.get("updateDescription") or {}).get("updatedFields", {}).keys())
        if coll == "jobs":
            self.publish("job.progress", {k: doc.get(k) for k in ("_id", "status", "step", "steps", "summary",
                                                                  "seconds", "error", "trigger")})
        elif coll == "rings":
            if doc.get("detected") and doc.get("is_new"):
                self.publish("ring.detected", {k: doc.get(k) for k in ("_id", "members", "size", "suspicion",
                                                                       "internal_amount", "metrics")})
        elif coll == "cdc_offsets":
            if "last_batch" in updated or change["operationType"] != "update":
                self.publish("cdc.batch", {"state": doc.get("state"), "events_applied": doc.get("events_applied"),
                                           "batch": doc.get("last_batch"), "at": doc.get("last_applied_at")})
            elif "state" in updated or "snapshot_progress" in updated:
                self.publish("pipeline.state", {"state": doc.get("state"),
                                                "snapshot_progress": doc.get("snapshot_progress")})
        elif coll == "pipeline_control":
            self.publish("pipeline.control", {k: doc.get(k) for k in ("paused", "crash", "seeding")})


def _offer(q: asyncio.Queue, msg: str):
    if q.qsize() > 500:  # slow client: drop the oldest message rather than grow without bound
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(msg)


HUB = Hub()
