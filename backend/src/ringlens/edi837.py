"""A faithful subset of the X12 837P (professional claim) transaction: enough segments to
carry everything RingLens stores, rendered and parsed with the standard delimiters
(~ segment, * element, : component)."""
from __future__ import annotations

from datetime import date, datetime


def _icd_out(code: str) -> str:
    return code.replace(".", "")


def _icd_in(code: str) -> str:
    return code if len(code) <= 3 else f"{code[:3]}.{code[3:]}"


def render(claim: dict, control: int = 1) -> str:
    """Canonical claim fields -> 837P text."""
    now = datetime.now()
    total = claim.get("total_charge", sum(float(li["charge"]) for li in claim["line_items"]))
    dos = claim["service_date"].strftime("%Y%m%d") if isinstance(claim["service_date"], (date, datetime)) \
        else claim["service_date"].replace("-", "")
    segs = [
        f"ISA*00*          *00*          *ZZ*{claim['payer_id'].replace('-', ''):<15}*ZZ*RINGLENS       "
        f"*{now:%y%m%d}*{now:%H%M}*^*00501*{control:09d}*0*P*:",
        f"GS*HC*{claim['payer_id'].replace('-', '')}*RINGLENS*{now:%Y%m%d}*{now:%H%M}*{control}*X*005010X222A1",
        "ST*837*0001*005010X222A1",
        f"BHT*0019*00*{control:09d}*{now:%Y%m%d}*{now:%H%M}*CH",
        f"NM1*85*2*BILLING PROVIDER*****XX*{claim['billing_npi']}",
        f"NM1*IL*1*MEMBER*****MI*{claim['patient_id']}",
        f"CLM*{claim.get('payer_claim_number', control)}*{total:.2f}***"
        f"{claim.get('place_of_service', '11')}:B:1*Y*A*Y*Y",
        f"DTP*472*D8*{dos}",
        "HI*" + "*".join(("ABK" if i == 0 else "ABF") + ":" + _icd_out(c) for i, c in enumerate(claim["diagnosis_codes"])),
    ]
    if claim.get("referring_npi"):
        segs.append(f"NM1*DN*1*REFERRING*****XX*{claim['referring_npi']}")
    segs.append(f"NM1*82*1*RENDERING*****XX*{claim['rendering_npi']}")
    for i, li in enumerate(claim["line_items"], 1):
        code = ":".join(["HC", li["cpt"], *li.get("modifiers", [])])
        segs.append(f"LX*{i}")
        segs.append(f"SV1*{code}*{li['charge']:.2f}*UN*{li['units']}***1")
    segs.append(f"SE*{len(segs) - 1}*0001")
    segs += [f"GE*1*{control}", f"IEA*1*{control:09d}"]
    return "~\n".join(segs) + "~"


def parse(text: str) -> dict:
    """837P text -> canonical claim fields. Raises ValueError on a malformed transaction."""
    out: dict = {"line_items": [], "diagnosis_codes": [], "referring_npi": None}
    seen = set()
    for raw in text.replace("\n", "").split("~"):
        if not raw.strip():
            continue
        el = raw.strip().split("*")
        tag = el[0]
        seen.add(tag)
        if tag == "NM1" and len(el) > 9:
            role, npi = el[1], el[9]
            if role == "85":
                out["billing_npi"] = npi
            elif role == "82":
                out["rendering_npi"] = npi
            elif role == "DN":
                out["referring_npi"] = npi
            elif role == "IL":
                out["patient_id"] = npi
        elif tag == "CLM":
            out["payer_claim_number"] = el[1]
            out["place_of_service"] = el[5].split(":")[0] if len(el) > 5 else "11"
        elif tag == "DTP" and el[1] == "472":
            d = el[3]
            out["service_date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        elif tag == "HI":
            out["diagnosis_codes"] = [_icd_in(x.split(":")[1]) for x in el[1:] if ":" in x]
        elif tag == "SV1":
            comp = el[1].split(":")
            out["line_items"].append({"cpt": comp[1], "modifiers": comp[2:], "charge": float(el[2]),
                                      "units": int(float(el[4]))})
        elif tag == "GS":
            out["payer_id"] = el[2][:3] + "-" + el[2][3:]
    for required in ("ISA", "ST", "CLM", "SE"):
        if required not in seen:
            raise ValueError(f"837P transaction is missing its {required} segment")
    if "rendering_npi" not in out:
        out["rendering_npi"] = out.get("billing_npi")
    return out
