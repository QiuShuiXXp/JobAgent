from enum import Enum

"""
统一错误码、AppError 与全局异常处理器
"""


class ErrorCode(Enum):
    OK = (0, "成功")
    INVALID_ARGUMENT = (1001, "参数不合法")
    NOT_FOUND = (1002, "资源不存在")
    UPLOAD_TOO_LARGE = (1003, "文件超过 10MB")
    UPLOAD_TYPE_UNSUPPORTED = (1004, "简历格式不支持")
    INTERNAL_ERROR = (1005, "内部错误")
    STATE_CONFLICT = (1009, "对话状态冲突，请重试")
    RESUME_PARSE_FAILED = (2001, "文件损坏或加密，解析失败")
    RESUME_PARSE_TIMEOUT = (2002, "简历解析超时")
    RESUME_FIELD_MISSING = (2003, "简历关键字段缺失，需补充")
    RESUME_NOT_CONFIRMED = (2004, "简历未确认，禁止岗位检索")
    INTENT_NOT_CONFIRMED = (3001, "求职意向未确认，禁止进入登录")
    INTENT_FIELD_MISSING = (3002, "意向必填项缺失，需澄清")
    LOGIN_INVALIDATED = (4002, "登录凭证失效")
    LOGIN_PAGE_TIMEOUT = (4003, "登录页打开超时")
    JOB_SEARCH_FAILED = (5001, "岗位检索失败")
    PLATFORM_CAPTCHA = (5002, "平台验证码/反爬拦截")
    NO_MATCHED_JOB = (5003, "无匹配岗位")
    BROWSER_UNAVAILABLE = (5004, "这台电脑没有可用的浏览器")
    PLATFORM_ACCOUNT_BANNED = (5005, "账号被平台暂时限制")
    JOB_ALREADY_GREETED = (6003, "该岗位已打过招呼")
    LLM_KEY_MISSING = (7001, "还没配置模型的 API Key")

    @property
    def code(self)->int:
        return self.value[0]
    @property
    def label(self)->str:
        return self.value[1]

# 业务异常基类
class AppError(Exception):

    def __init__(self, error: ErrorCode, message: str = "" ,http_status: int =400):
        self.error = error
        self.message = message or error.label
        self.http_status = http_status
        super().__init__(self.message)


