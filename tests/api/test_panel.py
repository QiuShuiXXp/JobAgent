"""/api/panel 契约测试：不真连壳，依赖换成假管理器。

supported 只有桌面壳内嵌左栏才为 true；collapse/reveal 双向切换，
收起后再展开必须回得到 presented=true。
"""
import pytest
from fastapi.testclient import TestClient

from jobagent.api.app import create_app
from jobagent.api.dependencies import get_browser_manager


class FakeViewport:
    def __init__(self, has_panel: bool):
        self.has_panel = has_panel


class FakeManager:
    def __init__(self, supported: bool = True, presented: bool = False):
        self.viewport = FakeViewport(supported)
        self._presented = presented
        self.calls: list[str] = []

    def is_presented(self):
        return self._presented

    def collapse(self):
        self.calls.append("collapse")
        if not self.viewport.has_panel:
            return False
        self._presented = False
        return True

    def present(self):
        self.calls.append("reveal")
        if not self.viewport.has_panel:
            return False
        self._presented = True
        return True


@pytest.fixture
def client():
    # 不进 with：不走 lifespan，就不会建库、不起浏览器
    app = create_app()
    app.state.fake = FakeManager()
    app.dependency_overrides[get_browser_manager] = lambda: app.state.fake
    return TestClient(app)


def test_dev_form_reports_unsupported(client):
    client.app.state.fake.viewport.has_panel = False
    assert client.get("/api/panel").json() == {"supported": False, "presented": False}


def test_collapse_then_reveal_round_trip(client):
    assert client.get("/api/panel").json() == {"supported": True, "presented": False}
    assert client.post("/api/panel", json={"action": "reveal"}).json() == {
        "supported": True, "presented": True}
    assert client.post("/api/panel", json={"action": "collapse"}).json() == {
        "supported": True, "presented": False}
    assert client.app.state.fake.calls == ["reveal", "collapse"]


def test_action_on_unsupported_form_is_400(client):
    client.app.state.fake.viewport.has_panel = False
    assert client.post("/api/panel", json={"action": "collapse"}).status_code == 400


def test_unknown_action_is_422(client):
    assert client.post("/api/panel", json={"action": "toggle"}).status_code == 422


def test_shell_not_responding_is_503(client):
    client.app.state.fake.collapse = lambda: False
    assert client.post("/api/panel", json={"action": "collapse"}).status_code == 503