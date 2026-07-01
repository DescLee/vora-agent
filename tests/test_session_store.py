from pathlib import Path

from manus_mini.models import Message, SessionState
from manus_mini.session_store import SessionStore


def test_session_store_saves_loads_and_lists_sessions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("上一轮问题"))
    session.messages.append(Message.agent("上一轮回答"))

    saved_path = store.save(session)
    loaded = store.load(session.session_id)
    summaries = store.list_sessions()

    assert saved_path == tmp_path / ".manus-mini" / "sessions" / f"{session.session_id}.json"
    assert loaded.session_id == session.session_id
    assert loaded.messages[-1].content == "上一轮回答"
    assert summaries[0].session_id == session.session_id
    assert summaries[0].message_count == 2
    assert summaries[0].last_user_message == "上一轮问题"


def test_session_store_rejects_unknown_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)

    try:
        store.load("missing")
    except FileNotFoundError as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("expected FileNotFoundError")
