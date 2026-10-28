"""RingLens command line.

    ringlens doctor                      check both databases and the GDS plugin
    ringlens seed --profile demo         (re)create the schema and load a synthetic world
    ringlens cdc                         run the CDC worker under a supervisor
    ringlens analyze                     signals -> graph -> scores -> evaluation
    ringlens explain                     show which index serves each showcase query
    ringlens api                         serve the REST/WebSocket API
    ringlens bench --seeds 101-105       multi-seed evaluation report
"""
from __future__ import annotations

import json
import logging
import sys

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
console = Console()


def _dump(obj):
    console.print_json(json.dumps(obj, default=str))


@app.command()
def doctor():
    """Check MongoDB (replica set), Neo4j, GDS and APOC."""
    from .db import cypher, mongo_client

    ok = True
    try:
        hello = mongo_client().admin.command("hello")
        build = mongo_client().admin.command("buildInfo")
        topo = "sharded (mongos)" if hello.get("msg") == "isdbgrid" else f"replica set {hello.get('setName')}"
        console.print(f"[green]MongoDB[/] {build['version']} · {topo} · primary={hello.get('isWritablePrimary')}")
    except Exception as exc:
        ok = False
        console.print(f"[red]MongoDB unreachable[/]: {exc}")
    try:
        v = cypher("CALL dbms.components() YIELD versions, edition RETURN versions[0] AS v, edition")[0]
        gds = cypher("RETURN gds.version() AS v")[0]["v"]
        apoc = cypher("RETURN apoc.version() AS v")[0]["v"]
        console.print(f"[green]Neo4j[/] {v['v']} {v['edition']} · GDS {gds} · APOC {apoc}")
    except Exception as exc:
        ok = False
        console.print(f"[red]Neo4j problem[/]: {exc}")
    raise typer.Exit(0 if ok else 1)


@app.command()
def seed(profile: str = typer.Option("demo", help="tiny | demo | large"),
         seed: int = typer.Option(1, help="random seed (deterministic)"),
         difficulty: str = typer.Option("realistic", help="easy | realistic | hard")):
    """Drop and recreate the schema, then load a synthetic world with planted fraud."""
    from .datagen.loader import seed as run_seed

    with Progress(TextColumn("{task.description}"), BarColumn(), TextColumn("{task.completed:,}/{task.total:,}"),
                  console=console) as prog:
        tasks: dict = {}

        def progress(stage, n, total):
            if stage not in tasks:
                tasks[stage] = prog.add_task(f"inserting {stage}", total=total)
            prog.advance(tasks[stage], n)

        console.print(f"Generating world: profile={profile} seed={seed} difficulty={difficulty}")
        meta = run_seed(profile, seed, difficulty, progress)
    _dump(meta)


@app.command()
def cdc(child: bool = typer.Option(False, "--child", hidden=True)):
    """Run the change-stream worker (supervised: restarted automatically after a crash)."""
    from . import cdc as cdc_mod

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if child:
        cdc_mod.run_worker()
    else:
        cdc_mod.supervise()


@app.command()
def status():
    """Show CDC state and store parity."""
    from .cdc import lag_report

    _dump(lag_report())


@app.command()
def analyze():
    """Run MongoDB signals, Neo4j graph signals, fusion and evaluation."""
    from .jobs import analyze as run

    with console.status("Running analytics..."):
        result = run("cli")
    _dump(result)


@app.command()
def explain():
    """Show which index serves each showcase query (IXSCAN vs COLLSCAN)."""
    from .internals import SHOWCASE, explain_query

    table = Table("query", "plan", "index", "returned", "keys examined", "docs examined", "ms")
    for key in SHOWCASE:
        for forced in (False, True):
            r = explain_query(key, collscan=forced)
            table.add_row(r["label"] + (" [forced COLLSCAN]" if forced else ""), r["stage"], r["index"] or "-",
                          f"{r['returned']:,}", f"{r['keys_examined']:,}", f"{r['docs_examined']:,}", f"{r['ms']}")
    console.print(table)


@app.command()
def api(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Serve the REST + WebSocket API."""
    import uvicorn

    uvicorn.run("ringlens.api:app", host=host, port=port, reload=reload, log_level="info")


@app.command()
def bench(seeds: str = typer.Option("101-105", help="e.g. 101-105 or 1,2,3"),
          profile: str = "demo", difficulty: str = "realistic"):
    """Seed, sync, analyze and evaluate several seeds; writes docs/results/bench-*.{json,md}.
    This replaces the current dataset, so run `ringlens seed` afterwards for the demo."""
    from .bench import run_bench

    if "-" in seeds:
        a, b = seeds.split("-")
        seed_list = list(range(int(a), int(b) + 1))
    else:
        seed_list = [int(s) for s in seeds.split(",")]
    _dump(run_bench(seed_list, profile, difficulty, console))


def main():
    app()


if __name__ == "__main__":
    sys.exit(main())
