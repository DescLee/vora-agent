from __future__ import annotations

import argparse
from pathlib import Path

from manus_mini.models import LoopLimits
from manus_mini.prompt_tui import PromptTui, PromptTuiOptions
from manus_mini.session_store import SessionStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manus-mini")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="list saved sessions")
    list_parser.add_argument("--cwd", type=Path, default=Path.cwd())

    resume_parser = subparsers.add_parser("resume", help="resume a saved session")
    resume_parser.add_argument("session_id")
    resume_parser.add_argument("--cwd", type=Path, default=Path.cwd())

    remove_parser = subparsers.add_parser("remove", help="remove a saved session")
    remove_parser.add_argument("session_id")
    remove_parser.add_argument("--cwd", type=Path, default=Path.cwd())

    tui_parser = subparsers.add_parser("tui", help="start the interactive TUI")
    tui_parser.add_argument("--cwd", type=Path, default=Path.cwd())
    tui_parser.add_argument("--dry-run", action="store_true")
    tui_parser.add_argument("--max-steps", type=int, default=3)
    tui_parser.add_argument("--max-react", type=int, default=10)
    tui_parser.add_argument("--max-reflect", type=int, default=3)
    tui_parser.add_argument("--max-tool-retries", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "list":
        _run_list(args.cwd)
        return
    if args.command == "resume":
        _run_resume(args.cwd, args.session_id)
        return
    if args.command == "remove":
        _run_remove(args.cwd, args.session_id)
        return
    if args.command == "tui" or args.command is None:
        _run_tui(
            cwd=args.cwd if hasattr(args, "cwd") else Path.cwd(),
            dry_run=bool(getattr(args, "dry_run", False)),
            max_steps=int(getattr(args, "max_steps", 3)),
            max_react=int(getattr(args, "max_react", 10)),
            max_reflect=int(getattr(args, "max_reflect", 3)),
            max_tool_retries=int(getattr(args, "max_tool_retries", 3)),
        )
        return

    parser.print_help()


def _run_list(cwd: Path) -> None:
    store = SessionStore(cwd)
    sessions = store.list_sessions()
    if not sessions:
        print("No saved sessions.")
        return
    for summary in sessions:
        print(
            f"{summary.session_id}\t{summary.updated_at:%Y-%m-%d %H:%M:%S}\t"
            f"{summary.message_count}\t{summary.last_user_message}"
        )


def _run_resume(cwd: Path, session_id: str) -> None:
    store = SessionStore(cwd)
    session = store.load(session_id)
    limits = session.active_task.limits if session.active_task is not None else LoopLimits()
    PromptTui(
        options=PromptTuiOptions(cwd=cwd, limits=limits),
        initial_session=session,
    ).run()


def _run_remove(cwd: Path, session_id: str) -> None:
    """Remove a saved session by its session_id.

    If the session exists, deletes it and prints a confirmation message.
    If not found, prints an error message and exits with non-zero status.
    """
    store = SessionStore(cwd)
    if store.delete(session_id):
        print(f"Session '{session_id}' has been removed.")
    else:
        print(f"Error: session '{session_id}' not found.")
        raise SystemExit(1)


def _run_tui(
    cwd: Path,
    dry_run: bool,
    max_steps: int,
    max_react: int,
    max_reflect: int,
    max_tool_retries: int,
) -> None:
    limits = LoopLimits(
        max_engineering_steps=max_steps,
        max_react_iterations=max_react,
        max_reflection_rounds=max_reflect,
        max_tool_retries=max_tool_retries,
    )
    PromptTui(options=PromptTuiOptions(cwd=cwd, limits=limits, dry_run=dry_run)).run()
