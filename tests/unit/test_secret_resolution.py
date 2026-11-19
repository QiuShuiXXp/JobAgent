"""密钥解析顺序的单测：一行空占位不许挡住别处真配好的值。

不碰真凭据管理器、不碰仓库 .env 和 %LOCALAPPDATA% 那份 config.env —— 形态、文件、
keyring 全换成 tmp_path 里的假件，跑多少遍都不会在这台机器上留下痕迹。
"""
from types import SimpleNamespace

import pytest

from jobagent.config import bootstrap, paths, secrets

NAME = secrets.QIANFAN_KEY_NAME


@pytest.fixture
def box(monkeypatch, tmp_path):
    """可控的双形态沙盒：config=当前形态的配置文件，dev=仓库 .env，ring=凭据管理器。

    box.desktop(False) 切到 dev 形态。
    """
    config = tmp_path / "config.env"
    dev = tmp_path / ".env"
    ring: dict[str, str] = {}
    config.write_text("", encoding="utf-8")
    dev.write_text("", encoding="utf-8")
    form = {"desktop": True}

    def set_secret(name, value):
        ring[name] = value
        return True

    monkeypatch.setattr(paths, "config_file", lambda: config)
    monkeypatch.setattr(paths, "ASSETS_ROOT", tmp_path)   # _dev_env_values 读它下面的 .env
    monkeypatch.setattr(paths, "is_desktop", lambda: form["desktop"])
    monkeypatch.setattr(secrets, "get_secret", lambda n, f=None: ring.get(n) or f)
    monkeypatch.setattr(secrets, "has_secret", lambda n: n in ring)
    monkeypatch.setattr(secrets, "set_secret", set_secret)
    return SimpleNamespace(config=config, dev=dev, ring=ring,
                           desktop=lambda on: form.__setitem__("desktop", on))


def _resolve() -> tuple[str, str]:
    return bootstrap.find_secret(NAME, bootstrap.config_value(NAME))


def test_桌面配置里的空占位不挡仓库值(box):
    """这就是那个 bug：config.env 是首启快照，里面 QIANFAN_API_KEY= 是空的。"""
    box.config.write_text(f"{NAME}=\nLLM_MODEL=gpt-4o-mini\n", encoding="utf-8")
    box.dev.write_text(f"{NAME}=repo-value\n", encoding="utf-8")
    assert _resolve() == ("repo-value", "仓库 .env")


def test_凭据管理器优先于两处文件(box):
    box.config.write_text(f"{NAME}=config-value\n", encoding="utf-8")
    box.dev.write_text(f"{NAME}=repo-value\n", encoding="utf-8")
    box.ring[NAME] = "ring-value"
    assert _resolve() == ("ring-value", "凭据管理器")


def test_dev形态不去翻凭据管理器(box):
    """dev 只认仓库 .env：凭据管理器是整机共用的，别让它悄悄盖掉本项目配的值。"""
    box.desktop(False)
    box.config.write_text(f"{NAME}=config-value\n", encoding="utf-8")
    box.ring[NAME] = "ring-value"
    assert _resolve() == ("config-value", "配置文件")


def test_三处都没有就是没配(box):
    box.config.write_text(f"{NAME}=\n", encoding="utf-8")
    assert _resolve() == ("", "")


def test_存密钥_桌面进凭据管理器_dev进配置文件(box):
    assert bootstrap.store_secret(NAME, "k1") == "凭据管理器"
    assert box.ring[NAME] == "k1"

    box.desktop(False)
    assert bootstrap.store_secret(NAME, "k2") == "配置文件"
    assert bootstrap.config_value(NAME) == "k2"
    assert _resolve() == ("k2", "配置文件")


def test_桌面配置不继承任何密钥():
    """首启生成 config.env 时密钥一律不抄过去：那是明文，还会被快照冻住。"""
    dev = {name: "值" for name in secrets.SECRET_NAMES}
    dev["LLM_MODEL"] = "gpt-4o-mini"
    out = bootstrap._desktop_values(dev)
    assert [k for k in out if k in secrets.SECRET_NAMES] == []
    assert out["LLM_MODEL"] == "gpt-4o-mini"
