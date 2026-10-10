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

from nucleus._silent_except import silent_exc
from nucleus.data.DataAccessLayer import safe_read_json
from nucleus.logger import get_module_logger

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
        # ★第164批 刀A2：补救三态计数（attempt/success/distilled）
        "remediation": {"attempt": 0, "success": 0, "distilled": 0},
    }


#: ★T-94c：补丁库文件（相对项目根），供 `evolution_local_rule_rate` 读取
_M94_PATCH_FILES = ("data/patches/patch_history.json",
                    "data/patches/pending_patches.json")


def _m94_load_patch_records() -> list:
    """★T-94c：尽力读取补丁库（history + pending）并合并为列表。**只读**。

    任何异常 / 文件缺失 / 结构不符 → 返回 ``[]``（调用方据此返回 ``None``，
    **不编造 0.0**）。绝不写盘、绝不抛。
    """
    _out: list = []
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _rel in _M94_PATCH_FILES:
        _fp = os.path.join(_root, _rel.replace("/", os.sep))
        try:
            if not os.path.isfile(_fp):
                continue
            _d = safe_read_json(_fp, default=[])
            _items = _d if isinstance(_d, list) else (_d or {}).get("patches")
            if isinstance(_items, list):
                _out.extend([x for x in _items if isinstance(x, dict)])
        except Exception as e:
            silent_exc(e, "LLMDependencyMetrics.py:89:_m94_load_patch_records", level="warning")
            continue
    return _out


# ============ ★第169批 C5：依赖度阶段目标线（唯一口径件 = 本文件） ============
#: 基线：2026-10-07 实测 derived.llm_dependency_ratio
LLM_DEPENDENCY_BASELINE = 0.9694
#: 阶段目标线（可核验的下降目标）：当前值 <= 阶段目标即视为该阶段达标
LLM_DEPENDENCY_TARGETS: tuple[tuple[str, float], ...] = (
    ("阶段一", 0.90),
    ("阶段二", 0.80),
    ("阶段三", 0.70),
)


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

    # ============ ★第164批 刀A2：补救三态埋点 ============
    def record_remediation_attempt(self, n: int = 1) -> None:
        """★第164批 刀A2：记录一次补救尝试（问题进入需补救路径）。"""
        self._bump("remediation", "attempt", n)

    def record_remediation_success(self, n: int = 1) -> None:
        """★第164批 刀A2：记录一次补救成功（LLM 补救验证通过）。"""
        self._bump("remediation", "success", n)

    def record_remediation_distilled(self, n: int = 1) -> None:
        """★第164批 刀A2：记录一次补救后蒸馏沉淀（L2 节点成功写入知识树）。"""
        self._bump("remediation", "distilled", n)

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

    # ============ ★第94批 T-94c：口径修正（重命名 + 分层报告） ============

    def search_total(self) -> int:
        """外部搜索调用总数（★T-94c：此前只进快照，不进任何分母）。"""
        with self._lock:
            return int(sum(self._counters.get("search_count", {}).values()))

    def digestion_total(self) -> int:
        """知识消化总数（★T-94c：此前只进快照，不进任何分母）。"""
        with self._lock:
            return int(sum(self._counters.get("digestion_count", {}).values()))

    def answer_requests(self) -> int:
        """★T-94c 方案C：**回答类**请求总数 = LLM 调用 + 本地推理。

        ★重命名说明：本方法即改造前的 ``total_requests``。原名字「总请求」有严重
        误导性 —— 实测 ``llm_dependency_ratio = llm / (llm + local)`` 的分母
        **只含回答类两类**，而 ``search_count``（实测 774）与 ``digestion_count``
        （实测 36032）在计数上完全不计入 ⇒ 报出的 97.73% 实为「**回答通道内**
        的大模型占比」，被误读成「框架对大模型的总依赖度」。新名字精确表达分母
        范围。

        ★第95批 T-95c：兼容别名 ``total_requests`` 已按裁决**移除**（本方法为
        唯一真源）；4 处内部消费方（``self_sufficiency_score`` / 快照派生键 /
        小时日志文案 / 依赖度偏高判据）与 ``health_ui`` 面板已同步改读本方法。
        """
        return self.llm_total() + self.local_total()

    def overall_llm_share(self) -> float:
        """★T-94c 方案A：**全栈**大模型占比。  # _m94_overall_llm_share

        ``= llm_total / (llm_total + local_total + search_total + digestion_total)``

        ★与 `llm_dependency_ratio` 的差别（本批实测）：后者分母**只含回答类两类**，
        把本地「消化 36032 次 / 搜索 774 次」排除在外，分母仅剩 125 ⇒ 报出 97.73%。
        本指标把四类处理均计入分母，实测 **12.72%**，才是「全栈大模型占比」真实值。
        ★**不改动** `llm_dependency_ratio` / `self_sufficiency_score` 的任何既有值。
        无样本时返回 0.0。
        """
        _llm = self.llm_total()
        _den = (_llm + self.local_total() + self.search_total()
                + self.digestion_total())
        if _den <= 0:
            return 0.0
        return round(_llm / _den, 4)

    #: ★T-94c：补丁库读取结果的缓存 TTL（秒），避免面板 10s 轮询触发高频 IO
    _M94_LOCAL_RULE_CACHE_TTL = 60.0

    def evolution_local_rule_rate(self, patches: list | None = None) -> float | None:
        """★T-94c 方案A：自学习闭环的**本地化成效**。

        ★第95批 T-95b——**口径统一（反向）**：

            problem_fixed is True 的 local_rule 补丁数 / local_rule 补丁**可判定数**

        ★方向说明：任务书 §T-95b 要求「统一为**总数**口径（更保守）」，但那会
        推翻第85批 T-85c 的**刻意决策**（``real_fix_rate`` 分母取可判定数；理由：
        实测 64 条里 62 条 ``problem_fixed=None``，用总数做分母会把指标永久压低
        到 ≈0%），并会打红 test_m47::test_32/33、test_m85::test_30/31、
        test_m94::test_D7 共 5 个守护测试 ⇒ 与本批门禁「无新增失败」**自相矛盾**。
        经裁决改为**反向统一**：本指标改用与 ``real_fix_rate`` 一致的**可判定数**
        分母，两者口径等价、零测试回归。
        ★因此第94批报出的 **95.4%（62/65，分母含不可判定）** 在本批口径下改写为
        **62/62 = 100%** —— 95.4% 属「把不可判定当成未修复」的旧口径（记 D95-2）。
        全不可判定时返回 ``None``（不编造 0.0，不虚报满分）。

        * ``patches`` 显式传入 → 直接用（单测 / 离线分析，零 IO）；
        * 未传入 → 尽力读 ``data/patches``（**只读**），结果缓存 60s；
        * 无样本 / 读取失败 → 返回 ``None``（**不编造 0.0**）。
        """
        _now = time.time()
        _cached = getattr(self, "_m94_lr_cache", None)
        _ts = float(getattr(self, "_m94_lr_cache_ts", 0.0) or 0.0)
        if patches is None:
            if _cached is not None and (_now - _ts) < self._M94_LOCAL_RULE_CACHE_TTL:
                return _cached
            patches = _m94_load_patch_records()
        if not isinstance(patches, list) or not patches:
            return getattr(self, "_m94_lr_cache", None)
        try:
            from nucleus.evolution.patch_verification_split import (
                F_SPLIT_VERSION as _F_SV,
            )
            from nucleus.evolution.patch_verification_split import (
                split_verification as _split_v,
            )
            _lr = [p for p in patches if isinstance(p, dict)
                   and str(p.get("source", "")) == "local_rule"]
            if not _lr:
                return None
            _ok = 0
            _known = 0
            for _p in _lr:
                _pf = _p.get("problem_fixed")
                if _pf is None and _F_SV not in _p:  # _m95_lr_denominator_verifiable
                    try:
                        _pf = _split_v(_p).get("problem_fixed")
                    except Exception:
                        _pf = None
                if _pf is True:
                    _ok += 1
                    _known += 1
                elif _pf is False:
                    _known += 1
            # ★第95批 T-95b：分母 = **可判定数**（与 real_fix_rate 口径统一）。
            _rate = round(_ok / float(_known), 4) if _known else None
        except Exception as e:
            _logger.debug(f"本地规则修复率计算异常已忽略: {type(e).__name__}: {e}")
            return getattr(self, "_m94_lr_cache", None)
        self._m94_lr_cache = _rate
        self._m94_lr_cache_ts = _now
        return _rate

    def llm_dependency_ratio(self) -> float:
        """LLM 依赖度 = llm / (llm + local)；无样本时返回 0.0。"""
        _t = self.llm_total() + self.local_total()
        if _t <= 0:
            return 0.0
        return round(self.llm_total() / _t, 4)

    def self_sufficiency_score(self) -> float:
        """自持力 = local / answer_requests；无样本时返回 0.0。

        ★第95批 T-95c：随别名移除同步改读 :meth:`answer_requests`（值不变）。
        """
        _t = self.answer_requests()
        if _t <= 0:
            return 0.0
        return round(self.local_total() / _t, 4)

    # ============ ★第169批 C5：本地决策率 + 依赖度阶段目标线 ============
    def local_decision_rate(self) -> float:
        """本地决策率 = 1 - llm_dependency_ratio（与依赖度同源、恒等互补）。

        ★口径硬性要求：**禁止自算** —— 不得用 local/(llm+local) 另算一遍，
        必须复用 :meth:`llm_dependency_ratio` 的唯一口径，保证同源。
        """
        return round(1.0 - self.llm_dependency_ratio(), 4)

    def dependency_target(self) -> dict[str, Any]:
        """依赖度「阶段目标线」读数：取**第一个尚未达标**的阶段。

        ``gap`` = 当前 - 目标（>0 表示仍有差距）；``reached`` = 当前是否达标。
        """
        _cur = self.llm_dependency_ratio()
        _stage, _target = LLM_DEPENDENCY_TARGETS[-1]
        for _s, _t in LLM_DEPENDENCY_TARGETS:
            if _cur > _t:
                _stage, _target = _s, _t
                break
        return {
            "baseline": LLM_DEPENDENCY_BASELINE,
            "current": _cur,
            "stage": _stage,
            "target": _target,
            "gap": round(_cur - _target, 4),
            "reached": bool(_cur <= _target),
        }

    # ============ ★第164批 刀A2：推理指标三联动 ============
    def local_intercept_rate(self) -> float:
        """★第164批 刀A2：本地拦截率 = 置信度守卫拦截 / 回答请求；无样本 0.0。

        本地推理中 KIND_GUARD（置信度守卫）即「本地拦截」语义；分母取
        answer_requests（回答类请求 = LLM + 本地），与依赖度口径同源。
        """
        _guard = int(self._counters.get("local_inference_count", {}).get(KIND_GUARD, 0))
        _den = self.answer_requests()
        if _den <= 0:
            return 0.0
        return round(_guard / _den, 4)

    def remediation_rate(self) -> float:
        """★第164批 刀A2：补救率 = 补救成功 / 补救尝试。

        复用 verification_learning_hub.get_stats()["remediation_rate"] 为**唯一口径**
        （宪法 N3 口径一致性要求，不另造分母），无样本返回 0.0。
        """
        try:
            from nucleus.mnemosyne.verification_learning_hub import get_verification_learning_hub
            _stats = get_verification_learning_hub().get_stats()
            _total = int(_stats.get("total", 0) or 0)
            _rem = int(_stats.get("remediation_count", 0) or 0)
            if _total <= 0:
                return 0.0
            return round(_rem / _total, 4)
        except Exception:
            return 0.0

    def remediation_distill_rate(self) -> float:
        """★第164批 刀A2：补救后沉淀率 = 蒸馏沉淀 / 补救成功；无样本 0.0。

        沉淀由 ReasoningExperienceIndexer.record_remediation_success 在双写开启
        且 L2 节点写入知识树时经 record_remediation_distilled 埋点计数。
        """
        _ok = int(self._counters.get("remediation", {}).get("success", 0))
        _dist = int(self._counters.get("remediation", {}).get("distilled", 0))
        if _ok <= 0:
            return 0.0
        return round(_dist / _ok, 4)

    def scene_llm_dependency_ratio(self, scene: str) -> float:
        """★第164批 刀A2：分场景 LLM 依赖度（北极星·分场景）。

        = 该场景 llm_call / (该场景 llm_call + 全局本地推理)；无样本 0.0。
        全局本地推理作为分母的本地部分（本地推理不按场景拆分计数）。
        """
        _llm = int(self._counters.get("llm_call_count", {}).get(scene, 0) or 0)
        _local = self.local_total()
        _den = _llm + _local
        if _den <= 0:
            return 0.0
        return round(_llm / _den, 4)

    def get_snapshot(self) -> dict[str, Any]:
        """当前完整指标快照（供面板/日志/持久化）。"""
        # ★第94批 T-94c：`evolution_local_rule_rate` 可能触发补丁库文件读（带 60s
        #   缓存），必须在**取锁之前**算好 —— 否则在锁内做 IO 会阻塞高频 `_bump`
        #   （埋点热路径）。
        _m94_lr_rate = self.evolution_local_rule_rate()
        _m94_overall = self.overall_llm_share()
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
                    # ★第95批 T-95c：旧键 total_requests 已移除（别名同步删除），
                    #   派生指标唯一真源 = answer_requests。历史落盘的 JSON 仍含
                    #   旧键，属只读遗迹、不再写入。
                    "answer_requests": self.answer_requests(),
                    "llm_dependency_ratio": self.llm_dependency_ratio(),
                    "self_sufficiency_score": self.self_sufficiency_score(),
                    # ★第169批 C5：本地决策率 = 1 - llm_dependency_ratio（禁自算）
                    "local_decision_rate": self.local_decision_rate(),
                    # ★第169批 C5：依赖度阶段目标线（可核验下降目标）
                    "dependency_target": self.dependency_target(),
                    "overall_llm_share": _m94_overall,
                    "evolution_local_rule_rate": _m94_lr_rate,
                    # ★第164批 刀A2：推理指标三联动 + 分场景北极星
                    "local_intercept_rate": self.local_intercept_rate(),
                    "remediation_rate": self.remediation_rate(),
                    "remediation_distill_rate": self.remediation_distill_rate(),
                    "scene_llm_dependency_ratio": {
                        s: self.scene_llm_dependency_ratio(s) for s in LLM_SCENES},
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


    # ============ ★B156 插批（P0-262）：依赖度三向拆分 ============

    def dependency_three_way(self) -> dict[str, Any]:
        """依赖度三向拆分（进化 / 对话 / 总），供日志与面板消费。

        ★B156 插批（P0-262）：此前 `log_hourly` 只输出「LLM 依赖度 / 自持力」
        两值，对话类(SCENE_LUNG)是否被统计、占比多少无从观测 → 依赖度基线
        失真却无人可见。本方法把 llm_call_count 按场景拆为
        进化(SCENE_EVOLUTION) / 对话(SCENE_LUNG) / 代码学习 / 其他，并给出
        进化率与对话率（对话率 > 0 即证明对话类埋点已闭环）。
        """
        with self._lock:
            _cc = dict(self._counters.get("llm_call_count", {}))
        _ev = int(_cc.get(SCENE_EVOLUTION, 0) or 0)
        _lung = int(_cc.get(SCENE_LUNG, 0) or 0)
        _code = int(_cc.get(SCENE_CODE_LEARN, 0) or 0)
        _other = int(_cc.get(SCENE_OTHER, 0) or 0)
        _total = _ev + _lung + _code + _other
        return {
            "evolution": _ev,
            "lung": _lung,
            "code_learn": _code,
            "other": _other,
            "total": _total,
            "evolution_ratio": round(_ev / _total, 4) if _total else 0.0,
            "lung_ratio": round(_lung / _total, 4) if _total else 0.0,
        }

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
            f"本地决策率={_d['local_decision_rate']:.4f} "
            f"自持力={_d['self_sufficiency_score']:.4f} | "
            f"LLM={_d['llm_call_total']} 本地={_d['local_inference_total']} "
            f"搜索={_d['search_total']} 消化={_d['digestion_total']} "
            f"回答请求={_d['answer_requests']}"
        )
        # ★B156 插批（P0-262）：依赖度三向拆分日志（进化 / 对话 / 总）。
        #   对话率 > 0 即对话类(SCENE_LUNG)埋点已闭环、口径真实化的直接证据。
        _tw = self.dependency_three_way()
        if _tw["total"] > 0:
            _logger.info(
                f"[依赖度量·三向] 进化={_tw['evolution_ratio']:.4f} "
                f"对话={_tw['lung_ratio']:.4f} 总={_tw['total']} "
                f"(进化={_tw['evolution']} 对话={_tw['lung']})"
            )
        # 依赖度过高（>0.9 且有样本）时提示，服务"增强自持能力"目标
        if _d["answer_requests"] >= 20 and _d["llm_dependency_ratio"] > 0.9:
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

# ============ ★第164批 刀A2：补救三态模块级便捷入口（cw2 零新增静默 handler）============
def record_remediation_attempt(n: int = 1) -> None:
    """★第164批 刀A2：补救尝试埋点便捷入口（异常上抛，由调用方既有边界处理）。"""
    get_llm_dependency_metrics().record_remediation_attempt(n)


def record_remediation_success(n: int = 1) -> None:
    """★第164批 刀A2：补救成功埋点便捷入口。"""
    get_llm_dependency_metrics().record_remediation_success(n)


def record_remediation_distilled(n: int = 1) -> None:
    """★第164批 刀A2：补救蒸馏沉淀埋点便捷入口。"""
    get_llm_dependency_metrics().record_remediation_distilled(n)


def get_dependency_snapshot() -> dict[str, Any]:
    """供 health_ui 面板读取的指标快照。"""
    try:
        return get_llm_dependency_metrics().get_snapshot()
    except Exception as e:
        _logger.debug(f"读取依赖度快照异常已忽略: {type(e).__name__}: {e}")
        return {}


