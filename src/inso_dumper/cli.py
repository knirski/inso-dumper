"""Stub CLI for T1; replaced by the real typer app in T9."""

from __future__ import annotations

import typer

app = typer.Typer(help="inso-dumper: local backup tool for the Inso platform (app.inso.pl)")


@app.callback()
def _root() -> None:
    pass


if __name__ == "__main__":
    app()
