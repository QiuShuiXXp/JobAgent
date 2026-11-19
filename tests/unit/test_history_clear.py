"""清空历史：会话/断点/摘要一起清，意向与简历槽保留。"""
import pytest

from jobagent.api.router.chat_router import clear_history
from jobagent.config.settings import settings
from jobagent.domain.contexts import TaskContext
from jobagent.domain.messages import BotMessage, FocusedObject, UserMessage
from jobagent.domain.state import DialogueState
from jobagent.infrastructure import db
from jobagent.repository.dialogue_repository import DialogueRepository
from jobagent.repository.session_repository import SessionRepository


def test_state_clear_history_drops_slots_too():
    state = DialogueState(sender_id="u1")
    state.global_slots.update({"intent_confirmed": "yes", "resume_summary": "{}"})
    state.start_session()
    state.begin_turn(UserMessage(message_id="m1", text="你好"))
    state.pending_turn.bot_messages.append(BotMessage(text="你好"))
    state.commit_pending_turn()
    state.active_task = TaskContext(flow_id="f", step_id="s", slots={})
    state.focused_object = FocusedObject(type="job_list", id="x", payload={})

    assert state.clear_history() == 1
    assert state.sessions == [] and state.current_session_id is None
    assert state.active_task is None and state.focused_object is None
    assert state.global_slots == {}, "从头走流程：意向/简历/登录槽也要清"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_base_url",
                        f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}")
    db.init_db_engine()
    return db


async def _seed(sender_id: str = "u1") -> None:
    await db.create_tables()
    state = DialogueState(sender_id=sender_id)
    state.global_slots.update({"intent_confirmed": "yes", "resume_summary": "{}"})
    state.start_session()
    state.begin_turn(UserMessage(message_id="m1", text="你好"))
    state.pending_turn.bot_messages.append(BotMessage(text="你好"))
    state.commit_pending_turn()
    state.active_task = TaskContext(flow_id="f", step_id="s", slots={})
    async with db.get_session() as session:
        await DialogueRepository(session).save_dialogue(state)
        await SessionRepository(session).save_fold("s1", sender_id, "旧摘要", 3)


async def test_endpoint_clears_history_and_summaries(temp_db):
    await _seed()
    result = await clear_history("u1")
    assert result == {"sender_id": "u1", "cleared_turns": 1, "cleared_summaries": 1}

    async with db.get_session() as session:
        state = await DialogueRepository(session).load_dialogue("u1")
        summary = await SessionRepository(session).latest_summary("u1")
    assert state.sessions == [] and state.current_session_id is None
    assert state.active_task is None
    assert state.global_slots == {}
    assert summary == ""
    await db.dispose_engine()


async def test_endpoint_is_idempotent_on_unknown_user(temp_db):
    await db.create_tables()
    result = await clear_history("nobody")
    assert result["cleared_turns"] == 0 and result["cleared_summaries"] == 0
    await db.dispose_engine()


async def test_http_route_is_registered(temp_db):
    """走一遍真正的 DELETE 路由，防止只测函数、路由没挂上。"""
    from fastapi.testclient import TestClient

    from jobagent.api.app import create_app

    await _seed()
    await db.dispose_engine()      # 换一个干净连接池给 TestClient 的事件循环
    db.init_db_engine()
    client = TestClient(create_app())    # 不进 with：不跑 lifespan
    resp = client.delete("/api/history/u1")
    assert resp.status_code == 200
    assert resp.json() == {"sender_id": "u1", "cleared_turns": 1, "cleared_summaries": 1}
    await db.dispose_engine()
