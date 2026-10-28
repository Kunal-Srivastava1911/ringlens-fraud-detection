# RingLens: implementation plan

Healthcare fraud detection and referral-ring analytics with polyglot persistence (MongoDB + Neo4j).
DBMS course project, Vellore Institute of Technology.

**Status (28 Oct 2026):** built. Phases 0–6 and 8 are done; Phase 7 (sharded cluster) is not. See the README for how to run it and for results.

Where the build deviated from this plan:
- **Native install instead of Docker** (your call). `scripts/setup-native.ps1` downloads portable MongoDB 8.0.15, mongosh, a private Java 21, Neo4j 5.26.12, GDS 2.13.7 and APOC into `.runtime/`. MongoDB runs as a single-member replica set. The `cluster` profile (§5) was therefore not built: replica-set internals are shown for the single member, and sharding remains a design described below.
- **Synchronous API.** The API uses sync PyMongo in FastAPI's thread pool rather than the async driver. The WebSocket hub runs change streams on a background thread.
- **Time-series collections can't be watched by change streams**, so the CDC worker also writes each batch's stats to `cdc_offsets`, which the live hub watches.
- **Benchmark at both difficulties.** The held-out benchmark was run at `realistic` and `hard` (seeds 101–105). Results are in `docs/results/`; the brokers weakness is reported there.
- **Stretch goals** (§19) were not built.

The original plan follows unchanged.

---

## 1. Goal

Build the system described in the synopsis as a local platform that starts with one command on Windows.

- **MongoDB** is the system of record for claims and computes the statistical fraud signals.
- **Neo4j** holds the provider referral graph and finds rings with graph algorithms.
- A **change-stream CDC worker** keeps Neo4j in step with MongoDB.
- A **scoring engine** fuses both kinds of evidence and explains every score.
- A **React console** shows all of this live: a 3D referral network, ring dossiers, a pipeline monitor, an evaluation lab and a database-internals lab.
- **Synthetic data with planted fraud** provides ground truth, so detection quality is measured rather than asserted.

## 2. The existing prototype

The nested `fraud-detection-project/` folder holds a working prototype. Its backend design is sound and covers most of the synopsis. These problems justify a rebuild rather than an extension:

| Area | Problem |
|---|---|
| Frontend | A plain vanilla-JS admin page. The graph library loads from unpkg, so it breaks offline. It is not "neat and flashy". |
| Tooling | Mac only (`run.sh`, `.venv/bin/…`, Colima, Mac instructions). |
| Scale | 240 providers and 20k claims is too small for index, sharding or traversal costs to show up. |
| Evaluation | It uses four hard-coded ring blueprints, with thresholds tuned and tested on the same seed. The reported F1 of 0.988 is optimistic and easy to challenge in a viva. |
| CDC correctness | Deleting a claim scans every `TREATED` relationship (`WHERE cid IN t.claim_ids`). An update that changes the referring provider leaves a stale `REFERRED` edge. Flag-only updates flood the stream. The consumer runs as a thread inside the API. |
| Intake | `_next_claim_id` reads the current max and adds one, so concurrent submissions race to the same id. |
| Sharding | It is a separate script with its own database. The app never runs on the sharded cluster, so the dashboard cannot show it. |

**Decision:** rebuild at the repository root and port the good pieces into the new structure:
- window-function z-scores
- the duplicate and unbundling pipelines
- the snapshot-to-stream handoff and pre-image deletes
- noisy-OR fusion with reasons
- the sharding init script

Zip the prototype into the session scratchpad as a temporary safety copy, then delete the nested folder.

## 3. Machine findings that shape the plan

| Finding | Consequence |
|---|---|
| Docker Desktop 29.4 and Compose v5.1 are installed, but the engine is not running | Start it in Phase 0 |
| i9-14900HX, 32 GB RAM, WSL2 | A sharded cluster (8 mongo processes) plus Neo4j fits comfortably |
| An RTX 4070 is present but inactive, so Intel UHD drives the display | Switch the laptop to hybrid or dGPU mode for demos; the UI degrades gracefully on the iGPU |
| `host.docker.internal` resolves to 172.20.10.2, a phone-hotspot address | Don't base replica-set addressing on it, because it goes stale on another network or offline (§5) |
| Python 3.11.9, Node 24 and pnpm 10 are installed; uv is not | Install uv with one `pip install` to get a locked environment |
| Port 5173 is taken by another Node process | Run the Vite dev server on 5180 |

## 4. Architecture

```
   payer feeds (JSON shapes, X12 837P subset)         live feed simulator / ring planter
                    └──────────────────┬─────────────────────────┘
                                       ▼
             claims_staging (TTL) → normalize → $jsonSchema validation
                                       ▼
 ┌─ MongoDB 8.0: system of record ─────────────────────────────────────────┐
 │ claims (line items embedded), providers, patients, cases, audit_log     │
 │ aggregation framework → provider_signals, claim flags   (statistical)   │──┐
 └─────────────────────────────────────────────────────────────────────────┘  │
            │ change streams: snapshot handoff, resume token, pre-images      │
            ▼                                                                 │
    cdc-worker: micro-batches → idempotent UNWIND … MERGE                     │
            ▼                                                                 │
 ┌─ Neo4j 5.26 + GDS: graph analytics ─────────────────────────────────────┐  │
 │ Provider -REFERRED-> Provider, Provider -TREATED-> Patient, bank, addr  │  │
 │ SCC, Louvain, PageRank, betweenness, node similarity → ring analysis    │  │
 └─────────────────────────────────────────────────────────────────────────┘  │
            │ graph signals                              statistical signals  │
            ▼                                                                 │
    scoring engine: noisy-OR fusion + attributions ◄──────────────────────────┘
            ▼   (scores, rings and alerts are written back to MongoDB)
    FastAPI: REST + WebSocket (live events from change streams) → React console
```

| Layer | Choice | Why |
|---|---|---|
| System of record | MongoDB 8.0 | Change streams with pre-images and `wallTime`, `$setWindowFields`, `$percentile`, time-series collections, compound hashed shard keys |
| Graph engine | Neo4j 5.26 LTS Community + GDS + APOC | GDS Community has every algorithm the synopsis names; APOC adds the BFS used in the traversal benchmark |
| Backend | Python 3.11, FastAPI, PyMongo (sync for jobs, native async for the API), neo4j driver, Typer CLI | One language for the generator, pipelines, CDC, scoring and API |
| Frontend | React + TypeScript + Vite, Tailwind + shadcn/ui, Motion, TanStack Query/Table, react-force-graph (3D/2D), ECharts | WebGL graph, animated charts (sankey, radar, heatmap), polished components |
| Contracts | OpenAPI → generated TypeScript types | Typed end to end |
| Packaging | Docker Compose profiles + PowerShell scripts | One command on Windows; fonts and libraries are bundled so the demo works offline |

## 5. Deployment topologies

There is one `infra/compose.yml` with profiles. The code is the same in every profile; the topology comes from environment variables. All ports bind to 127.0.0.1.

| Profile | What runs | Used for | How the app connects |
|---|---|---|---|
| `lite` (development default) | MongoDB single-member replica set (2 GB oplog) + Neo4j | Everything except real replication and sharding | `directConnection=true` to `localhost:27017` |
| `cluster` (demo) | Config-server replica set, 2 shards × 3-member replica sets, `mongos`, Neo4j | Sharding, replication and failover demos | Through `mongos` only, so replica-set hostnames never have to resolve on Windows. Shard members are also published on ports 27101–03 and 27201–03 for direct status and `stepDown` calls. |
| `app` (add-on) | `api`, `cdc-worker`, `web` (nginx serves the built SPA and proxies `/api` and `/ws`) | The one-command demo | Container network |
| `bench` | A separate MongoDB database and a throwaway Neo4j on port 7688 | Multi-seed evaluation without touching the demo data (Neo4j Community allows only one user database) | Container network |

## 6. Data model

### MongoDB (`ringlens` database)

| Collection | Contents | DBMS concepts exercised |
|---|---|---|
| `claims` | One claim per document. Line items are embedded; provider NPIs and the patient id are references. | `$jsonSchema` (strict, error); ESR compound `{rendering_npi, service_date: -1, total_charge}`; multikey `{line_items.cpt, service_date}`; partial `{flags, service_date}` where `flagged: true`; pre- and post-images; sharded on `{rendering_npi: "hashed", service_date: 1}` in `cluster` |
| `claims_staging` | Raw payer payloads and whether each was normalized or rejected | TTL index (24 h) |
| `providers`, `patients` | Master data: NPI with a valid Luhn check digit, address, bank-account hash, date of death | Validators; indexes on specialty, address key and bank hash |
| `provider_signals` | Materialized statistical signals | `$merge` target |
| `graph_signals`, `rings` | Exported from Neo4j. Rings keep stable ids across runs. | |
| `provider_scores`, `score_history` | Fused score with per-signal attribution and reasons, plus its history over time | Partial index on flagged providers; time-series collection |
| `cases`, `audit_log` | Analyst workflow | Multi-document transactions, `w: "majority"` |
| `cdc_offsets`, `pipeline_control` | Resume token, last applied cluster time, pause and crash switches | |
| `pipeline_metrics` | CDC lag and throughput samples | Time-series collection with TTL |
| `referral_edges` | Aggregated referral edge list | `$graphLookup` target for the traversal benchmark |
| `ringlens_truth.*` (separate database) | Ground-truth labels for providers, rings and claims | Never read by any detector |

Claim document (abridged):

```jsonc
{
  "_id": "CLM-01JBXW5Q7Z8T4K3M2N9P6R1SVA",   // ULID: no read-max+1 race; not the shard key
  "payer": { "id": "PYR-02", "format": "837P" },
  "rendering_npi": "1407832190", "billing_npi": "1407832190",
  "referring_npi": "1891723344",               // null when there is no referral
  "patient_id": "MBR-00418822",
  "service_date": "2026-09-14", "submitted_at": "2026-09-17T10:22:05Z",
  "place_of_service": "11", "diagnosis_codes": ["E11.9", "I10"],
  "line_items": [
    { "line": 1, "cpt": "99214", "modifiers": ["25"], "units": 1, "charge": 135.00, "minutes": 30 },
    { "line": 2, "cpt": "93000", "units": 1, "charge": 30.00 }
  ],
  "total_charge": 165.00, "status": "paid",
  "adjudication": { "allowed": 121.40, "paid": 97.12 },
  "payer_ext": { "prior_auth": "PA481920" },   // shape varies by payer
  "flagged": true, "flags": ["upcoding"]        // written by detectors
}
```

### Neo4j

```
(:Provider {npi, name, specialty, metro, lat, lon})
(:Patient {id})   (:BankAccount {hash})   (:Address {key})
(:Provider)-[:REFERRED {claim_id, date, amount, patient_id}]->(:Provider)   one per referral claim
(:Provider)-[:TREATED  {claim_id, date, amount}]->(:Patient)                 one per claim
(:Provider)-[:PAID_TO]->(:BankAccount)     (:Provider)-[:LOCATED_AT]->(:Address)

derived by analytics:
  (:Provider)-[:REFERS_TO {count, amount, patients}]->(:Provider)
  (:Provider)-[:SHARES_PATIENTS {jaccard, shared}]-(:Provider)
  scc, community, pagerank, betweenness, ring_id on Provider
```

Every business key has a uniqueness constraint, including `REFERRED.claim_id` and `TREATED.claim_id`, so the CDC upserts are idempotent by construction. Relationship uniqueness constraints need Neo4j 5.7 or later. Phase 2 confirms they work in Community; if not, it falls back to relationship indexes.

## 7. Synthetic data

**World model**
- 8 US metros and 16 specialties covering primary care, specialists and ancillary services (lab, imaging, PT, DME).
- Practice sizes are heavy-tailed, and some legitimate group practices share an address.
- About 60 CPT/HCPCS codes with prices and minutes: E/M levels, a lab panel and its components, imaging, and timed therapy and psychotherapy codes. About 40 ICD-10 codes tied to specialties.
- Patients have an age, sex, home metro and chronic-condition load that drives visit frequency. Some have a date of death.
- History covers the 18 months ending today, with weekday and seasonal patterns.
- Legitimate referrals flow primary care → specialist → ancillary, with regional preferential attachment. There are occasional back-referrals and real care-team loops, such as an oncology–imaging–lab cancer centre.

**Planted fraud** (counts and sizes are randomized per seed)

| Scenario | How it is planted | Meant to be caught by |
|---|---|---|
| Referral ring (3–8 providers) | Recruited patients recirculate around the cycle and a hub collects. Some rings share a bank account or address. Camouflaged rings keep per-claim billing normal. | SCC + ring analysis + PageRank |
| Broker | One provider bridges two rings | Betweenness |
| Patient-sharing collusion | Pairs bill the same recruited patients with no referral edge between them | Node similarity |
| Upcoding | E/M mix skewed to 99214/99215 | Specialty-relative z-score |
| Phantom billing | Services after the date of death; more timed minutes in a day than a person can work; patients with no other history | Aggregation rules, velocity |
| Unbundling | Panel components billed separately | Multikey rule |
| Duplicate billing | Resubmissions under new claim ids | `$group` rule |
| Shell identities | Unrelated providers paid into one bank account | Shared identity |

**Hard negatives** are legitimate look-alikes, so detection isn't trivial:
- big, busy practices
- specialties that naturally bill a lot of top-level E/M
- real care-team loops and reciprocal referral pairs
- group practices sharing a building
- vaccination-drive days

**Knobs**
- Profiles: `tiny` (tests, ~10k claims), `demo` (1,200 providers, 30k patients, ~350k claims), `large` (3,000 providers, 120k patients, ~1.5M claims).
- Difficulty: `easy | realistic | hard`, which controls camouflage, noise and decoys.
- A deterministic `--seed`.

Ground-truth labels go to the separate `ringlens_truth` database. Rings planted live from the UI are recorded there too, so the evaluation stays honest after a demo.

**Intake.** Bulk seeding inserts directly with unordered `insert_many`, which is still validated. The live feed takes the realistic route: staging → normalize → validate → `claims`. Normalization handles two JSON payer shapes and an X12 837P subset.

## 8. Detection

### 8.1 MongoDB statistical signals

These run in the aggregation framework and `$merge` into `provider_signals`. In `cluster` they read from secondaries.

| Signal | Technique |
|---|---|
| Peer-relative billing | `$group` per provider, then `$setWindowFields` partitioned by specialty (`$avg`, `$stdDevPop`, `$rank`), then a z-score on log billed per claim and per patient |
| Upcoding | Share of top-level E/M lines via `$filter`, z-scored within specialty |
| Billing velocity | `$setWindowFields` with a 7-day `range` window (`unit: "day"`), compared with the provider's own 90-day baseline |
| Impossible days | `$unwind` timed lines and sum minutes per provider per day |
| Services after death | `$lookup` against patients |
| Duplicates | `$group` on provider, patient, date and the `$sortArray` of codes |
| Unbundling | Multikey `$match` on component codes plus a `$filter` count |
| Shared identity | `$group` on bank hash and address key |
| Peer bands for charts | `$percentile` (p25/p50/p75) per specialty and month |

Claim-level hits are written back as `flags`, which feeds the partial index. The change stream filters out these flag-only updates so they don't flood the CDC.

### 8.2 Graph signals

GDS is called from Cypher, so every algorithm is visible as a query.

| Algorithm | Projection | Finds |
|---|---|---|
| Strongly connected components | Strong referral subgraph: `REFERS_TO` with count ≥ τ and distinct patients ≥ τp | Ring candidates (closed loops) |
| Louvain | Undirected, weighted referral graph | Dense collusive clusters that aren't strict cycles |
| PageRank (weighted) | Directed referral graph | The hub a ring funnels value to |
| Betweenness | Directed referral graph | Brokers bridging clusters |
| Node similarity (Jaccard) | Bipartite Provider–Patient | Pairs that share implausibly many patients with no referral edge |
| WCC | Provider–BankAccount/Address | Identity clusters |

### 8.3 Ring analysis

Each candidate gets a suspicion score built from these signals, which together separate rings from legitimate care-team loops:
- patient recirculation (the same patients travel the loop)
- internal-flow share
- reciprocity and density
- temporal compactness
- hub concentration
- identity overlap

Candidates are matched to the previous run by member Jaccard. Ring ids therefore stay stable, and only a genuinely new ring raises an alert.

### 8.4 Fusion

Each signal is normalized to [0, 1] with a documented transform and combined with a noisy-OR: `score = 1 − Π(1 − wᵢ·sᵢ)`. A single decisive signal, such as a lone upcoder's, is not diluted by quiet ones, and corroborating signals accumulate.

Each score stores per-signal attributions (shares in log space, shown as a waterfall) and plain-English reasons. Weights are versioned in `scoring_config`. For comparison, the engine also computes statistical-only, graph-only and weighted-sum variants. Thresholds τ and the weights are tuned on dev seeds only (§10).

## 9. CDC pipeline

1. **Snapshot handoff.** Record the `operationTime`, bulk-copy in `UNWIND` batches, then open the stream at `startAtOperationTime`, so nothing written during the copy is lost.
2. **Stream.** Use `fullDocument: updateLookup` with pre-images. A server-side `$match` drops updates that touch only detector-owned fields.
3. **Apply.** Process micro-batches of up to 1,000 events or 250 ms as grouped `UNWIND … MERGE` statements keyed on business ids. When a claim's endpoints change, delete its edges and merge them again. Deletes and voids use the pre-image.
4. **Offsets.** Save the resume token and the last applied cluster time to `cdc_offsets` only after the Neo4j transaction commits. With at-least-once delivery and idempotent writes, each change takes effect exactly once.
5. **Failure handling.** Retry with backoff. On `ChangeStreamHistoryLost` (the token is older than the oplog), re-snapshot automatically.
6. **Observability.** Per-batch lag (applied time − event `wallTime`) and throughput go to the `pipeline_metrics` time series. A reconciliation job compares counts and probes a sample of claim ids.
7. **Control.** Pause, resume and crash switches live in `pipeline_control` for the live demo. The container restart policy brings a crashed worker back.
8. **Cross-store barrier.** An analytics run records the cluster time when it starts and waits until the worker has applied past it, so the graph contains every claim the statistics saw.

## 10. Evaluation

- Tune thresholds and weights on dev seeds (1–2). Report results on held-out seeds (101–105) as mean ± std, at `realistic` difficulty.
- **Provider level:** precision, recall and F1 at the operating threshold; average precision (PR-AUC); precision@k for analyst capacity (k = 25, 50); recall per fraud type.
- **Ring level:** recovery at member Jaccard ≥ 0.5 and ≥ 0.8; member precision and recall.
- **Claim level:** precision and recall per rule.
- **Ablation:** remove one signal at a time and report the change in average precision.
- **System:** snapshot time, analytics run time, CDC lag p50/p95 at 50, 200 and 1,000 claims/s, index versus collection-scan timings, and the traversal benchmark.
- The thesis that fused scoring beats each store alone is a hypothesis to test. The report states whatever numbers come out.

## 11. API

The API is FastAPI with Pydantic response models and keyset pagination. Its OpenAPI schema generates the frontend's TypeScript types.

| Area | Endpoints |
|---|---|
| Overview | `GET /api/overview` returns KPIs, sparklines, risk distribution, top providers, latest rings and pipeline health |
| Providers | `GET /api/providers` (search, filter, sort), `GET /api/providers/{npi}` (dossier), `GET /api/providers/{npi}/network?hops=` |
| Rings | `GET /api/rings`, `GET /api/rings/{id}` (members, ordered cycle, flows, timeline, metrics) |
| Graph | `GET /api/graph/galaxy` returns the aggregated network for the 3D view |
| Claims | `GET /api/claims`, `GET /api/claims/{id}` (document plus source payload), `POST /api/claims` (returns MongoDB's `errInfo` on rejection) |
| Cases | `POST /api/cases/{npi}/transition`, `POST /api/rings/{id}/decision` (multi-document transaction) |
| Pipeline | `GET /api/pipeline/status`, `POST /api/pipeline/{pause,resume,crash}` |
| Simulation | `POST`/`DELETE /api/simulate/feed` (rate), `POST /api/simulate/ring` (size, camouflage) |
| Jobs | `POST /api/jobs/analyze`, with progress streamed over the WebSocket |
| Evaluation | `GET /api/evaluation`; `PUT /api/scoring/config` re-scores and returns the new metrics |
| Internals | `GET /api/internals/{mongo,neo4j,cluster}`, `POST /api/internals/explain`, `POST /api/internals/traversal-benchmark`, `POST /api/internals/failover` |
| Live | `WS /ws/live` emits `claim.ingested`, `claim.rejected`, `cdc.batch`, `pipeline.state`, `job.progress`, `score.updated` and `ring.detected` |

The WebSocket hub is itself a change-stream consumer of scores, rings, jobs and metrics. The UI therefore updates straight from the database, with no separate message bus.

## 12. Frontend

**Look.** The default is a dark "intelligence console", with a full light theme. It uses:
- deep neutral surfaces and glass panels with hairline borders
- one accent hue plus a sequential risk ramp, with contrast checked in both themes
- Inter for text and JetBrains Mono for ids and JSON, both self-hosted
- motion: count-up KPIs, staggered card entrances, route transitions, live pulses, and particles that travel along referral edges

**Global.**
- sidebar navigation and a Ctrl+K command palette to jump to any provider, ring or claim
- a live-status pill showing the socket and CDC lag
- toasts for new rings and job progress
- theme and performance-mode toggles
- full keyboard navigation

| Page | Purpose | Hero visual |
|---|---|---|
| Command Center | Situational awareness | Animated KPI tiles, live claim ticker, statistical × structural quadrant scatter, top-risk leaderboard, ring carousel |
| Network | Explore the referral graph | 3D galaxy (WebGL, with bloom on a capable GPU), coloured by community or risk; glowing ring edges with flowing particles; fly-to search |
| Rings | Triage and ring dossier | Orbit layout with particles circulating the loop, money-flow sankey, activity timeline, metrics radar, confirm or dismiss decision |
| Providers | Ranked worklist and dossier | Risk gauge, signal waterfall, reasons, peer distribution with a marker, billing against the peer band, E/M mix against peers, ego network, case history |
| Claims | Explore and submit | Document inspector (embedded line items, with the raw 837P beside the normalized JSON); submit form that shows MongoDB's validation error detail |
| Pipeline | CDC observability and chaos | Animated flow diagram driven by real throughput; lag p50/p95 and events/s; Mongo ↔ Neo4j parity meter; resume token; feed, plant, pause and crash controls; event console |
| Evaluation Lab | Prove the thesis | Statistical vs graph vs fused, PR curves, precision@k, recall by type, ring recovery, ablation, multi-seed table, and weight sliders with live re-scoring |
| DB Internals | Show the DBMS concepts | Index race (IXSCAN vs COLLSCAN), pipeline viewer, replica-set topology with `stepDown`, shard distribution and hotspot comparison, traversal benchmark |

**Performance.** The display currently runs on the Intel iGPU, so the galaxy is built to cope:
- It renders aggregated edges only (count ≥ 2, capped around 6k) with instanced meshes.
- Bloom and particles turn on in "cinematic" mode or when frame-time sampling shows headroom. Otherwise the view falls back to a 2D canvas.
- three.js and ECharts load lazily per route.
- `prefers-reduced-motion` turns off particles and count-ups.

## 13. Synopsis claims, where they are implemented and where they are shown

| Synopsis claim | Implemented in | Shown in the UI |
|---|---|---|
| Embed line items; reference providers and patients | Claim schema | Claims → document inspector |
| JSON Schema validation at write time | `schema/validators.py` | Claims → submit form (MongoDB `errInfo`) |
| ESR compound, partial, multikey and TTL indexes | `schema/indexes.py` | Internals → index race |
| Window functions, z-scores, materialized signals | `signals/mongo_pipelines.py` | Provider dossier peer charts; Internals → pipeline viewer |
| A shard key that avoids a timestamp hotspot | `cluster` profile, `schema/sharding.py` | Internals → shard distribution, hashed vs timestamp key |
| Replica sets, majority writes, secondary reads | `db/mongo.py`, `cluster` profile | Internals → topology, `stepDown` drill, read routing |
| Index-free adjacency | Neo4j model | Internals → traversal benchmark (Neo4j vs `$graphLookup` vs app-side joins) |
| SCC, Louvain, PageRank, betweenness | `signals/graph_algorithms.py` | Network, Rings |
| Bipartite projection → shared patients | Node similarity | Provider dossier, Network overlay |
| Change streams, resume token, idempotent upsert | `cdc/` | Pipeline → pause, crash, resume |
| Eventual consistency, CAP, ACID vs BASE | CDC design; transactions for case decisions | Pipeline lag and parity; ring decision |
| Composite score fusing both stores | `scoring/` | Dossier waterfall; Lab sliders |
| Precision and recall against planted rings | `evaluation/` | Evaluation Lab |

## 14. Repository layout

```
.
├── PLAN.md, README.md
├── docs/                  architecture, demo script, results, report figures
├── infra/compose.yml      profiles: lite, cluster, app, bench
├── infra/mongo/, neo4j/   init scripts and config
├── scripts/               dev.ps1, demo.ps1, reset.ps1
├── backend/
│   ├── pyproject.toml
│   ├── src/ringlens/
│   │   ├── settings.py, cli.py
│   │   ├── db/            mongo.py (write and read concerns), neo4j.py
│   │   ├── schema/        validators, indexes, time series, sharding
│   │   ├── datagen/       reference codes, world, legit behaviour, fraud scenarios, writer
│   │   ├── intake/        staging, payer adapters (JSON, 837P), live feed, ring planter
│   │   ├── cdc/           worker, mapping, cypher, offsets, metrics, reconcile
│   │   ├── signals/       mongo_pipelines, graph_algorithms, ring_analysis
│   │   ├── scoring/       normalize, fusion, explain
│   │   ├── evaluation/    metrics, bench
│   │   ├── internals/     explain lab, cluster status, failover, traversal bench
│   │   └── api/           app, routers/, ws hub, jobs
│   └── tests/             unit/, integration/
└── frontend/src/          app shell, routes/, components/{ui,charts,graph,pipeline}, lib/{api,ws,format}, theme/
```

Tooling: ruff, mypy and pytest for Python; ESLint, Prettier, strict `tsc`, Vitest and Playwright for the frontend.

## 15. Build phases

Each phase ends with something runnable and a check I will actually run. The backend comes first so every screen is built against real data, and phases 0–3 give an end-to-end slice (data → CDC → graph → signals) before any UI polish.

**Phase 0: Reset and foundations**
- Deliverables:
  - prototype zipped and removed
  - `git init`, with no commits unless you ask
  - `.gitignore` and `.gitattributes` (LF for shell scripts)
  - compose `lite` profile + Neo4j with GDS and APOC
  - backend skeleton with settings, DB clients and the `ringlens` CLI
  - frontend skeleton with theme tokens and router
  - `scripts/dev.ps1`, which starts the databases, API, worker and Vite
- Done when: `dev.ps1 up` gives healthy databases, `ringlens doctor` reports a MongoDB 8.0 replica-set primary, Neo4j 5.26 and the GDS version, and the web shell shows green health.

**Phase 1: System of record and synthetic world**
- Deliverables:
  - validators, indexes, time-series collections and pre-images
  - the generator, with fraud scenarios, hard negatives and the truth database
  - the intake path with payer adapters and ULID ids
- Done when:
  - `ringlens seed --profile demo` loads about 350k claims in roughly 2 minutes
  - generator and 837P-parser unit tests pass
  - crafted bad claims are rejected with readable errors
  - `ringlens explain` shows IXSCAN for every showcase query

**Phase 2: CDC worker**
- Deliverables: snapshot handoff, filtered stream, batched idempotent upserts, delete and endpoint-change handling, offsets, metrics, control switches, reconciliation and history-lost recovery.
- Done when the demo snapshot completes with matching counts, lag p95 stays under 1 s at 200 claims/s, and integration tests show:
  - replaying a batch three times is idempotent
  - a crash mid-batch followed by a restart leaves no gaps or duplicates
  - deletes and updates propagate
  - the re-snapshot path works

**Phase 3: Analytics**
- Deliverables:
  - MongoDB pipelines and claim flags
  - `REFERS_TO` aggregation, projections, algorithms, ring analysis and stable ring ids
  - a job orchestrator with the cross-store barrier
- Done when: `ringlens analyze` finishes in about 30 s at demo scale, and tests on hand-built graphs pass (a triangle is found by SCC, a bridge gets the top betweenness, a shared-patient pair is found by node similarity).

**Phase 4: Scoring and evaluation**
- Deliverables: fusion, attributions, reasons, config versions and history; metrics, ablation and the multi-seed `bench` profile.
- Done when: `ringlens bench --seeds 101-105` writes a Markdown and JSON report with mean ± std.

**Phase 5: API and live layer**
- Deliverables: routers, typed models, OpenAPI → TypeScript types, the WebSocket hub, simulators, pipeline control, transactions and internals endpoints.
- Done when: httpx contract tests pass, and a WebSocket smoke test receives `claim.ingested` and `cdc.batch` while the feed runs.

**Phase 6: Frontend**
- Deliverables, in this order:
  1. shell and design system
  2. Command Center and Providers
  3. Network and Rings
  4. Pipeline and Claims
  5. Evaluation Lab and DB Internals
- Done when:
  - a Playwright smoke test shows every route rendering real data with no console errors
  - screenshots are saved for the report
  - the galaxy runs smoothly on the dGPU and stays usable on the iGPU in performance mode
  - light and dark themes are both checked

**Phase 7: Cluster topology and resilience**
- Deliverables:
  - the `cluster` profile, the shard key, and the app running through `mongos`
  - the Internals cluster panels:
    - shard distribution
    - targeted vs scatter-gather explain
    - timestamp-key hotspot comparison
    - proof of secondary reads
    - `stepDown` drill
    - `w:1` vs majority latency
- Done when: `demo.ps1 -Topology cluster` comes up from scratch, and the Internals page shows both shards balanced and a successful failover.

**Phase 8: Hardening, docs and demo**
- Deliverables: a Windows-first README, the architecture doc, the demo script and report figures; a full test pass; a clean-slate dry run.
- Done when: starting from `docker compose down -v`, `scripts/demo.ps1` brings up a seeded, working console in one command, and all tests pass.

## 16. Demo storyline (about 10 minutes)

1. **Command Center.** About 350k claims, the live feed ticking, rings flagged and dollars at risk.
2. **Network.** Fly through the galaxy, where rings glow, and open one.
3. **Ring dossier.** Particles circulate the loop and the sankey shows the hub. Patient recirculation and a shared bank account are the evidence.
4. **The fusion argument.** On the quadrant scatter, a lone upcoder (caught only by statistics) sits beside a camouflaged ring (caught only by the graph).
5. **Live injection.** Plant a five-provider camouflaged ring. Claims stream in, CDC lag stays under a second, a re-score runs, and the "new ring detected" toast fires.
6. **Resilience.** Pause the pipeline and watch the two stores diverge; resume and they converge. Crash the worker mid-batch; it restarts and a live check shows zero duplicate edges.
7. **Internals.** Show the index race, shard distribution against a timestamp key, a `stepDown` drill with majority writes, and the traversal benchmark.
8. **Evaluation Lab.** Show PR curves for statistical vs graph vs fused on held-out seeds, then drag a weight and watch the metrics move.

## 17. Risks

| Risk | Mitigation |
|---|---|
| Replica-set hostnames under Docker Desktop | `lite` uses `directConnection`; `cluster` exposes only `mongos` to the app (§5) |
| The GDS/APOC download on first Neo4j boot needs internet | Cache plugins in a volume; bake the jars into a custom image if the download is flaky |
| The iGPU can't drive the 3D galaxy smoothly | Use dGPU mode for demos; adaptive quality; 2D fallback |
| The evaluation looks too good to be credible | Held-out seeds, `realistic` and `hard` difficulty, hard negatives, honest reporting |
| A paused worker outlives the oplog window | 2 GB oplog; automatic re-snapshot when history is lost |
| CRLF line endings break container init scripts | `.gitattributes` forces LF for `*.sh`; write mongosh init scripts in JS where possible |
| UI polish crowds out core work | Must-have flows first; stretch goals last (§19) |

## 18. Defaults assumed unless you say otherwise

- The working name is **RingLens**. It is easy to rename now and harder later.
- The authors are you plus the two teammates listed in the prototype README (Samarth Saxena, Shrijit Sinha).
- The prototype is zipped to the session scratchpad, then deleted.
- `git init` for diffs, with no commits unless you ask.
- `lite` for development, `cluster` for the final demo.
- Dark theme by default, with a light theme available.
- No authentication, since this is a local demo; the analyst name is picked from a list.

## 19. Stretch goals (only after everything above works)

- A US map with animated money-flow arcs between ring members (deck.gl). It needs map tiles, so it only works online.
- Learned fusion weights (logistic regression on dev seeds) as a calibrated variant.
- A Kanban case board with drag-and-drop transitions.
- An exportable case-file PDF.
