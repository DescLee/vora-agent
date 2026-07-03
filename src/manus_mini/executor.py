from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures import as_completed
from difflib import unified_diff
from pathlib import Path, PurePosixPath

from manus_mini.models import PendingConfirmation, SessionState, TaskState, TraceEvent, ToolCall
from manus_mini.tools.base import ToolPreview, ToolResult
from manus_mini.tools.file_tools import NOISE_DIR_NAMES
from manus_mini.tools.registry import ToolRegistry


RETRYABLE_TOOL_ERROR_CODES = {"TOOL_TIMEOUT", "TOOL_ERROR"}
USER_CANCELLED_ERROR_CODE = "USER_CANCELLED"
MAX_DIFF_PREVIEW_CHARS = 4000
DEFAULT_TOOL_THREAD_POOL_WORKERS = 8


def sanitize_tool_args(args: dict) -> dict:
    return {key: value for key, value in dict(args).items() if key != "workspace" and not str(key).startswith("_")}


def _tool_timeout_seconds(value: int | None) -> int | None:
    if value is None or value <= 0:
        return None
    return value


class Executor:
    def __init__(
        self,
        registry: ToolRegistry,
        dry_run: bool = False,
        max_workers: int = DEFAULT_TOOL_THREAD_POOL_WORKERS,
    ) -> None:
        self.registry = registry
        self.dry_run = dry_run
        self._tool_pool = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="manus-tool")

    def prepare_tool_call(self, call: ToolCall, session: SessionState) -> ToolCall:
        run_args = sanitize_tool_args(call.args)
        run_args = self._rewrite_missing_source_path(call.name, run_args, session.cwd)
        run_args["workspace"] = session.cwd
        pending = session.pending_confirmation
        if pending is not None and pending.approved and pending.tool_name == call.name:
            pending_args = sanitize_tool_args(pending.tool_args)
            if all(run_args.get(key) == value for key, value in pending_args.items()):
                run_args["confirmed"] = True
        return call.model_copy(update={"args": run_args})

    def _rewrite_missing_source_path(self, tool_name: str, args: dict, workspace: Path) -> dict:
        if tool_name not in {"read_file", "list_files"}:
            return args
        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            return args
        workspace_root = workspace.expanduser().resolve()
        requested = _resolve_display_path(workspace_root, raw_path)
        if requested.exists():
            return args
        basename = PurePosixPath(raw_path.replace("\\", "/")).name
        if not basename or basename in {".", ".."}:
            return args
        matches = [
            path
            for path in workspace_root.rglob(basename)
            if _is_rewrite_candidate(path, workspace_root, tool_name)
        ]
        if len(matches) != 1:
            return args
        rewritten = dict(args)
        rewritten["path"] = matches[0].relative_to(workspace_root).as_posix()
        rewritten["_path_rewritten"] = True
        return rewritten

    def execute(self, call: ToolCall, session: SessionState, task: TaskState) -> ToolResult:
        timeout_seconds = _tool_timeout_seconds(task.limits.max_tool_timeout_seconds)
        future = self._tool_pool.submit(self._execute_sync, call, session, task)
        try:
            return future.result(timeout=timeout_seconds)
        except KeyboardInterrupt:
            future.cancel()
            return self._cancelled_result(call.name)
        except FuturesTimeoutError:
            future.cancel()
            return ToolResult(tool_name=call.name, ok=False, summary="tool execution timed out", error_code="TOOL_TIMEOUT")

    def _execute_sync(self, call: ToolCall, session: SessionState, task: TaskState) -> ToolResult:
        max_attempts = max(1, task.limits.max_tool_retries + 1)
        last_result: ToolResult | None = None
        tool = self.registry.get(call.name)
        preview: ToolPreview | None = None
        requires_confirmation = bool(getattr(tool, "requires_confirmation", False))
        if self.dry_run or requires_confirmation or call.name in {"run_bash", "run_temp_script"}:
            try:
                preview = tool.preview(**call.args)
            except NotImplementedError:
                preview = None
        if preview is not None and preview.requires_confirmation and "confirmed" not in call.args:
            pending = self._build_pending_confirmation(tool, call, preview, session)
            session.pending_confirmation = pending
            task.trace_events.append(
                TraceEvent(
                    phase="tool",
                    message="Tool call requires confirmation",
                    data={
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "summary": preview.summary,
                        "requires_confirmation": True,
                    },
                )
            )
            if self.dry_run:
                task.trace_events[-1].message = "Dry-run skipped write tool"
                task.trace_events[-1].data["dry_run"] = True
                return ToolResult(tool_name=call.name, ok=False, summary=f"dry-run preview: {preview.summary}", error_code="DRY_RUN")
            return ToolResult(
                tool_name=call.name,
                ok=False,
                summary=f"confirmation required: {preview.summary}",
                error_code="WRITE_REQUIRES_CONFIRMATION",
            )

        diff_preview = self._build_nonblocking_diff_preview(call.name, call.args, session.cwd)
        if diff_preview:
            task.trace_events.append(
                TraceEvent(
                    phase="tool",
                    message="Tool diff preview",
                    data={
                        "message_type": "diff_preview",
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "diff_preview": diff_preview,
                    },
                )
            )

        for attempt in range(1, max_attempts + 1):
            try:
                result = tool.run(**call.args)
            except KeyboardInterrupt:
                return self._cancelled_result(call.name)
            except PermissionError as error:
                return ToolResult(tool_name=call.name, ok=False, summary=str(error) or error.__class__.__name__, error_code=str(error) or "PERMISSION_DENIED")
            except Exception as error:  # noqa: BLE001
                result = ToolResult(tool_name=call.name, ok=False, summary=str(error) or error.__class__.__name__, error_code="TOOL_ERROR")

            if result.ok:
                return result

            last_result = result
            if attempt < max_attempts and self._should_retry(result):
                task.trace_events.append(
                    TraceEvent(
                        phase="tool",
                        message="Tool retry scheduled",
                        data={
                            "tool_call_id": call.id,
                            "tool_name": call.name,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "error_code": result.error_code,
                        },
                    )
                )
                continue
            if not self._should_retry(result):
                return last_result

        assert last_result is not None
        return last_result.model_copy(update={"error_code": "TOOL_RETRY_EXHAUSTED"})

    def _should_retry(self, result: ToolResult) -> bool:
        return result.error_code in RETRYABLE_TOOL_ERROR_CODES

    def _cancelled_result(self, tool_name: str) -> ToolResult:
        return ToolResult(tool_name=tool_name, ok=False, summary="tool execution interrupted by user", error_code=USER_CANCELLED_ERROR_CODE)

    def _build_pending_confirmation(
        self,
        tool,
        call: ToolCall,
        preview: ToolPreview,
        session: SessionState,
    ) -> PendingConfirmation:
        diff_preview = self._build_diff_preview(tool.name, call.args, session.cwd)
        return PendingConfirmation(
            tool_name=call.name,
            tool_call_id=call.id,
            tool_args=dict(call.args),
            summary=preview.summary,
            prompt=_confirmation_prompt(preview),
            diff_preview=diff_preview,
        )

    def _build_diff_preview(self, tool_name: str, args: dict, workspace: Path) -> str:
        path = str(args.get("path", "")).strip()
        if not path:
            return ""
        if tool_name == "make_directory":
            return f"+ mkdir -p {path}\n"

        target = (workspace / path).expanduser().resolve()
        before = _read_text_preview(target)
        if tool_name == "replace_in_file":
            after = _preview_replace_content(before, args)
            if after is None:
                return ""
        elif tool_name == "append_file":
            after = before + str(args.get("content", ""))
        else:
            after = str(args.get("content", ""))

        diff = "".join(
            unified_diff(
                _split_lines(before),
                _split_lines(after),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
            )
        )
        if not diff:
            diff = f"--- a/{path}\n+++ b/{path}\n"
        if len(diff) <= MAX_DIFF_PREVIEW_CHARS:
            return diff
        truncated = diff[:MAX_DIFF_PREVIEW_CHARS]
        remaining = len(diff) - MAX_DIFF_PREVIEW_CHARS
        return f"{truncated}\n... [truncated {remaining} more char(s)]\n"

    def _build_nonblocking_diff_preview(self, tool_name: str, args: dict, workspace: Path) -> str:
        if tool_name != "replace_in_file":
            return ""
        return self._build_diff_preview(tool_name, args, workspace)

    def run_batch(self, batch: list[ToolCall], session: SessionState, task: TaskState) -> dict[str, ToolResult]:
        if len(batch) == 1:
            call = batch[0]
            return {call.id: self.execute(call, session, task)}

        timeout_seconds = _tool_timeout_seconds(task.limits.max_tool_timeout_seconds)
        futures = {self._tool_pool.submit(self._execute_sync, call, session, task): call for call in batch}
        results: dict[str, ToolResult] = {}
        try:
            for future in as_completed(futures, timeout=timeout_seconds):
                call = futures[future]
                results[call.id] = future.result()
        except FuturesTimeoutError:
            for future in futures:
                future.cancel()
            for call in batch:
                if call.id not in results:
                    results[call.id] = ToolResult(tool_name=call.name, ok=False, summary="tool execution timed out", error_code="TOOL_TIMEOUT")
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            task.trace_events.append(
                TraceEvent(
                    phase="tool",
                    message="Tool batch interrupted by user",
                    data={
                        "tool_call_ids": [call.id for call in batch],
                        "tool_names": [call.name for call in batch],
                    },
                )
            )
            for call in batch:
                if call.id not in results:
                    results[call.id] = self._cancelled_result(call.name)
        return results

    def shutdown(self) -> None:
        self._tool_pool.shutdown(wait=False, cancel_futures=True)


def _read_text_preview(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _resolve_display_path(workspace: Path, path: str) -> Path:
    raw = Path(path).expanduser()
    if raw.is_absolute():
        return raw.resolve(strict=False)
    return (workspace / raw).resolve(strict=False)


def _is_rewrite_candidate(path: Path, workspace: Path, tool_name: str) -> bool:
    if tool_name == "read_file" and not path.is_file():
        return False
    if tool_name == "list_files" and not (path.is_file() or path.is_dir()):
        return False
    try:
        relative_parts = path.relative_to(workspace).parts
    except ValueError:
        return False
    return not any(part in NOISE_DIR_NAMES for part in relative_parts[:-1])


def _split_lines(content: str) -> list[str]:
    return content.splitlines(keepends=True) if content else []


def _preview_replace_content(content: str, args: dict) -> str | None:
    old_text = args.get("old_text")
    new_text = args.get("new_text")
    if old_text is None or new_text is None:
        return None
    old_value = str(old_text)
    matches = _find_contextual_matches(
        content,
        old_value,
        before_text=str(args.get("before_text") or ""),
        after_text=str(args.get("after_text") or ""),
    )
    if not matches:
        return None
    expected = _positive_int(args.get("expected_replacements"), 1)
    if len(matches) != expected:
        return None
    return _replace_matches(content, matches, old_value, str(new_text))


def _confirmation_prompt(preview: ToolPreview) -> str:
    if preview.risk_level == "command":
        return f"即将执行: {preview.summary}"
    return f"即将修改: {preview.summary}"


def _positive_int(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _find_contextual_matches(content: str, old_text: str, before_text: str = "", after_text: str = "") -> list[int]:
    if not old_text:
        return []
    matches: list[int] = []
    start = 0
    while True:
        index = content.find(old_text, start)
        if index < 0:
            return matches
        before_ok = not before_text or content[:index].endswith(before_text)
        after_start = index + len(old_text)
        after_ok = not after_text or content[after_start:].startswith(after_text)
        if before_ok and after_ok:
            matches.append(index)
        start = index + len(old_text)


def _replace_matches(content: str, matches: list[int], old_text: str, new_text: str) -> str:
    if not matches:
        return content
    parts: list[str] = []
    cursor = 0
    for index in matches:
        parts.append(content[cursor:index])
        parts.append(new_text)
        cursor = index + len(old_text)
    parts.append(content[cursor:])
    return "".join(parts)
