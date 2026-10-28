"""Unit tests: no database needed."""
import math
import random
from collections import Counter
from datetime import date

import pytest

from ringlens import edi837
from ringlens.datagen.generator import Generator
from ringlens.datagen.ids import CROCKFORD, is_valid_npi, make_npi, npi_check_digit, ulid
from ringlens.evaluation import average_precision
from ringlens.scoring import DEFAULT_WEIGHTS, attributions, fuse, signal_vector


@pytest.fixture(scope="module")
def world():
    return Generator("tiny", seed=7, end_date=date(2026, 10, 27)).generate()


def test_npi_check_digit_matches_cms_example():
    # CMS publishes 123456789 -> check digit 3 (NPI 1234567893).
    assert npi_check_digit("123456789") == "3"
    assert is_valid_npi("1234567893")
    assert not is_valid_npi("1234567890")


def test_generated_npis_are_valid():
    rng = random.Random(1)
    assert all(is_valid_npi(make_npi(rng)) for _ in range(500))


def test_ulid_shape_and_ordering():
    a, b = ulid(1_700_000_000_000), ulid(1_700_000_000_001)
    assert len(a) == 26 and set(a) <= set(CROCKFORD)
    assert a[:10] < b[:10]  # time prefix sorts


def test_generator_is_deterministic():
    a = Generator("tiny", seed=3, end_date=date(2026, 10, 27)).generate()
    b = Generator("tiny", seed=3, end_date=date(2026, 10, 27)).generate()
    assert [c["_id"] for c in a.claims[:100]] == [c["_id"] for c in b.claims[:100]]
    assert a.truth_providers == b.truth_providers


def test_planted_rings_are_closed_loops(world):
    referrals = Counter((c["referring_npi"], c["rendering_npi"]) for c in world.claims if c["referring_npi"])
    for ring in world.truth_rings:
        m = ring["members"]
        for i in range(len(m)):  # every consecutive member pair must carry referrals, wrapping around
            assert referrals[(m[i], m[(i + 1) % len(m)])] > 0


def test_legitimate_patients_are_never_billed_after_death(world):
    dead = {p["_id"]: p["dod"] for p in world.patients if p["dod"]}
    late = [c for c in world.claims if c["patient_id"] in dead and c["service_date"] > dead[c["patient_id"]]]
    assert late, "phantom billers should create some after-death claims"
    assert all(world.truth_claims.get(c["_id"]) == "phantom_billing" for c in late)


def test_every_claim_has_consistent_totals(world):
    for c in world.claims[:2000]:
        assert c["line_items"]
        assert math.isclose(c["total_charge"], round(sum(li["charge"] for li in c["line_items"]), 2), abs_tol=0.011)


def test_837p_round_trip():
    claim = {"payer_id": "PYR-02", "billing_npi": "1234567893", "rendering_npi": "1234567893",
             "referring_npi": "1891723344", "patient_id": "MBR-00000042", "service_date": "2026-09-14",
             "place_of_service": "11", "diagnosis_codes": ["E11.9", "I10"],
             "line_items": [{"cpt": "99214", "modifiers": ["25"], "units": 1, "charge": 131.0},
                            {"cpt": "93000", "units": 1, "charge": 30.0}]}
    parsed = edi837.parse(edi837.render(claim))
    assert parsed["rendering_npi"] == "1234567893"
    assert parsed["referring_npi"] == "1891723344"
    assert parsed["patient_id"] == "MBR-00000042"
    assert parsed["service_date"] == "2026-09-14"
    assert parsed["diagnosis_codes"] == ["E11.9", "I10"]
    assert [li["cpt"] for li in parsed["line_items"]] == ["99214", "93000"]
    assert parsed["line_items"][0]["modifiers"] == ["25"]


def test_837p_rejects_truncated_transaction():
    with pytest.raises(ValueError):
        edi837.parse("ISA*00~ST*837*0001~")


def test_noisy_or_is_not_diluted_by_quiet_signals():
    only_upcoding = {k: 0.0 for k in DEFAULT_WEIGHTS} | {"upcoding": 1.0}
    assert fuse(only_upcoding, DEFAULT_WEIGHTS) == pytest.approx(100 * DEFAULT_WEIGHTS["upcoding"])
    both = only_upcoding | {"ring": 1.0}
    assert fuse(both, DEFAULT_WEIGHTS) > fuse(only_upcoding, DEFAULT_WEIGHTS)


def test_attributions_sum_to_the_score():
    s = {k: 0.0 for k in DEFAULT_WEIGHTS} | {"ring": 0.8, "billing": 0.5, "shared_identity": 1.0}
    score = fuse(s, DEFAULT_WEIGHTS)
    assert sum(attributions(s, DEFAULT_WEIGHTS, score).values()) == pytest.approx(score, abs=0.05)


def test_signal_vector_explains_what_fired():
    stat = {"specialty": "Cardiology", "z_top_em": 3.5, "top_em_ratio": 0.6, "shares_bank_with": ["1"]}
    graph = {"ring_id": "RING-0001", "ring_suspicion": 0.9, "ring_size": 5, "ring_hub": True, "ring_hub_share": 1.0}
    s, why = signal_vector(stat, graph)
    assert s["upcoding"] == 1.0 and s["ring"] == 0.9 and s["centrality"] == 1.0 and s["shared_identity"] == 1.0
    assert {"upcoding", "ring", "centrality", "shared_identity"} <= set(why)


def test_average_precision():
    ranked = [("a", 9), ("x", 8), ("b", 7), ("y", 1)]
    assert average_precision(ranked, {"a", "b"}) == pytest.approx((1 / 1 + 2 / 3) / 2, abs=1e-4)
