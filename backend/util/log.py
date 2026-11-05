"""Stage log output for the pipeline.

Every pipeline stage and generator reports progress through ``log``: one
line per message on stderr, flushed immediately so a line is visible as
soon as it is written when the process runs under ``docker compose run``.
stdout stays free for data (``./run`` and the CLI's own user-facing output
go through click.echo in backend/cli.py).
"""
from __future__ import annotations

import sys


def log(msg: str) -> None:
    print(msg, flush=True, file=sys.stderr)
