from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from vora.config import AppConfig
from vora.logging import project_logs_dir, project_memory_path, project_outputs_dir, project_storage_dir
from vora.mcp import add_mcp_server, format_mcp_command, load_mcp_config, mcp_config_path, parse_env_pairs, remove_mcp_server
from vora.models import LoopLimits, SessionState
from vora.prompt_tui import PromptTui, PromptTuiOptions
from vora.redaction import redact_sensitive_text
from vora.runtime import AgentRuntime
from vora.session_store import CorruptSessionError, SessionStore
from vora.skills.loader import load_skills_from_root
from vora.skills.manager import add_skill, remove_skill, skills_root
from vora.skills.registry import BUILTIN_SKILLS


MAX_LIST_MESSAGE_PREVIEW_CHARS = 120
PROJECT_OVERVIEW_EXAMPLE = 'Example: vora run "总结一下当前项目" --cwd .'
RUN_HELP_EPILOG = "\n".join(
    [
        PROJECT_OVERVIEW_EXAMPLE,
        "Quote multi-word prompts so your shell keeps them as one request.",
        "Then resume with: vora resume <session_id> --cwd .",
    ]
)
MAIN_HELP_EPILOG = "\n".join(["Interactive mode: vora --cwd .", RUN_HELP_EPILOG])
REMOVE_HELP_EPILOG = "\n".join(
    [
        "This also removes matching log directories.",
        "Example: vora remove <session_id> --cwd .",
    ]
)
CLEAR_HELP_EPILOG = "\n".join(
    [
        "This also removes matching log directories.",
        "Use --force only in scripts after checking the target --cwd.",
        "Example: vora clear --cwd .",
    ]
)
DOCTOR_HELP_EPILOG = "\n".join(
    [
        "This does not call the LLM API; it only checks local configuration and storage.",
        "Example: vora doctor --cwd .",
    ]
)
MCP_HELP_EPILOG = "\n".join(
    [
        "Examples:",
        "  vora mcp list --cwd .",
        "  vora mcp add filesystem --command npx --arg -y --arg @modelcontextprotocol/server-filesystem --cwd .",
        "  vora mcp remove filesystem --cwd .",
    ]
)
SKILLS_HELP_EPILOG = "\n".join(
    [
        "Examples:",
        "  vora skills list --cwd .",
        "  vora skills add ./skills/project-analysis --cwd .",
        "  vora skills remove project-analysis --cwd .",
    ]
)


class _HelpFormatter(argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter):
    pass


class _VoraArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        if self.prog == "vora run":
            self.exit(2, f"{self.prog}: error: {message}\n{PROJECT_OVERVIEW_EXAMPLE}\n")
        self.exit(2, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _VoraArgumentParser(
        prog="vora",
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

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="manage MCP server configs",
        description="manage MCP server configs",
        epilog=MCP_HELP_EPILOG,
        formatter_class=_HelpFormatter,
    )
    _add_mcp_subcommands(mcp_parser)

    skills_parser = subparsers.add_parser(
        "skills",
        help="manage local Skills",
        description="manage local Skills",
        epilog=SKILLS_HELP_EPILOG,
        formatter_class=_HelpFormatter,
    )
    _add_skills_subcommands(skills_parser)

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


def _add_mcp_subcommands(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="mcp_command")

    list_parser = subparsers.add_parser("list", help="list configured MCP servers")
    _add_cwd_option(list_parser, dest="subcommand_cwd", default=None)
    list_parser.add_argument("--global", dest="global_scope", action="store_true", help="use user-level MCP config")

    add_parser = subparsers.add_parser("add", help="add or update an MCP server config")
    add_parser.add_argument("name", help="MCP server name")
    add_parser.add_argument("--command", dest="mcp_server_command", required=True, help="server command to execute")
    add_parser.add_argument("--arg", dest="args", action="append", default=[], help="server command argument; repeat as needed")
    add_parser.add_argument("--env", dest="env", action="append", default=[], help="server environment pair KEY=VALUE; repeat as needed")
    _add_cwd_option(add_parser, dest="subcommand_cwd", default=None)
    add_parser.add_argument("--global", dest="global_scope", action="store_true", help="write user-level MCP config")

    remove_parser = subparsers.add_parser("remove", help="remove an MCP server config")
    remove_parser.add_argument("name", help="MCP server name")
    _add_cwd_option(remove_parser, dest="subcommand_cwd", default=None)
    remove_parser.add_argument("--global", dest="global_scope", action="store_true", help="remove from user-level MCP config")


def _add_skills_subcommands(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="skills_command")

    list_parser = subparsers.add_parser("list", help="list available Skills")
    _add_cwd_option(list_parser, dest="subcommand_cwd", default=None)
    list_parser.add_argument("--global", dest="global_scope", action="store_true", help="show only user-level Skills")

    add_parser = subparsers.add_parser("add", help="copy a Skill directory into this project")
    add_parser.add_argument("source", type=Path, help="source Skill directory containing skill.json")
    add_parser.add_argument("--name", help="override target Skill directory name")
    _add_cwd_option(add_parser, dest="subcommand_cwd", default=None)
    add_parser.add_argument("--global", dest="global_scope", action="store_true", help="copy into user-level Skills")

    remove_parser = subparsers.add_parser("remove", help="remove a project or user-level Skill")
    remove_parser.add_argument("name", help="Skill name")
    _add_cwd_option(remove_parser, dest="subcommand_cwd", default=None)
    remove_parser.add_argument("--global", dest="global_scope", action="store_true", help="remove from user-level Skills")


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
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(_normalize_repeated_option_values(raw_argv, "--arg"))
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
    if args.command == "mcp":
        _run_mcp(args, cwd)
        return
    if args.command == "skills":
        _run_skills(args, cwd)
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
        print(f'Start with: vora run "你的问题" --cwd {cwd}')
        print(f'Example: vora run "总结一下当前项目" --cwd {cwd}')
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
    print(f"Resume with: vora resume {sessions[0].session_id} --cwd {cwd}")
    print(f"Remove with: vora remove {sessions[0].session_id} --cwd {cwd}")
    print(f"Clear all with: vora clear --cwd {cwd}")


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

    print("Vora Doctor")
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
    print(f'- Run a task: vora run "总结一下当前项目" --cwd {cwd}')
    print(f"- Open interactive mode: vora --cwd {cwd}")
    print(f"- List sessions: vora list --cwd {cwd}")


def _run_mcp(args: argparse.Namespace, cwd: Path) -> None:
    command = getattr(args, "mcp_command", None)
    global_scope = bool(getattr(args, "global_scope", False))
    if command == "list":
        _run_mcp_list(cwd, global_scope=global_scope)
        return
    if command == "add":
        try:
            env = parse_env_pairs(getattr(args, "env", []))
            path = add_mcp_server(
                cwd,
                args.name,
                command=args.mcp_server_command,
                args=getattr(args, "args", []),
                env=env,
                global_scope=global_scope,
            )
        except ValueError as error:
            print(f"Error: {error}")
            raise SystemExit(1) from None
        scope = "user" if global_scope else "project"
        print(f"MCP server '{args.name}' added ({scope}).")
        print(f"MCP config: {path}")
        return
    if command == "remove":
        try:
            path = remove_mcp_server(cwd, args.name, global_scope=global_scope)
        except ValueError as error:
            print(f"Error: {error}")
            raise SystemExit(1) from None
        except KeyError:
            print(f"Error: MCP server '{args.name}' not found.")
            raise SystemExit(1) from None
        print(f"MCP server '{args.name}' removed.")
        print(f"MCP config: {path}")
        return
    print("Error: missing MCP subcommand. Use 'vora mcp --help'.")
    raise SystemExit(1)


def _run_mcp_list(cwd: Path, *, global_scope: bool) -> None:
    path = mcp_config_path(cwd, global_scope=global_scope)
    config = load_mcp_config(path)
    print(f"MCP config: {path}")
    if not config.servers:
        print("No MCP servers configured.")
        print(f"Add one with: vora mcp add filesystem --command npx --arg -y --arg @modelcontextprotocol/server-filesystem --cwd {cwd}")
        return

    print(f"MCP servers: {len(config.servers)}")
    print()
    print(f"{'NAME':<24} {'COMMAND'}")
    print(f"{'-' * 24} {'-' * 40}")
    for name, server in sorted(config.servers.items()):
        print(f"{name:<24} {format_mcp_command(server)}")


def _run_skills(args: argparse.Namespace, cwd: Path) -> None:
    command = getattr(args, "skills_command", None)
    global_scope = bool(getattr(args, "global_scope", False))
    if command == "list":
        _run_skills_list(cwd, global_scope=global_scope)
        return
    if command == "add":
        try:
            skill, target = add_skill(cwd, args.source, name=getattr(args, "name", None), global_scope=global_scope)
        except (ValueError, FileExistsError, OSError) as error:
            print(f"Error: {error}")
            raise SystemExit(1) from None
        scope = "user" if global_scope else "project"
        print(f"Skill '{skill.name}' added ({scope}).")
        print(f"Skill directory: {target}")
        return
    if command == "remove":
        try:
            target = remove_skill(cwd, args.name, global_scope=global_scope)
        except ValueError as error:
            print(f"Error: {error}")
            raise SystemExit(1) from None
        except FileNotFoundError:
            print(f"Error: Skill '{args.name}' not found.")
            raise SystemExit(1) from None
        print(f"Skill '{args.name}' removed.")
        print(f"Skill directory: {target}")
        return
    print("Error: missing Skills subcommand. Use 'vora skills --help'.")
    raise SystemExit(1)


def _run_skills_list(cwd: Path, *, global_scope: bool) -> None:
    project_root = skills_root(cwd, global_scope=False)
    user_root = skills_root(cwd, global_scope=True)
    rows: list[tuple[str, str, str]] = []
    if not global_scope:
        rows.extend((skill.name, "built-in", skill.description) for skill in BUILTIN_SKILLS)
        rows.extend((skill.name, "project", skill.description) for skill in load_skills_from_root(project_root))
    rows.extend((skill.name, "user", skill.description) for skill in load_skills_from_root(user_root))

    print("Skills:")
    print(f"- Project directory: {project_root}")
    print(f"- User directory: {user_root}")
    if not rows:
        print("No Skills found.")
        print(f"Add one with: vora skills add ./skills/my-skill --cwd {cwd}")
        return

    print()
    print(f"{'NAME':<24} {'SCOPE':<10} DESCRIPTION")
    print(f"{'-' * 24} {'-' * 10} {'-' * 40}")
    for name, scope, description in sorted(rows, key=lambda item: (item[1], item[0])):
        print(f"{name:<24} {scope:<10} {description}")


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
        print(f'Example: vora run "总结一下当前项目" --cwd {cwd}')
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
    print(f"Resume with: vora resume {result.session_id} --cwd {cwd}")


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
        print(f"List sessions with: vora list --cwd {cwd}")
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
            print(f"List sessions with: vora list --cwd {cwd}")
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
        print(f'Start with: vora run "你的问题" --cwd {cwd}')
        print(f"List sessions with: vora list --cwd {cwd}")
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
        print("Error: interactive terminal UI requires a terminal. Use 'vora --help' for non-interactive commands.")
        raise SystemExit(1)


def _run_prompt_tui(tui: PromptTui) -> None:
    try:
        tui.run()
    except OSError as exc:
        if getattr(exc, "errno", None) == 22:
            print("Error: interactive terminal UI requires a terminal. Use 'vora --help' for non-interactive commands.")
            raise SystemExit(1) from None
        raise


def _option_was_provided(argv: list[str], name: str) -> bool:
    return any(item == name or item.startswith(f"{name}=") for item in argv)


def _normalize_repeated_option_values(argv: list[str], option: str) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == option and index + 1 < len(argv) and argv[index + 1].startswith("-"):
            normalized.append(f"{option}={argv[index + 1]}")
            index += 2
            continue
        normalized.append(item)
        index += 1
    return normalized


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
