from __future__ import annotations

from datetime import date

import typer
from rich import print

app = typer.Typer(help="signal-farm CLI scaffold")
eval_app = typer.Typer(help="Eval commands")
goldens_app = typer.Typer(help="Golden-data commands")
inspect_app = typer.Typer(help="Inspection commands")

app.add_typer(eval_app, name="eval")
app.add_typer(goldens_app, name="goldens")
app.add_typer(inspect_app, name="inspect")


def _echo_todo(command: str, **kwargs: object) -> None:
    payload = ", ".join(f"{key}={value}" for key, value in kwargs.items())
    print(f"[bold cyan]{command}[/bold cyan] scaffold ready")
    if payload:
        print(payload)
    print("Implementation pending; use the spec as the source of truth.")


@app.command("run-day")
def run_day(target_date: date | None = None) -> None:
    _echo_todo("run-day", target_date=target_date or date.today())


@app.command("plan-queries")
def plan_queries(target_date: date | None = None) -> None:
    _echo_todo("plan-queries", target_date=target_date or date.today())


@app.command()
def fetch(run_id: str) -> None:
    _echo_todo("fetch", run_id=run_id)


@app.command()
def dedupe(run_id: str) -> None:
    _echo_todo("dedupe", run_id=run_id)


@app.command()
def novelty(run_id: str) -> None:
    _echo_todo("novelty", run_id=run_id)


@app.command()
def digest(run_id: str) -> None:
    _echo_todo("digest", run_id=run_id)


@app.command("kb-ingest")
def kb_ingest(run_id: str) -> None:
    _echo_todo("kb-ingest", run_id=run_id)


@eval_app.command("collect")
def eval_collect(run_id: str) -> None:
    _echo_todo("eval collect", run_id=run_id)


@eval_app.command("replay")
def eval_replay(run_id: str) -> None:
    _echo_todo("eval replay", run_id=run_id)


@goldens_app.command("propose")
def goldens_propose(run_id: str) -> None:
    _echo_todo("goldens propose", run_id=run_id)


@goldens_app.command("promote")
def goldens_promote(mode: str = "auto") -> None:
    _echo_todo("goldens promote", mode=mode)


@inspect_app.command("query-performance")
def inspect_query_performance() -> None:
    _echo_todo("inspect query-performance")


@inspect_app.command("novelty-frontier")
def inspect_novelty_frontier() -> None:
    _echo_todo("inspect novelty-frontier")


@inspect_app.command("cassette-stats")
def inspect_cassette_stats() -> None:
    _echo_todo("inspect cassette-stats")


if __name__ == "__main__":
    app()
