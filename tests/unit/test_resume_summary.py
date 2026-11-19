"""简历结构化模型：work_years 的宽松解析（别为「4年」这种写法触发整轮重试）。"""
from jobagent.services.resume_service import ResumeSummary


def test_work_years_accepts_chinese_suffix():
    assert ResumeSummary(work_years="4年").work_years == 4
    assert ResumeSummary(work_years="3-5年").work_years == 3


def test_work_years_unknown_is_none():
    assert ResumeSummary(work_years="经验不限").work_years is None
    assert ResumeSummary().work_years is None
