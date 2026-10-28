"""Claim intake: raw payer feed -> staging (TTL) -> normalize -> $jsonSchema validation -> claims.

Each payer sends its own shape: two JSON layouts and X12 837P text. Whatever arrives is
kept in `claims_staging` (expired after 24 h by a TTL index) together with the outcome,
so a rejected claim can be inspected next to MongoDB's validation error.
"""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from pymongo.errors import WriteError

from . import edi837
from .datagen import reference as ref
from .datagen.ids import ulid
from .db import mongo_db, truth_db

Listener = Callable[[str, dict], None]
_listeners: list[Listener] = []


def subscribe(fn: Listener):
    _listeners.append(fn)


def _emit(kind: str, data: dict):
    for fn in list(_listeners):
        try:
            fn(kind, data)
        except Exception:
            pass


class ClaimRejected(ValueError):
    def __init__(self, message: str, details: dict | None = None, staging_id=None):
        super().__init__(message)
        self.details = details or {}
        self.staging_id = staging_id


# ---------------------------------------------------------------- payer adapters
def to_json_a(c: dict) -> dict:
    return {"member": {"id": c["patient_id"]}, "provider": {"billing": c["billing_npi"], "rendering": c["rendering_npi"],
                                                            "referring": c.get("referring_npi")},
            "dos": c["service_date"], "pos": c.get("place_of_service", "11"), "dx": c["diagnosis_codes"],
            "services": [{"code": li["cpt"], "mod": li.get("modifiers", []), "units": li["units"],
                          "charge": li["charge"]} for li in c["line_items"]]}


def to_json_b(c: dict) -> dict:
    return {"patient_id": c["patient_id"], "rendering_npi": c["rendering_npi"], "referring_npi": c.get("referring_npi"),
            "service_date": c["service_date"], "diagnoses": c["diagnosis_codes"],
            "lines": [{"procedure": li["cpt"], "qty": li["units"], "amount": li["charge"]} for li in c["line_items"]],
            "plan": {"tier": "silver", "network": "in"}}


def normalize(fmt: str, payload) -> dict:
    """Payer-specific payload -> canonical fields."""
    if fmt == "837P":
        return edi837.parse(payload)
    if fmt == "json-a":
        p = payload
        return {"patient_id": p["member"]["id"], "billing_npi": p["provider"].get("billing") or p["provider"]["rendering"],
                "rendering_npi": p["provider"]["rendering"], "referring_npi": p["provider"].get("referring"),
                "service_date": p["dos"], "place_of_service": p.get("pos", "11"), "diagnosis_codes": p.get("dx", []),
                "line_items": [{"cpt": s["code"], "modifiers": s.get("mod", []), "units": s["units"],
                                "charge": s["charge"]} for s in p.get("services", [])]}
    if fmt == "json-b":
        p = payload
        return {"patient_id": p["patient_id"], "billing_npi": p["rendering_npi"], "rendering_npi": p["rendering_npi"],
                "referring_npi": p.get("referring_npi"), "service_date": p["service_date"],
                "diagnosis_codes": p.get("diagnoses", []),
                "line_items": [{"cpt": s["procedure"], "units": s["qty"], "charge": s["amount"]} for s in p.get("lines", [])]}
    raise ValueError(f"unknown payer format {fmt!r}")


def _build_claim(fields: dict, payer_id: str, fmt: str) -> dict:
    db = mongo_db()
    provider = db.providers.find_one({"_id": fields.get("rendering_npi")}, {"specialty": 1})
    now = datetime.now(timezone.utc)
    sd = fields.get("service_date")
    service_date = datetime.fromisoformat(sd).replace(tzinfo=timezone.utc) if isinstance(sd, str) else sd
    lines = []
    for i, li in enumerate(fields.get("line_items", []), 1):
        line = {"line": i, "cpt": str(li.get("cpt", "")), "units": li.get("units"), "charge": li.get("charge")}
        if isinstance(line["units"], float) and line["units"].is_integer():
            line["units"] = int(line["units"])
        if li.get("modifiers"):
            line["modifiers"] = li["modifiers"]
        minutes = ref.CODE_INFO.get(line["cpt"], (None, None, 0))[2]
        if minutes and isinstance(line["units"], int):
            line["minutes"] = minutes * line["units"]
        lines.append(line)
    total = round(sum(float(li["charge"] or 0) for li in lines), 2)
    payer_name = ref.PAYERS.get(payer_id, ("Unknown payer", fmt, 0.75))[0]
    return {
        "_id": "CLM-" + ulid(int(now.timestamp() * 1000)),
        "payer": {"id": payer_id, "name": payer_name, "format": fmt},
        "rendering_npi": fields.get("rendering_npi"), "billing_npi": fields.get("billing_npi") or fields.get("rendering_npi"),
        "referring_npi": fields.get("referring_npi") or None,
        "rendering_specialty": provider["specialty"] if provider else None,
        "patient_id": fields.get("patient_id"), "service_date": service_date, "submitted_at": now,
        "place_of_service": fields.get("place_of_service", "11"), "diagnosis_codes": fields.get("diagnosis_codes", []),
        "line_items": lines, "total_charge": total, "status": "submitted",
        "adjudication": {"allowed": 0.0, "paid": 0.0}, "payer_ext": {"received_via": "live-intake"},
    }


def _explain_validation(exc: WriteError) -> dict:
    """Flatten MongoDB's nested errInfo into (path, rule, reason) rows a person can read."""
    info = (exc.details or {}).get("errInfo", {}).get("details", {})
    problems: list[dict] = []

    def walk(node, path: str):
        if isinstance(node, list):
            for item in node:
                walk(item, path)
            return
        if not isinstance(node, dict):
            return
        if "propertyName" in node:
            path = f"{path}.{node['propertyName']}" if path else node["propertyName"]
        if "itemIndex" in node:
            path = f"{path}[{node['itemIndex']}]"
        if "missingProperties" in node:
            problems.append({"path": path or "(document)", "rule": "required",
                             "reason": "missing " + ", ".join(node["missingProperties"])})
        elif "reason" in node and node.get("operatorName") not in ("items", "properties"):
            row = {"path": path or "(document)", "rule": node.get("operatorName"), "reason": node["reason"]}
            if "consideredValue" in node:
                row["value"] = node["consideredValue"]
            if "specifiedAs" in node:
                row["expected"] = node["specifiedAs"]
            problems.append(row)
        for val in node.values():
            if isinstance(val, (dict, list)):
                walk(val, path)

    walk(info, "")
    return {"message": "Document failed validation", "problems": problems[:12], "raw": info}


def ingest(payload, fmt: str, payer_id: str, source: str = "api") -> dict:
    """Land the raw payload in staging, normalize it, and let the JSON Schema validator
    decide whether it may enter the system of record."""
    db = mongo_db()
    now = datetime.now(timezone.utc)
    staging_id = db.claims_staging.insert_one({"payer_id": payer_id, "format": fmt, "payload": payload,
                                               "source": source, "received_at": now, "status": "received"}).inserted_id
    try:
        claim = _build_claim(normalize(fmt, payload), payer_id, fmt)
    except (KeyError, ValueError, TypeError) as exc:
        db.claims_staging.update_one({"_id": staging_id}, {"$set": {"status": "rejected", "error": str(exc)}})
        _emit("claim.rejected", {"payer": payer_id, "format": fmt, "reason": f"normalization: {exc}"})
        raise ClaimRejected(f"Could not normalize {fmt} payload: {exc}", staging_id=staging_id) from exc
    try:
        db.claims.insert_one(claim)
    except WriteError as exc:
        details = _explain_validation(exc)
        db.claims_staging.update_one({"_id": staging_id}, {"$set": {"status": "rejected", "error": details["problems"]}})
        first = details["problems"][0] if details["problems"] else {"path": "?", "reason": "schema"}
        _emit("claim.rejected", {"payer": payer_id, "format": fmt, "reason": f"$jsonSchema: {first['path']} {first['reason']}"})
        raise ClaimRejected("Rejected by MongoDB $jsonSchema validation", details, staging_id) from exc
    db.claims_staging.update_one({"_id": staging_id}, {"$set": {"status": "promoted", "claim_id": claim["_id"]}})
    _emit("claim.ingested", {"id": claim["_id"], "npi": claim["rendering_npi"], "specialty": claim["rendering_specialty"],
                             "referring": claim["referring_npi"], "amount": claim["total_charge"],
                             "payer": payer_id, "format": fmt, "source": source})
    return claim


# ---------------------------------------------------------------- simulated payer traffic
class _World:
    """Small cache of the data needed to invent believable new claims."""

    def __init__(self):
        db = mongo_db()
        self.providers = {p["_id"]: p for p in db.providers.find({}, {"specialty": 1, "kind": 1, "metro": 1,
                                                                    "preferred_referrals": 1})}
        self.patients = list(db.patients.aggregate([{"$match": {"dod": None}}, {"$sample": {"size": 4000}},
                                                    {"$project": {"pcp_npi": 1, "payer_id": 1, "metro": 1}}]))
        self.by_metro_spec: dict = {}
        for npi, p in self.providers.items():
            self.by_metro_spec.setdefault((p["metro"], p["specialty"]), []).append(npi)


def _lines(rng: random.Random, specialty: str, kind: str) -> list[dict]:
    if kind == "ancillary":
        code = rng.choice(ref.PROCEDURES[specialty])[0]
        return [{"cpt": code, "units": 1, "charge": round(ref.CODE_INFO[code][1] * rng.uniform(0.96, 1.08), 2)}]
    mix = ref.EM_MIX.get(specialty, ref.EM_MIX[kind])
    em = rng.choices(ref.EM_ORDER, mix)[0]
    lines = [{"cpt": em, "units": 1, "charge": round(ref.EM_CODES[em][1] * rng.uniform(0.96, 1.08), 2)}]
    if rng.random() < 0.3 and ref.PROCEDURES.get(specialty):
        code = rng.choice(ref.PROCEDURES[specialty])[0]
        lines[0]["modifiers"] = ["25"]
        lines.append({"cpt": code, "units": 1, "charge": round(ref.CODE_INFO[code][1] * rng.uniform(0.96, 1.08), 2)})
    return lines


def _send(canonical: dict, payer_id: str, source: str):
    fmt = ref.PAYERS[payer_id][1]
    if fmt == "837P":
        payload = edi837.render({**canonical, "payer_id": payer_id})
    elif fmt == "json-a":
        payload = to_json_a(canonical)
    else:
        payload = to_json_b(canonical)
    return ingest(payload, fmt, payer_id, source)


class Feed:
    """Background thread submitting believable claims at a steady rate through intake.
    About 1 in 80 is deliberately malformed so the validator has something to reject."""

    def __init__(self):
        self.rate = 0.0
        self.thread: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.sent = self.rejected = 0
        self.started_at: datetime | None = None

    def status(self) -> dict:
        return {"running": bool(self.thread and self.thread.is_alive()), "rate": self.rate, "sent": self.sent,
                "rejected": self.rejected, "started_at": self.started_at}

    def start(self, rate: float):
        self.rate = max(0.5, min(200.0, rate))
        if self.thread and self.thread.is_alive():
            return
        self.stop_flag.clear()
        self.sent = self.rejected = 0
        self.started_at = datetime.now(timezone.utc)
        self.thread = threading.Thread(target=self._run, daemon=True, name="claim-feed")
        self.thread.start()

    def stop(self):
        self.stop_flag.set()

    def _one(self, world: _World, rng: random.Random):
        pat = rng.choice(world.patients)
        npi = pat["pcp_npi"]
        referring = None
        prov = world.providers[npi]
        if rng.random() < 0.35 and prov.get("preferred_referrals"):
            referring, npi = npi, rng.choice(prov["preferred_referrals"])
            prov = world.providers[npi]
        day = (datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 3))).date().isoformat()
        diag = ref.DIAGNOSES.get(prov["specialty"], ref.DIAGNOSES["primary"])
        canonical = {"patient_id": pat["_id"], "billing_npi": npi, "rendering_npi": npi, "referring_npi": referring,
                     "service_date": day, "place_of_service": "11", "diagnosis_codes": rng.sample(diag, 1),
                     "line_items": _lines(rng, prov["specialty"], prov["kind"])}
        if rng.random() < 1 / 80:  # malformed on purpose
            fault = rng.choice(["units", "npi", "cpt", "dx"])
            if fault == "units":
                canonical["line_items"][0]["units"] = 0
            elif fault == "npi":
                canonical["rendering_npi"] = canonical["billing_npi"] = "12345"
            elif fault == "cpt":
                canonical["line_items"][0]["cpt"] = "XX"
            else:
                canonical["diagnosis_codes"] = ["not-a-code"]
        _send(canonical, pat.get("payer_id", "PYR-01"), "feed")

    def _run(self):
        world, rng = _World(), random.Random()
        next_at = time.monotonic()
        while not self.stop_flag.is_set():
            try:
                self._one(world, rng)
                self.sent += 1
            except ClaimRejected:
                self.rejected += 1
            except Exception:
                time.sleep(1)
            next_at += 1.0 / self.rate
            delay = next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_at = time.monotonic()


FEED = Feed()


def plant_ring(size: int = 5, camouflage: bool = True, metro: str | None = None) -> dict:
    """Plant a brand-new referral ring through the normal intake path. Its members and
    recruited patients are recorded as ground truth so evaluation stays honest."""
    rng = random.Random()
    db, truth = mongo_db(), truth_db()
    taken = {t["_id"] for t in truth.providers.find({}, {"_id": 1})}
    metro = metro or rng.choice(list(ref.METROS))
    pool = [p for p in db.providers.find({"metro": metro, "kind": {"$in": ["specialist", "ancillary"]}},
                                         {"specialty": 1, "kind": 1}) if p["_id"] not in taken]
    if len(pool) < size:
        raise ValueError(f"not enough uninvolved providers in {metro}")
    members = rng.sample(pool, size)
    recruits = [p["_id"] for p in db.patients.aggregate([{"$match": {"metro": metro, "dod": None}},
                                                         {"$sample": {"size": 8 + 3 * size}}])]
    payer = rng.choice(list(ref.PAYERS))
    n = 0
    today = datetime.now(timezone.utc).date()
    for _ in range(9 * size):
        pat = rng.choice(recruits)
        day = today - timedelta(days=rng.randint(15, 60))
        i = rng.randrange(size)
        for _ in range(rng.randint(size, 2 * size)):
            src, dst = members[i % size], members[(i + 1) % size]
            day = min(today, day + timedelta(days=rng.randint(1, 5)))
            lines = _lines(rng, dst["specialty"], dst["kind"])
            if not camouflage:
                for li in lines:
                    li["charge"] = round(li["charge"] * 1.8, 2)
            diag = ref.DIAGNOSES.get(dst["specialty"], ref.DIAGNOSES["primary"])
            canonical = {"patient_id": pat, "billing_npi": dst["_id"], "rendering_npi": dst["_id"],
                         "referring_npi": src["_id"], "service_date": day.isoformat(), "place_of_service": "11",
                         "diagnosis_codes": rng.sample(diag, 1), "line_items": lines}
            _send(canonical, payer, "ring-planter")
            n += 1
            i += 1
    live_count = truth.rings.count_documents({"source": "live"})
    ring_id = f"PR-L{live_count + 1:02d}"
    ids = [m["_id"] for m in members]
    truth.rings.insert_one({"_id": ring_id, "members": ids, "hub": ids[0], "metro": metro,
                            "camouflaged": camouflage, "source": "live", "planted_at": datetime.now(timezone.utc)})
    for m in ids:
        truth.providers.update_one({"_id": m}, {"$addToSet": {"fraud_types": "referral_ring"},
                                                "$set": {"ring_id": ring_id, "role": "member"}}, upsert=True)
    _emit("ring.planted", {"ring_id": ring_id, "members": ids, "claims": n, "metro": metro})
    return {"planted_ring": ring_id, "members": ids, "metro": metro, "claims_submitted": n, "camouflaged": camouflage}
