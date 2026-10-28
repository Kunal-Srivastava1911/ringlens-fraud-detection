# Demo script (about 10 minutes)

Before the demo: run `.\scripts\start.ps1`, open http://localhost:8000. Set the laptop to its NVIDIA/hybrid graphics mode for the 3D view. On the **Pipeline** page, start the claim feed at about 15 claims/s so every screen shows live traffic.

| # | Page | What to show | What it demonstrates |
|---|---|---|---|
| 1 | **Command Center** | KPI tiles; the live intake ticker; the claims-per-day chart. | One MongoDB collection is the system of record for ~350k claims. |
| 2 | **Command Center → quadrant** | Blue dots (statistics only) sit on the x-axis, orange dots (graph only) on the y-axis, aqua dots get both. | The synopsis claim that neither store is enough on its own. |
| 3 | **Network** | Rotate the 3D galaxy: 8 metro clusters, red ring edges with particles. Search a ring member to fly to it. | Neo4j property graph; REFERS_TO aggregated from per-claim REFERRED edges. |
| 4 | **Rings → a ring** | Particles circulating the loop. In the radar, patient recirculation is ~90–100%. Patient journeys show the same member going round the loop many times. | SCC finds the loop; the ring analysis separates it from legitimate co-managing specialists, which also form cycles but refer each patient once. |
| 5 | **Ring → Confirm fraud** | The toast reports that N documents were committed in one transaction. | Multi-document ACID transaction, `w: majority`. |
| 6 | **Provider dossier** (a ring hub) | Attribution waterfall (blue = MongoDB, orange = Neo4j), billing against the peer band, E/M mix vs peers, ego network. | Explainable fusion; `$setWindowFields` z-scores; `$percentile` bands. |
| 7 | **Pipeline → Plant ring & analyze** | Claims flow through intake (some as X12 837P), lag stays at a few ms, analytics runs, then a **New referral ring detected** toast appears. | Change streams → idempotent upserts → GDS → fusion, end to end and live. |
| 8 | **Pipeline → Pause**, wait 10 s, **Resume** | Parity bars fall behind, then converge. The resume token is shown. | Eventual consistency; resumable change streams; CAP (intake never waits for Neo4j). |
| 9 | **Pipeline → Crash** | The worker applies a batch and dies before saving its token. The supervisor restarts it, those events are redelivered, and **Duplicate edges** stays at 0. | At-least-once delivery + idempotent upsert = each change takes effect exactly once. |
| 10 | **Claims → Submit a claim → Break it** | MongoDB's own validation error, flattened into field / rule / value / expected. Switch to X12 837P to show the raw EDI next to the stored document. | JSON Schema validation at write time; staging with TTL; embedded line items. |
| 11 | **DB Internals → Index race** | Same query with and without its index: e.g. 50 vs 346,000 documents examined. | ESR compound, partial, multikey indexes. |
| 12 | **DB Internals → Traversal benchmark** | All three methods are milliseconds at this size (say so honestly), but only Cypher can list closed loops through a provider. | Index-free adjacency, and expressiveness rather than raw speed. |
| 13 | **Evaluation Lab** | PR curves; recall by fraud type; the held-out seed tables (mean ± std); drag a weight and watch the metrics move. | Ground truth makes precision and recall measured rather than asserted. |

Toggle **Ground truth** (top bar) at any point to show which detections match planted fraud.

## Likely questions

- **Why two databases?** Claims are documents: nested line items, payer-specific shapes, huge volume. Rings are cycles in a relationship network. Each store answers one of these well and the other poorly. The quadrant and the per-type recall chart show this with numbers.
- **What if Neo4j is down?** Intake keeps working (MongoDB is the system of record). The worker resumes from its token when Neo4j returns. If the token has fallen off the 2 GB oplog, it re-snapshots automatically.
- **Isn't synthetic data too easy?** The data includes legitimate look-alikes: co-managing specialists who refer in cycles, big practices, high-acuity specialties and vaccination drives. Thresholds were tuned on seed 1 and evaluated on held-out seeds 101–105, at both `realistic` and `hard` difficulty. See `docs/results/`.
- **Why noisy-OR?** A weighted average dilutes one decisive signal (a lone upcoder) with the quiet ones. A noisy-OR lets each signal flag a provider on its own, and corroborating evidence accumulates. The Lab page compares it with a weighted sum.
