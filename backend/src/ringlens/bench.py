"""Multi-seed evaluation on held-out datasets.

The single-dataset numbers on the Evaluation Lab come from seed 1, the dataset the
thresholds and weights were tuned on. This benchmark generates fresh worlds from other
seeds, runs the full pipeline on each (seed -> CDC snapshot -> analytics), and reports
mean +- standard deviation. Needs the CDC worker running (`ringlens cdc`); it replaces the
current dataset and restores the demo dataset (seed 1) at the end.
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from . import cdc, jobs
from .datagen.loader import seed as run_seed
from .db import mongo_db
from .evaluation import evaluate

RESULTS = Path(__file__).resolve().parents[3] / "docs" / "results"
VARIANTS = ["statistical", "graph", "fused", "weighted_sum"]


def _wait_for_snapshot(timeout: float = 600) -> None:
    epoch = mongo_db().pipeline_control.find_one({"_id": "neo4j"})["epoch"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = cdc.state()
        if st.get("epoch") == epoch and st.get("state") == "streaming" and st.get("snapshot"):
            return
        time.sleep(1)
    raise TimeoutError("CDC worker did not snapshot the new dataset. Is `ringlens cdc` running?")


def _ms(values: list[float]) -> dict:
    return {"mean": round(statistics.mean(values), 4),
            "std": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
            "min": round(min(values), 4), "max": round(max(values), 4)}


def run_bench(seeds: list[int], profile: str = "demo", difficulty: str = "realistic", console=None,
              restore: bool = True) -> dict:
    say = console.print if console else print
    per_seed = []
    started = datetime.now(timezone.utc)
    for s in seeds:
        t0 = time.perf_counter()
        say(f"[bold]seed {s}[/] · generating {profile}/{difficulty}…")
        meta = run_seed(profile, s, difficulty)
        _wait_for_snapshot()
        jobs.analyze(f"bench-seed-{s}")
        ev = evaluate(store=False)
        row = {"seed": s, "claims": meta["claims"], "fraudulent_providers": meta["fraudulent_providers"],
               "rings_planted": ev["rings"]["planted"], "rings_recovered": ev["rings"]["recovered"],
               "rings_exact": ev["rings"]["exact"], "false_rings": len(ev["rings"]["false_rings"]),
               "by_type": {k: v["recall"] for k, v in ev["by_type"].items()},
               "seconds": round(time.perf_counter() - t0, 1)}
        for v in VARIANTS:
            m = ev["variants"][v]
            row[v] = {k: m.get(k) for k in ("precision", "recall", "f1", "average_precision") if m.get(k) is not None}
            row[v]["precision_at_25"] = m["precision_at"]["25"]
        per_seed.append(row)
        say(f"  fused F1 {row['fused']['f1']:.3f} · stat {row['statistical']['f1']:.3f} · graph {row['graph']['f1']:.3f}"
            f" · rings {row['rings_recovered']}/{row['rings_planted']} · {row['seconds']}s")

    summary: dict = {}
    for v in VARIANTS:
        summary[v] = {k: _ms([r[v][k] for r in per_seed]) for k in per_seed[0][v]}
    summary["rings"] = {"planted": sum(r["rings_planted"] for r in per_seed),
                        "recovered": sum(r["rings_recovered"] for r in per_seed),
                        "exact": sum(r["rings_exact"] for r in per_seed),
                        "false_rings": sum(r["false_rings"] for r in per_seed)}
    types = sorted({t for r in per_seed for t in r["by_type"]})
    summary["recall_by_type"] = {t: _ms([r["by_type"].get(t, 0.0) for r in per_seed if t in r["by_type"]]) for t in types}
    result = {"seeds": seeds, "profile": profile, "difficulty": difficulty, "started_at": started,
              "finished_at": datetime.now(timezone.utc), "summary": summary, "per_seed": per_seed}

    RESULTS.mkdir(parents=True, exist_ok=True)
    stem = f"bench-{difficulty}-{started:%Y%m%d-%H%M}"
    (RESULTS / f"{stem}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    (RESULTS / f"{stem}.md").write_text(_markdown(result), encoding="utf-8")
    say(f"wrote {RESULTS / stem}.json and .md")

    if restore:
        say("restoring the demo dataset (seed 1)…")
        run_seed(profile, 1, "realistic")
        _wait_for_snapshot()
        jobs.analyze("bench-restore")
    # Stored after the restore, because seeding drops every collection.
    mongo_db().bench_runs.insert_one(result)
    return {"summary": summary, "files": str(RESULTS / stem)}


def _markdown(r: dict) -> str:
    s = r["summary"]
    lines = [f"# Held-out benchmark: {r['difficulty']} difficulty",
             "", f"Seeds {', '.join(map(str, r['seeds']))} · profile `{r['profile']}` · finished {r['finished_at']:%Y-%m-%d %H:%M} UTC",
             "", "| Detector | Precision | Recall | F1 | Average precision | Precision@25 |", "|---|---|---|---|---|---|"]
    names = {"statistical": "Statistical only (MongoDB)", "graph": "Graph only (Neo4j)", "fused": "**Fused (noisy-OR)**",
             "weighted_sum": "Weighted sum (baseline)"}
    for v in VARIANTS:
        m = s[v]
        cell = lambda k, m=m: f"{m[k]['mean']:.3f} ± {m[k]['std']:.3f}" if k in m else "—"  # noqa: E731
        lines.append(f"| {names[v]} | {cell('precision')} | {cell('recall')} | {cell('f1')} | {cell('average_precision')} | {cell('precision_at_25')} |")
    rg = s["rings"]
    lines += ["", f"Rings: {rg['recovered']}/{rg['planted']} recovered (Jaccard ≥ 0.5), {rg['exact']} exactly, "
                  f"{rg['false_rings']} detected rings matching no planted ring.", "", "Recall by fraud type (fused):", ""]
    lines += [f"- {t}: {v['mean']:.2f} (min {v['min']:.2f})" for t, v in s["recall_by_type"].items()]
    lines += ["", "| Seed | Fused F1 | Stat F1 | Graph F1 | Rings |", "|---|---|---|---|---|"]
    lines += [f"| {p['seed']} | {p['fused']['f1']:.3f} | {p['statistical']['f1']:.3f} | {p['graph']['f1']:.3f} | "
              f"{p['rings_recovered']}/{p['rings_planted']} |" for p in r["per_seed"]]
    return "\n".join(lines) + "\n"
