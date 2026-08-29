"""Allow ``python -m inso_dumper`` as an entry point."""

from __future__ import annotations

from inso_dumper.cli import app

if __name__ == "__main__":
    app()
