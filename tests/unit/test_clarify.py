"""澄清器话术：每个 reason 都得渲染出能照做的一句话。

这条测试是来补一个藏了两年的洞：REASON_TEMPLATES 里 6 个模板名有 5 个文件根本不存在，
`render()` 抛 TemplateNotFound 被 respond() 的裸 except 吞掉，于是绝大多数澄清都退化成
一句「我没太理解你的意思」；启动自检也查不出来，因为它只遍历磁盘上已有的模板。
"""
import pytest

from jobagent.engine.clarify import ClarifyResponser
from jobagent.plan.turn_plan import ClarifyReason
from jobagent.prompts.loader import PromptLoader


@pytest.fixture
def responder() -> ClarifyResponser:
    return ClarifyResponser(PromptLoader())


async def test_every_reason_renders_a_real_sentence(responder):
    for reason in ClarifyReason:
        text = (await responder.respond(reason)).text
        assert text.strip(), f"{reason.name} 渲染出空话术（模板是 TODO 占位？）"
        assert len(text) > 10, f"{reason.name} 话术短到没法照做：{text!r}"


async def test_unrecognized_points_at_something_the_user_can_do(responder):
    """没听懂时不能只道歉：要把现在能做的三件事摆出来。"""
    text = (await responder.respond(ClarifyReason.MISSING_TRACK)).text
    for hint in ("上传简历", "登录", "我想找工作"):
        assert hint in text, text


async def test_flow_locked_names_the_blocked_flow(responder):
    named = (await responder.respond(ClarifyReason.FLOW_LOCKED, "岗位筛选")).text
    assert "岗位筛选" in named, named
    # 拿不到流程名（flow_id 为空或已改名）时也要成句，不能留个空括号或裸占位符
    unnamed = (await responder.respond(ClarifyReason.FLOW_LOCKED)).text
    assert "这件事" in unnamed and "{{" not in unnamed and "（）" not in unnamed, unnamed
