from __future__ import annotations

import argparse
import sys
from pathlib import Path

from manus_mini.models import LoopLimits
from manus_mini.prompt_tui import PromptTui, PromptTuiOptions
from manus_mini.redaction import redact_sensitive_text
from manus_mini.session_store import CorruptSessionError, SessionStore


MAX_LIST_MESSAGE_PREVIEW_CHARS = 120


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manus-mini",
        description="Self-managed coding agent TUI with resumable sessions, guarded tools, and local project storage.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_runtime_options(parser, include_defaults=True, cwd_dest="global_cwd")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="list saved sessions")
    _add_cwd_option(list_parser, dest="subcommand_cwd", default=None)

    resume_parser = subparsers.add_parser("resume", help="resume a saved session")
    resume_parser.add_argument("session_id")
    _add_cwd_option(resume_parser, dest="subcommand_cwd", default=None)

    remove_parser = subparsers.add_parser("remove", help="remove a saved session")
    remove_parser.add_argument("session_id")
    _add_cwd_option(remove_parser, dest="subcommand_cwd", default=None)

    clear_parser = subparsers.add_parser("clear", help="clear all saved sessions")
    _add_cwd_option(clear_parser, dest="subcommand_cwd", default=None)
    clear_parser.add_argument("--force", "-f", action="store_true", help="skip confirmation prompt")

    tui_parser = subparsers.add_parser(
        "tui",
        help="start the interactive TUI",
        description="start the interactive TUI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_runtime_options(tui_parser, include_defaults=False, cwd_dest="subcommand_cwd")
    return parser


def _add_runtime_options(parser: argparse.ArgumentParser, *, include_defaults: bool, cwd_dest: str) -> None:
    defaults = {
        "max_steps": 3 if include_defaults else argparse.SUPPRESS,
        "max_react": 99 if include_defaults else argparse.SUPPRESS,
        "max_reflect": 3 if include_defaults else argparse.SUPPRESS,
        "max_tool_retries": 3 if include_defaults else argparse.SUPPRESS,
    }
    _add_cwd_option(parser, dest=cwd_dest, default=Path.cwd() if include_defaults else argparse.SUPPRESS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False if include_defaults else argparse.SUPPRESS,
        help="preview tool execution without side effects",
    )
    parser.add_argument(
        "--max-steps",
        type=_positive_cli_int,
        default=defaults["max_steps"],
        help="engineering loop limit",
    )
    parser.add_argument(
        "--max-react",
        type=_positive_cli_int,
        default=defaults["max_react"],
        help="ReAct iteration limit",
    )
    parser.add_argument(
        "--max-reflect",
        type=_positive_cli_int,
        default=defaults["max_reflect"],
        help="reflection loop limit",
    )
    parser.add_argument(
        "--max-tool-retries",
        type=_positive_cli_int,
        default=defaults["max_tool_retries"],
        help="tool retry limit",
    )


def _add_cwd_option(parser: argparse.ArgumentParser, *, dest: str, default: object) -> None:
    parser.add_argument(
        "--cwd",
        dest=dest,
        type=Path,
        default=default,
        help="working directory used for project storage and tool execution",
    )


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    cwd = getattr(args, "subcommand_cwd", None) or args.global_cwd or Path.cwd()
    dry_run = bool(args.dry_run) if getattr(args, "dry_run", None) is not None else False
    max_steps = int(args.max_steps) if getattr(args, "max_steps", None) is not None else 3
    max_react = int(args.max_react) if getattr(args, "max_react", None) is not None else 99
    max_reflect = int(args.max_reflect) if getattr(args, "max_reflect", None) is not None else 3
    max_tool_retries = int(args.max_tool_retries) if getattr(args, "max_tool_retries", None) is not None else 3

    if args.command == "list":
        _run_list(cwd)
        return
    if args.command == "resume":
        _run_resume(
            cwd=cwd,
            session_id=args.session_id,
            dry_run=dry_run,
            max_steps=max_steps,
            max_react=max_react,
            max_reflect=max_reflect,
            max_tool_retries=max_tool_retries,
            limit_overrides={
                "max_engineering_steps": _option_was_provided(raw_argv, "--max-steps"),
                "max_react_iterations": _option_was_provided(raw_argv, "--max-react"),
                "max_reflection_rounds": _option_was_provided(raw_argv, "--max-reflect"),
                "max_tool_retries": _option_was_provided(raw_argv, "--max-tool-retries"),
            },
        )
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


def _run_resume(
    cwd: Path,
    session_id: str,
    dry_run: bool,
    max_steps: int,
    max_react: int,
    max_reflect: int,
    max_tool_retries: int,
    limit_overrides: dict[str, bool],
) -> None:
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
    if limit_overrides["max_engineering_steps"]:
        limits.max_engineering_steps = max_steps
    if limit_overrides["max_react_iterations"]:
        limits.max_react_iterations = max_react
    if limit_overrides["max_reflection_rounds"]:
        limits.max_reflection_rounds = max_reflect
    if limit_overrides["max_tool_retries"]:
        limits.max_tool_retries = max_tool_retries
    PromptTui(
        options=PromptTuiOptions(cwd=cwd, limits=limits, dry_run=dry_run),
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


def _option_was_provided(argv: list[str], name: str) -> bool:
    return any(item == name or item.startswith(f"{name}=") for item in argv)


def _positive_cli_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _format_last_user_message(message: str) -> str:
    preview = redact_sensitive_text(message).replace("\r", " ").replace("\n", " ")
    if len(preview) <= MAX_LIST_MESSAGE_PREVIEW_CHARS:
        return preview
    return preview[: MAX_LIST_MESSAGE_PREVIEW_CHARS - 3].rstrip() + "..."
