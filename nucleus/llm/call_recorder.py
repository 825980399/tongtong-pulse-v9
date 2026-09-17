"""LLM 调用全程留存管道 · 数据结构与存储层（主线第40批 T1 / P0-254）。

背景
----
第三方评估实测：框架累计 **2103 次**大模型调用，而落盘的"输入-输出对"仅约 50 条
（留存率 **2.4%**）→ 内在模型（意图分类器 / 响应预测器 / 补丁质量评估器 …）全部
"没有米下锅"。本模块提供一条**低侵入、可灰度、可隔离**的留存管道。

职责边界
--------
* 本模块**只负责**：数据规范化 → 脱敏 → 截断 → JSONL 追加落盘 → 滚窗清理。
* 本模块**不负责**：判断 origin（由调用方显式传入，见 ``ORIGINS``）、不发起任何
  LLM 调用、不做任何业务决策。
* 任何异常都**不得**向调用方抛出（记录器故障绝不能影响主推理流程）。

存储
----
``<LLM_TRACE_DIR>/calls_YYYYMMDD.jsonl``，每行一条 JSON（UTF-8, ensure_ascii=False）。
追加写入由进程内 ``threading.Lock`` 串行化（框架为多线程环境）。
回填反馈写入 ``<LLM_TRACE_DIR>/feedback_YYYYMMDD.jsonl``（追加不可改行 → 旁路文件）。

灰度
----
* ``ENABLE_LLM_CALL_RECORDER``（默认 True）：总开关，关闭时 ``record`` 直接返回 None
  且**零 IO**（连目录都不创建）。
* ``LLM_TRACE_DIR`` / ``LLM_TRACE_RETENTION_DAYS`` / ``LLM_TRACE_MAX_TEXT_LEN`` /
  ``LLM_TRACE_SANITIZE``：目录、滚窗天数、单字段截断、脱敏。
* 构造参数 ``base_dir`` 可注入 → 测试写临时目录，**绝不碰生产 data/**。
"""
from __future__ import annotations

import datetime as _dt
import functools
import json
import os
import re
import threading
import time
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# 常量：origin（四类调用来源，由调用方显式传入，本模块不做猜测）
# ---------------------------------------------------------------------------
ORIGIN_USER_QUERY = "user_query"          # 对话入口（用户提问）
ORIGIN_SYSTEM_INTERNAL = "system_internal"  # 系统内部（自检 / 监控 / 后台学习）
ORIGIN_EVOLUTION_TASK = "evolution_task"  # 自主进化循环
ORIGIN_DIGESTION = "digestion"            # 知识消化
ORIGINS = (ORIGIN_USER_QUERY, ORIGIN_SYSTEM_INTERNAL,
           ORIGIN_EVOLUTION_TASK, ORIGIN_DIGESTION)

#: caller 名（PulseLung 既有口径）→ origin 的**建议**映射（调用方仍可显式覆盖）。
ORIGIN_ALIASES = {
    "user_dialog": ORIGIN_USER_QUERY,
    "background_learning": ORIGIN_SYSTEM_INTERNAL,
    "evolution": ORIGIN_EVOLUTION_TASK,
    "digestion": ORIGIN_DIGESTION,
}

# ---------------------------------------------------------------------------
# 常量：status
# ---------------------------------------------------------------------------
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUSES = (STATUS_SUCCESS, STATUS_FAILED, STATUS_TIMEOUT)

# ---------------------------------------------------------------------------
# 常量：字段默认值（★主线第44批 T1 / P1-285 + P1-286）
# ---------------------------------------------------------------------------
#: ``prompt_version`` 缺省标记 —— 调用方未显式传版本号时写入该值，
#: 便于事后排查「哪次调用没带版本」。此前该字段恒为空串 → 无法按版本归因。
DEFAULT_PROMPT_VERSION = "unknown"

#: 失败记录缺省说明 —— 渠道未给出异常细节时写入。
#: 此前 ``status != success`` 的记录 ``error`` 恒空（111/111）→ 失败不可诊断。
DEFAULT_FAILED_ERROR = "(no error detail)"

#: ``error`` 字段默认截断长度（字符）—— 异常瀑布文本可能极长，需有上界。
DEFAULT_ERROR_MAX_LEN = 500

#: 脱敏模式（凭证 / 密钥）。★保守优先：只替换"明显像密钥"的片段，
#: 宁可漏掉个别变体，也不误伤正常问答文本。
_SANITIZE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{12,}=*"),
    re.compile(r"(?i)\b(?:api[_-]?key|apikey|access[_-]?key|secret|password)"
               r"\b\s*[:=]\s*[\"']?[A-Za-z0-9\-._/+=]{8,}"),
)
_SANITIZE_REPLACEMENT = "***"


def _cfg(name: str, default: Any) -> Any:
    """读取 config 项（config 不可用时按默认值，绝不抛出）。"""
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def _in_test_env() -> bool:
    """是否处于 pytest 环境（用于**拒写生产目录**，见 `record` 的守卫）。"""
    try:
        import sys as _sys
        return ("pytest" in _sys.modules) or bool(os.environ.get("PYTEST_CURRENT_TEST"))
    except Exception:
        return False


def _is_production_trace_dir(path: str) -> bool:
    """路径是否位于项目 `data/` 生产数据区（默认追踪目录即在此）。"""
    try:
        _p = os.path.abspath(path).replace("\\", "/").lower()
        _root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))).replace("\\", "/").lower()
        return _p.startswith(_root + "/data/")
    except Exception:
        return True


def sanitize_text(text: Any) -> str:
    """脱敏：剔除凭证 / 密钥模式。非字符串入参先 ``str()``。"""
    if text is None:
        return ""
    _s = text if isinstance(text, str) else str(text)
    for _p in _SANITIZE_PATTERNS:
        _s = _p.sub(_SANITIZE_REPLACEMENT, _s)
    return _s


def _truncate(text: str, limit: int) -> str:
    """截断（``limit <= 0`` 表示不限）。保留尾部标记便于事后察觉。"""
    if limit and limit > 0 and len(text) > limit:
        return text[:limit] + "...[truncated %d]" % (len(text) - limit)
    return text


def format_error(exc: Any, limit: int | None = None) -> str:
    """把异常 / 任意错误对象规范化为 ``"类型: 消息"`` 文本并截断。

    ★第44批 T1（P1-286）：``failed`` 记录的 ``error`` 字段此前**全空**
    （实测 111/111），失败不可诊断、反复无效重试。统一从本入口生成，
    保证「异常类型 + 异常消息」两段都在，且长度有上界（默认 500 字符）。

    异常对象以外的入参（字符串/None）也安全处理；输出恒为已脱敏文本。
    """
    if exc is None:
        return ""
    if isinstance(exc, BaseException):
        _txt = "%s: %s" % (type(exc).__name__, exc)
    else:
        _txt = str(exc)
    _lim = DEFAULT_ERROR_MAX_LEN if limit is None else int(limit)
    if _lim > 0 and len(_txt) > _lim:
        _txt = _txt[:_lim]
    return sanitize_text(_txt)


class LLMCallRecorder:
    """LLM 调用对留存器（JSONL 追加写 + 滚窗清理）。

    线程安全：实例内持有一把 ``threading.Lock``；框架为多线程环境，
    追加写必须串行化，避免 JSONL 行交错。
    """

    def __init__(self, base_dir: str | None = None) -> None:
        self._lock = threading.Lock()
        #: 注入目录优先（测试隔离）；否则读 config，再退默认相对路径
        self._base_dir_override = base_dir
        self._written = 0
        self._failed = 0
        self._last_rotate_day = ""

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------
    def enabled(self) -> bool:
        return bool(_cfg("ENABLE_LLM_CALL_RECORDER", True))

    def base_dir(self) -> str:
        """落盘根目录（绝对路径）。注入 > config(相对项目根) > 默认。"""
        if self._base_dir_override:
            return self._base_dir_override
        _rel = str(_cfg("LLM_TRACE_DIR", "data/llm_traces") or "data/llm_traces")
        if os.path.isabs(_rel):
            return _rel
        _root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        return os.path.join(_root, _rel.replace("/", os.sep))

    def max_text_len(self) -> int:
        try:
            return int(_cfg("LLM_TRACE_MAX_TEXT_LEN", 8000) or 0)
        except Exception:
            return 8000

    def retention_days(self) -> int:
        try:
            return int(_cfg("LLM_TRACE_RETENTION_DAYS", 90) or 0)
        except Exception:
            return 90

    def sanitize_enabled(self) -> bool:
        return bool(_cfg("LLM_TRACE_SANITIZE", True))

    def prompt_version_enabled(self) -> bool:
        """「未传版本号 → 标记缺省值」总开关（``ENABLE_LLM_TRACE_PROMPT_VERSION``）。"""
        return bool(_cfg("ENABLE_LLM_TRACE_PROMPT_VERSION", True))

    def default_prompt_version(self) -> str:
        """缺省版本号（``LLM_TRACE_DEFAULT_PROMPT_VERSION``，默认 ``"unknown"``）。"""
        return str(_cfg("LLM_TRACE_DEFAULT_PROMPT_VERSION", DEFAULT_PROMPT_VERSION)
                   or DEFAULT_PROMPT_VERSION)

    def error_capture_enabled(self) -> bool:
        """「失败记录补 error 明细」总开关（``ENABLE_LLM_TRACE_ERROR_CAPTURE``）。"""
        return bool(_cfg("ENABLE_LLM_TRACE_ERROR_CAPTURE", True))

    def error_max_len(self) -> int:
        """``error`` 字段截断长度（``LLM_TRACE_ERROR_MAX_LEN``，默认 500）。"""
        try:
            return int(_cfg("LLM_TRACE_ERROR_MAX_LEN", DEFAULT_ERROR_MAX_LEN) or 0)
        except Exception:
            return DEFAULT_ERROR_MAX_LEN

    def _resolve_prompt_version(self, prompt_version: Any) -> str:
        """解析最终写入的 ``prompt_version``（★第44批 T1 / P1-285）。

        * 调用方显式传入 → 原样保留（仅去空白）。
        * 未传 → 写缺省标记 ``"unknown"``，便于事后统计"未标注版本"的比例。
        * 关闭 ``ENABLE_LLM_TRACE_PROMPT_VERSION`` → 完全复现修复前行为（写空串，零回归）。
        """
        try:
            _v = str(prompt_version or "").strip()
            if _v:
                return _v
            if not self.prompt_version_enabled():
                return ""
            return self.default_prompt_version()
        except Exception:
            return ""

    def _resolve_error(self, error: Any, status: str) -> str:
        """解析最终写入的 ``error`` 字段（★第44批 T1 / P1-286）。

        * 调用方给了明细 → 脱敏 + 按 ``error_max_len`` 截断后保留。
        * 未给明细且状态非 success → 写缺省说明（此前恒为空串 → 失败不可诊断）。
        * 关闭 ``ENABLE_LLM_TRACE_ERROR_CAPTURE`` → 复现修复前行为
          （沿用 ``max_text_len`` 截断、失败不补说明，零回归）。
        """
        try:
            _raw = "" if error is None else (
                error if isinstance(error, str) else str(error))
            _cap = self.error_capture_enabled()
            if not _raw.strip() and str(status) != STATUS_SUCCESS and _cap:
                _raw = DEFAULT_FAILED_ERROR
            if self.sanitize_enabled():
                _raw = sanitize_text(_raw)
            _lim = self.error_max_len() if _cap else self.max_text_len()
            return _truncate(_raw, _lim or 0)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 落盘
    # ------------------------------------------------------------------
    def _day_str(self, ts: float) -> str:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y%m%d")

    def _path_for(self, ts: float, prefix: str = "calls") -> str:
        return os.path.join(self.base_dir(),
                            "%s_%s.jsonl" % (prefix, self._day_str(ts)))

    def _prepare_text(self, text: Any) -> str:
        _s = text if isinstance(text, str) else ("" if text is None else str(text))
        if self.sanitize_enabled():
            _s = sanitize_text(_s)
        return _truncate(_s, self.max_text_len())

    def _rotate(self, now: float) -> None:
        """滚窗清理：删除超过 ``retention_days`` 的 ``calls_*.jsonl``。

        每天最多执行一次（按日期串去重），避免热路径反复列目录。
        """
        _days = self.retention_days()
        if _days <= 0:
            return
        _today = self._day_str(now)
        if self._last_rotate_day == _today:
            return
        self._last_rotate_day = _today
        try:
            _d = self.base_dir()
            if not os.path.isdir(_d):
                return
            _cutoff = now - _days * 86400.0
            for _fn in os.listdir(_d):
                if not _fn.startswith("calls_") or not _fn.endswith(".jsonl"):
                    continue
                _fp = os.path.join(_d, _fn)
                try:
                    if os.path.getmtime(_fp) < _cutoff:
                        os.remove(_fp)
                except OSError:
                    continue
        except Exception:
            # 清理失败不影响主流程（下一次触发会重试）
            return

    def record(self, *, origin: str, prompt: Any, response: Any,
               prompt_version: str = "", channel: str = "", model: str = "",
               duration: float = 0.0, tokens: int = 0, status: str = STATUS_SUCCESS,
               error: str = "", ts: float | None = None) -> str | None:
        """留存一条调用对，返回 ``trace_id``（供后续 ``record_feedback``）。

        Returns:
            trace_id（形如 ``m40-<hex12>``）；**开关关闭 / 落盘失败**时返回 ``None``。
        """
        if not self.enabled():
            return None
        # ★主线第43批 T0（_m43_t0_guard）：**测试环境污染防御**。
        #   未显式注入 base_dir（= 生产默认）且在 pytest 下 → 拒写。
        #   实测：calls_20260913.jsonl 231 条中 146 条为测试桩；
        #   显式注入隔离目录（测试/工具）不受影响 → 零回归。
        if self._base_dir_override is None and _in_test_env() \
                and _is_production_trace_dir(self.base_dir()):
            return None
        _ts = float(ts if ts is not None else time.time())
        _tid = "m40-" + uuid.uuid4().hex[:12]
        _rec = {
            "trace_id": _tid,
            "ts": _ts,
            "origin": str(origin or ORIGIN_SYSTEM_INTERNAL),
            "prompt": self._prepare_text(prompt),
            "response": self._prepare_text(response),
            "prompt_version": self._resolve_prompt_version(prompt_version),
            "channel": str(channel or ""),
            "model": str(model or ""),
            "duration": round(float(duration or 0.0), 4),
            "tokens": int(tokens or 0),
            "status": str(status or STATUS_SUCCESS),
            "error": self._resolve_error(error, str(status or STATUS_SUCCESS)),
            "feedback": None,
        }
        try:
            _line = json.dumps(_rec, ensure_ascii=False, default=str)
        except Exception:
            self._failed += 1
            return None
        try:
            _d = self.base_dir()
            if not os.path.isdir(_d):
                os.makedirs(_d, exist_ok=True)
            with self._lock:
                with open(self._path_for(_ts), "a", encoding="utf-8") as _f:
                    _f.write(_line + "\n")
                self._written += 1
            self._rotate(_ts)
            return _tid
        except Exception:
            self._failed += 1
            return None

    def record_feedback(self, trace_id: str, feedback: Any,
                        ts: float | None = None) -> bool:
        """预留：登记下游反馈（采纳 / 拒绝 / 用户重问）。

        ★JSONL 追加不可改行 → 反馈写入**旁路文件**
        ``feedback_YYYYMMDD.jsonl``，由消费者按 ``trace_id`` 关联。
        本批（第40批）**只提供接口**，回填逻辑由关键消费者（cortex 路由 /
        补丁验证器）在后续批次接入。
        """
        if not self.enabled() or not trace_id:
            return False
        _ts = float(ts if ts is not None else time.time())
        _rec = {"trace_id": str(trace_id), "ts": _ts, "feedback": feedback}
        try:
            _line = json.dumps(_rec, ensure_ascii=False, default=str)
            _d = self.base_dir()
            if not os.path.isdir(_d):
                os.makedirs(_d, exist_ok=True)
            with self._lock:
                with open(self._path_for(_ts, prefix="feedback"),
                          "a", encoding="utf-8") as _f:
                    _f.write(_line + "\n")
            return True
        except Exception:
            self._failed += 1
            return False

    def stats(self) -> dict[str, Any]:
        """运行期统计（不触发任何 IO 扫描）。"""
        return {
            "enabled": self.enabled(),
            "base_dir": self.base_dir(),
            "written": self._written,
            "failed": self._failed,
            "retention_days": self.retention_days(),
            "max_text_len": self.max_text_len(),
            "sanitize": self.sanitize_enabled(),
            "prompt_version_enabled": self.prompt_version_enabled(),
            "default_prompt_version": self.default_prompt_version(),
            "error_capture_enabled": self.error_capture_enabled(),
            "error_max_len": self.error_max_len(),
        }


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------
_recorder: LLMCallRecorder | None = None
_singleton_lock = threading.Lock()


def get_call_recorder() -> LLMCallRecorder | None:
    """进程内单例（首次调用时创建）。**永不抛出。**"""
    global _recorder
    try:
        with _singleton_lock:
            if _recorder is None:
                _recorder = LLMCallRecorder()
            return _recorder
    except Exception:
        return None


def reset_call_recorder() -> None:
    """重置单例（测试用）。"""
    global _recorder
    with _singleton_lock:
        _recorder = None


def record_call(**kwargs: Any) -> str | None:
    """便捷入口：``get_call_recorder().record(**kwargs)``（单例不可用时返回 None）。"""
    _r = get_call_recorder()
    if _r is None:
        return None
    return _r.record(**kwargs)


def record_evolution_call(*, prompt: Any, response: Any,
                          status: str = STATUS_SUCCESS, error: Any = "",
                          prompt_version: str = "", channel: str = "",
                          model: str = "", duration: float = 0.0,
                          tokens: int = 0, ts: float | None = None) -> str | None:
    """进化循环专用留存入口（★第44批 T1 / P1-285 + P1-286）。

    背景：实测 ``data/llm_traces`` 的 ``origin`` 分布**只有** ``user_query`` /
    ``system_internal``（153 / 93），``evolution_task`` **恒为 0**；而按依赖度统计，
    **进化循环占 LLM 调用的 98.8%**。根因：``LLMEvolutionEngine`` /
    ``SelfReflectionEngine`` / ``SafeEvolutionExecutor`` 三个引擎**各自直连 HTTP**，
    从未接入留存管道 → 内在模型最关键的数据源完全空白。

    本入口把它们补进管道，并统一带 ``prompt_version`` 与失败 ``error``。

    灰度：``ENABLE_EVOLUTION_CALL_TRACE``（默认 True）；关闭 → 直接返回 ``None``（零 IO）。
    本函数**永不抛出**；``record()`` 自带的「测试环境拒绝写生产目录」守卫同样生效。
    """
    try:
        if not bool(_cfg("ENABLE_EVOLUTION_CALL_TRACE", True)):
            return None
        _pv = prompt_version or str(_cfg("EVOLUTION_PROMPT_VERSION", "evolution.v1") or "")
        return record_call(origin=ORIGIN_EVOLUTION_TASK, prompt=prompt,
                           response=response if response is not None else "",
                           prompt_version=_pv, channel=channel, model=model,
                           duration=duration, tokens=tokens, status=status,
                           error=error, ts=ts)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 装饰器：进化引擎埋点（★第44批 T1 / P1-285 + P1-286）
# ---------------------------------------------------------------------------
def trace_evolution_call(prompt_pos: int = 2, version: str = ""):
    """装饰器：把一次进化引擎 LLM 调用留存进管道。

    ``prompt_pos`` = ``prompt`` 在**位置参数**中的下标（含 ``self``）。
    例：``_call_llm(self, system, prompt, ...)`` → ``2``；
    ``_call_llm_with_followup(self, api_url, api_key, prompt, ...)`` → ``3``。

    行为：
    * 正常返回且非空 → ``status="success"``，``response`` = 返回值。
    * 返回空 / None → ``status="failed"``；引擎若在 ``self._m44_last_error``
      留了明细则采用，否则写缺省说明（``DEFAULT_FAILED_ERROR``）。
    * 抛异常 → 记 ``format_error(e)`` 后**原样再抛**（不改变调用方语义）。
    * 留存开关关闭 / 任何留存异常 → 被吞掉，绝不影响进化主流程。
    """
    def _deco(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            _self = args[0] if args else None
            _prompt = kwargs.get("prompt", None)
            if _prompt is None and len(args) > prompt_pos:
                _prompt = args[prompt_pos]
            _t0 = time.time()
            _resp = None
            _err = ""
            _status = STATUS_SUCCESS
            try:
                _resp = fn(*args, **kwargs)
                if not _resp:
                    _status = STATUS_FAILED
                    _err = str(getattr(_self, "_m44_last_error", "") or "")
                    if not _err:
                        _err = DEFAULT_FAILED_ERROR
                return _resp
            except Exception as _e:
                _status = STATUS_FAILED
                try:
                    _err = format_error(_e)
                except Exception:
                    _err = "%s: %s" % (type(_e).__name__, _e)
                raise
            finally:
                try:
                    record_evolution_call(
                        prompt=_prompt if _prompt is not None else "",
                        response=_resp if _resp else "",
                        status=_status, error=_err,
                        prompt_version=version,
                        model=str(getattr(_self, "_m44_last_model", "") or ""),
                        duration=time.time() - _t0)
                except Exception:
                    pass
        return _wrapper
    return _deco
