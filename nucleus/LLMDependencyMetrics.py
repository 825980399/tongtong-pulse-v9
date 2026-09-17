# -*- coding: utf-8 -*-
"""
LLMDependencyMetrics.py —— LLM依赖度指标

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 量化框架对大模型的依赖程度与调用分布
机制: 基于LLMDependencyMetrics类实现，包含10个核心方法
定位: 自省监测层
"""

import json
import os
import threading
import time
from typing import Any

from nucleus.logger import get_module_logger
from nucleus.data.DataAccessLayer import safe_read_json


_logger = get_module_logger("LLMDependencyMetrics")

# 测试隔离：tmp/test_isolation 的 redirect_all 会 patch 本模块的该变量
_ISO_BASE_DIR: str | None = None

# ============ 场景/类别常量（防拼写漂移） ============

# LLM 调用场景
SCENE_LUNG = "肺回答"
SCENE_CODE_LEARN = "代码学习"
SCENE_EVOLUTION = "自主进化"
SCENE_OTHER = "其他"
LLM_SCENES = (SCENE_LUNG, SCENE_CODE_LEARN, SCENE_EVOLUTION, SCENE_OTHER)

# 本地推理类别
KIND_RULE = "规则通道"
KIND_SIMPLE = "本地简单回答"
KIND_GUARD = "置信度守卫"
LOCAL_KINDS = (KIND_RULE, KIND_SIMPLE, KIND_GUARD)

# 外部搜索类别
SEARCH_HEADLESS = "无头浏览器"
SEARCH_WIKI = "百科"
SEARCH_RSS = "RSS"
SEARCH_KINDS = (SEARCH_HEADLESS, SEARCH_WIKI, SEARCH_RSS)

# 知识消化类别
DIGEST_KNOWLEDGE = "知识消化"
DIGEST_FILE = "文件消化"

HOUR_SECONDS = 3600
_HISTORY_MAX = 168  # 保留最近 168 个小时快照（7 天）


def _blank_counters() -> dict[str, dict[str, int]]:
    return {
        "llm_call_count": {k: 0 for k in LLM_SCENES},
        "local_inference_count": {k: 0 for k in LOCAL_KINDS},
        "search_count": {k: 0 for k in SEARCH_KINDS},
        "digestion_count": {DIGEST_KNOWLEDGE: 0, DIGEST_FILE: 0},
    }


class LLMDependencyMetrics:
    """LLM 依赖度量器：4 类计数器 + 依赖度 / 自持力派生指标。"""

    DEFAULT_FILE = "llm_dependency.json"

    def __init__(self, base_dir: str | None = None, auto_hourly_log: bool = True):
        self._lock = threading.RLock()
        if base_dir is None:
            base_dir = _ISO_BASE_DIR  # 测试隔离优先
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "metrics",
            )
        self._base_dir = base_dir
        try:
            os.makedirs(self._base_dir, exist_ok=True)
        except Exception as e:
            _logger.warning(f"指标目录创建失败，降级为仅内存计数: {type(e).__name__}: {e}")
        self._save_path = os.path.join(self._base_dir, self.DEFAULT_FILE)

        self._counters: dict[str, dict[str, int]] = _blank_counters()
        self._started_at: str = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._last_updated: str = self._started_at
        self._history: list[dict[str, Any]] = []
        self._load()

        if auto_hourly_log:
            self._ensure_hourly_thread()

    # ============ 计数入口 ============

    def record_llm_call(self, scene: str = SCENE_OTHER, n: int = 1) -> None:
        """记录一次大模型调用（在真实调用出口埋点）。"""
        self._bump("llm_call_count", scene if scene in LLM_SCENES else SCENE_OTHER, n)

    def record_local_inference(self, kind: str = KIND_SIMPLE, n: int = 1) -> None:
        """记录一次本地推理（规则通道 / 简单问题本地回答 / 置信度守卫）。"""
        self._bump("local_inference_count", kind if kind in LOCAL_KINDS else KIND_SIMPLE, n)

    def record_search(self, kind: str = SEARCH_HEADLESS, n: int = 1) -> None:
        """记录一次外部搜索（无头浏览器 / 百科 / RSS）。"""
        self._bump("search_count", kind if kind in SEARCH_KINDS else SEARCH_HEADLESS, n)

    def record_digestion(self, kind: str = DIGEST_KNOWLEDGE, n: int = 1) -> None:
        """记录一次知识消化。"""
        self._bump("digestion_count", kind, n)

    def _bump(self, group: str, key: str, n: int = 1) -> None:
        with self._lock:
            bucket = self._counters.setdefault(group, {})
            bucket[key] = int(bucket.get(key, 0)) + int(n)
            self._last_updated = time.strftime("%Y-%m-%dT%H:%M:%S")
            # 轻量：不每次落盘，避免高频 IO 拖慢主链路（由小时线程 + 显式 save 落盘）

    # ============ 派生指标 ============

    def llm_total(self) -> int:
        with self._lock:
            return int(sum(self._counters.get("llm_call_count", {}).values()))

    def local_total(self) -> int:
        with self._lock:
            return int(sum(self._counters.get("local_inference_count", {}).values()))

    def total_requests(self) -> int:
        """回答类请求总数 = LLM 调用 + 本地推理（与依赖度分母口径一致）。"""
        return self.llm_total() + self.local_total()

    def llm_dependency_ratio(self) -> float:
        """LLM 依赖度 = llm / (llm + local)；无样本时返回 0.0。"""
        _t = self.llm_total() + self.local_total()
        if _t <= 0:
            return 0.0
        return round(self.llm_total() / _t, 4)

    def self_sufficiency_score(self) -> float:
        """自持力 = local / total_requests；无样本时返回 0.0。"""
        _t = self.total_requests()
        if _t <= 0:
            return 0.0
        return round(self.local_total() / _t, 4)

    def get_snapshot(self) -> dict[str, Any]:
        """当前完整指标快照（供面板/日志/持久化）。"""
        with self._lock:
            return {
                "started_at": self._started_at,
                "last_updated": self._last_updated,
                "counters": {g: dict(v) for g, v in self._counters.items()},
                "derived": {
                    "llm_call_total": self.llm_total(),
                    "local_inference_total": self.local_total(),
                    "search_total": int(sum(self._counters.get("search_count", {}).values())),
                    "digestion_total": int(sum(self._counters.get("digestion_count", {}).values())),
                    "total_requests": self.total_requests(),
                    "llm_dependency_ratio": self.llm_dependency_ratio(),
                    "self_sufficiency_score": self.self_sufficiency_score(),
                },
                "history": list(self._history),
            }

    # ============ 持久化 ============

    def save(self) -> bool:
        """落盘到人可读 JSON。"""
        try:
            with self._lock:
                _data = self.get_snapshot()
            _tmp = self._save_path + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(_data, f, ensure_ascii=False, indent=2)
            os.replace(_tmp, self._save_path)
            return True
        except Exception as e:
            _logger.debug(f"指标落盘异常已忽略: {type(e).__name__}: {e}")
            return False

    def _load(self) -> None:
        if not os.path.exists(self._save_path):
            return
        try:
            _d = safe_read_json(self._save_path, default={})
            if not isinstance(_d, dict):
                return
            with self._lock:
                self._started_at = _d.get("started_at", self._started_at)
                for g, v in (_d.get("counters") or {}).items():
                    if isinstance(v, dict):
                        self._counters.setdefault(g, {}).update(
                            {k: int(x) for k, x in v.items()})
                _h = _d.get("history")
                if isinstance(_h, list):
                    self._history = _h[-_HISTORY_MAX:]
        except Exception as e:
            _logger.debug(f"指标加载异常已忽略（按空计数续跑）: {type(e).__name__}: {e}")

    def reset_metrics(self, period: str = "all") -> bool:
        """重置计数器。

        period: "day" 仅清 24h 内的小时快照 / "week" 仅清 7 天内 / "all" 全清（默认）。
        """
        try:
            with self._lock:
                if period == "day":
                    _cut = time.time() - 24 * HOUR_SECONDS
                    self._history = [h for h in self._history
                                     if float(h.get("ts_epoch", 0) or 0) >= _cut]
                elif period == "week":
                    _cut = time.time() - 7 * 24 * HOUR_SECONDS
                    self._history = [h for h in self._history
                                     if float(h.get("ts_epoch", 0) or 0) >= _cut]
                else:
                    self._counters = _blank_counters()
                    self._history = []
                    self._started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._last_updated = time.strftime("%Y-%m-%dT%H:%M:%S")
            _logger.info(f"[依赖度量] 已重置计数器 period={period}")
            return self.save()
        except Exception as e:
            _logger.warning(f"重置指标异常已忽略（需关注）: {type(e).__name__}: {e}")
            return False

    # ============ 每小时日志 ============

    _hourly_started = False

    def _ensure_hourly_thread(self) -> None:
        if LLMDependencyMetrics._hourly_started:
            return
        LLMDependencyMetrics._hourly_started = True

        def _loop():
            while True:
                try:
                    time.sleep(HOUR_SECONDS)
                    self.log_hourly()
                except Exception as e:
                    _logger.debug(f"小时指标线程异常已忽略: {type(e).__name__}: {e}")

        _t = threading.Thread(target=_loop, name="LLMDepMetrics-Hourly", daemon=True)
        _t.start()

    def log_hourly(self) -> None:
        """记录并输出一次小时级依赖度指标。"""
        _snap = self.get_snapshot()
        _d = _snap["derived"]
        with self._lock:
            self._history.append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "ts_epoch": time.time(),
                **_d,
            })
            self._history = self._history[-_HISTORY_MAX:]
        self.save()
        _logger.info(
            f"[依赖度量·小时] LLM依赖度={_d['llm_dependency_ratio']:.4f} "
            f"自持力={_d['self_sufficiency_score']:.4f} | "
            f"LLM={_d['llm_call_total']} 本地={_d['local_inference_total']} "
            f"搜索={_d['search_total']} 消化={_d['digestion_total']} "
            f"总请求={_d['total_requests']}"
        )
        # 依赖度过高（>0.9 且有样本）时提示，服务"增强自持能力"目标
        if _d["total_requests"] >= 20 and _d["llm_dependency_ratio"] > 0.9:
            _logger.warning(
                f"[依赖度量] 大模型依赖度偏高（{_d['llm_dependency_ratio']:.4f}），"
                f"建议增强本地推理覆盖"
            )


# ============ 单例与便捷函数 ============

_metrics: LLMDependencyMetrics | None = None
_metrics_lock = threading.RLock()


def get_llm_dependency_metrics() -> LLMDependencyMetrics:
    """获取进程内共享的 LLM 依赖度量器。"""
    global _metrics
    with _metrics_lock:
        if _metrics is None:
            _metrics = LLMDependencyMetrics()
        return _metrics


def reset_llm_dependency_metrics() -> None:
    """重置单例（测试用）。"""
    # 动态调用注意：本函数被测试隔离夹具 tmp/test_isolation.py 以字符串元组
    # ("nucleus.LLMDependencyMetrics", "reset_llm_dependency_metrics") 动态引用，
    # 用于测试后重置模块级单例状态；死代码扫描器因排除 tmp/ 而漏报，请勿删除。
    global _metrics
    with _metrics_lock:
        _metrics = None


def record_llm_call(scene: str = SCENE_OTHER, n: int = 1) -> None:
    """记录一次大模型调用（埋点便捷入口，异常绝不上抛影响主链路）。"""
    try:
        get_llm_dependency_metrics().record_llm_call(scene, n)
    except Exception as e:
        _logger.debug(f"LLM调用埋点异常已忽略: {type(e).__name__}: {e}")


def record_local_inference(kind: str = KIND_SIMPLE, n: int = 1) -> None:
    """记录一次本地推理。"""
    try:
        get_llm_dependency_metrics().record_local_inference(kind, n)
    except Exception as e:
        _logger.debug(f"本地推理埋点异常已忽略: {type(e).__name__}: {e}")


def record_search(kind: str = SEARCH_HEADLESS, n: int = 1) -> None:
    """记录一次外部搜索。"""
    try:
        get_llm_dependency_metrics().record_search(kind, n)
    except Exception as e:
        _logger.debug(f"外部搜索埋点异常已忽略: {type(e).__name__}: {e}")


def record_digestion(kind: str = DIGEST_KNOWLEDGE, n: int = 1) -> None:
    """记录一次知识消化。"""
    try:
        get_llm_dependency_metrics().record_digestion(kind, n)
    except Exception as e:
        _logger.debug(f"知识消化埋点异常已忽略: {type(e).__name__}: {e}")


def get_dependency_snapshot() -> dict[str, Any]:
    """供 health_ui 面板读取的指标快照。"""
    try:
        return get_llm_dependency_metrics().get_snapshot()
    except Exception as e:
        _logger.debug(f"读取依赖度快照异常已忽略: {type(e).__name__}: {e}")
        return {}


def log_dependency_now() -> None:
    """立即输出一次依赖度指标（供手动触发/启动时观测）。"""
    try:
        get_llm_dependency_metrics().log_hourly()
    except Exception as e:
        _logger.debug(f"输出依赖度指标异常已忽略: {type(e).__name__}: {e}")
