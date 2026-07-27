"""Deprecated interactive conversational CLI for ChipAgent.

ChipAgent's supported interactive surface is now the MCP server in
:mod:`chipagent.mcp`. This module remains only to give direct callers a clear
migration message instead of silently starting the old in-process REPL.
"""
from __future__ import annotations

import sys
from typing import Iterator


def _stream_print(chunks: Iterator[str]) -> None:
    """Print streamed chunks with a typing effect (no trailing newline)."""
    for chunk in chunks:
        sys.stdout.write(chunk)
        sys.stdout.flush()
    sys.stdout.write("\n")


def repl(use_llm: bool = True) -> None:
    """Deprecated: use ``python -m chipagent.mcp`` with an MCP host."""
    print(
        "chipagent chat is deprecated. Start the MCP server with "
        "`python -m chipagent.mcp` or the `chipagent-mcp` console script.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Deprecated ChipAgent conversational CLI")
    p.add_argument("--no-llm", dest="no_llm", action="store_true",
                   help="Force the offline deterministic path (no gateway calls)")
    args = p.parse_args()
    repl(use_llm=not args.no_llm)


if __name__ == "__main__":
    main()
