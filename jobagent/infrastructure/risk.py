"""
BOSS直聘 风控：识别 + 话术。
"""
import urllib.parse

# BOSS 风控码：36 是"去做个验证"，32 是账号级暂时禁用
RISK_CODE_VERIFY = 36
RISK_CODE_BANNED = 32

HEALTHY_PATHS = ("/web/geek/jobs", "/web/user", "/job_detail", "/web/geek/chat")


VERIFY_TIMEOUT_HINT = ("安全验证没在页面上等到通过：那个页面先留着，"
                       "你在里面完成验证后回复我一声，我再搜一次")
STILL_BLOCKED_HINT = ("验证过了但还是没放行，这个号/IP 被临时限流了："
                      "页面已经收干净，等十几分钟再让我重搜一次")
BANNED_HINT = ("BOSS直聘暂时限制了这个账号，人工过验证点不掉：等几个小时再让我重搜，"
               "期间反复重试只会一直续期")


def is_blocked_url(url: str) -> bool:
    """BOSS 风控落地页判定（实测形态）：
    - 跳 /web/passport/zp/verify.html?...&code=36「您的账户存在异常行为」
    - 跳 /web/passport/zp/403.html?...&code=32「已暂时被禁止使用」，比 36 高一档
    - 被弹回首页并带 _security_check=1_<ts>，目标页根本没加载

    注意 _security_check 通过校验后也会挂在正常 URL 上（岗位照常返回），那种不算挡住。
    """
    low = (url or "").lower()
    if "verify.html" in low or "403.html" in low or f"code={RISK_CODE_VERIFY}" in low:
        return True
    return "_security_check" in low and not any(p in low for p in HEALTHY_PATHS)


def risk_code_of(url: str) -> int | None:
    """风控落地页 URL 上带的平台码；不在风控页返回 None。

    在风控页但 URL 没带 code（首页 _security_check 弹回）也返回 None，
    按"还能人工过验证"那一档处理——32 才会明着带在 URL 上。
    """
    if not is_blocked_url(url):
        return None
    query = urllib.parse.urlparse(url).query
    raw = (urllib.parse.parse_qs(query).get("code") or [""])[0]
    return int(raw) if raw.isdigit() else None


def api_risk(body) -> tuple[int, str] | None:
    """接口在 JSON 里自己报的风控码：code 非 0 时把 (code, message) 原样带出来。

    被封时 BOSS 既不跳页也不报错，只回 {"code":32,"message":"...","zpData":[]}，
    光看 zpData 空不空就会把"号被限制"误判成"这个关键词没岗位"。
    """
    if not isinstance(body, dict):
        return None
    code = body.get("code")
    if not isinstance(code, int) or code == 0:
        return None
    return code, str(body.get("message") or "")


def looks_like_captcha(html: str) -> bool:
    """HTML 里带验证码/风控页特征词。

    启发式检查，只用来在"等不到目标元素"时区分是验证码挡了还是页面结构变了。
    """
    low = html.lower()
    return any(k in low for k in ("captcha", "验证码", "verify", "安全验证"))


class RiskBlocked(Exception):
    """被 BOSS 风控挡住。code 是平台报的码，None 表示只认出"被弹走了"没拿到码。"""

    def __init__(self, code: int | None = None, message: str = "") -> None:
        super().__init__(f"risk control blocked: code={code} {message}".strip())
        self.code = code
        self.message = message

    @property
    def human_passable(self) -> bool:
        """32 是账号级禁用：展开页面也没东西可点，别拉用户做白工。"""
        return self.code != RISK_CODE_BANNED

    @property
    def detail(self) -> str:
        """平台原话，拼在话术后面——用户能拿去问 BOSS 客服。"""
        return f"（平台原话：{self.message}）" if self.message else ""
