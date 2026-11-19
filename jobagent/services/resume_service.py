"""简历解析：校验 → 文本抽取 → LLM 结构化 → 缺项标记。

四段职责各自独立，只通过公开方法串联，互不耦合：
  validate     上传期三道闸（后缀/大小/魔数），/api/upload 调用
  extract_text 文件字节 → 纯文本，按后缀分派（PDF / DOCX）
  parse        纯文本 → 结构化摘要（LLM + 强类型校验 + 修复重试 + 缺项标记）
  parse_file   file_ref → 摘要，编排「读文件 → 抽文本 → LLM 结构化」
"""
import asyncio
import io
import logging
import re
import time
from pathlib import Path
from typing import Any

import pdfplumber
from docx import Document as DocxReader
from pydantic import BaseModel, ValidationError, field_validator
from pypdf import PdfReader

from jobagent.api.errors import AppError, ErrorCode
from jobagent.infrastructure.file_store import FileStore

logger = logging.getLogger("jobagent.resume")

# ---- 上传校验常量 ----
ALLOWED_SUFFIXES = frozenset({".pdf", ".doc", ".docx"})
MAX_SIZE = 10 * 1024 * 1024
# 防「改后缀伪装」：按文件头魔数判真实格式
MAGIC = {b"%PDF": ".pdf", b"\xd0\xcf\x11\xe0": ".doc", b"PK\x03\x04": ".docx"}

# ---- 简历解析预算 ----
PARSE_TIMEOUT_S = 180.0
MAX_TEXT_CHARS = 8000    # 文本截取8000
REPAIR_SUFFIX = """上次输出不符合以下字段要求，请修正：{errors}"""


class ResumeSummary(BaseModel):
    """LLM 结构化输出的强类型契约"""
    name: str | None = None
    contact: str | None = None
    experiences: list[dict] = []
    projects: list[dict] = []
    education: list[dict] = []
    skills: list[str] = []
    expected_position: str | None = None
    expected_salary: str | None = None
    work_years: int | None = None
    degree: str | None = None

    @field_validator("work_years", mode="before")
    @classmethod
    def _coerce_years(cls, v):
        if isinstance(v, str):
            m = re.search(r"\d+", v)
            return int(m.group()) if m else None
        return v


class ResumeService:

    def __init__(self, file_store: FileStore, extract_template: str) -> None:
        self.file_store = file_store
        self.extract_template = extract_template

    # ----------  上传校验（/api/upload 调用）----------
    def validate(self, file_name: str, size: int, head_bytes: bytes) -> None:
        """后缀白名单 → 大小上限 → 文件头魔数，三道闸全过才放行。"""
        suffix = Path(file_name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise AppError(ErrorCode.UPLOAD_TYPE_UNSUPPORTED)
        if size > MAX_SIZE:
            raise AppError(ErrorCode.UPLOAD_TOO_LARGE)
        if not any(head_bytes.startswith(magic) for magic in MAGIC):
            raise AppError(ErrorCode.RESUME_PARSE_FAILED, "文件头魔数校验失败")

    # ---------- 文本抽取 ----------
    def extract_text(self, raw: bytes, suffix: str) -> str:
        """按后缀分派到 PDF / DOCX 抽取；抽取库异常统一收敛为 2001。"""
        try:
            if suffix == ".pdf":
                return self._extract_pdf(raw)
            if suffix == ".docx":
                return self._extract_docx(raw)
            raise AppError(ErrorCode.UPLOAD_TYPE_UNSUPPORTED,
                           "旧版 DOC 请转存为 DOCX 后重新上传")
        except AppError:
            raise
        except Exception as e:      # noqa: BLE001 抽取库异常统一收敛为 2001
            logger.warning("简历文本抽取失败：%r", e)
            raise AppError(ErrorCode.RESUME_PARSE_FAILED) from e

    @staticmethod
    def _extract_pdf(raw: bytes) -> str:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise AppError(ErrorCode.RESUME_PARSE_FAILED, "PDF 已加密")
        text = "\n".join(p.extract_text() or "" for p in reader.pages).strip()
        if not text:  # 扫描件无文本层 → pdfplumber 回退
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages).strip()
        if not text:
            raise AppError(ErrorCode.RESUME_PARSE_FAILED, "未检出文本（可能为扫描件）")
        return text

    @staticmethod
    def _extract_docx(raw: bytes) -> str:
        doc = DocxReader(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:  # 表格里的经历别丢
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(parts).strip()

    # ----------  LLM 结构化 ----------
    async def parse(self, text: str) -> dict[str, Any]:
        """纯文本 → 摘要 dict：LLM 抽取 + 强类型校验（修复重试一次）+ 缺项标记。"""
        started = time.perf_counter()
        try:
            data = await asyncio.wait_for(self._extract_structured(text),
                                          timeout=PARSE_TIMEOUT_S)
        except asyncio.TimeoutError as e:
            logger.warning("简历解析超时，已用 %.1fs（%d 字符）",
                           time.perf_counter() - started, len(text))
            raise AppError(ErrorCode.RESUME_PARSE_TIMEOUT,
                           f"简历解析超时（{PARSE_TIMEOUT_S:.0f}s）", http_status=504) from e
        return self._mark_completeness(data)

    async def _extract_structured(self, text: str) -> dict[str, Any]:
        """一次 LLM 调用 + 至多一次 schema 修复重试，返回摘要 dict。

        - JSON 解析失败由 ainvoke_json 内部重试（传输层）；
        - 这里只重试「JSON 合法但字段不合 schema」：把校验错误喂回 LLM。
        """
        budget = text[:MAX_TEXT_CHARS]
        data = await self._llm_json(self.extract_template, budget)
        try:
            return ResumeSummary(**data).model_dump()
        except ValidationError as e:
            logger.info("简历摘要 schema 校验未过，发起修复重试（第二轮 LLM）")
            try:
                return ResumeSummary(**await self._llm_json(
                    self.extract_template + REPAIR_SUFFIX.format(errors=self._format_validation_errors(e)),
                    budget)).model_dump()
            except ValidationError:
                raise AppError(ErrorCode.RESUME_PARSE_FAILED,
                               "简历摘要字段校验未通过", http_status=502) from e

    @staticmethod
    def _format_validation_errors(e: ValidationError) -> str:
        """把校验错误格式化成字段级清单（喂回 LLM 修复用）。"""
        return "\n".join(
            f"- {'.'.join(str(x) for x in err['loc'])}：{err['msg']}"
            for err in e.errors())

    async def _llm_json(self, template_text: str, resume_text: str) -> dict[str, Any]:
        """单次 LLM 结构化调用：记单轮耗时供诊断；总预算由 parse 统一套。"""
        from jobagent.infrastructure.llm_client import ainvoke_json
        started = time.perf_counter()
        data = await ainvoke_json(template_text, {"resume_text": resume_text})
        logger.info("简历结构化单轮 LLM 用时 %.1fs（%d 字符）",
                    time.perf_counter() - started, len(resume_text))
        return data

    # ----------  缺项标记----------
    @staticmethod
    def _mark_completeness(summary: dict) -> dict:
        missing = []
        if not summary.get("name") or not summary.get("contact"):
            missing.append("basic_info")
        if not summary.get("expected_position"):
            missing.append("expected_position")
        summary["completeness"] = {"missing": missing}
        return summary

    # ---------- ⑤ file_ref → 摘要（编排前三段）----------
    async def parse_file(self, file_ref: str) -> dict[str, Any]:

        # 读文件
        raw = self.file_store.load(str(file_ref))
        # 抽取文本
        text = self.extract_text(raw, _suffix_of(file_ref))
        # llm解析
        return await self.parse(text)


def _suffix_of(file_ref: str) -> str:
    """file_ref 形如 "<uuid>.pdf" → ".pdf"（取原始后缀，供文本抽取分派）。"""
    return "." + file_ref.rsplit(".", 1)[-1]
