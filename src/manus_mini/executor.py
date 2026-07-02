from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures import as_completed
from difflib import unified_diff
from pathlib import Path

from manus_mini.models import PendingConfirmation, SessionState, TaskState, TraceEvent, ToolCall
from manus_mini.tools.base import ToolPreview, ToolResult
from manus_mini.tools.registry import ToolRegistry


RETRYABLE_TOOL_ERROR_CODES = {"TOOL_TIMEOUT", "TOOL_ERROR"}
USER_CANCELLED_ERROR_CODE = "USER_CANCELLED"
MAX_DIFF_PREVIEW_CHARS = 4000


def sanitize_tool_args(args: dict) -> dict:
    return {key: value for key, value in dict(args).items() if key != "workspace"}


class Executor:
    def __init__(self, registry: ToolRegistry, dry_run: bool = False) -> None:
        self.registry = registry
        self.dry_run = dry_run

    def prepare_tool_call(self, call: ToolCall, session: SessionState) -> ToolCall:
        run_args = sanitize_tool_args(call.args)
        run_args["workspace"] = session.cwd
        pending = session.pending_confirmation
        if pending is not None and pending.approved and pending.tool_name == call.name:
            pending_args = sanitize_tool_args(pending.tool_args)
            if all(run_args.get(key) == value for key, value in pending_args.items()):
                run_args["confirmed"] = True
        return call.model_copy(update={"args": run_args})

    def execute(self, call: ToolCall, session: SessionState, task: TaskState) -> ToolResult:
        max_attempts = max(1, task.limits.max_tool_retries + 1)
        last_result: ToolResult | None = None
        tool = self.registry.get(call.name)
        preview: ToolPreview | None = None
        requires_confirmation = bool(getattr(tool, "requires_confirmation", False))
        if self.dry_run or requires_confirmation:
            preview = tool.preview(**call.args)
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

        for attempt in range(1, max_attempts + 1):
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(tool.run, **call.args)
                result = future.result(timeout=task.limits.max_tool_timeout_seconds)
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                return self._cancelled_result(call.name)
            except FuturesTimeoutError:
                pool.shutdown(wait=False, cancel_futures=True)
                return ToolResult(tool_name=call.name, ok=False, summary="tool execution timed out", error_code="TOOL_TIMEOUT")
            except PermissionError as error:
                pool.shutdown(wait=False, cancel_futures=True)
                return ToolResult(tool_name=call.name, ok=False, summary=str(error) or error.__class__.__name__, error_code=str(error) or "PERMISSION_DENIED")
            except Exception as error:  # noqa: BLE001
                pool.shutdown(wait=False, cancel_futures=True)
                result = ToolResult(tool_name=call.name, ok=False, summary=str(error) or error.__class__.__name__, error_code="TOOL_ERROR")
            else:
                pool.shutdown(wait=False, cancel_futures=True)

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
            prompt=f"即将修改: {preview.summary}",
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
        if tool_name == "append_file":
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

    def run_batch(self, batch: list[ToolCall], session: SessionState, task: TaskState) -> dict[str, ToolResult]:
        if len(batch) == 1:
            call = batch[0]
            return {call.id: self.execute(call, session, task)}

        pool = ThreadPoolExecutor(max_workers=len(batch))
        futures = {pool.submit(self.execute, call, session, task): call for call in batch}
        results: dict[str, ToolResult] = {}
        try:
            for future in as_completed(futures):
                call = futures[future]
                results[call.id] = future.result()
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
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return results


def _read_text_preview(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _split_lines(content: str) -> list[str]:
    return content.splitlines(keepends=True) if content else []
