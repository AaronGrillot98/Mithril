from __future__ import annotations

import json
import sys

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from mithril import __version__
from mithril.config import settings
from mithril.detectors import default_pipeline

app = typer.Typer(
    add_completion=False,
    help="Mithril — a firewall for LLMs.",
)
console = Console()


@app.command()
def serve(
    host: str = typer.Option(settings.host, help="Bind address."),
    port: int = typer.Option(settings.port, help="Bind port."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (dev)."),
) -> None:
    """Run the Mithril proxy server."""
    console.print(f"[bold cyan]Mithril v{__version__}[/]")
    console.print(f"  mode      : [bold]{settings.mode}[/]")
    console.print(f"  threshold : [bold]{settings.threshold}[/]")
    console.print(f"  upstream  : [bold]{settings.upstream_url}[/]")
    console.print(f"  listening : [bold]http://{host}:{port}[/]\n")
    uvicorn.run("mithril.server:app", host=host, port=port, reload=reload)


@app.command()
def scan(
    text: str = typer.Argument(None, help="Text to scan. Reads stdin if omitted."),
    threshold: float = typer.Option(settings.threshold, help="Block threshold."),
    json_out: bool = typer.Option(False, "--json", help="Emit raw JSON."),
) -> None:
    """Scan a single piece of text and report findings."""
    if text is None:
        text = sys.stdin.read()
    if not text.strip():
        console.print("[red]No text provided.[/]")
        raise typer.Exit(code=2)

    pipeline = default_pipeline(threshold=threshold)
    result = pipeline.scan(text)

    if json_out:
        typer.echo(json.dumps(result.model_dump(), indent=2))
        raise typer.Exit(code=1 if result.blocked else 0)

    verdict_color = "red" if result.blocked else "green"
    verdict = "BLOCKED" if result.blocked else "ALLOWED"
    console.print(
        f"[bold {verdict_color}]{verdict}[/]  "
        f"score={result.score:.2f}  severity={result.top_severity}  "
        f"findings={len(result.findings)}"
    )

    if result.findings:
        table = Table(show_header=True, header_style="bold")
        table.add_column("Detector")
        table.add_column("Rule")
        table.add_column("Severity")
        table.add_column("Conf")
        table.add_column("Message")
        for f in result.findings:
            table.add_row(
                f.detector, f.rule_id, f.severity, f"{f.confidence:.2f}", f.message
            )
        console.print(table)

    raise typer.Exit(code=1 if result.blocked else 0)


@app.command()
def version() -> None:
    """Print the Mithril version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
