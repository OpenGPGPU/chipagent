"""Interaction layer (Section 3.1 / 6.1.1).

Phase 1 provides a simple conversational + task-panel-style entry via the
CLI. A user submits a natural-language request, the system prints a
structured task receipt, runs the workflow, and displays the result with
artifact locations and a log summary. This is the thin "friendly face" the
plan recommends over the internal workflow details.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .workflow import WorkflowOrchestrator


def format_result(result: Dict[str, Any]) -> str:
    """Render a workflow result as a human-readable summary."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append(f"Task status : {result.get('status')}")
    lines.append(f"Task type   : {result.get('task_type')}")
    if result.get("error"):
        lines.append(f"Error       : {result['error']}")

    output = result.get("output", {})
    checks = output.get("checks", {})
    lines.append(f"Lint        : {checks.get('lint')}")
    lines.append(f"Syntax      : {checks.get('syntax')}")

    artifacts = result.get("artifacts", {})
    if artifacts:
        lines.append("-" * 60)
        lines.append("Artifacts:")
        for key, path in artifacts.items():
            lines.append(f"  {key}: {path}")

    lines.append("-" * 60)
    lines.append("Execution log:")
    for entry in result.get("logs", []):
        step = entry.get("step", "?")
        status = entry.get("status", "?")
        lines.append(f"  [{step}] {status}")

    code = output.get("code", "")
    if code:
        lines.append("-" * 60)
        lines.append("Generated RTL:")
        lines.append(code.rstrip())
    lines.append("=" * 60)
    return "\n".join(lines)


def submit_request(
    request: str,
    *,
    context_path: Optional[str] = None,
    context_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    use_llm: bool = True,
    show_result: bool = True,
) -> Dict[str, Any]:
    """Submit a natural-language request and return the workflow result."""
    orchestrator = WorkflowOrchestrator(use_llm=use_llm)
    result = orchestrator.run(
        request,
        context_path=context_path,
        context_dir=context_dir,
        output_dir=output_dir,
    )
    if show_result:
        print(format_result(result))
    return result


def repl() -> None:
    """A minimal multi-turn interactive prompt (task-panel style)."""
    print("ChipAgent phase 1 prototype. Type 'exit' to quit.")
    while True:
        try:
            request = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not request:
            continue
        if request.lower() in {"exit", "quit"}:
            break
        result = submit_request(request, use_llm=False)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ChipAgent interaction layer")
    parser.add_argument("request", nargs="?", help="Natural language request to process")
    parser.add_argument("--context", dest="context_path")
    parser.add_argument("--context-dir", dest="context_dir")
    parser.add_argument("--output-dir", dest="output_dir")
    parser.add_argument("--no-llm", dest="no_llm", action="store_true")
    parser.add_argument("--repl", action="store_true", help="Start an interactive session")
    args = parser.parse_args()

    if args.repl:
        repl()
        return

    if not args.request:
        parser.error("a request is required (or use --repl)")

    output_dir = args.output_dir or str(Path("generated").resolve())
    submit_request(
        args.request,
        context_path=args.context_path,
        context_dir=args.context_dir,
        output_dir=output_dir,
        use_llm=not args.no_llm,
    )


if __name__ == "__main__":
    main()
