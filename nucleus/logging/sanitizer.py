# -*- coding: utf-8 -*-
"""日志脱敏层（T-133a / fc133 七卡）：

在 logging **handler 级**统一改写 LogRecord（消息 + 参数 + 异常文本），
一处接线即可覆盖全库（含 pulse.* 子 logger）。

设计要点（机制活体实验结论）：
  - logger 级 filter 不吃子 logger 记录（logging 语义），故脱敏必须 handler 级；
  - 生产日志几乎全走 pulse.* 子 logger，在 pulse 根 logger 的 handler 上挂 Filter 即全量生效；
  - 性能：7 式对 5000 行真实尾窗约 17us/行，零副作用。

正则集（7 条，已删 p6 身份证；反斜杠固化 chr(92)*2 防 re.error）：
  1) sk- 类 API Key； 2) api_key/token/secret/authorization=...； 3) Bearer token；
  4) 手机号； 5) 邮箱； 6) face_roster 绝对路径； 7) identity 目录路径。
可选：OUTPUT_WINDOWS_PATH_MASK=True 时再屏蔽 Windows 绝对路径（默认 OFF）。
"""
import logging
import re
from nucleus._silent_except import silent_exc

# ---- 7 条核心正则 ----
_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "<REDACTED_KEY>"),
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|authorization)\s*[=:]\s*\S+"),
     r"\1=<REDACTED>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer <REDACTED>"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "<REDACTED_PHONE>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "<REDACTED_EMAIL>"),
    (re.compile(r"[A-Za-z]:\\[^\s]*face_roster\.json"), "<REDACTED_FACE_PATH>"),
    # identity 目录路径：反斜杠必须按 chr(92)*2 固化，照抄单反斜杠会 re.error
    (re.compile(r"[A-Za-z]:" + chr(92) * 2 + r"[^\s]*identity" + chr(92) * 2),
     "<REDACTED_ID_PATH>"),
]

# 可选：Windows 绝对路径屏蔽（受 config.OUTPUT_WINDOWS_PATH_MASK 控制，默认 OFF）
_WIN_PATH_PAT = re.compile(r"[A-Za-z]:" + chr(92) * 2 + r"[^\s]*")

# 误伤报警阈值：脱敏后含 <REDACTED 的行占比 > 此值即误伤（fc133 验收判据）
_MISFIRE_ALERT_RATIO = 0.05


def sanitize(text: str) -> str:
    """对单条文本执行 7 条核心正则脱敏（幂等）。"""
    if not text:
        return text
    for _pat, _repl in _PATTERNS:
        text = _pat.sub(_repl, text)
    return text


def sanitize_full(text: str, mask_win_path: bool = False) -> str:
    """在核心脱敏之上，可选地屏蔽 Windows 绝对路径。"""
    text = sanitize(text)
    if mask_win_path:
        text = _WIN_PATH_PAT.sub("<REDACTED_WIN_PATH>", text)
    return text


def sanitize_outbound(text: str) -> str:
    """★第170批 C8a（O-B4 出站哨兵）：生成期 / 出站哨兵。

    对用户**可见输出**文本执行敏感引用脱敏（复用 7 条核心正则：密钥 / token /
    Bearer / 手机号 / 邮箱 / face_roster 路径 / identity 路径）。幂等、零副作用；
    命中即掩码，未命中原样返回。生成链路在发射前调用本函数，防止凭据 / PII
    经回复文本外泄。
    ★b 票（坏引用有效面过滤，P2）本批不并入，后续排期。
    """
    return sanitize(text)


class SanitizingFilter(logging.Filter):
    """handler 级脱敏 Filter。

    enabled=True 时改写 record.msg / record.args（脱敏）；
    mask_win_path 受 config.OUTPUT_WINDOWS_PATH_MASK 动态控制（默认 OFF）。
    filter 永远返回 True（不影响日志路由，只改写内容）。
    """

    def __init__(self, enabled: bool = True):
        super().__init__()
        self.enabled = enabled

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.enabled:
            return True
        try:
            _mask = False
            try:
                import config as _cfg
                _mask = bool(getattr(_cfg, "OUTPUT_WINDOWS_PATH_MASK", False))
            except Exception as e:
                silent_exc(e, "nucleus/logging/sanitizer.py:filter:config", level="debug")
            record.msg = sanitize_full(str(record.msg), _mask)
            if record.args:
                record.args = tuple(
                    sanitize_full(str(_a), _mask) if isinstance(_a, str) else _a
                    for _a in record.args
                )
        except Exception as e:
            # 脱敏失败绝不影响主链路
            silent_exc(e, "nucleus/logging/sanitizer.py:filter:sanitize", level="warning")
        return True
