"""★第360批 刀0-2：DeepSeek 付费出口全链路埋点。

背景（小林 2026-10-24 亲查）：DeepSeek 开放平台显示 2026-10-23 单日
`deepseek-v4-pro` 122 次 / 1.09 亿 tokens，而框架账本只记 450,129 tokens、
全框架日志与生产代码**均无 v4-pro 活跃调用点**。v4-pro 静态溯源已穷尽，
故在**出口最底层**埋点：下次运行时无论调用源是主进程、multiprocessing 子进程、
动态 import 还是自我修改后的代码，只要它走框架内任一 HTTP 出口，就会被记录。

设计要点：
- **只追加写**（append-only），单行 JSON；写失败静默降级为日志 warning，
  **绝不阻断主流程**（埋点不是业务）。
- 两阶段：``begin()`` 记请求（ts/pid/url/model/caller/调用栈前 5 帧/prompt 字节数），
  ``finish()`` 记结果（usage/成功与否）；两次以 ``rid`` 关联。
- 付费判定 `is_paid_endpoint()` 以**配置**为准（`PAID_CHANNEL_NAMES` 对应渠道的
  host + `REMOTE_API_CONFIG.api_url` 的 host），不硬编码单一域名；
  命中付费出口一律 INFO 留痕，命中 **v4-pro** 这类大上下文收费模型额外打
  **WARNING + 调用栈**，便于在平台再次出现时一眼定位。

覆盖的出口（本批已逐个接线）：
1. ``organs/body/PulseLung.py::_call_channel``  （渠道池主路径）
2. ``organs/body/PulseLung.py::_call_remote_api``（单端点兜底路径）
3. ``nucleus/reasoning/channel_client.py::call_channel``（进化链/子进程常用）
4. ``nucleus/ssrf_guard.py::safe_http_json``（统一 JSON 出口）

不覆盖（非 LLM，且非付费端点，已列清单交星轨）：
``nucleus/knowledge/RssCollector.py``、``nucleus/knowledge/WikiQuerier.py``。
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback

from nucleus._silent_except import silent_exc

_TRACE_LOCK = threading.Lock()
_TRACE_DIRNAME = os.path.join("data", "logs")
_TRACE_BASENAME = "paid_api_trace.jsonl"
# ★路径锚在**项目根**（与 ChannelQuotaMonitor 同法）：不依赖进程 cwd，
#   multiprocessing 子进程与"从别处启动"的场景也能写到同一个文件。
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 单文件软上限：超过后每次写盘只在首次告警一次（**不丢记录**，避免证据断档）
_SOFT_MAX_BYTES = 64 * 1024 * 1024
_size_warned = False
_disabled = False


def _m360_trace_enabled() -> bool:
    """埋点总开关（`ENABLE_PAID_API_TRACE`，默认 True）。"""
    try:
        import config as _cfg
        return bool(getattr(_cfg, "ENABLE_PAID_API_TRACE", True))
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::_m360_trace_enabled", level="debug")
        return True


def _m360_trace_path() -> str:
    """埋点文件路径（默认 `<data>/logs/paid_api_trace.jsonl`）。"""
    try:
        import config as _cfg
        _rel = str(getattr(_cfg, "PAID_API_TRACE_REL", "") or "").strip()
        if _rel:
            return _rel if os.path.isabs(_rel) else os.path.join(_PROJECT_ROOT, _rel)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::_m360_trace_path", level="debug")
    return os.path.join(_PROJECT_ROOT, _TRACE_DIRNAME, _TRACE_BASENAME)


def _m360_stack(depth: int = 5) -> list:
    """取调用栈前 N 帧（**跳过本模块自身**），格式 `文件:行号:函数`。"""
    _out = []
    try:
        _me = os.path.basename(__file__)
        for _fr in traceback.extract_stack(limit=depth + 12)[::-1]:
            if os.path.basename(_fr.filename) == _me:
                continue
            _out.append("{}:{}:{}".format(_fr.filename, _fr.lineno, _fr.name))
            if len(_out) >= depth:
                break
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::_m360_stack", level="debug")
    return _out


def _m360_host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(str(url)).hostname or "").lower()
    except Exception as _e:
        silent_exc(_e, where="nucleus.llm.paid_api_trace::_m360_host_of urlparse", level="debug")
        return ""


def _m360_paid_hosts() -> set:
    """付费端点 host 集合（**以配置为准**，不硬编码单一域名）。"""
    _hosts = set()
    try:
        import config as _cfg
        _paid_names = set(getattr(_cfg, "PAID_CHANNEL_NAMES", []) or [])
        for _ch in (_cfg.get_active_channels(include_disabled=True) or []):
            if isinstance(_ch, dict) and str(_ch.get("name")) in _paid_names:
                _h = _m360_host_of(_ch.get("api_url", ""))
                if _h:
                    _hosts.add(_h)
        # 单端点兜底：REMOTE_API_CONFIG.api_url（付费官方端点）
        _h = _m360_host_of((getattr(_cfg, "REMOTE_API_CONFIG", {}) or {}).get("api_url", ""))
        if _h:
            _hosts.add(_h)
        # 高级任务端点（advanced_api_url，收费）
        _h = _m360_host_of((getattr(_cfg, "REMOTE_API_CHANNELS", {}) or {})
                           .get("advanced_api_url", ""))
        if _h:
            _hosts.add(_h)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::_m360_paid_hosts", level="debug")
    return _hosts


def is_paid_endpoint(url: str) -> bool:
    """判定该 URL 是否指向付费端点（判定失败一律 False ⇒ 宁少记不多报）。"""
    try:
        _h = _m360_host_of(url)
        if not _h:
            return False
        for _ph in _m360_paid_hosts():
            if _h == _ph or _h.endswith("." + _ph):
                return True
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::is_paid_endpoint", level="debug")
    return False


def is_expensive_model(model: str) -> bool:
    """是否属"大上下文收费模型"（v4-pro 一族）⇒ 额外 WARNING 便于一眼定位。"""
    _m = str(model or "").lower()
    return ("v4-pro" in _m) or _m.endswith("-pro")


def _m360_log(msg: str, level: str = "info") -> None:
    """埋点自身的留痕（走模块 logger，失败静默）。"""
    try:
        from nucleus.logger import get_module_logger
        _lg = get_module_logger("paid_api_trace")
        getattr(_lg, level if level in ("info", "warning", "error") else "info")(msg)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::_m360_log", level="debug")


def _m360_append(rec: dict) -> bool:
    """追加一行（模块锁 + 单次 open/write/fsync）；任何异常只告警不抛出。"""
    global _size_warned
    if _disabled:
        return False
    try:
        _path = _m360_trace_path()
        os.makedirs(os.path.dirname(_path), exist_ok=True)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::_m360_append makedirs", level="debug")
        return False
    try:
        _line = json.dumps(rec, ensure_ascii=False, default=str)
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::_m360_append dumps", level="debug")
        return False
    try:
        with _TRACE_LOCK:
            _fd = os.open(_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(_fd, (_line + "\n").encode("utf-8"))
                try:
                    os.fsync(_fd)
                except Exception as _e:
                    silent_exc(_e, where="nucleus.llm.paid_api_trace::_m360_append fsync", level="debug")
            finally:
                os.close(_fd)
            if not _size_warned:
                try:
                    if os.path.getsize(_path) > _SOFT_MAX_BYTES:
                        _size_warned = True
                        _m360_log("[付费埋点] %s 已超过 %d 字节软上限，"
                                  "继续追加但请尽快归档" % (_path, _SOFT_MAX_BYTES),
                                  level="warning")
                except Exception as _e:
                    silent_exc(_e, where="nucleus.llm.paid_api_trace::_m360_append getsize", level="debug")
        return True
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::_m360_append write", level="debug")
        return False


def begin(url: str, model: str = "", caller: str = "", payload=None) -> str:
    """记录一次**付费**出口请求；非付费端点直接返回空串（零开销）。

    返回 ``rid``（用于 ``finish`` 关联结果）；埋点未命中/失败时返回空串。
    """
    if not _m360_trace_enabled():
        return ""
    if not is_paid_endpoint(url):
        return ""
    _rid = "{:.6f}.{}".format(time.time(), os.getpid())
    _pb = 0
    try:
        if payload is None:
            _pb = 0
        elif isinstance(payload, (bytes, bytearray)):
            _pb = len(payload)
        elif isinstance(payload, str):
            _pb = len(payload.encode("utf-8"))
        else:
            _pb = len(json.dumps(payload, ensure_ascii=False, default=str)
                      .encode("utf-8"))
    except Exception as e:
        silent_exc(e, where="nucleus.llm.paid_api_trace::begin payload_size", level="debug")
        _pb = -1
    _rec = {
        "event": "request",
        "rid": _rid,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts_epoch": round(time.time(), 3),
        "pid": os.getpid(),
        "url": str(url),
        "model": str(model or ""),
        "caller": str(caller or ""),
        "stack": _m360_stack(5),
        "prompt_bytes": _pb,
    }
    _m360_append(_rec)
    if is_expensive_model(model):
        _m360_log("[付费出口] ⚠ 大上下文收费模型 model={} caller={} url={} 调用栈={}".format(
            model, caller, url, " <- ".join(_rec["stack"])), level="warning")
    else:
        _m360_log("[付费出口] model={} caller={} url={} prompt_bytes={}".format(
            model, caller, url, _pb), level="info")
    return _rid


def finish(rid: str, ok: bool, usage=None, error: str = "") -> None:
    """回填一次请求的结果（usage / 成功与否）；``rid`` 为空则忽略。"""
    if not rid or not _m360_trace_enabled():
        return
    _rec = {
        "event": "result",
        "rid": rid,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts_epoch": round(time.time(), 3),
        "pid": os.getpid(),
        "ok": bool(ok),
        "usage": usage if isinstance(usage, dict) else None,
        "error": str(error or "")[:300],
    }
    _m360_append(_rec)


def get_trace_path() -> str:
    """埋点文件路径（供心跳/运维查看）。"""
    return _m360_trace_path()
