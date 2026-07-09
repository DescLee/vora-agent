from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from manus_mini.memory import MemoryManager
from manus_mini.models import Message
from manus_mini.session import SessionManager


def test_user_preference_can_be_saved_and_retrieved(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory.db")

    manager.add(
        scope="user",
        kind="preference",
        content="用户偏好：报告尽量用 Markdown 输出。",
        tags=["preference", "markdown"],
    )

    results = manager.search("markdown", limit=5)

    assert len(results) == 1
    assert results[0].content == "用户偏好：报告尽量用 Markdown 输出。"
    assert results[0].id


def test_sensitive_content_is_not_written_to_long_term_memory(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory.db")

    assert (
        manager.add_if_allowed(
            scope="user",
            kind="preference",
            content="API_KEY=abc123",
            tags=["secret"],
        )
        is None
    )
    assert (
        manager.add_if_allowed(
            scope="user",
            kind="preference",
            content="TOKEN=xyz",
            tags=["secret"],
        )
        is None
    )
    assert (
        manager.add_if_allowed(
            scope="user",
            kind="preference",
            content="PASSWORD=abc",
            tags=["secret"],
        )
        is None
    )
    assert (
        manager.add_if_allowed(
            scope="user",
            kind="preference",
            content="SECRET plan",
            tags=["secret"],
        )
        is None
    )
    assert (
        manager.add_if_allowed(
            scope="user",
            kind="preference",
            content="我的 key 是 sk-live-secret",
            tags=["secret"],
        )
        is None
    )
    assert (
        manager.add_if_allowed(
            scope="user",
            kind="preference",
            content="数据库 password: abc123",
            tags=["secret"],
        )
        is None
    )

    assert manager.search("secret", limit=10) == []


def test_search_result_exposes_id_and_content(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory.db")

    memory = manager.add(
        scope="project",
        kind="decision",
        content="决定：采用 TUI 作为第一版交互界面。",
        tags=["decision", "tui"],
    )

    results = manager.search("TUI", limit=5)

    assert len(results) == 1
    assert results[0].id == memory.id
    assert results[0].content == memory.content


def test_delete_matching_memory_items(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory.db")

    manager.add(scope="user", kind="preference", content="用户偏好：尽量使用 Markdown。", tags=["preference"])
    manager.add(scope="project", kind="decision", content="决定：采用 TUI。", tags=["decision"])

    deleted = manager.delete_matching("Markdown")

    assert deleted == 1
    assert manager.search("Markdown") == []
    assert len(manager.search("TUI")) == 1


def test_session_manager_can_forget_memories(tmp_path: Path) -> None:
    memory_manager = MemoryManager(tmp_path / "memory.db")
    memory_manager.add(scope="user", kind="preference", content="用户偏好：报告用 Markdown。", tags=["markdown"])
    manager = SessionManager(tmp_path, memory_manager=memory_manager)

    session = manager.handle_user_message("忘记 Markdown")

    assert session.messages[-1].content == "已删除 1 条长期记忆。"
    assert memory_manager.search("Markdown") == []


def test_memory_manager_supports_cross_thread_access(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory.db")
    manager.add(scope="user", kind="preference", content="用户偏好：尽量用 Markdown。", tags=["markdown"])

    with ThreadPoolExecutor(max_workers=1) as executor:
        results = executor.submit(manager.search, "Markdown", 5).result()

    assert len(results) == 1
    assert results[0].content == "用户偏好：尽量用 Markdown。"


def test_session_manager_can_compact_context_manually(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    manager.current.messages.extend(Message.user(f"历史消息 {index} " + "x" * 80) for index in range(8))

    session = manager.handle_user_message("压缩上下文")

    assert session.compression_snapshots
    assert session.messages[0].role == "system"
    assert session.messages[0].content.startswith("历史上下文摘要：")
    assert session.messages[-1].content.startswith("已手动压缩上下文")
    assert "压缩前估算" in session.messages[-1].content
    assert "目标预算" in session.messages[-1].content
    assert "保留消息" in session.messages[-1].content
