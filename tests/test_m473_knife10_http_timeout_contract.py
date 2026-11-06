# -*- coding: utf-8 -*-
"""第473批 刀10 门控单测：`safe_http_json` 的读超时契约。

病灶
----
`safe_http_json` 只写了三个 handler：

    except urllib.error.HTTPError   → HTTP 码 + 正文
    except urllib.error.URLError    → "请求失败: <urlopen error …>"
    except (ValueError, JSONDecodeError) → "响应解析失败: …"

而**读超时**抛的是 `TimeoutError`（3.10+ 是 `socket.timeout` 的别名）——
它是 `OSError` 的子类，却是 `URLError` 的**兄弟而非子类**，三个 handler 全接不住
⇒ 异常直接穿透 `safe_http_json`，打破「一律返回 (bool, data)」的契约。

现场（三份轮转日志 2026-10-02 ~ 11-07 全量统计）
    `[LLM修复] 修复建议生成异常，本轮 LLM 通道降级返回 None` 共 **51 条**
    **51 条全部**是 `TimeoutError: The read operation timed out`
冒泡让它绕过 465 的失败记账三件套（`_m465_last_fail` 归类 / `_m465_fail_streak`
连击熔断 / `_m451` 连续 3 次才 WARNING）⇒ 每轮刷一条 WARNING、熔断永不因超时
开启、渠道健康度看不到超时。

判据
----
* 超时 ⇒ `(False, "请求失败: 读超时: …")`，且**不抛**；
* 消息经 `_m465_fail_kind` 必须仍归 `error`（465 明文：超时**不换道**，
  换到另一个慢渠道只会把本轮耗时成倍拉长）—— 这是「另一个入口」的独立基准；
* 灰度关闭 ⇒ `raise`，逐字回到旧行为；
* 非超时异常（`RuntimeError` 等）**必须继续冒泡** —— 只补这一类，不许借机
  把契约扩大成「吞掉所有异常」。
"""

from __future__ import annotations

import ast
import importlib
import os
import socket
import types
import urllib.error
import urllib.request

import pytest

import config

# ★铁律60：包里同名导出会盖掉子模块，取模块一律 importlib。
SSRF = importlib.import_module("nucleus.ssrf_guard")
SEE = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
PAT = importlib.import_module("nucleus.llm.paid_api_trace")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

#: 日志里的原文（51 条全这一句，逐字取自 logs/pulse.log*）
TIMEOUT_MSG = "The read operation timed out"


def _patch_urlopen(monkeypatch, exc: Exception, sink=None) -> None:
    """把 ssrf_guard 里的 urllib 换成只抛异常的替身（保留 Request / error 命名空间）。"""
    def _raise(*_a, **_k):
        if sink is not None:
            sink.append(exc)
        raise exc

    fake = types.SimpleNamespace(
        request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=_raise),
        error=urllib.error,
    )
    monkeypatch.setattr(SSRF, "urllib", fake)
    monkeypatch.setattr(SSRF, "is_safe_http_url", lambda _u: (True, ""))


@pytest.fixture()
def no_trace(monkeypatch):
    """不写生产埋点：begin 恒空串 ⇒ rid 为空 ⇒ finish 直接跳过。"""
    monkeypatch.setattr(PAT, "begin", lambda *_a, **_k: "")


def _post(timeout: int = 30):
    return SSRF.safe_http_json(_URL, method="POST", data=b"{}", timeout=timeout)


# ==================== 主判据：超时变结果 ====================

def test_read_timeout_returns_failure_tuple(monkeypatch, no_trace):
    """核心：读超时不再穿透契约，返回 (False, 文本)。"""
    _patch_urlopen(monkeypatch, TimeoutError(TIMEOUT_MSG))
    out = _post()
    assert isinstance(out, tuple) and len(out) == 2, out
    ok, data = out
    assert ok is False
    assert isinstance(data, str)
    assert "读超时" in data, data
    assert TIMEOUT_MSG in data, data


def test_socket_timeout_alias_is_covered(monkeypatch, no_trace):
    """`socket.timeout` 在 3.10+ 就是 `TimeoutError` 的别名，必须同一分支覆盖。"""
    assert socket.timeout is TimeoutError, "3.10+ 前提不成立，本用例需重估"
    _patch_urlopen(monkeypatch, socket.timeout(TIMEOUT_MSG))  # noqa: UP041
    ok, data = _post()
    assert ok is False
    assert "读超时" in data


def test_message_is_attributed_as_error_by_465(monkeypatch, no_trace):
    """★独立基准：消息喂给 465 归类器必须仍是 `error` ⇒ 超时不换道（465 明文规格）。"""
    _patch_urlopen(monkeypatch, TimeoutError(TIMEOUT_MSG))
    _ok, data = _post()
    assert SEE._m465_fail_kind(data) == "error", data
    assert SEE._m465_fail_kind(data) not in ("rate_limited", "quota"), \
        "超时若被归成限流/额度，会触发换道，把本轮耗时成倍拉长"


# ==================== 灰度：关掉逐字回滚 ====================

def test_switch_off_reraises_legacy(monkeypatch, no_trace):
    monkeypatch.setattr(config, "ENABLE_M473_HTTP_TIMEOUT_AS_RESULT", False, raising=False)
    _patch_urlopen(monkeypatch, TimeoutError(TIMEOUT_MSG))
    with pytest.raises(TimeoutError):
        _post()


def test_switch_defaults_on_when_attr_missing(monkeypatch, no_trace):
    """属性缺失 ⇒ 默认 True（旧行为是 bug，缺省必须走修复后的路径）。"""
    _sentinel = object()
    _old = getattr(config, "ENABLE_M473_HTTP_TIMEOUT_AS_RESULT", _sentinel)
    try:
        if hasattr(config, "ENABLE_M473_HTTP_TIMEOUT_AS_RESULT"):
            delattr(config, "ENABLE_M473_HTTP_TIMEOUT_AS_RESULT")
        assert SSRF._m473_timeout_as_result_on() is True
    finally:
        if _old is not _sentinel:
            config.ENABLE_M473_HTTP_TIMEOUT_AS_RESULT = _old


# ==================== 不许扩大契约 ====================

def test_non_timeout_exception_still_propagates(monkeypatch, no_trace):
    """`RuntimeError` 之类必须继续冒泡：只补超时这一类，不许吞编程错误。"""
    _patch_urlopen(monkeypatch, RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        _post()


def test_urlerror_branch_unchanged(monkeypatch, no_trace):
    """既有 URLError 分支格式逐字不变（超时若被包装成 URLError 仍走旧路）。"""
    _patch_urlopen(monkeypatch, urllib.error.URLError("timed out"))
    ok, data = _post()
    assert ok is False
    assert data.startswith("请求失败: "), data
    assert "读超时" not in data, data


# ==================== 埋点闭环 ====================

def test_trace_finish_called_on_timeout(monkeypatch):
    """有 rid 时必须回填 `error=TimeoutError`，否则付费埋点会留下永远开着的记录。"""
    seen = []
    monkeypatch.setattr(PAT, "begin", lambda *_a, **_k: "rid-473-10")
    monkeypatch.setattr(PAT, "finish", lambda rid, ok, usage=None, error="": seen.append(
        (rid, ok, error)))
    _patch_urlopen(monkeypatch, TimeoutError(TIMEOUT_MSG))
    ok, _data = _post()
    assert ok is False
    assert seen == [("rid-473-10", False, "TimeoutError")], seen


def test_finish_safe_skips_empty_rid(monkeypatch):
    """rid 为空 ⇒ 不调用 finish（既有语义：begin 未成功就不收尾）。"""
    called = []
    monkeypatch.setattr(PAT, "finish", lambda *a, **k: called.append((a, k)))
    SSRF._m360_finish_safe("", False, error="TimeoutError")
    assert called == []


# ==================== 接线（AST） ====================

#: 源码与 AST 只读一次（模块级 ⇒ 普通 class 也能用，不依赖 unittest 的 setUpClass）
with open(os.path.join(_ROOT, "nucleus", "ssrf_guard.py"), "r", encoding="utf-8") as _fh:
    _SSRF_SRC = _fh.read()
_SSRF_TREE = ast.parse(_SSRF_SRC)


class TestWiring:
    @staticmethod
    def _safe_fn():
        for _n in _SSRF_TREE.body:
            if isinstance(_n, ast.FunctionDef) and _n.name == "safe_http_json":
                return _n
        return None

    def test_timeout_handler_exists_in_safe_http_json(self):
        _fn = self._safe_fn()
        assert _fn is not None, "找不到 safe_http_json"
        _names = []
        for _h in _fn.body:
            if isinstance(_h, ast.Try):
                for _hd in _h.handlers:
                    _t = _hd.type
                    if isinstance(_t, ast.Name):
                        _names.append(_t.id)
                    elif isinstance(_t, ast.Tuple):
                        _names.extend(_e.id for _e in _t.elts if isinstance(_e, ast.Name))
        assert "TimeoutError" in _names, f"safe_http_json 的 handler 里没有 TimeoutError：{_names}"

    def test_handler_consults_gray_switch_and_finishes_trace(self):
        """handler 体必须 (1) 查灰度（2）补埋点（3）返回失败元组。"""
        _fn = self._safe_fn()
        _body_src = None
        for _h in _fn.body:
            if not isinstance(_h, ast.Try):
                continue
            for _hd in _h.handlers:
                _t = _hd.type
                if isinstance(_t, ast.Name) and _t.id == "TimeoutError":
                    _body_src = ast.dump(ast.Module(body=_hd.body, type_ignores=[]))
        assert _body_src is not None
        assert "_m473_timeout_as_result_on" in _body_src, "handler 未接灰度开关"
        assert "_m360_finish_safe" in _body_src, "handler 未回填付费埋点"
        assert "Raise(" in _body_src, "handler 关掉灰度时没有 raise 回滚路径"

    def test_helper_defined_before_use(self):
        _defs = {_n.name: _n.lineno for _n in _SSRF_TREE.body
                 if isinstance(_n, ast.FunctionDef)}
        assert "_m473_timeout_as_result_on" in _defs
        assert "_m360_finish_safe" in _defs
        _safe_line = _defs.get("safe_http_json")
        assert _safe_line is not None
        assert _defs["_m473_timeout_as_result_on"] < _safe_line
        assert _defs["_m360_finish_safe"] < _safe_line

    def test_config_switch_present(self):
        with open(os.path.join(_ROOT, "config.py"), "r", encoding="utf-8") as fh:
            _cfg = fh.read()
        assert "ENABLE_M473_HTTP_TIMEOUT_AS_RESULT" in _cfg
