import json
from pathlib import Path

from manus_mini.models import Message, SessionState, TaskState
from manus_mini.logging import project_logs_dir, project_memory_path, project_sessions_dir
from manus_mini.session_store import SessionStore


def test_session_store_saves_loads_and_lists_sessions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("上一轮问题"))
    session.messages.append(Message.agent("上一轮回答"))

    saved_path = store.save(session)
    loaded = store.load(session.session_id)
    summaries = store.list_sessions()

    assert saved_path == project_sessions_dir(tmp_path) / f"{session.session_id}.json"
    assert loaded.session_id == session.session_id
    assert loaded.messages[-1].content == "上一轮回答"
    assert summaries[0].session_id == session.session_id
    assert summaries[0].message_count == 2
    assert summaries[0].last_user_message == "上一轮问题"


def test_session_store_loads_legacy_runtime_timeout_sessions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("旧任务"))
    session.active_task = TaskState.create(goal="旧任务", cwd=tmp_path)
    data = session.model_dump(mode="json")
    data["active_task"]["errors"].append(
        {
            "code": "RUNTIME_TIMEOUT",
            "message": "runtime exceeded 180 seconds",
            "retryable": True,
        }
    )
    store.sessions_dir.mkdir(parents=True, exist_ok=True)
    (store.sessions_dir / f"{session.session_id}.json").write_text(
        json.dumps(data),
        encoding="utf-8",
    )

    loaded = store.load(session.session_id)
    summaries = store.list_sessions()

    assert loaded.active_task is not None
    assert loaded.active_task.errors[0].code == "RUNTIME_TIMEOUT"
    assert summaries[0].session_id == session.session_id


def test_session_store_saves_current_schema_version(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)

    saved_path = store.save(session)
    data = json.loads(saved_path.read_text(encoding="utf-8"))

    assert data["schema_version"] == 1


def test_session_store_migrates_unknown_error_code_to_unknown_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("旧任务"))
    session.active_task = TaskState.create(goal="旧任务", cwd=tmp_path)
    data = session.model_dump(mode="json")
    data.pop("schema_version", None)
    data["active_task"]["errors"].append(
        {
            "code": "OLD_NETWORK_ERROR",
            "message": "old provider failed",
            "retryable": True,
        }
    )
    store.sessions_dir.mkdir(parents=True, exist_ok=True)
    (store.sessions_dir / f"{session.session_id}.json").write_text(
        json.dumps(data),
        encoding="utf-8",
    )

    loaded = store.load(session.session_id)
    summaries = store.list_sessions()

    assert loaded.schema_version == 1
    assert loaded.active_task is not None
    assert loaded.active_task.errors[0].code == "UNKNOWN_ERROR"
    assert loaded.active_task.errors[0].message == "old provider failed"
    assert loaded.active_task.errors[0].retryable is True
    assert loaded.active_task.errors[0].metadata["legacy_code"] == "OLD_NETWORK_ERROR"
    assert summaries[0].session_id == session.session_id


def test_session_store_rejects_unknown_session(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)

    try:
        store.load("missing")
    except FileNotFoundError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_session_store_rejects_path_traversal_session_id(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.session_id = "../outside"

    for action in (
        lambda: store.save(session),
        lambda: store.load("../outside"),
        lambda: store.delete("../outside"),
        lambda: store.delete_logs_for_session("../sessions"),
    ):
        try:
            action()
        except ValueError as error:
            assert "invalid session_id" in str(error)
        else:
            raise AssertionError("expected ValueError")


def test_session_store_log_cleanup_cannot_escape_logs_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    saved_path = store.save(session)

    try:
        store.delete_logs_for_session("../sessions")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")

    assert saved_path.exists()


def test_session_store_cleans_logs_from_user_manus_mini(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    session_id = "session-abc123"
    log_dir = project_logs_dir(tmp_path) / session_id
    other_log_dir = project_logs_dir(tmp_path) / "session-other"
    log_dir.mkdir(parents=True)
    other_log_dir.mkdir(parents=True)

    store = SessionStore(tmp_path)

    assert store.delete_logs_for_session(session_id) == 1
    assert not log_dir.exists()
    assert other_log_dir.exists()


def test_session_store_migrates_legacy_project_manus_mini(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    legacy_session = SessionState.create(cwd=tmp_path)
    legacy_sessions_dir = tmp_path / ".manus-mini" / "sessions"
    legacy_sessions_dir.mkdir(parents=True)
    (legacy_sessions_dir / f"{legacy_session.session_id}.json").write_text(
        legacy_session.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (tmp_path / ".manus-mini" / "memory.db").write_bytes(b"legacy-memory")

    SessionStore(tmp_path)

    migrated_session = project_sessions_dir(tmp_path) / f"{legacy_session.session_id}.json"
    assert migrated_session.exists()
    assert project_memory_path(tmp_path).read_bytes() == b"legacy-memory"


def test_session_store_migration_does_not_overwrite_existing_project_data(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    legacy_sessions_dir = tmp_path / ".manus-mini" / "sessions"
    legacy_sessions_dir.mkdir(parents=True)
    (legacy_sessions_dir / "session-existing.json").write_text("legacy", encoding="utf-8")
    (tmp_path / ".manus-mini" / "memory.db").write_bytes(b"legacy-memory")
    project_sessions_dir(tmp_path).mkdir(parents=True)
    (project_sessions_dir(tmp_path) / "session-existing.json").write_text("current", encoding="utf-8")
    project_memory_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    project_memory_path(tmp_path).write_bytes(b"current-memory")

    SessionStore(tmp_path)

    assert (project_sessions_dir(tmp_path) / "session-existing.json").read_text(encoding="utf-8") == "current"
    assert project_memory_path(tmp_path).read_bytes() == b"current-memory"
