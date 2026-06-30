from pathlib import Path

from manus_mini.memory import MemoryManager


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
