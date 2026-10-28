# Held-out benchmark: realistic difficulty

Seeds 101, 102, 103, 104, 105 · profile `demo` · finished 2026-10-27 18:59 UTC

| Detector | Precision | Recall | F1 | Average precision | Precision@25 |
|---|---|---|---|---|---|
| Statistical only (MongoDB) | 0.964 ± 0.034 | 0.699 ± 0.035 | 0.809 ± 0.011 | 0.814 ± 0.013 | 1.000 ± 0.000 |
| Graph only (Neo4j) | 0.960 ± 0.061 | 0.577 ± 0.040 | 0.719 ± 0.036 | 0.936 ± 0.029 | 1.000 ± 0.000 |
| **Fused (noisy-OR)** | 0.946 ± 0.037 | 0.981 ± 0.011 | 0.963 ± 0.023 | 0.984 ± 0.012 | 1.000 ± 0.000 |
| Weighted sum (baseline) | — | — | — | 0.982 ± 0.012 | 1.000 ± 0.000 |

Rings: 31/35 recovered (Jaccard ≥ 0.5), 26 exactly, 0 detected rings matching no planted ring.

Recall by fraud type (fused):

- broker: 0.20 (min 0.00)
- duplicate_billing: 1.00 (min 1.00)
- patient_sharing: 1.00 (min 1.00)
- phantom_billing: 1.00 (min 1.00)
- referral_ring: 1.00 (min 1.00)
- shell_identity: 1.00 (min 1.00)
- unbundling: 1.00 (min 1.00)
- upcoding: 1.00 (min 1.00)

| Seed | Fused F1 | Stat F1 | Graph F1 | Rings |
|---|---|---|---|---|
| 101 | 0.989 | 0.819 | 0.775 | 7/8 |
| 102 | 0.985 | 0.804 | 0.718 | 6/6 |
| 103 | 0.968 | 0.803 | 0.740 | 8/8 |
| 104 | 0.943 | 0.825 | 0.680 | 6/6 |
| 105 | 0.930 | 0.794 | 0.683 | 4/7 |
