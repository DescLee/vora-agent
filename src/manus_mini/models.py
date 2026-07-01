from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class LoopLimits(BaseModel):
    max_engineering_steps: int = 3
    max_react_iterations: int = 10
    max_reflection_rounds: int = 3
    max_tool_retries: int = 3
    max_tool_timeout_seconds: int = 30
    max_runtime_seconds: int = 180
    max_estimated_tokens: int = 128_000


class Message(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    role: Literal["user", "agent", "system", "tool"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tool_call_ids: list[str] = Field(default_factory=list)
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def agent(
        cls,
        content: str,
        tool_call_ids: list[str] | None = None,
    ) -> "Message":
        return cls(role="agent", content=content, tool_call_ids=list(tool_call_ids or []))

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def tool(cls, content: str, tool_call_id: str) -> "Message":
        return cls(role="tool", content=content, tool_call_id=tool_call_id)


class AgentError(BaseModel):
    code: Literal[
        "FILE_NOT_FOUND",
        "PATH_OUT_OF_WORKSPACE",
        "INVALID_TOOL_PARAMS",
        "USER_CANCELLED",
        "MAX_STEPS_REACHED",
        "MAX_REACT_ITERATIONS_REACHED",
        "MAX_REFLECTION_ROUNDS_REACHED",
        "RUNTIME_TIMEOUT",
        "TOKEN_BUDGET_EXCEEDED",
        "TOOL_TIMEOUT",
        "TOOL_RETRY_EXHAUSTED",
        "INVALID_LLM_OUTPUT",
        "LLM_ERROR",
        "UNKNOWN_ERROR",
    ]
    message: str
    retryable: bool = False


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: new_id("artifact"))
    path: Path
    kind: Literal["markdown", "text", "patch", "json"] = "markdown"
    summary: str = ""


class Observation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("obs"))
    tool_call_id: str | None = None
    ok: bool
    summary: str
    content: str = ""


class TraceEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("trace"))
    phase: Literal["react", "llm", "tool", "reflection", "runtime"]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    description: str
    intent: Literal["chat", "research", "code", "automation", "report"]
    status: Literal["pending", "running", "done", "skipped", "failed"] = "pending"


class TaskState(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    session_id: str = ""
    goal: str
    cwd: Path
    status: Literal[
        "planning",
        "acting",
        "observing",
        "reflecting",
        "reporting",
        "waiting_confirmation",
        "done",
        "failed",
    ] = "planning"
    plan: list[PlanStep] = Field(default_factory=list)
    current_step_index: int = 0
    observations: list[Observation] = Field(default_factory=list)
    trace_events: list[TraceEvent] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    errors: list[AgentError] = Field(default_factory=list)
    limits: LoopLimits = Field(default_factory=LoopLimits)
    model_context_limit: int | None = None
    last_prompt_tokens: int | None = None
    last_completion_tokens: int | None = None
    last_total_tokens: int | None = None
    step_count: int = 0
    result: str = ""

    @classmethod
    def create(cls, goal: str, cwd: Path, limits: LoopLimits | None = None) -> "TaskState":
        return cls(goal=goal, cwd=cwd, limits=limits or LoopLimits())


class PendingConfirmation(BaseModel):
    tool_name: str
    tool_call_id: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    prompt: str = ""
    approved: bool = False


class CompressionSnapshot(BaseModel):
    id: str = Field(default_factory=lambda: new_id("compression"))
    covered_message_ids: list[str]
    covered_observation_ids: list[str]
    summary: str
    retained_facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("memory"))
    scope: Literal["user", "project", "session", "artifact"]
    kind: Literal["preference", "project_summary", "artifact_summary", "decision", "constraint"]
    content: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    source_message_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: new_id("session"))
    cwd: Path
    messages: list[Message] = Field(default_factory=list)
    active_task: TaskState | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    compression_snapshots: list[CompressionSnapshot] = Field(default_factory=list)
    pending_confirmation: PendingConfirmation | None = None
    run_ids: list[str] = Field(default_factory=list)

    @classmethod
    def create(cls, cwd: Path) -> "SessionState":
        return cls(cwd=cwd)


class ContextSegment(BaseModel):
    id: str
    kind: Literal["plain_message", "tool_exchange", "summary", "memory", "artifact"]
    messages: list[Message] = Field(default_factory=list)
    estimated_tokens: int
    priority: int


class ContextBundle(BaseModel):
    current_user_message: Message
    recent_messages: list[Message] = Field(default_factory=list)
    relevant_memories: list[MemoryItem] = Field(default_factory=list)
    compression_summaries: list[CompressionSnapshot] = Field(default_factory=list)
    active_artifacts: list[Artifact] = Field(default_factory=list)
    recent_observations: list[Observation] = Field(default_factory=list)


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    resource_keys: list[str] = Field(default_factory=list)
    risk_level: Literal["safe", "write", "command"] = "safe"


__all__ = [
    "AgentError",
    "Artifact",
    "CompressionSnapshot",
    "ContextBundle",
    "ContextSegment",
    "LoopLimits",
    "MemoryItem",
    "Message",
    "Observation",
    "PendingConfirmation",
    "PlanStep",
    "SessionState",
    "TaskState",
    "ToolCall",
    "TraceEvent",
    "new_id",
]
