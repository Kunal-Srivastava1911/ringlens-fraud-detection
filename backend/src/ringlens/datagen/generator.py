"""Synthetic claims world with deliberately planted fraud (the ground truth for evaluation).

Legitimate behaviour
  Patients see their primary-care provider (PCP). PCPs refer to a few preferred
  specialists in their metro (preferential attachment: bigger practices attract more),
  and order labs; specialists refer onward to imaging, labs, physical therapy and DME.
  The referral network is therefore close to a DAG. Hard negatives make detection
  non-trivial: big busy practices, specialties that legitimately bill a lot of top-level
  visits, co-managing specialist triads that refer to each other (legitimate cycles),
  group practices sharing a building, and vaccination-drive days.

Planted fraud (counts and sizes randomized per seed)
  referral_ring     recruited patients recirculate around a closed loop of providers; a
                    hub collects extra referrals; camouflaged rings bill normally per claim
  broker            bridges two rings (one-way, so the rings stay separate components)
  patient_sharing   two providers bill the same recruited patients with no referral link
  upcoding          E/M level mix skewed to 99214/99215
  phantom_billing   bursts of long timed services for unknown or deceased patients
  unbundling        labs bill panel components separately instead of the panel code
  duplicate_billing the same service resubmitted under a new claim id
  shell_identity    unrelated providers paid into one bank account
"""
from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from . import reference as ref
from .ids import make_npi, ulid


@dataclass(frozen=True)
class Profile:
    providers: int
    patients: int
    claims: int


PROFILES = {
    "tiny": Profile(150, 3000, 12_000),
    # Hosted demo: fits MongoDB Atlas free (512 MB) and AuraDB Free (200k nodes / 400k rels).
    "cloud": Profile(400, 8000, 48_000),
    "demo": Profile(1200, 30_000, 350_000),
    "large": Profile(3000, 120_000, 1_500_000),
}

# camouflage: share of rings whose members bill normally per claim.
# hops: how many times a recruited patient travels round the loop, as a multiple of ring size.
# gap: max days between hops. decoys: multiplier on legitimate look-alikes.
DIFFICULTY = {
    "easy": {"camouflage": 0.2, "hops": (1.0, 3.0), "gap": 9, "decoys": 0.5},
    "realistic": {"camouflage": 0.5, "hops": (0.8, 2.0), "gap": 12, "decoys": 1.0},
    "hard": {"camouflage": 0.85, "hops": (0.6, 1.3), "gap": 25, "decoys": 1.6},
}

HISTORY_DAYS = 548  # 18 months
POS = {"primary": "11", "specialist": "11", "Radiology": "22", "Clinical Laboratory": "81",
       "Physical Therapy": "11", "DME Supplier": "12"}


def _utc(d: date, hour: int = 0, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute), tzinfo=timezone.utc)


class Generator:
    def __init__(self, profile: str = "demo", seed: int = 1, difficulty: str = "realistic",
                 end_date: date | None = None):
        self.profile_name, self.p = profile, PROFILES[profile]
        self.seed, self.difficulty, self.d = seed, difficulty, DIFFICULTY[difficulty]
        self.rng = random.Random(seed)
        self.end = end_date or datetime.now(timezone.utc).date()
        self.start = self.end - timedelta(days=HISTORY_DAYS)
        self.scale = self.p.providers / 1200
        # Ring volume follows the per-provider claim volume of the profile.
        self.volume = max(0.3, min(2.0, (self.p.claims / self.p.providers) / 290))

        self.providers: list[dict] = []
        self.by_npi: dict[str, dict] = {}
        self.patients: list[dict] = []
        self.claims: list[dict] = []
        self.behaviour: dict[str, set[str]] = {}
        self.taken: set[str] = set()
        self.truth_providers: dict[str, dict] = {}
        self.truth_rings: list[dict] = []
        self.truth_claims: dict[str, str] = {}
        self.decoys: list[dict] = []
        self._build_day_pool()

    # ------------------------------------------------------------------ time
    def _build_day_pool(self):
        """Days weighted by weekday (clinics are quiet at weekends) and season (winter peak)."""
        self.days, cum, total = [], [], 0.0
        for i in range(HISTORY_DAYS + 1):
            d = self.start + timedelta(days=i)
            w = 1.0 if d.weekday() < 5 else (0.25 if d.weekday() == 5 else 0.08)
            w *= 1.0 + 0.18 * math.cos((d.timetuple().tm_yday - 20) / 365 * 2 * math.pi)
            total += w
            self.days.append(d)
            cum.append(total)
        self.day_cum = cum

    def _day(self, lo: date | None = None, hi: date | None = None) -> date:
        a = 0 if lo is None else max(0, (lo - self.start).days)
        b = len(self.days) - 1 if hi is None else min(len(self.days) - 1, (hi - self.start).days)
        lo_w = self.day_cum[a - 1] if a > 0 else 0.0
        x = self.rng.uniform(lo_w, self.day_cum[b])
        return self.days[min(bisect.bisect_left(self.day_cum, x), b)]

    # ------------------------------------------------------------------ world
    def _address(self, metro: str) -> dict:
        state, _, _, zip3 = ref.METROS[metro]
        r = self.rng
        return {"line1": f"{r.randint(10, 9899)} {r.choice(ref.STREETS)} {r.choice(['St', 'Ave', 'Blvd', 'Rd', 'Pkwy'])}",
                "city": metro, "state": state, "zip": f"{zip3}{r.randint(0, 99):02d}"}

    def make_providers(self):
        r = self.rng
        metros = list(ref.METROS)
        metro_w = [1.6, 1.4, 1.1, 1.0, 0.8, 0.8, 0.8, 0.7]
        total_share = sum(s for _, s in ref.SPECIALTIES.values())
        used: set[str] = set()
        for specialty, (kind, share) in ref.SPECIALTIES.items():
            n = max(2, round(self.p.providers * share / total_share))
            for i in range(n):
                # Spread every specialty over every metro first, then weight by metro size.
                metro = metros[i % len(metros)] if i < len(metros) else r.choices(metros, metro_w)[0]
                npi = make_npi(r)
                while npi in used:
                    npi = make_npi(r)
                used.add(npi)
                _, lat, lon, _ = ref.METROS[metro]
                if kind == "ancillary":
                    name = f"{r.choice(ref.LAST)} {ref.ORG_SUFFIX[specialty]}"
                else:
                    name = f"Dr. {r.choice(ref.FIRST)} {r.choice(ref.LAST)}"
                addr = self._address(metro)
                p = {
                    "_id": npi, "name": name, "specialty": specialty, "kind": kind, "metro": metro,
                    "address": addr, "address_key": f"{addr['line1']}|{addr['zip']}".upper(),
                    "bank_hash": "%016x" % r.getrandbits(64),
                    "location": {"type": "Point", "coordinates": [round(lon + r.gauss(0, 0.09), 5),
                                                                  round(lat + r.gauss(0, 0.07), 5)]},
                    "enrolled_on": _utc(self.start - timedelta(days=r.randint(90, 5000))),
                    "practice_size": round(min(6.0, r.lognormvariate(0, 0.55)), 3),
                }
                self.providers.append(p)
        self.by_npi = {p["_id"]: p for p in self.providers}
        self.pool: dict[tuple[str, str], list[str]] = {}
        for p in self.providers:
            self.pool.setdefault((p["specialty"], p["metro"]), []).append(p["_id"])

        # Legitimate group practices share a building (bait for address-based false positives).
        for metro in metros:
            for _ in range(2):
                cands = [p for p in self.providers if p["metro"] == metro and p["kind"] != "ancillary"]
                group = r.sample(cands, min(3, len(cands)))
                for m in group[1:]:
                    m["address"] = dict(group[0]["address"])
                    m["address_key"] = group[0]["address_key"]
                self.decoys.append({"kind": "shared_address_group", "members": [g["_id"] for g in group]})

    def _weighted_pick(self, npis: list[str]) -> str:
        return self.rng.choices(npis, [self.by_npi[n]["practice_size"] for n in npis])[0]

    def _wire_preferences(self):
        """Preferred referral targets: preferential attachment within the metro."""
        r = self.rng
        self.prefs: dict[str, dict[str, list[str]]] = {}
        for p in self.providers:
            prefs: dict[str, list[str]] = {}
            if p["kind"] == "primary":
                targets = ref.SPECIALISTS + ["Clinical Laboratory", "Radiology"]
            elif p["kind"] == "specialist":
                targets = ref.ANCILLARY
            else:
                targets = []
            for spec in targets:
                pool = self.pool.get((spec, p["metro"]), [])
                if pool:
                    k = min(len(pool), 2 if spec in ref.SPECIALISTS else r.randint(1, 2))
                    chosen: list[str] = []
                    while len(chosen) < k:
                        c = self._weighted_pick(pool)
                        if c not in chosen:
                            chosen.append(c)
                    prefs[spec] = chosen
            self.prefs[p["_id"]] = prefs
            p["preferred_referrals"] = sorted({n for v in prefs.values() for n in v})

    def make_patients(self):
        r = self.rng
        metros = list(ref.METROS)
        metro_w = [1.6, 1.4, 1.1, 1.0, 0.8, 0.8, 0.8, 0.7]
        payers = list(ref.PAYERS)
        for i in range(1, self.p.patients + 1):
            metro = r.choices(metros, metro_w)[0]
            age = min(98, max(0, int(r.triangular(0, 95, 45))))
            dob = self.end - timedelta(days=age * 365 + r.randint(0, 364))
            if age < 18:
                pcps = self.pool.get(("Pediatrics", metro), [])
            else:
                pcps = self.pool.get(("Family Medicine", metro), []) + self.pool.get(("Internal Medicine", metro), [])
            dod = None
            if age > 68 and r.random() < 0.045:
                dod = self._day(self.start + timedelta(days=60))
            _, _, _, zip3 = ref.METROS[metro]
            self.patients.append({
                "_id": f"MBR-{i:08d}", "name": f"{r.choice(ref.FIRST)} {r.choice(ref.LAST)}",
                "dob": _utc(dob), "sex": r.choices(["F", "M", "X"], [50, 49, 1])[0], "metro": metro,
                "zip": f"{zip3}{r.randint(0, 99):02d}",
                "payer_id": r.choices(payers, [35, 25, 22, 18])[0],
                "pcp_npi": self._weighted_pick(pcps),
                "chronic_conditions": r.choices([0, 1, 2, 3], [45, 30, 17, 8])[0],
                "dod": _utc(dod) if dod else None,
            })
        self.patient_by_id = {p["_id"]: p for p in self.patients}
        self.patients_by_metro: dict[str, list[dict]] = {}
        for p in self.patients:
            self.patients_by_metro.setdefault(p["metro"], []).append(p)
        w = [1 + 1.5 * p["chronic_conditions"] for p in self.patients]
        self.patient_cum = list(_accumulate(w))

    # ------------------------------------------------------------------ claims
    def _line(self, n: int, code: str, units: int = 1, modifiers: list[str] | None = None) -> dict:
        _, price, minutes = ref.CODE_INFO[code]
        line = {"line": n, "cpt": code, "units": units,
                "charge": round(price * units * self.rng.uniform(0.96, 1.08), 2)}
        if minutes:
            line["minutes"] = minutes * units
        if modifiers:
            line["modifiers"] = modifiers
        return line

    def _lines_for(self, npi: str) -> tuple[list[dict], str | None]:
        """Line items for one visit; returns (lines, fraud label for the claim or None)."""
        r = self.rng
        p = self.by_npi[npi]
        spec, kind = p["specialty"], p["kind"]
        beh = self.behaviour.get(npi, set())
        label = None
        lines: list[dict] = []
        if spec == "Clinical Laboratory":
            if "unbundle" in beh and r.random() < 0.8:
                for code, *_ in r.sample(ref.LAB_COMPONENTS, r.randint(5, 7)):
                    lines.append(self._line(len(lines) + 1, code))
                label = "unbundling"
            elif r.random() < 0.6:
                lines.append(self._line(1, ref.LAB_PANEL[0]))
            if not lines or r.random() < 0.5:
                lines.append(self._line(len(lines) + 1, r.choice(ref.LAB_OTHER)[0]))
            return lines, label
        if spec == "Physical Therapy":
            if r.random() < 0.15:
                lines.append(self._line(1, "97161"))
            for code, *_ in r.sample(ref.PROCEDURES[spec][:3], r.randint(1, 3)):
                lines.append(self._line(len(lines) + 1, code, units=r.randint(1, 2)))
            return lines, None
        if kind == "ancillary":  # Radiology, DME
            code = r.choice(ref.PROCEDURES[spec])[0]
            lines.append(self._line(1, code))
            return lines, None

        upcode = "upcode" in beh or "inflate" in beh
        mix = ref.EM_UPCODED if upcode else ref.EM_MIX.get(spec, ref.EM_MIX[kind])
        em = r.choices(ref.EM_ORDER, mix)[0]
        if upcode and em in ("99214", "99215") and "upcode" in beh:
            label = "upcoding"
        proc_p = 0.85 if "inflate" in beh else (0.45 if kind == "specialist" else 0.3)
        procs = ref.PROCEDURES.get(spec, [])
        if procs and r.random() < proc_p:
            n = r.randint(1, 2) if "inflate" in beh else 1
            lines.append(self._line(1, em, modifiers=["25"]))
            for code, *_ in r.sample(procs, min(n, len(procs))):
                lines.append(self._line(len(lines) + 1, code))
        else:
            lines.append(self._line(1, em))
        return lines, label

    def _payer_ext(self, fmt: str) -> dict:
        r = self.rng
        if fmt == "837P":
            return {"interchange_control": f"{r.randint(10 ** 8, 10 ** 9 - 1)}", "claim_frequency": "1",
                    "transaction_set": "005010X222A1"}
        if fmt == "json-b":
            return {"plan": {"tier": r.choice(["gold", "silver", "bronze"]), "network": "in"}}
        return {"member_group": f"GRP-{r.randint(100, 999)}"}

    def claim(self, npi: str, patient: dict, day: date, referring: str | None = None,
              lines: list[dict] | None = None, label: str | None = None) -> dict | None:
        if day > self.end or day < self.start:
            return None
        r = self.rng
        p = self.by_npi[npi]
        if lines is None:
            lines, label2 = self._lines_for(npi)
            label = label or label2
        total = round(sum(li["charge"] for li in lines), 2)
        submitted = _utc(day, r.randint(7, 19), r.randint(0, 59)) + timedelta(days=r.randint(1, 18))
        end_dt = _utc(self.end, 23, 59)
        if submitted > end_dt:
            submitted = end_dt - timedelta(minutes=r.randint(1, 600))
        age = (self.end - day).days
        status = "submitted" if age < 10 else r.choices(["paid", "adjudicated", "denied"], [86, 8, 6])[0]
        payer_name, fmt, factor = ref.PAYERS[patient["payer_id"]]
        allowed = round(total * factor, 2)
        diag_pool = ref.DIAGNOSES.get(p["specialty"], ref.DIAGNOSES["primary"])
        doc = {
            "_id": "CLM-" + ulid(int(submitted.timestamp() * 1000), r),
            "payer": {"id": patient["payer_id"], "name": payer_name, "format": fmt},
            "rendering_npi": npi, "billing_npi": npi, "referring_npi": referring,
            "rendering_specialty": p["specialty"], "patient_id": patient["_id"],
            "service_date": _utc(day), "submitted_at": submitted,
            "place_of_service": POS.get(p["specialty"], POS.get(p["kind"], "11")),
            "diagnosis_codes": r.sample(diag_pool, min(len(diag_pool), r.randint(1, 3))),
            "line_items": lines, "total_charge": total, "status": status,
            "adjudication": {"allowed": allowed if status != "denied" else 0.0,
                             "paid": round(allowed * 0.8, 2) if status == "paid" else 0.0},
            "payer_ext": self._payer_ext(fmt),
        }
        self.claims.append(doc)
        if label:
            self.truth_claims[doc["_id"]] = label
        return doc

    def _random_patient(self) -> dict:
        x = self.rng.uniform(0, self.patient_cum[-1])
        return self.patients[bisect.bisect_left(self.patient_cum, x)]

    @staticmethod
    def _alive(patient: dict, day: date) -> bool:
        return patient["dod"] is None or day <= patient["dod"].date()

    def make_legit_claims(self, target: int):
        r = self.rng
        metro_specialists: dict[str, list[str]] = {}
        for p in self.providers:
            if p["kind"] == "specialist":
                metro_specialists.setdefault(p["metro"], []).append(p["_id"])
        anc_types, anc_w = ["Radiology", "Clinical Laboratory", "Physical Therapy", "DME Supplier"], [4, 3, 3, 1]
        start_len = len(self.claims)
        while len(self.claims) - start_len < target:
            pat = self._random_patient()
            day = self._day()
            if not self._alive(pat, day):
                continue
            pcp = pat["pcp_npi"]
            self.claim(pcp, pat, day)
            prefs = self.prefs[pcp]
            if r.random() < 0.28 and prefs.get("Clinical Laboratory"):
                self.claim(r.choice(prefs["Clinical Laboratory"]), pat, day + timedelta(days=r.randint(0, 3)), pcp)
            if r.random() >= 0.33:
                continue
            specs = [s for s in ref.SPECIALISTS if prefs.get(s)]
            if not specs:
                continue
            spec = r.choice(prefs[r.choice(specs)])
            d2 = day + timedelta(days=r.randint(4, 30))
            if not self._alive(pat, d2) or self.claim(spec, pat, d2, pcp) is None:
                continue
            for _ in range(r.choices([0, 1, 2], [45, 35, 20])[0]):
                d3 = d2 + timedelta(days=r.randint(14, 70))
                if self._alive(pat, d3):
                    self.claim(spec, pat, d3)
            sprefs = self.prefs[spec]
            if r.random() < 0.5:
                anc_type = r.choices(anc_types, anc_w)[0]
                if sprefs.get(anc_type):
                    anc = r.choice(sprefs[anc_type])
                    d4 = d2 + timedelta(days=r.randint(1, 10))
                    visits = r.randint(3, 8) if anc_type == "Physical Therapy" else 1
                    for _ in range(visits):
                        if self._alive(pat, d4):
                            self.claim(anc, pat, d4, spec)
                        d4 += timedelta(days=r.randint(2, 5))
            if r.random() < 0.025:  # occasional back-referral to the PCP
                self.claim(pcp, pat, d2 + timedelta(days=r.randint(10, 40)), spec)
            if r.random() < 0.03:  # occasional specialist-to-specialist referral
                others = [s for s in metro_specialists.get(pat["metro"], []) if s != spec]
                if others:
                    self.claim(r.choice(others), pat, d2 + timedelta(days=r.randint(7, 40)), spec)

    # ------------------------------------------------------------------ fraud
    def _label(self, npi: str, fraud_type: str, **extra):
        entry = self.truth_providers.setdefault(npi, {"_id": npi, "fraud_types": []})
        if fraud_type not in entry["fraud_types"]:
            entry["fraud_types"].append(fraud_type)
        entry.update(extra)

    def _pick(self, specialties: list[str], metro: str | None = None, smallest: bool = False) -> str:
        pool = [p for p in self.providers if p["specialty"] in specialties and p["_id"] not in self.taken
                and (metro is None or p["metro"] == metro)]
        if not pool:
            pool = [p for p in self.providers if p["specialty"] in specialties and p["_id"] not in self.taken]
        if smallest:
            pool.sort(key=lambda p: p["practice_size"])
            chosen = pool[min(len(pool) - 1, self.rng.randint(0, 2))]
        else:
            chosen = self.rng.choice(pool)
        self.taken.add(chosen["_id"])
        return chosen["_id"]

    def _count(self, lo: float, hi: float, minimum: int = 1) -> int:
        return max(minimum, round(self.rng.uniform(lo, hi) * self.scale))

    def plan_fraud(self):
        """Choose fraudulent providers and their behaviour before any claim is generated."""
        r = self.rng
        metros = list(ref.METROS)
        hubs = ["Cardiology", "Orthopedics", "Neurology", "Oncology", "Pulmonology", "Gastroenterology"]
        others = ref.PRIMARY[:2] + ref.SPECIALISTS + ["Radiology", "Clinical Laboratory", "Physical Therapy",
                                                     "DME Supplier"]
        self.rings = []
        n_rings = self._count(6, 9, minimum=2)
        for i in range(n_rings):
            size = r.choice([3, 4, 4, 5, 5, 6, 7, 8] if self.scale >= 0.5 else [3, 4, 4, 5])
            metro = r.choice(metros)
            hub = self._pick(hubs, metro)
            members = [hub] + [self._pick([r.choice(others)], metro) for _ in range(size - 1)]
            camo = r.random() < self.d["camouflage"]
            ring = {"_id": f"PR-{i + 1:02d}", "members": members, "hub": hub, "metro": metro,
                    "camouflaged": camo, "source": "seed"}
            self.rings.append(ring)
            for m in members:
                self._label(m, "referral_ring", ring_id=ring["_id"], role="hub" if m == hub else "member",
                            camouflaged=camo)
                if not camo:
                    self.behaviour.setdefault(m, set()).add("inflate")
            if r.random() < 0.5:
                share = r.sample(members, min(len(members), r.randint(2, 3)))
                for m in share[1:]:
                    self.by_npi[m]["bank_hash"] = self.by_npi[share[0]]["bank_hash"]
            if r.random() < 0.3:
                a, b = r.sample(members, 2)
                self.by_npi[b]["address"] = dict(self.by_npi[a]["address"])
                self.by_npi[b]["address_key"] = self.by_npi[a]["address_key"]

        self.brokers = []
        for _ in range(min(len(self.rings) // 2, self._count(1, 2))):
            a, b = r.sample(self.rings, 2)
            broker = self._pick(["Pulmonology", "Neurology", "Endocrinology", "Gastroenterology"], a["metro"])
            self.brokers.append((broker, a, b))
            self._label(broker, "broker", bridges=[a["_id"], b["_id"]])

        self.sharing_pairs = []
        combos = [("DME Supplier", "Physical Therapy"), ("Psychiatry", "Physical Therapy"),
                  ("DME Supplier", "Family Medicine"), ("Dermatology", "Clinical Laboratory")]
        for i in range(self._count(2, 3)):
            sa, sb = combos[i % len(combos)]
            metro = r.choice(metros)
            pair = (self._pick([sa], metro, smallest=True), self._pick([sb], metro, smallest=True))
            self.sharing_pairs.append(pair)
            for n in pair:
                self._label(n, "patient_sharing", partner=pair[1] if n == pair[0] else pair[0])
                self.by_npi[n]["practice_size"] = 0.2  # tiny legitimate practice

        for _ in range(self._count(9, 13, minimum=2)):
            n = self._pick(ref.PRIMARY + [s for s in ref.SPECIALISTS if s != "Oncology"])
            self.behaviour.setdefault(n, set()).add("upcode")
            self._label(n, "upcoding")
        self.phantoms = [self._pick(["Psychiatry", "Physical Therapy", "Family Medicine"])
                         for _ in range(self._count(4, 6))]
        for n in self.phantoms:
            self._label(n, "phantom_billing")
        for _ in range(self._count(3, 5)):
            n = self._pick(["Clinical Laboratory"])
            self.behaviour.setdefault(n, set()).add("unbundle")
            self._label(n, "unbundling")
        self.duplicators = [self._pick(ref.PRIMARY + ["Cardiology", "Radiology", "Physical Therapy"])
                            for _ in range(self._count(3, 5))]
        for n in self.duplicators:
            self._label(n, "duplicate_billing")
        for _ in range(self._count(1.5, 2.5)):
            metro = r.choice(metros)
            group = [self._pick(["DME Supplier", "Physical Therapy", "Clinical Laboratory"], metro)
                     for _ in range(r.randint(2, 3))]
            for n in group[1:]:
                self.by_npi[n]["bank_hash"] = self.by_npi[group[0]]["bank_hash"]
            for n in group:
                self._label(n, "shell_identity", group=group)

        # Legitimate look-alikes.
        self.triads = []
        for _ in range(max(1, round(3 * self.scale * self.d["decoys"]))):
            metro = r.choice(metros)
            triad = [self._pick([s], metro) for s in ("Cardiology", "Pulmonology", "Endocrinology")]
            self.triads.append(triad)
            self.decoys.append({"kind": "co_management_triad", "members": triad})
        self.drive_pcps = [self._pick(["Family Medicine", "Pediatrics"])
                           for _ in range(max(1, round(6 * self.scale * self.d["decoys"])))]
        self.decoys.append({"kind": "vaccination_drive", "members": self.drive_pcps})

    def _metro_patients(self, metro: str, k: int) -> list[dict]:
        alive = [p for p in self.patients_by_metro[metro] if p["dod"] is None]
        return self.rng.sample(alive, min(k, len(alive)))

    def make_fraud_claims(self):
        r = self.rng
        lo, hi = self.d["hops"]
        for ring in self.rings:
            members, size = ring["members"], len(ring["members"])
            recruits = self._metro_patients(ring["metro"], 10 + 4 * size)
            ring["recruits"] = len(recruits)
            active_from = self._day(hi=self.end - timedelta(days=150))
            active_to = min(self.end, active_from + timedelta(days=r.randint(120, 330)))
            for _ in range(max(6, round(r.randint(10, 15) * size * self.volume))):
                pat = r.choice(recruits)
                day = self._day(active_from, active_to)
                i = r.randrange(size)
                for _ in range(max(2, round(size * r.uniform(lo, hi)))):
                    src, dst = members[i % size], members[(i + 1) % size]
                    day += timedelta(days=r.randint(2, self.d["gap"]))
                    self.claim(dst, pat, day, src, label="referral_ring")
                    i += 1
                if r.random() < 0.5:  # extra value funnelled to the hub
                    src = r.choice(members[1:])
                    self.claim(ring["hub"], pat, day + timedelta(days=r.randint(1, 4)), src, label="referral_ring")

        for broker, a, b in self.brokers:
            pats = self._metro_patients(a["metro"], 30)
            for _ in range(max(8, round(40 * self.volume))):
                pat = r.choice(pats)
                day = self._day(hi=self.end - timedelta(days=20))
                self.claim(broker, pat, day, r.choice(a["members"]), label="broker")
                self.claim(r.choice(b["members"]), pat, day + timedelta(days=r.randint(3, 12)), broker,
                           label="broker")

        for x, y in self.sharing_pairs:
            pats = self._metro_patients(self.by_npi[x]["metro"], r.randint(30, 50))
            for pat in pats:
                for _ in range(r.randint(2, 4)):
                    day = self._day()
                    self.claim(x, pat, day, label="patient_sharing")
                    self.claim(y, pat, day + timedelta(days=r.randint(0, 2)), label="patient_sharing")

        everyone = [p for p in self.patients]
        for n in self.phantoms:
            spec = self.by_npi[n]["specialty"]
            long_code = {"Psychiatry": "90837", "Physical Therapy": "97110", "Family Medicine": "99215"}[spec]
            units = 4 if spec == "Physical Therapy" else 1
            dead = [p for p in self.patients if p["dod"] is not None]
            for _ in range(r.randint(6, 10)):
                day = self._day()
                for _ in range(r.randint(28, 45)):
                    pat = r.choice(everyone)
                    self.claim(n, pat, day, lines=[self._line(1, long_code, units=units)], label="phantom_billing")
            for pat in r.sample(dead, min(len(dead), r.randint(5, 10))):
                after = pat["dod"].date() + timedelta(days=r.randint(5, 90))
                self.claim(n, pat, min(after, self.end), lines=[self._line(1, long_code, units=units)],
                           label="phantom_billing")

        for triad in self.triads:  # legitimate co-management: different patients, one referral each
            metro = self.by_npi[triad[0]]["metro"]
            pats = self._metro_patients(metro, 120)
            for pat in pats:
                a, b = r.sample(triad, 2)
                self.claim(b, pat, self._day(), a)

        for pcp in self.drive_pcps:  # legitimate vaccination drives: many very short visits
            for _ in range(2):
                day = self._day()
                for pat in self._metro_patients(self.by_npi[pcp]["metro"], r.randint(55, 85)):
                    self.claim(pcp, pat, day, lines=[self._line(1, "99211"), self._line(2, "90471")])

    def make_duplicates(self):
        r = self.rng
        dup_set = set(self.duplicators)
        originals = [c for c in self.claims if c["rendering_npi"] in dup_set]
        by_provider: dict[str, list[dict]] = {}
        for c in originals:
            by_provider.setdefault(c["rendering_npi"], []).append(c)
        for claims in by_provider.values():
            for orig in r.sample(claims, max(3, int(len(claims) * r.uniform(0.12, 0.2)))):
                submitted = orig["submitted_at"] + timedelta(days=r.randint(4, 30), minutes=r.randint(1, 900))
                dup = {**orig, "_id": "CLM-" + ulid(int(submitted.timestamp() * 1000), r),
                       "submitted_at": submitted, "line_items": [dict(li) for li in orig["line_items"]],
                       "adjudication": dict(orig["adjudication"]), "payer_ext": dict(orig["payer_ext"])}
                self.claims.append(dup)
                self.truth_claims[dup["_id"]] = "duplicate"

    # ------------------------------------------------------------------ driver
    def generate(self) -> "Generator":
        self.make_providers()
        self.make_patients()
        self.plan_fraud()
        self._wire_preferences()
        self.make_fraud_claims()
        self.make_legit_claims(max(0, int(self.p.claims * 0.985) - len(self.claims)))
        self.make_duplicates()
        self.claims.sort(key=lambda c: c["submitted_at"])
        self.truth_rings = [{k: v for k, v in ring.items()} for ring in self.rings]
        return self

    def meta(self) -> dict:
        return {"_id": "meta", "seed": self.seed, "profile": self.profile_name, "difficulty": self.difficulty,
                "start": _utc(self.start), "end": _utc(self.end), "providers": len(self.providers),
                "patients": len(self.patients), "claims": len(self.claims),
                "fraudulent_providers": len(self.truth_providers), "rings": len(self.truth_rings),
                "generated_at": datetime.now(timezone.utc)}


def _accumulate(values):
    total = 0.0
    for v in values:
        total += v
        yield total
