# -*- coding: utf-8 -*-
"""
SelfCalibrator.py —— 自我校准器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 推理参数的自我校准与优化
机制: 基于SelfCalibrator类实现，包含10个核心方法
定位: 推理治理层
"""

import json
import os
import threading
import time
from typing import Any

from nucleus.logger import get_module_logger
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus._silent_except import silent_exc


_logger = get_module_logger('SelfCalibrator')

try:
    from nucleus.logger import get_module_logger
    _logger = get_module_logger("SelfCalibrator")
except Exception as e:
    silent_exc(e, "nucleus/reasoning/SelfCalibrator.py:30:自校准操作异常", level="warning")
    _logger = None


def _log_debug(msg: str) -> None:
    if _logger is not None:
        try:
            _logger.debug(msg)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SelfCalibrator::_log_debug L40")


class SelfCalibrator:
    """自我校准器。"""

    # EMA 平滑系数：0.1 表示每次只采纳 10% 的新观察，抗噪声
    EMA_ALPHA = 0.1
    # 校准偏移量边界（避免过度修正）
    OFFSET_BOUND = 25.0

    def __init__(self, base_dir: str | None = None):
        self._lock = threading.Lock()
        # {source: {"offset": float, "samples": int, "correct": int}}
        self._data: dict[str, dict[str, float]] = {}
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._save_path = os.path.join(base_dir, "self_calibration.json")
        self._last_save = 0.0
        self._load()

    # ========== 校准 ==========

    def calibrate(self, confidence: float, source: str = "generic") -> float:
        """对给定置信度做校准，返回调整后的置信度（0~100）。

        校准方向由该来源历史 EMA 偏移量决定：
          偏移量 = 实际正确率均值 - 预测置信度均值
        """
        try:
            _conf = float(confidence)
        except (TypeError, ValueError) as e:
            silent_exc(e, where="nucleus.reasoning.SelfCalibrator::calibrate L72")
            return 0.0
        with self._lock:
            _offset = self._data.get(source, {}).get("offset", 0.0)
        _cal = _conf + _offset
        return round(max(0.0, min(100.0, _cal)), 1)

    def record_outcome(self, source: str, predicted_conf: float,
                       correct: bool) -> None:
        """记录一次「预测置信度 → 实际结果」的对照，反向修正校准偏移量。

        Args:
            source: 推理来源（如 causal_chain / inductive / analogical）
            predicted_conf: 预测时给出的置信度（0~100）
            correct: 实际是否正确（验证通过 / 反馈正面 = True）
        """
        try:
            _p = float(predicted_conf)
        except (TypeError, ValueError):
            _p = 50.0
        _actual = 100.0 if correct else 0.0
        _err = _actual - _p  # 实际 - 预测；负 = 过度自信
        with self._lock:
            _entry = self._data.setdefault(source, {"offset": 0.0, "samples": 0.0, "correct": 0.0})
            _samples = _entry.get("samples", 0.0)
            _correct = _entry.get("correct", 0.0)
            if _samples <= 0:
                _offset = _err * 0.5  # 首样本保守采纳一半
            else:
                _offset = _entry.get("offset", 0.0)
                _offset = (1 - self.EMA_ALPHA) * _offset + self.EMA_ALPHA * _err
            _offset = max(-self.OFFSET_BOUND, min(self.OFFSET_BOUND, _offset))
            _entry["offset"] = round(_offset, 3)
            _entry["samples"] = _samples + 1
            _entry["correct"] = _correct + (1.0 if correct else 0.0)
            # 后台日志：校准偏移量显著变化时记录（供观测校准演化）
            if _logger is not None and abs(_offset) >= 5.0:
                _log_debug(f"置信度校准: source={source}, 偏移→{round(_offset, 1)} "
                           f"(样本{int(_samples) + 1})")
        _now = time.time()
        if _now - self._last_save > 20:
            self.save()

    # ========== 状态查询 ==========

    def get_stats(self) -> dict[str, Any]:
        """统计信息（供日志/调试）"""
        with self._lock:
            return {
                "sources": len(self._data),
                "total_samples": sum(e.get("samples", 0) for e in self._data.values()),
                "calibration": {k: {"offset": v.get("offset", 0.0),
                                    "samples": int(v.get("samples", 0)),
                                    "accuracy": round(v.get("correct", 0) / v.get("samples", 1) * 100, 1)
                                    if v.get("samples", 0) else 0.0}
                               for k, v in self._data.items()},
            }

    # ========== 持久化 ==========

    def _load(self) -> None:
        try:
            if os.path.exists(self._save_path):
                _raw = safe_read_json(self._save_path, default={})
                self._data = _raw or {}
        except Exception as e:
            _logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._save_path), exist_ok=True)
            with self._lock:
                _dump = dict(self._data)
            safe_write_json(self._save_path, _dump, indent=1)
            self._last_save = time.time()
        except Exception as e:
            _logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")


# ========== 模块级单例 ==========
_calibrator: SelfCalibrator | None = None
_calibrator_lock = threading.Lock()


def get_self_calibrator() -> SelfCalibrator:
    """获取 SelfCalibrator 单例"""
    global _calibrator
    if _calibrator is None:
        with _calibrator_lock:
            if _calibrator is None:
                _calibrator = SelfCalibrator()
    return _calibrator



# ======================================================================
# ★第九批 任务1（B-3 置信度证据化）
#
# 背景：框架里有五十多处 `confidence = 0.9` 硬编码——它们跟真实证据毫无关系：
#   不管这次推理有没有依据、历史上这类推理对不对，一律 0.9。后果有二：
#     ① 没证据的推理也显得很确定，误导下游（本地推理明明 0.52，别处却恒 0.9）；
#     ② 置信度校准里的「历史推理相似度」恒为 0.5 占位（P1-5），校准形同虚设。
#
# 方案（与已有 SelfCalibrator 的 EMA 校准互补，不互相干扰）：
#     conf = base × hist_factor × evidence_factor
#       hist_factor     = 0.7 + 0.3 × 该推理类型的历史成功率   ∈ [0.7, 1.0]
#       evidence_factor = min(1.0, 0.8 + 0.07 × 证据条数)      ∈ [0.8, 1.0]
#   即：**证据充分 + 历史可靠 → 回到 base（0.9）；证据稀薄 → 如实下调**。
#   冷启动（无历史）用 0.7 成功率，不会一上来就压到很低，也不会假装满分。
#
# 零冲突：所有调用点统一走 evidence_confidence()；开关关闭时原值返回。
# ======================================================================

# ★第九批：测试隔离用默认目录（tmp/test_isolation.redirect_all 会重定向到 tmp/test_data）
_ISO_BASE_DIR: str | None = None


class EvidenceCalibrator:
    """按推理类型统计成功率 + 按证据条数打折的置信度计算器。"""

    # 冷启动默认成功率（没有历史样本时）
    COLD_START_RATE = 0.7
    # 达到该样本数后完全信任历史统计（此前与冷启动值做加权，抗小样本抖动）
    FULL_TRUST_SAMPLES = 10
    # 证据系数：基础 0.8，每条证据 +0.07，封顶 1.0（即 3 条证据就满）
    EVIDENCE_BASE = 0.8
    EVIDENCE_PER_ITEM = 0.07
    EVIDENCE_CAP = 1.0

    def __init__(self, base_dir: str | None = None):
        self._lock = threading.RLock()
        # ★第十一批 批次B 修复：_ISO_BASE_DIR 必须**优先于**默认目录生效。
        #   原写法先 `if base_dir is None: base_dir = <生产 data 目录>`，随后再判
        #   `if base_dir is None and _ISO_BASE_DIR` —— 此时 base_dir 已被赋值，
        #   条件恒为假，导致 tmp/test_isolation 设的 _ISO_BASE_DIR 从未生效，
        #   测试进程会把证据写到生产 data/self_calibration_evidence.json（隔离失效）。
        if base_dir is None and _ISO_BASE_DIR:
            base_dir = _ISO_BASE_DIR
        if base_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._base_dir = base_dir
        # 允许配置覆盖存储路径（config.CONFIDENCE_EVIDENCE_CONFIG.storage_path）
        #
        # ★第十一批 批次B 修复（假闭环根因之一）：配置里的 storage_path 形如
        #   "data/reasoning/self_calibration_evidence.json" —— **已含文件名**。
        #   原写法无条件 `os.path.join(base_dir, storage_path)` 再拼一次文件名，
        #   得到 ".../self_calibration_evidence.json/self_calibration_evidence.json"
        #   （把 .json 当目录），save() 时 open() 必抛 NotADirectoryError 并被
        #   try/except 吞掉 → 历史成功率**从未真正落盘**，每次重启都回到冷启动
        #   0.7，这正是「EvidenceCalibrator 假闭环」的物理原因。
        #   现按「是否已含 .json 文件名」分别处理。
        _save_path = os.path.join(base_dir, "self_calibration_evidence.json")
        try:
            import config as _cfg
            _p = getattr(_cfg, "CONFIDENCE_EVIDENCE_CONFIG", {}).get("storage_path")
            if _p:
                _p = str(_p)
                _cand = _p if os.path.isabs(_p) else os.path.join(base_dir, _p)
                _save_path = _cand if _cand.lower().endswith(".json") \
                    else os.path.join(_cand, "self_calibration_evidence.json")
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SelfCalibrator::__init__ L243")
        self._save_path = _save_path
        try:
            os.makedirs(os.path.dirname(self._save_path), exist_ok=True)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SelfCalibrator::__init__ L248")
        # {rtype: {"samples": int, "success": int}}
        self._buckets: dict[str, dict[str, float]] = {}
        self._load()

    # ---------- 历史成功率 ----------

    def success_rate(self, rtype: str = "generic") -> float:
        """该推理类型的历史成功率（含冷启动平滑）。"""
        with self._lock:
            _b = self._buckets.get(rtype or "generic")
            if not _b:
                return self.COLD_START_RATE
            _n = float(_b.get("samples", 0) or 0)
            if _n <= 0:
                return self.COLD_START_RATE
            _rate = float(_b.get("success", 0) or 0) / _n
            # 小样本平滑：样本越少越偏向冷启动值
            _w = min(1.0, _n / float(self.FULL_TRUST_SAMPLES))
            return round(self.COLD_START_RATE * (1 - _w) + _rate * _w, 4)

    def record(self, rtype: str = "generic", correct: bool = True) -> None:
        """记录一次推理结果（正确/错误），用于统计该类型的成功率。"""
        with self._lock:
            _b = self._buckets.setdefault(rtype or "generic", {"samples": 0.0, "success": 0.0})
            _b["samples"] = float(_b.get("samples", 0)) + 1
            if correct:
                _b["success"] = float(_b.get("success", 0)) + 1
        self.save()

    def record_weighted(self, rtype: str = "generic", correct: bool = True,
                        weight: float = 1.0) -> None:
        """按权重记录一次推理结果（★第十一批 任务2：三路回报源加权）。

        与 record() 的区别：samples/success 按 weight 累加而非固定 +1，
        使「推理经验库/用户纠错/大模型兜底」三路信号能按 0.5/0.3/0.2 的
        权重共同塑造历史成功率，而不是互相覆盖。
        """
        try:
            _w = float(weight)
        except Exception:
            _w = 1.0
        if _w <= 0:
            return
        with self._lock:
            _b = self._buckets.setdefault(rtype or "generic", {"samples": 0.0, "success": 0.0})
            _b["samples"] = float(_b.get("samples", 0)) + _w
            if correct:
                _b["success"] = float(_b.get("success", 0)) + _w
        self.save()

    # ---------- 证据强度 ----------

    @classmethod
    def evidence_strength(cls, evidence: Any) -> float:
        """把证据换算成系数：无证据 0.8，3 条及以上 1.0。"""
        _n = 0
        if evidence is None:
            _n = 0
        elif isinstance(evidence, bool):
            _n = 1 if evidence else 0
        elif isinstance(evidence, (int, float)):
            _n = max(0, int(evidence))
        elif isinstance(evidence, (list, tuple, set)):
            _n = len(evidence)
        elif isinstance(evidence, dict):
            # 字典按「非空值个数」计证据
            _n = sum(1 for _v in evidence.values() if _v not in (None, "", 0, [], {}))
        else:
            _n = 1
        return min(cls.EVIDENCE_CAP, cls.EVIDENCE_BASE + cls.EVIDENCE_PER_ITEM * _n)

    # ---------- 核心计算 ----------

    def compute(self, base: float, rtype: str = "generic",
                evidence: Any = None) -> float:
        """证据化置信度 = base × 历史成功率系数 × 证据强度系数。"""
        try:
            _base = float(base)
        except (TypeError, ValueError) as e:
            silent_exc(e, where="nucleus.reasoning.SelfCalibrator::compute L328")
            return 0.0
        _hist = 0.7 + 0.3 * self.success_rate(rtype)
        _ev = self.evidence_strength(evidence)
        _conf = _base * _hist * _ev
        _conf = max(0.0, min(1.0, _conf))
        _log_debug(f"置信度证据化: type={rtype}, base={_base}, "
                   f"hist={round(_hist, 3)}, ev={round(_ev, 3)} → {round(_conf, 3)}")
        return round(_conf, 4)

    # ---------- 历史推理相似度（P1-5 接线） ----------

    def history_similarity(self, question: str, rtype: str = "") -> float:
        """查推理经验库，返回与历史正确推理的相似度（0~1）。

        查不到/异常时回退到配置占位值（默认 0.5）并记 DEBUG——
        绝不因为经验库不可用而让置信度失真或抛异常。
        """
        _fallback = 0.5
        try:
            import config as _cfg
            _fallback = float(getattr(_cfg, "CONFIDENCE_EVIDENCE_CONFIG", {}).get(
                "history_similarity_fallback", 0.5) or 0.5)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SelfCalibrator::history_similarity L342")
        if not question:
            return _fallback
        try:
            from nucleus.reasoning.ReasoningExperienceIndexer import (
                get_reasoning_experience_indexer,
            )
            _hits = get_reasoning_experience_indexer().search_with_semantic(
                question, top_k=3) or []
            if not _hits:
                _log_debug(f"历史推理相似度: 无命中，回退{_fallback}")
                return _fallback
            _best = float(_hits[0].get("score", 0.0) or 0.0)
            _sim = max(0.0, min(1.0, _best))
            _log_debug(f"历史推理相似度: {_sim}(命中{len(_hits)}条, "
                       f"类型={_hits[0].get('match_type')})")
            return round(_sim, 4)
        except Exception as _e:
            _log_debug(f"历史推理相似度查询失败，回退{_fallback}: {_e}")
            return _fallback

    # ---------- 状态/持久化 ----------

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "types": len(self._buckets),
                "buckets": {k: {"samples": int(v.get("samples", 0)),
                                "success": int(v.get("success", 0)),
                                "rate": self.success_rate(k)}
                            for k, v in self._buckets.items()},
            }

    def _load(self) -> None:
        try:
            if os.path.exists(self._save_path):
                _raw = safe_read_json(self._save_path, default={})
                self._buckets = (_raw or {}).get("buckets", {}) or {}
        except Exception as _e:
            _log_debug(f"证据化校准加载失败，以空库启动: {_e}")

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._save_path), exist_ok=True)
            with self._lock:
                _dump = {"version": 1, "updated": time.time(), "buckets": self._buckets}
            _tmp = self._save_path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(_dump, f, ensure_ascii=False, indent=1)
            os.replace(_tmp, self._save_path)
        except Exception as _e:
            _log_debug(f"证据化校准落盘失败（内存数据未丢）: {_e}")

    def reset(self) -> None:
        with self._lock:
            self._buckets = {}
        try:
            if os.path.exists(self._save_path):
                os.remove(self._save_path)
        except Exception as e:
            silent_exc(e, where="nucleus.reasoning.SelfCalibrator::reset L411")


# ---------- 证据化校准单例 ----------

_evidence: EvidenceCalibrator | None = None
_evidence_lock = threading.Lock()


# ============ ★第十一批 任务2：三路回报源（0.5 / 0.3 / 0.2） ============

WEIGHT_LOCAL_INFERENCE = 0.5   # 回报源1：推理经验库（本地推理结果采纳情况）
WEIGHT_USER_CORRECTION = 0.3   # 回报源2：用户显式纠错
WEIGHT_LLM_FALLBACK = 0.2      # 回报源3：本地推理不足转大模型兜底

# 用户显式纠错语句（仅判定短句，避免长段里偶然出现"不对"被误判）
_CORRECTION_PATTERNS = (
    "不对", "错了", "不是这样", "不是这样的", "你说错", "说错了",
    "搞错了", "不正确", "错了吧", "不对吧", "答错了", "搞反了",
)
_CORRECTION_MAX_LEN = 80

# 上一轮本地推理追踪（供下一轮用户纠错时回标）
_last_local_infer: dict[str, Any] = {"rtype": None, "ts": 0.0}
_last_lock = threading.RLock()


def detect_user_correction(text: str) -> bool:
    """检测用户是否在做显式纠错（"不对"/"错了"/"不是这样"等）。

    只判定 ≤80 字的短句：长段提问里偶然出现"不对"不应被判为纠错。
    """
    if not text:
        return False
    _t = str(text).strip()
    if not _t or len(_t) > _CORRECTION_MAX_LEN:
        return False
    return any(_p in _t for _p in _CORRECTION_PATTERNS)


def mark_local_inference(rtype: str = "generic") -> None:
    """记录「本轮由本地推理作答」，供下一轮用户纠错时回标。"""
    with _last_lock:
        _last_local_infer["rtype"] = rtype or "generic"
        _last_local_infer["ts"] = time.time()


def feedback_local_inference(rtype: str = "generic", correct: bool = True) -> None:
    """回报源1：本地推理结果的正确性（权重 0.5）。"""
    try:
        _c = get_evidence_calibrator()
        _c.record_weighted(rtype or "generic", bool(correct), WEIGHT_LOCAL_INFERENCE)
        _logger.info(
            f"[置信度证据] 本地推理回报 rtype={rtype} correct={correct} → "
            f"历史成功率={_c.success_rate(rtype or 'generic')}")
    except Exception as e:
        _logger.debug(f"本地推理回报异常已忽略: {type(e).__name__}: {e}")


def feedback_user_correction(rtype: str | None = None) -> bool:
    """回报源2：用户显式纠错（权重 0.3），回标上一轮本地推理为错误。

    返回是否真的产生回标（无上一轮本地推理时返回 False，避免误伤）。
    """
    with _last_lock:
        _r = rtype or _last_local_infer.get("rtype")
        _last_local_infer["rtype"] = None
    if not _r:
        return False
    try:
        _c = get_evidence_calibrator()
        _c.record_weighted(_r, False, WEIGHT_USER_CORRECTION)
        _logger.warning(
            f"[置信度证据] 检测到用户纠错 → 回标 rtype={_r} 为错误，"
            f"历史成功率={_c.success_rate(_r)}")
        return True
    except Exception as e:
        _logger.debug(f"用户纠错回报异常已忽略: {type(e).__name__}: {e}")
        return False


def feedback_llm_fallback(rtype: str = "generic") -> None:
    """回报源3：本地推理不足转大模型兜底（权重 0.2），标记为未通过。"""
    try:
        _c = get_evidence_calibrator()
        _c.record_weighted(rtype or "generic", False, WEIGHT_LLM_FALLBACK)
        _logger.info(
            f"[置信度证据] 本地不足转大模型 rtype={rtype} → "
            f"历史成功率={_c.success_rate(rtype or 'generic')}")
    except Exception as e:
        _logger.debug(f"兜底回报异常已忽略: {type(e).__name__}: {e}")


def feedback_if_correction(user_text: str) -> bool:
    """对话入口调用：命中纠错语句则回标上一轮本地推理为错误。"""
    if not detect_user_correction(user_text):
        return False
    return feedback_user_correction()


def get_evidence_calibrator(base_dir: str | None = None) -> EvidenceCalibrator:
    """获取 EvidenceCalibrator 单例。"""
    global _evidence
    if _evidence is None:
        with _evidence_lock:
            if _evidence is None:
                _evidence = EvidenceCalibrator(base_dir=base_dir)
    return _evidence



def evidence_confidence(base: float, rtype: str = "generic",
                        evidence: Any = None) -> float:
    """★统一的证据化置信度入口（所有消除硬编码的调用点都走这里）。

    开关 ENABLE_CONFIDENCE_EVIDENCE 关闭时**原值返回**——保证零行为变化。

    Args:
        base: 原硬编码的名义置信度（如 0.9）
        rtype: 推理类型（symbolic / logic / rule / multi_step / knowledge …）
        evidence: 证据（条数/列表/字典），用于计算证据强度
    """
    try:
        import config as _cfg
        if not getattr(_cfg, "ENABLE_CONFIDENCE_EVIDENCE", False):
            return base
    except Exception:
        return base
    try:
        return get_evidence_calibrator().compute(base, rtype, evidence)
    except Exception:
        return base  # 任何异常都不许影响主链路
