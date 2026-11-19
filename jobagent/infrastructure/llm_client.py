import logging
import time
from langchain.chat_models import init_chat_model
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate


from jobagent.api.errors import AppError, ErrorCode
from jobagent.config.settings import settings


LLM_TIMEOUT_S = 90

logger = logging.getLogger("jobagent.llm")


_llm = None


def get_llm():
    global _llm
    if _llm is None:
        if not settings.llm_api_key:
            raise AppError(ErrorCode.LLM_KEY_MISSING, ErrorCode.LLM_KEY_MISSING.label,
                           http_status=409)
        _llm = init_chat_model(
            model=settings.llm_model,
            model_provider="openai",
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            temperature=0,
            timeout=LLM_TIMEOUT_S,
            extra_body={"thinking": {"type": "disabled"}},
            max_retries=0,
        )
    return _llm


def reset_llm() -> None:
    global _llm
    _llm = None


_json_parser = JsonOutputParser()


# 模板渲染函数
async def ainvoke_json(template_text: str, inputs: dict):
    template = PromptTemplate.from_template(
        template_text, template_format="jinja2"
    )

    chain = template | get_llm() | _json_parser
    started = time.perf_counter()

    for attempt in (1, 2):
        try:
            result = await chain.ainvoke(inputs)
            logger.debug("LLM JSON 解析成功，用时 %.1fs（第 %d 次尝试）",
                         time.perf_counter() - started, attempt)
            return result
        except OutputParserException as e:
            logger.warning("LLM 输出不是合法 JSON（第 %d 次，%.1fs）：%r",
                           attempt, time.perf_counter() - started, e)
        except Exception as e:                    # noqa: BLE001
            logger.warning("LLM 调用失败，用时 %.1fs：%r",
                           time.perf_counter() - started, e)
            raise
    raise AppError(ErrorCode.RESUME_PARSE_TIMEOUT,
                   "LLM 输出解析失败", http_status=504)
