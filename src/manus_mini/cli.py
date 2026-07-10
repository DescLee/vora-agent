from __future__ import annotations

import argparse
from pathlib import Path

from manus_mini.models import LoopLimits
from manus_mini.prompt_tui import PromptTui, PromptTuiOptions
from manus_mini.redaction import redact_sensitive_text
from manus_mini.session_store import CorruptSessionError, SessionStore


MAX_LIST_MESSAGE_PREVIEW_CHARS = 120


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manus-mini")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--max-react", type=int, default=99)
    parser.add_argument("--max-reflect", type=int, default=3)
    parser.add_argument("--max-tool-retries", type=int, default=3)
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="list saved sessions")
    list_parser.add_argument("--cwd", type=Path)

    resume_parser = subparsers.add_parser("resume", help="resume a saved session")
    resume_parser.add_argument("session_id")
    resume_parser.add_argument("--cwd", type=Path)

    remove_parser = subparsers.add_parser("remove", help="remove a saved session")
    remove_parser.add_argument("session_id")
    remove_parser.add_argument("--cwd", type=Path)

    clear_parser = subparsers.add_parser("clear", help="clear all saved sessions")
    clear_parser.add_argument("--cwd", type=Path)
    clear_parser.add_argument("--force", "-f", action="store_true", help="skip confirmation prompt")

    tui_parser = subparsers.add_parser("tui", help="start the interactive TUI")
    tui_parser.add_argument("--cwd", type=Path)
    tui_parser.add_argument("--dry-run", action="store_true")
    tui_parser.add_argument("--max-steps", type=int)
    tui_parser.add_argument("--max-react", type=int)
    tui_parser.add_argument("--max-reflect", type=int)
    tui_parser.add_argument("--max-tool-retries", type=int)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = args.cwd or Path.cwd()
    dry_run = bool(args.dry_run) if getattr(args, "dry_run", None) is not None else False
    max_steps = int(args.max_steps) if getattr(args, "max_steps", None) is not None else 3
    max_react = int(args.max_react) if getattr(args, "max_react", None) is not None else 99
    max_reflect = int(args.max_reflect) if getattr(args, "max_reflect", None) is not None else 3
    max_tool_retries = int(args.max_tool_retries) if getattr(args, "max_tool_retries", None) is not None else 3

    if args.command == "list":
        _run_list(cwd)
        return
    if args.command == "resume":
        _run_resume(cwd, args.session_id)
        return
    if args.command == "remove":
        _run_remove(cwd, args.session_id)
        return
    if args.command == "clear":
        _run_clear(cwd, args.force)
        return
    if args.command == "tui" or args.command is None:
        _run_tui(
            cwd=cwd,
            dry_run=dry_run,
            max_steps=max_steps,
            max_react=max_react,
            max_reflect=max_reflect,
            max_tool_retries=max_tool_retries,
        )
        return

    parser.print_help()


def _run_list(cwd: Path) -> None:
    store = SessionStore(cwd)
    sessions = store.list_sessions()
    if not sessions:
        print("No saved sessions.")
        print(f"Session directory: {store.sessions_dir}")
        return
    for summary in sessions:
        print(
            f"{summary.session_id}\t{summary.updated_at:%Y-%m-%d %H:%M:%S}\t"
            f"{summary.message_count}\t{_format_last_user_message(summary.last_user_message)}"
        )


def _run_resume(cwd: Path, session_id: str) -> None:
    store = SessionStore(cwd)
    try:
        session = store.load(session_id)
    except CorruptSessionError:
        print(f"Error: session '{session_id}' is unreadable or corrupt.")
        raise SystemExit(1) from None
    except ValueError:
        print(f"Error: invalid session id '{session_id}'.")
        raise SystemExit(1) from None
    except FileNotFoundError:
        print(f"Error: session '{session_id}' not found.")
        raise SystemExit(1) from None
    limits = session.active_task.limits if session.active_task is not None else LoopLimits()
    PromptTui(
        options=PromptTuiOptions(cwd=cwd, limits=limits),
        initial_session=session,
    ).run()


def _run_remove(cwd: Path, session_id: str) -> None:
    """Remove a saved session by its session_id.

    Also removes the corresponding session log directory under logs/.
    If the session exists, deletes it and prints a confirmation message.
    If not found, prints an error message and exits with non-zero status.
    """
    store = SessionStore(cwd)
    try:
        if not store.delete(session_id):
            print(f"Error: session '{session_id}' not found.")
            raise SystemExit(1)
        logs_deleted = store.delete_logs_for_session(session_id)
    except ValueError:
        print(f"Error: invalid session id '{session_id}'.")
        raise SystemExit(1) from None

    if logs_deleted:
        print(f"Session '{session_id}' has been removed (also cleaned {logs_deleted} log dir(s)).")
    else:
        print(f"Session '{session_id}' has been removed.")


def _run_clear(cwd: Path, force: bool) -> None:
    """Clear all saved sessions and their corresponding logs.

    If --force/-f is not provided, prompts the user for confirmation.
    Prints the number of sessions and log dirs that were deleted.
    """
    store = SessionStore(cwd)
    sessions = store.list_sessions()
    count = len(sessions)
    if count == 0:
        print("No saved sessions to clear.")
        return
    if not force:
        answer = input(f"Are you sure you want to clear all {count} saved session(s)? [y/N] ").strip().lower()
        if answer not in ("y", "yes", "确认", "是"):
            print("Clear cancelled.")
            return

    count = store.clear_all()
    logs_deleted = store.clear_all_logs()
    print(f"All {count} saved session(s) have been cleared (also cleaned {logs_deleted} log dir(s)).")


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


def _format_last_user_message(message: str) -> str:
    preview = redact_sensitive_text(message).replace("\r", " ").replace("\n", " ")
    if len(preview) <= MAX_LIST_MESSAGE_PREVIEW_CHARS:
        return preview
    return preview[: MAX_LIST_MESSAGE_PREVIEW_CHARS - 3].rstrip() + "..."
