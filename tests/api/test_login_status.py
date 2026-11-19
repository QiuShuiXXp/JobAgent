"""/api/login_status 契约测试：浏览器不真起来，依赖换成假管理器。

window_open 要求"实例存在 + 带窗口"两个条件同时成立：无头实例在跑也不能算开着窗口。
"""
import pytest
from fastapi.testclient import TestClient

from jobagent.api.app import create_app
from jobagent.api.dependencies import get_browser_manager


class FakeManager:
    def __init__(self, *, instance: bool, presented: bool, logged_in: bool):
        self.instance = instance
        self.presented = presented
        self._logged_in = logged_in

    def has_context(self, user_id):
        return self.instance

    def is_presented(self):
        return self.presented

    async def logged_in(self, user_id):
        return self._logged_in


@pytest.fixture
def client():
    # 不进 with：不走 lifespan，就不会建库、不起浏览器
    app = create_app()
    app.state.fake = FakeManager(instance=True, presented=True, logged_in=False)
    app.dependency_overrides[get_browser_manager] = lambda: app.state.fake
    return TestClient(app)


def _get(client, sender_id="u1"):
    resp = client.get("/api/login_status", params={"sender_id": sender_id})
    assert resp.status_code == 200
    return resp.json()


def test_reports_open_window_and_no_login_yet(client):
    assert _get(client) == {"window_open": True, "logged_in": False}


def test_headless_instance_is_not_an_open_window(client):
    """搜索/打招呼跑的无头实例不该被前端当成"登录窗口还开着"。"""
    client.app.state.fake.presented = False
    assert _get(client) == {"window_open": False, "logged_in": False}


def test_no_instance_means_no_window(client):
    client.app.state.fake.instance = False
    assert _get(client) == {"window_open": False, "logged_in": False}


def test_cookie_detected_login_turns_into_auto_confirm_payload(client):
    """logged_in=true 就是前端替用户自动提交确认的扳机。"""
    client.app.state.fake._logged_in = True
    body = _get(client)
    assert body == {"window_open": True, "logged_in": True}
