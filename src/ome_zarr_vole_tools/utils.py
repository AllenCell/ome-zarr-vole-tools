"""Logging setup and summary reporting."""

from __future__ import annotations

import logging
from typing import List, Tuple

from rich.console import Console
from rich.table import Table


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging with suppressed library noise."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy libraries
    for name in ("ome_zarr", "zarr", "dask", "fsspec", "urllib3", "numcodecs"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log_summary(
    successes: List[str],
    failures: List[Tuple[str, str]],
) -> None:
    """Print a Rich table summarizing the conversion results."""
    console = Console()
    console.print()

    table = Table(title="Conversion Summary")
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Detail")

    for path in successes:
        table.add_row(path, "[green]OK[/green]", "")

    for path, error in failures:
        table.add_row(path, "[red]FAILED[/red]", error)

    console.print(table)
    console.print(
        f"\n[bold]{len(successes)} succeeded[/bold], "
        f"[bold red]{len(failures)} failed[/bold red]"
    )
