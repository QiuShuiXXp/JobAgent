"""/api/settings 契约测试：联网搜索 Key 的读写，以及明文密钥永不回前端。

不进 lifespan（不建库、不起浏览器）；落盘的两个函数都换成字典，
所以跑这些用例既不动真凭据管理器，也不改仓库 .env。
"""
import pytest
from fastapi.testclient import TestClient

from jobagent.api.app import create_app
from jobagent.config import bootstrap, secrets
from jobagent.config.settings import settings


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def store(monkeypatch):
    """假存放处：记下写了什么就完事。"""
    written: dict[str, str] = {}
    monkeypatch.setattr(bootstrap, "store_secret",
                        lambda name, value: written.update({name: value}) or "凭据管理器")
    monkeypatch.setattr(bootstrap, "upsert_config_values",
                        lambda values, path=None: written.update(values))
    return written


def test_get_报告两把key的状态_但不吐明文(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert {"llm_key_set", "search_key_set", "search_key_masked", "search_key_store"} <= set(data)
    assert data["search_key_set"] == bool(settings.qianfan_api_key)
    assert data["search_key_masked"] == "" or data["search_key_masked"].startswith("••••")
    # 配了 Key 的机器上，这条就是"接口没有把密钥漏给前端"的守门人
    assert settings.qianfan_api_key not in resp.text
    assert settings.llm_api_key not in resp.text


def test_put_存联网搜索key_当前进程立刻生效(client, store, monkeypatch):
    monkeypatch.setattr(settings, "qianfan_api_key", "")
    resp = client.put("/api/settings/llm", json={"search_api_key": "  fake-key-1234567890 "})
    assert resp.status_code == 200
    assert resp.json()["search_key_set"] is True
    # 粘贴带的首尾空白要剪掉，中间的空格才是错误
    assert store[secrets.QIANFAN_KEY_NAME] == "fake-key-1234567890"
    assert settings.qianfan_api_key == "fake-key-1234567890"


def test_put_留空表示不改(client, store, monkeypatch):
    monkeypatch.setattr(settings, "qianfan_api_key", "already-stored")
    monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")
    resp = client.put("/api/settings/llm",
                      json={"search_api_key": None, "model": "some-model"})
    assert resp.status_code == 200
    assert secrets.QIANFAN_KEY_NAME not in store
    assert settings.qianfan_api_key == "already-stored"


def test_put_key中间有空格被拒(client, store):
    resp = client.put("/api/settings/llm", json={"search_api_key": "bad key"})
    assert resp.status_code == 400
    assert secrets.QIANFAN_KEY_NAME not in store
