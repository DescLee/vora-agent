from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError

from manus_mini.models import PendingConfirmation, SessionState, TaskState, TraceEvent, ToolCall
from manus_mini.tools.base import ToolPreview, ToolResult
from manus_mini.tools.registry import ToolRegistry


RETRYABLE_TOOL_ERROR_CODES = {"TOOL_TIMEOUT", "TOOL_ERROR"}


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
        if self.dry_run and preview is not None and preview.risk_level == "write":
            session.pending_confirmation = PendingConfirmation(
                tool_name=call.name,
                tool_call_id=call.id,
                tool_args=dict(call.args),
                summary=preview.summary,
                prompt=f"即将修改: {preview.summary}",
            )
            task.trace_events.append(
                TraceEvent(
                    phase="tool",
                    message="Dry-run skipped write tool",
                    data={
                        "tool_call_id": call.id,
                        "tool_name": call.name,
                        "summary": preview.summary,
                        "requires_confirmation": True,
                        "dry_run": True,
                    },
                )
            )
            return ToolResult(tool_name=call.name, ok=False, summary=f"dry-run preview: {preview.summary}", error_code="DRY_RUN")
        if preview is not None and preview.requires_confirmation and "confirmed" not in call.args:
            session.pending_confirmation = PendingConfirmation(
                tool_name=call.name,
                tool_call_id=call.id,
                tool_args=dict(call.args),
                summary=preview.summary,
                prompt=f"即将修改: {preview.summary}",
            )
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

    def run_batch(self, batch: list[ToolCall], session: SessionState, task: TaskState) -> dict[str, ToolResult]:
        if len(batch) == 1:
            call = batch[0]
            return {call.id: self.execute(call, session, task)}

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            results = executor.map(lambda call: (call.id, self.execute(call, session, task)), batch)
            return dict(results)
