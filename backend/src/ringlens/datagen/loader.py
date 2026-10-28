"""Write a generated world into MongoDB (and its labels into the separate truth database)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from ..db import mongo_db, truth_db
from ..schema import create_schema
from .generator import Generator

BATCH = 5000


def _insert(coll, docs: list[dict], progress: Callable[[int], None] | None = None):
    def run(chunk):
        coll.insert_many(chunk, ordered=False)  # still validated by $jsonSchema
        return len(chunk)

    chunks = [docs[i:i + BATCH] for i in range(0, len(docs), BATCH)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n in pool.map(run, chunks):
            if progress:
                progress(n)


def seed(profile: str = "demo", seed: int = 1, difficulty: str = "realistic",
         progress: Callable[[str, int, int], None] | None = None) -> dict:
    db = mongo_db()
    # Tell a running CDC worker to stand by; it re-snapshots when the new epoch appears.
    db.pipeline_control.update_one({"_id": "neo4j"}, {"$set": {"seeding": True}}, upsert=True)
    try:
        create_schema(drop=True)
        gen = Generator(profile, seed, difficulty).generate()
        report = (lambda stage, total: (lambda n: progress(stage, n, total))) if progress else (lambda s, t: None)
        _insert(db.providers, gen.providers, report("providers", len(gen.providers)))
        _insert(db.patients, gen.patients, report("patients", len(gen.patients)))
        _insert(db.claims, gen.claims, report("claims", len(gen.claims)))

        truth = truth_db()
        if gen.truth_providers:
            truth.providers.insert_many(list(gen.truth_providers.values()))
        if gen.truth_rings:
            truth.rings.insert_many(gen.truth_rings)
        if gen.truth_claims:
            truth.claims.insert_many([{"_id": k, "type": v} for k, v in gen.truth_claims.items()])
        if gen.decoys:
            truth.decoys.insert_many(gen.decoys)
        truth.meta.replace_one({"_id": "meta"}, gen.meta(), upsert=True)
    finally:
        db.pipeline_control.update_one(
            {"_id": "neo4j"},
            {"$set": {"seeding": False, "epoch": datetime.now(timezone.utc), "paused": False, "crash": False}},
            upsert=True)
    meta = gen.meta()
    meta.pop("_id")
    return meta
