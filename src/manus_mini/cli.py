from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from manus_mini.config import AppConfig
from manus_mini.logging import project_logs_dir, project_memory_path, project_outputs_dir, project_storage_dir
from manus_mini.models import LoopLimits, SessionState
from manus_mini.prompt_tui import PromptTui, PromptTuiOptions
from manus_mini.redaction import redact_sensitive_text
from manus_mini.runtime import AgentRuntime
from manus_mini.session_store import CorruptSessionError, SessionStore


MAX_LIST_MESSAGE_PREVIEW_CHARS = 120
PROJECT_OVERVIEW_EXAMPLE = 'Example: manus-mini run "总结一下当前项目" --cwd .'
RUN_HELP_EPILOG = "\n".join(
    [
        PROJECT_OVERVIEW_EXAMPLE,
        "Quote multi-word prompts so your shell keeps them as one request.",
        "Then resume with: manus-mini resume <session_id> --cwd .",
    ]
)
MAIN_HELP_EPILOG = "\n".join(["Interactive mode: manus-mini --cwd .", RUN_HELP_EPILOG])
REMOVE_HELP_EPILOG = "\n".join(
    [
        "This also removes matching log directories.",
        "Example: manus-mini remove <session_id> --cwd .",
    ]
)
CLEAR_HELP_EPILOG = "\n".join(
    [
        "This also removes matching log directories.",
        "Use --force only in scripts after checking the target --cwd.",
        "Example: manus-mini clear --cwd .",
    ]
)
DOCTOR_HELP_EPILOG = "\n".join(
    [
        "This does not call the LLM API; it only checks local configuration and storage.",
        "Example: manus-mini doctor --cwd .",
    ]
)


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


class _ManusArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        if self.prog == "manus-mini run":
            self.exit(2, f"{self.prog}: error: {message}\n{PROJECT_OVERVIEW_EXAMPLE}\n")
        self.exit(2, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _ManusArgumentParser(
        prog="manus-mini",
        description="Self-managed coding agent runtime with resumable sessions, guarded tools, and local project storage.",
        epilog=MAIN_HELP_EPILOG,
        formatter_class=_HelpFormatter,
    )
    _add_runtime_options(parser, include_defaults=True, cwd_dest="global_cwd")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="list saved sessions")
    _add_cwd_option(list_parser, dest="subcommand_cwd", default=None)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check local setup and storage paths",
        description="check local setup and storage paths",
        epilog=DOCTOR_HELP_EPILOG,
        formatter_class=_HelpFormatter,
    )
    _add_cwd_option(doctor_parser, dest="subcommand_cwd", default=None)

    run_parser = subparsers.add_parser(
        "run",
        help="run one prompt and save a session",
        description="run one prompt and save a session",
        epilog=RUN_HELP_EPILOG,
        formatter_class=_HelpFormatter,
    )
    run_parser.add_argument("prompt", nargs="+", help="prompt text to execute once")
    _add_runtime_options(run_parser, include_defaults=False, cwd_dest="subcommand_cwd")

    resume_parser = subparsers.add_parser(
        "resume",
        help="resume a saved session",
        description="resume a saved session",
        formatter_class=_HelpFormatter,
    )
    resume_parser.add_argument("session_id")
    _add_runtime_options(resume_parser, include_defaults=False, cwd_dest="subcommand_cwd")

    remove_parser = subparsers.add_parser(
        "remove",
        help="remove a saved session",
        description="remove one saved session for the working directory",
        epilog=REMOVE_HELP_EPILOG,
        formatter_class=_HelpFormatter,
    )
    remove_parser.add_argument("session_id", help="saved session id to remove")
    _add_cwd_option(remove_parser, dest="subcommand_cwd", default=None)

    clear_parser = subparsers.add_parser(
        "clear",
        help="clear all saved sessions",
        description="clear all saved sessions for the working directory",
        epilog=CLEAR_HELP_EPILOG,
        formatter_class=_HelpFormatter,
    )
    _add_cwd_option(clear_parser, dest="subcommand_cwd", default=None)
    clear_parser.add_argument("--force", "-f", action="store_true", help="skip confirmation prompt")
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
    if args.command == "doctor":
        _run_doctor(cwd)
        return
    if args.command == "run":
        _run_once(
            cwd=cwd,
            prompt=" ".join(args.prompt).strip(),
            dry_run=dry_run,
            max_steps=max_steps,
            max_react=max_react,
            max_reflect=max_reflect,
            max_tool_retries=max_tool_retries,
        )
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
    if args.command is None:
        _run_default_tui(
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
    print(f"Session directory: {store.sessions_dir}")
    if not sessions:
        print("No saved sessions.")
        print(f'Start with: manus-mini run "你的问题" --cwd {cwd}')
        print(f'Example: manus-mini run "总结一下当前项目" --cwd {cwd}')
        return

    print(f"Saved sessions: {len(sessions)}")
    print()
    print(f"{'SESSION ID':<22} {'UPDATED':<19} {'MESSAGES':>8}  LAST USER MESSAGE")
    print(f"{'-' * 22} {'-' * 19} {'-' * 8}  {'-' * 40}")
    for summary in sessions:
        print(
            f"{summary.session_id:<22} "
            f"{summary.updated_at:%Y-%m-%d %H:%M:%S} "
            f"{summary.message_count:>8}  "
            f"{_format_last_user_message(summary.last_user_message)}"
        )
    print()
    print(f"Resume with: manus-mini resume {sessions[0].session_id} --cwd {cwd}")
    print(f"Remove with: manus-mini remove {sessions[0].session_id} --cwd {cwd}")
    print(f"Clear all with: manus-mini clear --cwd {cwd}")


def _run_doctor(cwd: Path) -> None:
    config = AppConfig.from_env(cwd / ".env")
    store = SessionStore(cwd)
    storage_dir = project_storage_dir(cwd)
    logs_dir = project_logs_dir(cwd)
    outputs_dir = project_outputs_dir(cwd)
    memory_path = project_memory_path(cwd)
    sessions = store.list_sessions()
    llm_ready = (
        config.llm_provider == "openai-compatible"
        and bool(config.llm_base_url)
        and bool(config.llm_api_key)
    )

    print("Manus Mini Doctor")
    print(f"CWD: {cwd}")
    print()
    print("Storage")
    print(f"- Project storage: {storage_dir}")
    print(f"- Sessions: {store.sessions_dir} ({len(sessions)} saved)")
    print(f"- Logs: {logs_dir}")
    print(f"- Outputs: {outputs_dir}")
    print(f"- Memory DB: {memory_path}")
    print()
    print("LLM Config")
    print(f"- Status: {'ready' if llm_ready else 'incomplete'}")
    print(f"- Provider: {config.llm_provider or '[missing]'}")
    print(f"- Base URL: {config.llm_base_url or '[missing]'}")
    print(f"- API key: {'configured' if config.llm_api_key else '[missing]'}")
    print(f"- Model: {config.llm_model}")
    print(f"- Config source: {config.llm_config_source or '[not found]'}")
    print()
    print("Next")
    if not llm_ready:
        print("- Configure LLM_PROVIDER=openai-compatible, LLM_BASE_URL, LLM_API_KEY and LLM_MODEL.")
    print(f'- Run a task: manus-mini run "总结一下当前项目" --cwd {cwd}')
    print(f"- Open interactive mode: manus-mini --cwd {cwd}")
    print(f"- List sessions: manus-mini list --cwd {cwd}")


def _run_once(
    cwd: Path,
    prompt: str,
    dry_run: bool,
    max_steps: int,
    max_react: int,
    max_reflect: int,
    max_tool_retries: int,
) -> None:
    if not prompt:
        print("Error: prompt is required.")
        print(f'Example: manus-mini run "总结一下当前项目" --cwd {cwd}')
        raise SystemExit(1)
    limits = LoopLimits(
        max_engineering_steps=max_steps,
        max_react_iterations=max_react,
        max_reflection_rounds=max_reflect,
        max_tool_retries=max_tool_retries,
    )
    session = SessionState.create(cwd=cwd)
    runtime = AgentRuntime(default_limits=limits, dry_run=dry_run, cwd=cwd)
    result = runtime.on_user_message(prompt, session)
    SessionStore(cwd).save(result)
    final_message = result.messages[-1].content if result.messages else ""
    if final_message:
        print(final_message)
        print()
    print(f"Session ID: {result.session_id}")
    if result.active_task is not None:
        print(f"Status: {result.active_task.status}")
    print(f"Resume with: manus-mini resume {result.session_id} --cwd {cwd}")


def _run_default_tui(
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
    _ensure_interactive_terminal()
    _run_prompt_tui(PromptTui(options=PromptTuiOptions(cwd=cwd, limits=limits, dry_run=dry_run)))


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
        print(f"List sessions with: manus-mini list --cwd {cwd}")
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
    _ensure_interactive_terminal()
    _run_prompt_tui(
        PromptTui(
            options=PromptTuiOptions(cwd=cwd, limits=limits, dry_run=dry_run),
            initial_session=session,
        )
    )


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
            print(f"List sessions with: manus-mini list --cwd {cwd}")
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
        print(f'Start with: manus-mini run "你的问题" --cwd {cwd}')
        print(f"List sessions with: manus-mini list --cwd {cwd}")
        return
    if not force:
        try:
            answer = input(f"Are you sure you want to clear all {count} saved session(s)? [y/N] ").strip().lower()
        except EOFError:
            print("Clear cancelled.")
            return
        if answer not in ("y", "yes", "确认", "是"):
            print("Clear cancelled.")
            return

    count = store.clear_all()
    logs_deleted = store.clear_all_logs()
    print(f"All {count} saved session(s) have been cleared (also cleaned {logs_deleted} log dir(s)).")


def _ensure_interactive_terminal() -> None:
    if not sys.stdin.isatty():
        print("Error: interactive terminal UI requires a terminal. Use 'manus-mini --help' for non-interactive commands.")
        raise SystemExit(1)


def _run_prompt_tui(tui: PromptTui) -> None:
    try:
        tui.run()
    except OSError as exc:
        if getattr(exc, "errno", None) == 22:
            print("Error: interactive terminal UI requires a terminal. Use 'manus-mini --help' for non-interactive commands.")
            raise SystemExit(1) from None
        raise


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
