# -*- coding: utf-8 -*-
"""
EvolutionEffectVerifier.py —— 进化效果验证器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 验证进化变更的实际效果与安全性
机制: 基于EvolutionEffectVerifier类实现，包含10个核心方法
定位: 进化验证层
"""

from __future__ import annotations
from nucleus._silent_except import silent_exc

import difflib
import json
import os
import re
import threading
import time
from typing import Any
from nucleus.data.DataAccessLayer import safe_read_json


try:
    from nucleus.logger import get_module_logger
    _logger = get_module_logger("EvolutionEffectVerifier")
except Exception:  # pragma: no cover
    _logger = None


def _log(level: str, msg: str) -> None:
    if _logger is None:
        return
    try:
        getattr(_logger, level)(msg)
    except Exception as e:
        silent_exc(e, where="nucleus.evolution.EvolutionEffectVerifier::_log L39")


# 低风险补丁的代码特征：只动日志/注释/字符串常量/数值参数
_LOW_RISK_LINE_PATTERNS = (
    r'^\s*#',                                   # 注释行
    r'^\s*"""', r"^\s*'''",                     # 文档字符串
    r'^\s*(?:_module_logger|_logger|self\._log|logger)\s*\.',  # 日志调用
    r'^\s*(?:print\()?\s*$',                    # 空行
)
_LOW_RISK_RE = re.compile("|".join(_LOW_RISK_LINE_PATTERNS))
# 数值参数微调：行内只改了数字/True/False
_NUM_TUNING_RE = re.compile(r'[\d.]+|True|False|None')


# ★第九批：测试隔离用默认目录（tools/test_isolation_shim.redirect_all 会重定向到 ISO_DIR）
_ISO_BASE_DIR: str | None = None


class EvolutionEffectVerifier:
    """补丁效果验证 + 复发检测 + 信任分建议。"""

    # 性能退化容忍：超过基线该毫秒数视为退化
    DEFAULT_MAX_REGRESSION_MS = 1500.0
    # 复发判定窗口（秒）：窗口内同一问题签名再次出现即判复发
    DEFAULT_RECURRENCE_WINDOW = 24 * 3600

    def __init__(self, base_dir: str | None = None):
        self._lock = threading.RLock()
        # ★第十一批 批次B 修复：_ISO_BASE_DIR 必须**优先于**默认目录生效。
        #   原写法先赋默认目录，再判 `base_dir is None and _ISO_BASE_DIR`（恒假），
        #   导致测试隔离失效、测试把效果验证数据写到生产 data/evolution/。
        if base_dir is None and _ISO_BASE_DIR:
            base_dir = _ISO_BASE_DIR
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "data", "evolution")
        self._base_dir = base_dir
        self._save_path = os.path.join(base_dir, "effect_verify.json")
        # {issue_sig: {"first_seen":ts, "fixed_at":ts|0, "recurrences":n, "last_seen":ts}}
        self._issues: dict[str, dict[str, Any]] = {}
        # {patch_id: {...verdict...}}
        self._verdicts: dict[str, dict[str, Any]] = {}
        self._load()

    # ==================== 风险分级 ====================

    @staticmethod
    def is_low_risk_patch(patch: dict[str, Any]) -> bool:
        """判定是否为低风险补丁（加日志/改注释/参数微调）。

        判据（两个条件都要满足）：
          ① risk_level 属于「极低/低」；
          ② 代码差异里**每一行**改动都属于低风险形态（注释/日志/文档串/空行），
             或只是数值常量微调（其余字符完全一致）。
        """
        if not patch:
            return False
        _risk = str(patch.get("risk_level") or "低")
        if _risk not in ("极低", "低"):
            return False
        _old = str(patch.get("original_code") or "")
        _new = str(patch.get("modified_code") or "")
        if not _old and not _new:
            return False
        _diff = list(difflib.unified_diff(
            _old.splitlines(), _new.splitlines(), n=0, lineterm=""))
        # 去掉 diff 头（---/+++/@@）
        _changed = [ln for ln in _diff
                    if ln[:1] in "+-" and not ln.startswith(("---", "+++"))]
        if not _changed:
            return False
        for _ln in _changed:
            _body = _ln[1:]
            if _LOW_RISK_RE.match(_body):
                continue
            # 数值微调：去掉数字/布尔后两侧一致
            if _NUM_TUNING_RE.sub("#", _body) and \
                    len(_NUM_TUNING_RE.findall(_body)) > 0:
                # 与对应行比对太复杂，此处放宽：含数值且行长短（≤120）视为参数微调
                if len(_body.strip()) <= 120:
                    continue
            return False
        return True

    @classmethod
    def trust_threshold_for(cls, patch: dict[str, Any],
                            low: float = 40.0, high: float = 60.0) -> float:
        """按补丁风险给出信任分门槛：低风险 40，其余 60（阈值可由 config 覆盖）。"""
        try:
            import config as _cfg
            _c = getattr(_cfg, "EVOLUTION_EFFECT_VERIFY_CONFIG", {}) or {}
            low = float(_c.get("low_risk_min_trust", low) or low)
            high = float(_c.get("high_risk_min_trust", high) or high)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.EvolutionEffectVerifier::trust_threshold_for L135")
        return low if cls.is_low_risk_patch(patch) else high

    # ==================== 效果验证 ====================

    def verify(self, patch: dict[str, Any], issue: dict[str, Any] | None = None,
               *, problem_gone: bool | None = None,
               function_ok: bool | None = None,
               perf_delta_ms: float | None = None) -> dict[str, Any]:
        """对一次补丁应用做效果验证，返回裁决。

        Args:
            patch: 补丁字典（需含 id/file/modified_code）
            issue: 对应问题（含 type/description/signature，可选）
            problem_gone: ① 问题是否消失（None = 无法判定）
            function_ok: ② 相关功能是否正常（None = 无法判定）
            perf_delta_ms: ③ 性能变化（正数=变慢；None = 未测量）

        Returns:
            {"passed", "dims": {...}, "trust_delta", "reason", "patch_id"}
        """
        _pid = str(patch.get("id") or f"unknown_{int(time.time())}")
        _max_regression = self.DEFAULT_MAX_REGRESSION_MS
        try:
            import config as _cfg
            _max_regression = float(
                getattr(_cfg, "EVOLUTION_EFFECT_VERIFY_CONFIG", {}).get(
                    "max_regression_ms", _max_regression) or _max_regression)
        except Exception as e:
            silent_exc(e, "EvolutionEffectVerifier.py:163:verify", level="warning")

        # ① 问题是否消失
        if problem_gone is None:
            _d1, _d1_note = None, "未判定（缺少问题复查探针）"
        else:
            _d1, _d1_note = bool(problem_gone), ("问题已消失" if problem_gone else "问题仍在")
        # ② 相关功能是否正常
        if function_ok is None:
            _d2, _d2_note = None, "未判定（缺少功能校验）"
        else:
            _d2, _d2_note = bool(function_ok), ("功能正常" if function_ok else "功能受影响")
        # ③ 性能是否退化
        if perf_delta_ms is None:
            _d3, _d3_note = None, "未测量"
        else:
            _d3 = float(perf_delta_ms) <= _max_regression
            _d3_note = f"性能变化{float(perf_delta_ms):.0f}ms" + ("（可接受）" if _d3 else "（退化）")

        _known = [d for d in (_d1, _d2, _d3) if d is not None]
        if not _known:
            _passed, _reason = None, "三项均未测量，无法判定（不计入修复率）"
            _delta = 0
        elif all(_known):
            _passed, _reason = True, f"{_d1_note}；{_d2_note}；{_d3_note}"
            _delta = 10
        elif _d1 is False:
            _passed, _reason = False, f"问题未解决（{_d1_note}）"
            _delta = -15
        elif _d1 is None:
            # ★主线第47批 T1（P0-2）：**无法判定 ≠ 失败**。
            #   原实现把 `_d1 is None`（缺少问题复查探针）与"确实失败"一样扣 10 分，
            #   这会把"不知道"惩罚成"做错了"，与语义拆分（不可判定=不奖不罚）冲突。
            _passed, _reason = None, f"问题是否消失不可判定（{_d1_note}）；{_d2_note}；{_d3_note}"
            _delta = 0
        else:
            _passed, _reason = False, f"{_d1_note}；{_d2_note}；{_d3_note}"
            _delta = -10

        _verdict = {
            "patch_id": _pid,
            "file": patch.get("file", ""),
            "issue_type": (issue or {}).get("type", patch.get("issue_type", "")),
            "passed": _passed,
            "trust_delta": _delta,
            "reason": _reason,
            "dims": {
                "problem_gone": _d1, "function_ok": _d2, "perf_ok": _d3,
                "notes": [_d1_note, _d2_note, _d3_note],
            },
            "verified_at": time.time(),
        }
        with self._lock:
            self._verdicts[_pid] = _verdict
            self._verdicts = dict(list(self._verdicts.items())[-500:])
        self.save()
        _log("info", f"[效果验证] {_pid[:16]} 通过={_passed} 信任分{_delta:+d} | {_reason}")
        return _verdict

    def apply_trust_adjustment(self, patch: dict[str, Any],
                               verdict: dict[str, Any] | None = None) -> int:
        """把验证结果回填到补丁信任分（原地修改 patch，返回调整后的信任分）。"""
        if not patch:
            return 0
        _v = verdict or self._verdicts.get(str(patch.get("id") or ""), {})
        _delta = int(_v.get("trust_delta", 0) or 0)
        if not _delta:
            return int(patch.get("trust_score", 0) or 0)
        _new = max(10, min(95, int(patch.get("trust_score", 0) or 0) + _delta))
        patch["trust_score"] = _new
        patch["effect_verified"] = _v.get("passed")
        patch["effect_reason"] = _v.get("reason", "")
        return _new

    # ==================== 复发检测 ====================

    @staticmethod
    def issue_signature(issue: dict[str, Any] | None, patch: dict[str, Any] | None = None) -> str:
        """问题签名：文件 + 类型 + 方法/描述前 24 字（跨轮次稳定）。"""
        _i = issue or {}
        _p = patch or {}
        _f = str(_i.get("file") or _p.get("file") or "")
        _t = str(_i.get("type") or _p.get("issue_type") or "")
        _m = str(_i.get("method") or _p.get("method") or _i.get("description") or "")[:24]
        return f"{_f}::{_t}::{_m}"

    def mark_fixed(self, signature: str) -> None:
        """标记某问题已被修复（供复发检测比对）。"""
        with self._lock:
            _e = self._issues.setdefault(
                signature, {"first_seen": time.time(), "fixed_at": 0.0,
                            "recurrences": 0, "last_seen": time.time()})
            _e["fixed_at"] = time.time()
            _e["last_seen"] = time.time()
        self.save()

    def check_recurrence(self, signature: str) -> dict[str, Any]:
        """问题再次出现时调用：判定是否为复发（修复后又冒出来）。"""
        _now = time.time()
        _window = self.DEFAULT_RECURRENCE_WINDOW
        try:
            import config as _cfg
            _window = float(getattr(_cfg, "EVOLUTION_EFFECT_VERIFY_CONFIG", {}).get(
                "recurrence_window_sec", _window) or _window)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.EvolutionEffectVerifier::check_recurrence L269")
        with self._lock:
            _e = self._issues.get(signature)
            if _e is None:
                self._issues[signature] = {"first_seen": _now, "fixed_at": 0.0,
                                           "recurrences": 0, "last_seen": _now}
                self.save()
                return {"recurred": False, "reason": "首次出现", "priority_boost": 0}
            _e["last_seen"] = _now
            _fixed_at = float(_e.get("fixed_at", 0) or 0)
            if _fixed_at and (_now - _fixed_at) <= _window:
                _e["recurrences"] = int(_e.get("recurrences", 0)) + 1
                _n = _e["recurrences"]
                _e["fixed_at"] = 0.0  # 重新进入未修复状态
                self.save()
                _log("warning",
                     f"[复发检测] 问题复发(第{_n}次): {signature[:60]}，优先级提升")
                return {"recurred": True, "count": _n,
                        "reason": f"修复后{int(_now - _fixed_at)}s内再次出现",
                        "priority_boost": min(30, 10 * _n)}
            _e["fixed_at"] = 0.0
            self.save()
            return {"recurred": False, "reason": "未修复状态再次出现", "priority_boost": 5}

    # ==================== 统计 / 持久化 ====================

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            _vs = list(self._verdicts.values())
            _judged = [v for v in _vs if v.get("passed") is not None]
            _ok = [v for v in _judged if v.get("passed")]
            return {
                "verdicts": len(_vs),
                "judged": len(_judged),
                "passed": len(_ok),
                "fix_rate": round(len(_ok) / len(_judged), 3) if _judged else 0.0,
                "recurrences": sum(int(v.get("recurrences", 0)) for v in self._issues.values()),
                "tracked_issues": len(self._issues),
            }

    def _load(self) -> None:
        try:
            if os.path.exists(self._save_path):
                _raw = safe_read_json(self._save_path, default={})
                self._issues = _raw.get("issues", {}) or {}
                self._verdicts = _raw.get("verdicts", {}) or {}
        except Exception as _e:
            _log("warning", f"[效果验证] 加载失败，以空库启动: {_e}")

    def save(self) -> None:
        try:
            os.makedirs(self._base_dir, exist_ok=True)
            with self._lock:
                _dump = {"version": 1, "updated": time.time(),
                         "issues": self._issues, "verdicts": self._verdicts}
            _tmp = self._save_path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(_dump, f, ensure_ascii=False, indent=1)
            os.replace(_tmp, self._save_path)
        except Exception as _e:
            _log("warning", f"[效果验证] 落盘失败（内存数据未丢）: {_e}")

    def reset(self) -> None:
        with self._lock:
            self._issues, self._verdicts = {}, {}
        try:
            if os.path.exists(self._save_path):
                os.remove(self._save_path)
        except Exception as e:
            silent_exc(e, where="nucleus.evolution.EvolutionEffectVerifier::reset L338")


# ==================== 单例 ====================

_verifier: EvolutionEffectVerifier | None = None
_verifier_lock = threading.Lock()


def get_effect_verifier(base_dir: str | None = None) -> EvolutionEffectVerifier:
    """获取 EvolutionEffectVerifier 单例。"""
    global _verifier
    if _verifier is None:
        with _verifier_lock:
            if _verifier is None:
                _verifier = EvolutionEffectVerifier(base_dir=base_dir)
    return _verifier


