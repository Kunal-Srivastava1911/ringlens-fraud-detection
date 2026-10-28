# RingLens: healthcare fraud and referral-ring analytics

**Polyglot persistence with MongoDB + Neo4j** · DBMS course project, Vellore Institute of Technology
Kunal Srivastava (24BCE2476) · Samarth Saxena (24BCT0307) · Shrijit Sinha (24BCE2469)

Healthcare fraud comes in two shapes:
- **Anomalies inside claims:** upcoding, phantom billing, unbundling, duplicates. These are statistical questions over documents.
- **Collusion between providers:** above all, *referral rings* that pass patients and money around a closed loop. These are graph questions.

RingLens answers each in the database built for it:

- **MongoDB 8.0** is the system of record. It holds every claim as a validated document and computes statistical signals with the aggregation framework.
- **Neo4j 5.26 + Graph Data Science** holds the referral network and finds rings with SCC, Louvain, PageRank, betweenness and node similarity.
- A **change-stream CDC worker** keeps the graph in step.
- A **noisy-OR scoring engine** fuses both kinds of evidence and explains every score.
- An **8-page live console** makes all of it visible.

Detection quality is **measured** against fraud planted in synthetic data, on held-out datasets.

```
 payer feeds (2 JSON shapes + X12 837P)      live feed · ring planter
            └──────────────┬──────────────────────┘
                           ▼
        claims_staging (TTL 24 h) → normalize → $jsonSchema → claims
 ┌─ MongoDB 8.0 (replica set rs0): system of record ───────────────────────┐
 │ claims (line items embedded) · providers · patients · cases · audit_log  │
 │ aggregation: $setWindowFields z-scores, rolling velocity, rules → $merge  │──┐ statistical
 └──────────────────────────────────────────────────────────────────────────┘  │ signals
        │ change streams: snapshot handoff · resume token · pre-images          │
        ▼                                                                      │
   CDC worker: micro-batches → idempotent UNWIND … MERGE (supervised)          │
        ▼                                                                      │
 ┌─ Neo4j 5.26 + GDS 2.13: graph analytics ─────────────────────────────────┐  │
 │ (Provider)-[:REFERRED {claim_id}]->(Provider) · -[:TREATED]->(Patient)    │  │
 │ SCC · Louvain · PageRank · betweenness · node similarity · WCC → rings    │  │
 └──────────────────────────────────────────────────────────────────────────┘  │
        │ graph signals                                                        │
        ▼                                                                      │
   fusion: noisy-OR + attributions + reasons ◄─────────────────────────────────┘
        ▼
   FastAPI (REST + WebSocket fed by change streams) → React console
```

## Results

The thresholds and weights were tuned on **seed 1**. The numbers below come from **five fresh datasets (seeds 101–105)** the system was never tuned on. Each dataset has about 345k claims, 1,200 providers and roughly 70 planted fraudulent providers.

**`realistic` difficulty** (mean ± std over 5 seeds):

| Detector | Precision | Recall | F1 | Average precision |
|---|---|---|---|---|
| Statistical only (MongoDB) | 0.964 ± 0.034 | 0.699 ± 0.035 | 0.809 ± 0.011 | 0.814 ± 0.013 |
| Graph only (Neo4j) | 0.960 ± 0.061 | 0.577 ± 0.040 | 0.719 ± 0.036 | 0.936 ± 0.029 |
| **Fused (noisy-OR)** | **0.946 ± 0.037** | **0.981 ± 0.011** | **0.963 ± 0.023** | **0.984 ± 0.012** |

**`hard` difficulty** has more camouflaged rings, fewer hops per patient, slower loops and more legitimate look-alikes:

| Detector | Precision | Recall | F1 | Average precision |
|---|---|---|---|---|
| Statistical only (MongoDB) | 0.927 ± 0.035 | 0.618 ± 0.045 | 0.740 ± 0.034 | 0.731 ± 0.029 |
| Graph only (Neo4j) | 0.979 ± 0.026 | 0.568 ± 0.054 | 0.717 ± 0.044 | 0.931 ± 0.040 |
| **Fused (noisy-OR)** | **0.937 ± 0.021** | **0.972 ± 0.025** | **0.954 ± 0.021** | **0.976 ± 0.023** |

- **Referral rings:** 31 of 35 planted rings recovered at `realistic` difficulty (member Jaccard ≥ 0.5, 26 of them exactly) and 32 of 35 at `hard` (25 exactly). No detected ring matched nothing planted.
- **Each store misses a class of fraud:**
  - Statistics alone catch under half of ring members, because camouflaged rings bill normally per claim. This was measured on the tuning dataset.
  - The graph alone catches no upcoders, phantom billers or unbundlers.
  - Fused recall is 1.00 for every fraud type except brokers.
- **Known weakness:** *brokers* (a provider bridging two rings) are caught only 20% of the time. Betweenness within specialty is too weak a signal on its own.

Full per-seed tables: [docs/results/](docs/results/). The **Evaluation Lab** page shows PR curves, ablation and live re-weighting.

Systems numbers on this laptop (i9-14900HX, 32 GB):

| Step | Time |
|---|---|
| Generate + load 345k validated claims | 12 s |
| CDC snapshot into Neo4j (345k TREATED + 154k REFERRED edges) | 19 s |
| CDC lag during live intake | ~3 ms per batch |
| Full analytics run (aggregations + GDS + fusion + evaluation) | 15 s |
| Indexed provider-timeline query vs forced collection scan | ~0 ms / 50 docs vs ~110 ms / 346k docs |

## Run it (Windows)

Requirements: **Python 3.11+**, **Node 20+ with pnpm**, and an internet connection the first time. No Docker, no admin rights and no system-wide installs are needed.

```powershell
.\scripts\start.ps1          # first run downloads portable MongoDB/Neo4j/Java into .runtime\ (~700 MB)
```

The script:
1. Installs whatever is missing.
2. Starts MongoDB (single-member replica set `rs0` on 127.0.0.1:27017) and Neo4j (bolt://127.0.0.1:7687).
3. Generates the dataset if the database is empty.
4. Starts the CDC worker and the API.
5. Builds the console and opens **http://localhost:8000**.

| Command | Purpose |
|---|---|
| `.\scripts\start.ps1 -Dev` | Vite dev server with hot reload on :5180 |
| `.\scripts\start.ps1 -Reseed` | Regenerate the synthetic world |
| `.\scripts\stop.ps1` | Stop everything |
| `.\scripts\db.ps1 start\|stop\|status\|reset` | Just the databases |
| Neo4j Browser | http://localhost:7474 (user `neo4j`, password `ringlens-dev`) |

CLI (`backend\.venv\Scripts\ringlens.exe`):

```
ringlens doctor                         check MongoDB replica set, Neo4j, GDS, APOC
ringlens seed --profile demo --seed 1   tiny | demo | large; --difficulty easy | realistic | hard
ringlens cdc                            supervised change-stream worker
ringlens analyze                        signals → barrier → GDS → fusion → evaluation
ringlens explain                        IXSCAN vs forced COLLSCAN for each showcase query
ringlens bench --seeds 101-105          held-out evaluation (needs the CDC worker running)
ringlens api                            REST + WebSocket on :8000
```

Tests: `cd backend; .\.venv\Scripts\python.exe -m pytest`. The unit tests need nothing; the integration tests need the databases running.

## The console

| Page | What it shows |
|---|---|
| **Command Center** | KPIs, live intake ticker, claims per day, the *statistical vs graph* quadrant, top risks, ring cards with animated loops |
| **Network** | 3D/2D WebGL galaxy of the referral graph. Ring edges glow with flowing particles; bloom in cinematic mode; fly-to search |
| **Rings** | Ring triage and dossier: animated loop, ring-analysis radar, weekly activity, money flows, patient journeys, confirm/clear in one transaction |
| **Providers** | Ranked worklist. Each dossier has a score gauge, signal attribution, peer billing band (`$percentile`), E/M mix vs peers, ego network, case workflow |
| **Claims** | Document inspector with the raw 837P beside the stored JSON, keyset pagination, and a submit form showing MongoDB's validation errors |
| **Pipeline** | Animated CDC flow, store parity, lag chart, event stream, and chaos controls: live feed, plant a ring, pause, crash |
| **Evaluation Lab** | Statistical vs graph vs fused, PR curves, recall by type, ablation, held-out tables, live weight sliders |
| **DB Internals** | Index race, collections and index usage, replica-set state, Neo4j constraints, traversal benchmark |

The **Ground truth** toggle in the top bar shows planted fraud next to every detection.

## How each synopsis concept is implemented

| Synopsis concept | Implementation | Where to see it |
|---|---|---|
| Embed line items; reference providers and patients | [schema.py](backend/src/ringlens/schema.py) | Claims → document inspector |
| JSON Schema validation at write time | `$jsonSchema`, `validationLevel: strict`, `validationAction: error` | Claims → *Break it* |
| ESR compound, partial, multikey and TTL indexes | [schema.py](backend/src/ringlens/schema.py) `INDEXES` | Internals → index race |
| Window functions → peer z-scores → materialized signals | [signals_mongo.py](backend/src/ringlens/signals_mongo.py): `$setWindowFields`, 7-day `range` windows, `$merge` | Provider dossier; Internals → pipelines |
| Replica set, majority writes, secondary reads | [db.py](backend/src/ringlens/db.py): `w: majority, j: true`; analytics `secondaryPreferred` + `readConcern: majority` | Internals → topology |
| Time-series collections | `pipeline_metrics` (TTL 3 days), `score_history` | Pipeline lag chart |
| ACID transactions | Case and ring decisions in [api.py](backend/src/ringlens/api.py) | Ring → Confirm fraud |
| Property graph, index-free adjacency | [cdc.py](backend/src/ringlens/cdc.py) model + uniqueness constraints | Network; Internals → Neo4j |
| SCC, Louvain, PageRank, betweenness | [signals_graph.py](backend/src/ringlens/signals_graph.py) | Rings, Network |
| Bipartite projection → shared patients | GDS node similarity on Provider–Patient, minus pairs with a referral edge | Provider dossier (violet links) |
| CDC with change streams, resume tokens, idempotent upsert | [cdc.py](backend/src/ringlens/cdc.py): snapshot handoff, server-side filter, pre-image deletes, token saved after commit, history-lost re-snapshot | Pipeline → pause / crash |
| Eventual consistency, CAP, ACID vs BASE | Graph lags MongoDB by the measured CDC latency; a cross-store barrier before analytics | Pipeline parity + lag |
| Composite fraud score | [scoring.py](backend/src/ringlens/scoring.py): noisy-OR, log-space attributions | Dossier waterfall; Lab sliders |
| Synthetic data with planted rings, precision and recall | [datagen/generator.py](backend/src/ringlens/datagen/generator.py), [evaluation.py](backend/src/ringlens/evaluation.py), [bench.py](backend/src/ringlens/bench.py) | Evaluation Lab |

## The synthetic world

Eight US metros and 16 specialties. Practice sizes are heavy-tailed, and history covers 18 months with weekday and seasonal patterns. Legitimate care flows primary care → specialist → ancillary.

**Planted fraud** (randomized per seed):
- referral rings of 3–8 providers (camouflaged or inflated), with brokers bridging rings
- patient-sharing pairs
- upcoders
- phantom billers (after-death claims, impossible days)
- unbundling labs
- duplicate billers
- shell identities sharing a bank account

**Legitimate look-alikes**, so detection isn't trivial:
- co-managing specialist triads that refer in cycles
- group practices sharing a building
- big busy practices
- high-acuity specialties
- vaccination-drive days

Ground truth lives in a separate `ringlens_truth` database that no detector reads.

## Layout

```
backend/src/ringlens/   settings, db, schema, datagen/, intake + edi837, cdc, signals_mongo, signals_graph,
                        scoring, evaluation, bench, jobs, internals, live (WebSocket hub), api, cli
backend/tests/          unit (no DB) + integration (live stores)
frontend/src/           React 19 + TypeScript + Vite + Tailwind 4: pages/, components/ (Galaxy, RingOrbit, Chart), lib/
scripts/                setup-native, db, start, stop (PowerShell)
docs/                   demo script, benchmark results
```
