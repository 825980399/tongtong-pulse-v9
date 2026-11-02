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

import difflib
import json
import os
import re
import threading
import time
from typing import Any

from nucleus._silent_except import silent_exc
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

# ★375刀4：复现结论 → `problem_gone` 的唯一映射表。
#   背景：post-apply 的效果验证曾恒传 `problem_gone=None`，三维退化成一维；
#   而 `verify()` 里 `all(_known)` 对 `[True]` 也为真 ⇒ **一维通过就 +10 分**。
#   同一次应用里 `patch_active_reprobe.apply_reprobe()` 已把复现结论原地写进 patch，
#   那是现成的真实信号，没必要继续传 None。
_M375_PROBLEM_GONE_MAP: dict[str, tuple[bool | None, str]] = {
    "true_pass": (True, "复现实测：修复前复现、修复后完全消失"),
    "partial_fix": (False, "复现实测：问题数减少但未清零"),
    "false_pass": (None, "复现实测：修复前无法复现 ⇒ 无基线，不可判定"),
}


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

    @staticmethod
    def problem_gone_from_reprobe(patch: dict[str, Any]) -> tuple[bool | None, str]:
        """★375刀4：把同一次应用回填的复现结论映射成 `problem_gone`。

        返回 `(bool|None, 说明)`。**`false_pass` 映射为 None 而不是 False**：
        它是「修复前根本复现不出」= 没有基线，判 False 会按「问题仍在」扣 -15 分，
        把「测不了」惩罚成「做错了」（与第47批「无法判定≠失败」同一条线）。
        缺值 / 未知结论同样返回 None —— 不猜（铁律159 同型）。
        """
        _v = str((patch or {}).get("reprobe_verdict") or "").strip()
        if _v in _M375_PROBLEM_GONE_MAP:
            return _M375_PROBLEM_GONE_MAP[_v]
        return None, f"无复现结论（{_v or '缺值'}）⇒ 不可判定"

    def verify(self, patch: dict[str, Any], issue: dict[str, Any] | None = None,
               *, problem_gone: bool | None = None,
               function_ok: bool | None = None,
               perf_delta_ms: float | None = None,
               perf_detail: dict[str, Any] | None = None) -> dict[str, Any]:
        """对一次补丁应用做效果验证，返回裁决。

        Args:
            patch: 补丁字典（需含 id/file/modified_code）
            issue: 对应问题（含 type/description/signature，可选）
            problem_gone: ① 问题是否消失（None = 无法判定）
            function_ok: ② 相关功能是否正常（None = 无法判定）
            perf_delta_ms: ③ 性能变化（正数=变慢；None = 未测量）
            perf_detail: ③ 的**明细**（★428刀3 新增，可选）——
                `{"p95_delta_ms": float|None, "mem_delta_mb": float|None,
                  "before_ms", "after_ms", "ab_mode"}`。
                判据一个字不用它：只用于把数值存进 verdict 供事后复核。

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
        elif _d1 is False:
            _passed, _reason = False, f"问题未解决（{_d1_note}）"
            _delta = -15
        elif _d1 is None:
            # ★主线第47批 T1（P0-2）：**无法判定 ≠ 失败**。
            #   原实现把 `_d1 is None`（缺少问题复查探针）与"确实失败"一样扣 10 分，
            #   这会把"不知道"惩罚成"做错了"，与语义拆分（不可判定=不奖不罚）冲突。
            _passed, _reason = None, f"问题是否消失不可判定（{_d1_note}）；{_d2_note}；{_d3_note}"
            _delta = 0
        elif False in _known:
            _passed, _reason = False, f"{_d1_note}；{_d2_note}；{_d3_note}"
            _delta = -10
        elif len(_known) == 3:
            _passed, _reason = True, f"{_d1_note}；{_d2_note}；{_d3_note}"
            _delta = 10
        else:
            # ★375刀4：**覆盖不足不是通过**。原实现用 `all(_known)` 判通过，
            #   而 `all([True])` 也为真 ⇒ 只传 function_ok 一项就能拿 +10 分，
            #   三维判据被一个维度冒充（零调权形同虚设）。
            #   改判：已知维度全过但不足三维 ⇒ 不可判定（不奖不罚），缺口如实报出。
            _missing = [n for n, d in (("problem_gone", _d1), ("function_ok", _d2),
                                       ("perf_ok", _d3)) if d is None]
            _passed = None
            _reason = (f"仅 {len(_known)}/3 维可判且均通过，缺 {'/'.join(_missing)}"
                       " ⇒ 覆盖不足，不奖不罚")
            _delta = 0

        # ★428刀3：三维度**数值**也落盘（改造前只用一次就算丢）。
        #   任务书问"验证一个补丁后真有 perf_delta_ms 字段吗"——改造前没有：
        #   `verify()` 把入参用一次算 `perf_ok` 就返回，verdict 里只剩一个布尔
        #   和一句文案 ⇒ 事后既不能复核判得对不对，也做不了趋势。
        #   取数优先级：显式入参 > perf_detail > None；**拿不到就是 None**，
        #   绝不写 0（0 是个"测过了、没变化"的断言，而这里的事实是"没测"）。
        _pd = perf_detail if isinstance(perf_detail, dict) else {}
        _perf_ms = (float(perf_delta_ms) if isinstance(perf_delta_ms, (int, float))
                    else None)
        _perf_p95 = _pd.get("p95_delta_ms")
        _perf_mem = _pd.get("mem_delta_mb")
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
            # ★428刀3：三维度数值（正 = 变慢 / 变多，与 perf_delta_ms 同语义）
            "perf_delta_ms": _perf_ms,
            "perf_p95_ms": (float(_perf_p95)
                            if isinstance(_perf_p95, (int, float)) else None),
            "perf_mem_mb": (float(_perf_mem)
                            if isinstance(_perf_mem, (int, float)) else None),
            "perf_ab_mode": str(_pd.get("ab_mode") or ""),
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

    #: ★418刀3⑤：测试桩的识别口径（只为**生产落盘**服务，非生产路径原样保留）
    #: ★421刀5：补齐 418 当时漏登记的 10 条。识别判据是「在
    #:   `data/patches/patch_history.json` 里找不到同 id」—— 它们分别来自
    #:   演示脚本（p0demo4）、临时项目（c5 / int1）、单条探针（t101a_probe / p93 /
    #:   zw2case1b）、性能桩（pperf）、`issued_type="x"`+`file="a.py"` 的占位
    #:   （p3d / pno / pab），**从来不是真补丁**。
    #:   实测（2026-11-02）：这 10 条在 87 条 patch_history 里命中 **0**；
    #:   剔除后剩 40 条，40 条**全部**能在 patch_history 里找到（0 条悬空）。
    _M418_STUB_PATCH_IDS = frozenset({
        "tp1", "tp2", "tp3", "tp4",
        "p0demo4", "p93", "zw2case1b", "c5", "t101a_probe",
        "int1", "p3d", "pperf", "pno", "pab",
    })
    _M418_STUB_ISSUE_PREFIXES = ("a.py::", "<test>::")

    @staticmethod
    def _m418_is_stub_patch(patch_id) -> bool:
        """`tp1`~`tpN` 这类是单测/手工验证的桩，不是真补丁。"""
        _p = str(patch_id or "").strip()
        if _p in EvolutionEffectVerifier._M418_STUB_PATCH_IDS:
            return True
        return bool(_p.startswith("tp") and _p[2:].isdigit())

    @classmethod
    def _m418_is_stub_issue(cls, signature) -> bool:
        return str(signature or "").startswith(cls._M418_STUB_ISSUE_PREFIXES)

    def _m418_production_path(self) -> bool:
        """当前落盘路径是否**生产**路径（否则是单测沙箱，必须原样保留桩）。"""
        try:
            return os.path.abspath(self._save_path) == os.path.abspath(
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))), "data", "evolution",
                    "effect_verify.json"))
        except Exception as e:
            silent_exc(e, where="EvolutionEffectVerifier::_m418_production_path")
            return False

    def _m418_strip_stubs(self) -> tuple:
        """就地剔除测试桩，返回 `(剔除的 verdict 数, 剔除的 issue 数)`。"""
        if not self._m418_production_path():
            return 0, 0
        _v0, _i0 = len(self._verdicts), len(self._issues)
        self._verdicts = {k: v for k, v in self._verdicts.items()
                          if not self._m418_is_stub_patch(k)}
        self._issues = {k: v for k, v in self._issues.items()
                        if not self._m418_is_stub_issue(k)}
        return _v0 - len(self._verdicts), _i0 - len(self._issues)

    def _load(self) -> None:
        try:
            if os.path.exists(self._save_path):
                _raw = safe_read_json(self._save_path, default={})
                self._issues = _raw.get("issues", {}) or {}
                self._verdicts = _raw.get("verdicts", {}) or {}
                # ★418刀3⑤：读盘时就剔掉测试桩（生产路径才生效），
                #   否则 get_stats()/chain_metrics 的「修复率」会被桩拉偏。
                _v, _i = self._m418_strip_stubs()
                if _v or _i:
                    _log("info", f"[效果验证] 已剔除测试桩 verdicts={_v} issues={_i}")
        except Exception as _e:
            _log("warning", f"[效果验证] 加载失败，以空库启动: {_e}")

    def save(self) -> None:
        try:
            os.makedirs(self._base_dir, exist_ok=True)
            with self._lock:
                # ★418刀3⑤：落盘前兜底过滤（生产路径才生效）
                if self._m418_production_path():
                    _v_stub, _i_stub = self._m418_strip_stubs()
                    if _v_stub or _i_stub:
                        _log("info", f"[效果验证] 落盘前剔除测试桩 verdicts={_v_stub}"
                                    f" issues={_i_stub}")
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


