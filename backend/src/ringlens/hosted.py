"""One-process hosting for the live demo (Render free tier: a single web service).

Locally, start.ps1 runs the CDC worker as its own supervised process. A free web service
gets one process, so when RINGLENS_EMBED_CDC=1 the worker runs as a background thread of the
API instead. When RINGLENS_BOOTSTRAP_PROFILE is set and MongoDB has no claims, the first start
also seeds that synthetic world, waits for the worker's snapshot into Neo4j, and runs the
analytics once, so the console opens on real results. Restarts (free services sleep when
idle) resume from the stored change-stream token and skip the bootstrap.
"""
from __future__ import annotations

import logging
import threading
import time

from .settings import get_settings

log = logging.getLogger("ringlens.hosted")
STATUS: dict = {"bootstrap": "off"}


def start() -> None:
    s = get_settings()
    if s.embed_cdc:
        threading.Thread(target=_cdc_forever, name="cdc-worker", daemon=True).start()
    if s.bootstrap_profile:
        STATUS["bootstrap"] = "starting"
        threading.Thread(target=_bootstrap, name="bootstrap", daemon=True).start()


def _cdc_forever() -> None:
    from . import cdc

    while True:  # the supervised child process, as a thread
        try:
            cdc.run_worker()
        except Exception:  # noqa: BLE001 - keep the worker alive whatever happens
            log.exception("CDC worker crashed; restarting in 5 s")
            time.sleep(5)


def _wait_for_snapshot(timeout_s: float) -> bool:
    from .cdc import state

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (state() or {}).get("state") == "streaming":
            return True
        time.sleep(3)
    return False


def _bootstrap() -> None:
    from . import jobs
    from .datagen.loader import seed
    from .db import mongo_db

    s = get_settings()
    try:
        db = mongo_db()
        if db.claims.estimated_document_count() == 0:
            STATUS["bootstrap"] = f"seeding profile {s.bootstrap_profile}"
            log.info("empty database: seeding profile=%s seed=%s", s.bootstrap_profile, s.bootstrap_seed)
            seed(s.bootstrap_profile, s.bootstrap_seed, "realistic")
        if db.jobs.find_one({"kind": "analyze", "status": "done"}):
            STATUS["bootstrap"] = "done (existing results)"
            return
        STATUS["bootstrap"] = "waiting for the CDC snapshot into Neo4j"
        if not _wait_for_snapshot(timeout_s=1800):
            STATUS["bootstrap"] = "CDC snapshot did not finish in 30 min"
            return
        for attempt in range(3):
            STATUS["bootstrap"] = f"running analytics (attempt {attempt + 1})"
            try:
                jobs.analyze("bootstrap")
                STATUS["bootstrap"] = "done"
                return
            except RuntimeError:  # someone already started a run
                STATUS["bootstrap"] = "done (run already in progress)"
                return
            except Exception:  # noqa: BLE001 - e.g. an Aura session that failed to start
                log.exception("bootstrap analytics failed; retrying in 60 s")
                time.sleep(60)
        STATUS["bootstrap"] = "analytics failed 3 times; use Run analytics in the console"
    except Exception as exc:  # noqa: BLE001
        log.exception("bootstrap failed")
        STATUS["bootstrap"] = f"failed: {exc}"
