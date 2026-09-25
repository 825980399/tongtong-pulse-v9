# -*- coding: utf-8 -*-
"""
IntentChannels.py —— 意图通道

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: QICA意图分类的多通道定义与权重
机制: 大型模块（1134行），包含3个类、10个核心方法，采用分层架构实现
定位: 意图分类层
"""

from nucleus._silent_except import silent_exc
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from collections.abc import Callable

import config
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json


# ========== 路径与常量 ==========
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_HERE))

# ========== 24 个意图类型（原 16 + 星轨补充 8） ==========
ALL_INTENTS = (
    # —— 原有 16 ——
    "身份确认", "关系查询", "知识查询", "概念解释", "规则查阅", "技术推理",
    "创造性思考", "深度分析", "对比分析", "情感表达", "情感问候", "系统命令",
    "一般对话", "状态查询", "健康检查", "元认知报告",
    # —— 新增 8 ——
    "任务执行", "记忆回想", "学习成长", "时间日程", "搜索获取", "追问澄清",
    "否定质疑", "请求帮助",
)

# ========== 24 意图 → 6 路径（融合层内部抽象） ==========
INTENT_TO_PATH = {
    "身份确认": "identity",
    "关系查询": "identity",
    "情感表达": "identity",
    "情感问候": "identity",
    "知识查询": "knowledge_retrieve",
    "概念解释": "knowledge_retrieve",
    "记忆回想": "knowledge_retrieve",
    "搜索获取": "knowledge_retrieve",
    "一般对话": "knowledge_retrieve",
    "规则查阅": "rule_reason",
    "系统命令": "rule_reason",
    "状态查询": "rule_reason",
    "时间日程": "rule_reason",
    "健康检查": "rule_reason",
    "元认知报告": "rule_reason",
    "学习成长": "rule_reason",
    "技术推理": "multi_step",
    "深度分析": "multi_step",
    "对比分析": "multi_step",
    "任务执行": "multi_step",
    "请求帮助": "multi_step",
    "创造性思考": "contemplation",
    "追问澄清": "contemplation",
    "否定质疑": "contemplation",
}

# ========== 6 路径 → 8 method（方案A：对外仍输出现有 method） ==========
PATH_TO_METHOD = {
    "knowledge_retrieve": "knowledge_retrieve",
    "rule_reason": "rule_reason",
    "multi_step": "multi_step_execute",
    "contemplation": "deep_think",
    "identity": "rule_reason",
    "llm_fallback": None,  # 不输出 method，由调用方标记 need_llm
}

# ========== 24 意图 → 8 method（保留原 _intent_to_method 取值，新增意图按星轨表） ==========
INTENT_TO_METHOD = {
    "身份确认": "rule_reason",
    "关系查询": "rule_reason",
    "情感表达": "rule_reason",
    "情感问候": "rule_reason",
    "知识查询": "knowledge_retrieve",
    "概念解释": "knowledge_retrieve",
    "规则查阅": "rule_reason",
    "技术推理": "cognitive_compute",
    "创造性思考": "deep_think",
    "深度分析": "multi_branch_deep_think",
    "对比分析": "multi_step_execute",
    "系统命令": "rule_reason",
    "一般对话": "knowledge_retrieve",
    "状态查询": "rule_reason",
    "健康检查": "health_check",
    "元认知报告": "meta_cognitive_report",
    "任务执行": "multi_step_execute",
    "记忆回想": "knowledge_retrieve",
    "学习成长": "meta_cognitive_report",
    "时间日程": "rule_reason",
    "搜索获取": "knowledge_retrieve",
    "追问澄清": "deep_think",
    "否定质疑": "deep_think",
    "请求帮助": "multi_step_execute",
}

# ========== 24 意图 → 默认知识路径（供 _resolve_knowledge_paths 起点） ==========
# 原有 16 个意图的路径沿用 _intent_to_method 中的定义；新增 8 个意图按语义归属设定。
INTENT_DEFAULT_PATHS = {
    "身份确认": ["/身份/自我", "/身份/使命"],
    "关系查询": ["/身份/家庭"],
    "情感表达": ["/身份/自我"],
    "情感问候": ["/身份/自我"],
    "知识查询": ["/知识", "/技术"],
    "概念解释": ["/知识", "/技术"],
    "规则查阅": ["/自我/架构", "/本能"],
    "技术推理": ["/技术/架构", "/技术/编程"],
    "创造性思考": ["/知识/创新", "/自我/架构"],
    "深度分析": ["/自我/架构", "/知识"],
    "对比分析": ["/知识", "/技术"],
    "系统命令": ["/自我/状态"],
    "一般对话": ["/知识"],
    "状态查询": ["/自我/状态"],
    "健康检查": ["/自我/状态"],
    "元认知报告": ["/自我/状态", "/自我/架构"],
    "任务执行": ["/自我/状态", "/技术"],
    "记忆回想": ["/知识", "/自我/架构"],
    "学习成长": ["/自我/状态", "/自我/架构"],
    "时间日程": ["/自我/状态"],
    "搜索获取": ["/知识", "/技术"],
    "追问澄清": ["/知识", "/自我/架构"],
    "否定质疑": ["/知识", "/自我/架构"],
    "请求帮助": ["/知识", "/自我/架构"],
}

# ========== 6 路径 → QICA channel（决定超时预算） ==========
PATH_TO_CHANNEL = {
    "identity": "fast",
    "rule_reason": "fast",
    "knowledge_retrieve": "knowledge",
    "multi_step": "deep",
    "contemplation": "deep",
    "llm_fallback": "deep",
}

# ========== 实体 → 意图（通道④） ==========
_ENTITY_PERSON = ("小林", "星轨", "路灯", "曈曈", "任宥曈")
_ENTITY_SYSTEM = ("PulseNet", "pulsenet", "脉冲场", "信息场", "知识树", "新人类")

# ========== 复杂度信号（通道⑦） ==========
_STRUCT_MARKERS = ("因为", "所以", "但是", "如果", "那么", "而且", "首先", "其次",
                   "最后", "一方面", "另一方面", "综上")
_ABSTRACT_MARKERS = ("本质", "意义", "价值", "原理", "机制", "逻辑", "关系",
                     "规律", "哲学", "概念", "抽象", "维度")


def _default_log(level: Any, msg: str) -> None:
    """无日志器时的兜底（测试环境用）。"""


def _norm_scores(scores: dict[str, float]) -> dict[str, float]:
    """把通道原始分做 min-max 归一化到 [0,1]，增强意图间区分度。

    全零或全等时返回均匀分布，避免除零。
    """
    if not scores:
        return {k: 0.0 for k in ALL_INTENTS}
    vals = list(scores.values())
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-8:
        return {k: (0.5 if v > 1e-8 else 0.0) for k, v in scores.items()}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def _zero_scores() -> dict[str, float]:
    return {k: 0.0 for k in ALL_INTENTS}


# ==================================================================
# 原型向量库
# ==================================================================
class IntentPrototypeStore:
    """意图原型向量库（单例，懒加载，只读）。

    向量已在构建时 L2 归一化，运行时算余弦只需点积。
    模型不可用时 degrade 为空库，语义通道返回零分，由其他通道兜底。
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._loaded = False
        self._names: list[str] = []
        self._matrix = None  # lazy numpy array
        self._dim = 0
        self._load_error = ""

    @classmethod
    def get_instance(cls) -> "IntentPrototypeStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def load(self, path: str | None = None) -> bool:
        """加载原型库 JSON。可重复调用，已加载则跳过。"""
        if self._loaded:
            return self._matrix is not None
        with self._lock:
            if self._loaded:
                return self._matrix is not None
            try:
                _p = path or getattr(config, "QICA_INTENT_PROTOTYPES_PATH",
                                     "data/qica/intent_prototypes.json")
                if not os.path.isabs(_p):
                    _p = os.path.join(_PROJECT_ROOT, _p)
                if not os.path.exists(_p):
                    self._load_error = "原型库文件不存在: %s" % _p
                    self._loaded = True
                    return False
                payload = safe_read_json(_p, default={})
                intents = payload.get("intents", {})
                if not intents:
                    self._load_error = "原型库为空"
                    self._loaded = True
                    return False
                import numpy as np
                self._names = list(intents.keys())
                self._matrix = np.asarray(
                    [intents[n] for n in self._names], dtype="float32")
                self._dim = int(payload.get("dim", self._matrix.shape[1]))
                self._loaded = True
                return True
            except Exception as e:
                self._load_error = "%s: %s" % (type(e).__name__, e)
                self._loaded = True
                return False

    @property
    def available(self) -> bool:
        self.load()
        return self._matrix is not None

    @property
    def error(self) -> str:
        return self._load_error

    def similarities(self, vector: Any) -> dict[str, float]:
        """用已编码的问题向量算与各意图原型的余弦相似度。

        Args:
            vector: 已编码的问题向量（内部会再归一化，调用方无需处理）

        Returns:
            {意图: 余弦相似度}；不可用时返回空 dict
        """
        if not self.available or vector is None:
            return {}
        try:
            import numpy as np
            v = np.asarray(vector, dtype="float32")
            n = float(np.linalg.norm(v))
            if n < 1e-8:
                return {}
            v = v / n
            sims = self._matrix @ v
            return {name: float(sims[i]) for i, name in enumerate(self._names)}
        except Exception:
            return {}


# ==================================================================
# 错误反馈闭环（任务1.4）
# ==================================================================
class ClassificationFeedback:
    """分类反馈存储：记录每次分类是否与实际执行路径一致。

    数据落盘 data/qica/classification_feedback.json（受
    config.ENABLE_QICA_FEEDBACK_LEARNING 控制，默认关闭）。
    连续 3 次判错的意图会触发原型微调 —— 但★只在内存中生效，
    不回写原型库 JSON，避免自动学习污染人工复核过的原型。
    """

    ERROR_TRIGGER = 3          # 连续判错触发阈值（任务书要求）
    ADJUST_ALPHA = 0.05        # 原型微调步长

    def __init__(self, path: str | None = None) -> None:
        self._path = path or getattr(
            config, "QICA_CLASSIFICATION_FEEDBACK_PATH",
            "data/qica/classification_feedback.json")
        if not os.path.isabs(self._path):
            self._path = os.path.join(_PROJECT_ROOT, self._path)
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"version": "v1.0", "intents": {}, "adjust_events": []}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                if os.path.exists(self._path):
                    _d = safe_read_json(self._path, default={})
                    if isinstance(_d, dict) and isinstance(_d.get("intents"), dict):
                        self._data = _d
                        self._data.setdefault("adjust_events", [])
            except Exception:
                pass  # 反馈数据损坏不影响主流程
            self._loaded = True

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            safe_write_json(self._path, self._data, indent=2)
        except Exception as e:
            silent_exc(e, "IntentChannels.py:320:save", level="warning")

    def record(self, intent: str, ok: bool, actual_intent: str | None = None) -> dict:
        """记录一次分类反馈，返回该意图的最新统计。"""
        self.load()
        with self._lock:
            st = self._data["intents"].setdefault(
                intent, {"total": 0, "ok": 0, "consecutive_errors": 0, "last_actual": ""})
            st["total"] = int(st.get("total", 0)) + 1
            if ok:
                st["ok"] = int(st.get("ok", 0)) + 1
                st["consecutive_errors"] = 0
            else:
                st["consecutive_errors"] = int(st.get("consecutive_errors", 0)) + 1
                if actual_intent:
                    st["last_actual"] = actual_intent
            return dict(st)

    def stats(self, intent: str) -> dict:
        self.load()
        return dict(self._data["intents"].get(intent, {}))

    def success_rate(self, intent: str) -> float:
        st = self.stats(intent)
        total = int(st.get("total", 0))
        if total <= 0:
            return 0.5
        return int(st.get("ok", 0)) / total

    def should_adjust(self, intent: str) -> bool:
        st = self.stats(intent)
        return int(st.get("consecutive_errors", 0)) >= self.ERROR_TRIGGER

    def mark_adjusted(self, intent: str, to_intent: str) -> None:
        """记录一次原型微调事件，并清零连续错误计数。"""
        with self._lock:
            self._data["intents"].setdefault(intent, {}).update(
                {"consecutive_errors": 0, "last_actual": to_intent})
            self._data["adjust_events"].append({
                "from": intent, "to": to_intent,
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "alpha": self.ADJUST_ALPHA,
                "persisted": False,   # ★仅内存微调，未回写原型库
            })
            # 只保留最近 50 条，避免文件无限增长
            self._data["adjust_events"] = self._data["adjust_events"][-50:]

    def adjust_prototype(self, intent_from: str, intent_to: str) -> bool:
        """★内存中把 intent_from 的原型沿 intent_to 的反方向微调。

        目的：降低「本该判为 intent_to 却判成 intent_from」的误判概率。
        ★不回写 intent_prototypes.json —— 自动学习结果需人工复核后再重建。
        """
        store = IntentPrototypeStore.get_instance()
        if not store.available or intent_from not in store._names:
            return False
        if intent_to not in store._names:
            return False
        try:
            import numpy as np
            i = store._names.index(intent_from)
            j = store._names.index(intent_to)
            v = store._matrix[i] - self.ADJUST_ALPHA * store._matrix[j]
            n = float(np.linalg.norm(v))
            if n < 1e-8:
                return False
            store._matrix[i] = (v / n).astype("float32")
            return True
        except Exception:
            return False


_FEEDBACK_SINGLETON: "ClassificationFeedback | None" = None
_FEEDBACK_LOCK = threading.Lock()


def get_feedback_store() -> ClassificationFeedback:
    """反馈库单例。"""
    global _FEEDBACK_SINGLETON
    if _FEEDBACK_SINGLETON is None:
        with _FEEDBACK_LOCK:
            if _FEEDBACK_SINGLETON is None:
                _FEEDBACK_SINGLETON = ClassificationFeedback()
    return _FEEDBACK_SINGLETON


# ==================================================================
# 意图 3 层层次（L1 大类 → L2 中类 → L3 具体意图）
# ==================================================================
class IntentHierarchy:
    """意图层次结构（只读，单例，懒加载）。

    数据来自 data/qica/intent_hierarchy.json（由
    tools/build_qica_intent_hierarchy.py 生成并做 24 意图全覆盖校验）。
    层次归属仅作为分类结果的**附加信息**，不参与融合打分。
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._loaded = False
        self._l1: dict[str, str] = {}
        self._l2: dict[str, str] = {}
        self._error = ""

    @classmethod
    def get_instance(cls) -> "IntentHierarchy":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def load(self) -> bool:
        if self._loaded:
            return bool(self._l1)
        with self._lock:
            if self._loaded:
                return bool(self._l1)
            try:
                _p = os.path.join(
                    _PROJECT_ROOT,
                    getattr(config, "QICA_INTENT_HIERARCHY_PATH",
                            "data/qica/intent_hierarchy.json"))
                if not os.path.exists(_p):
                    self._error = "层次文件不存在: %s" % _p
                    self._loaded = True
                    return False
                _d = safe_read_json(_p, default={})
                self._l1 = dict(_d.get("intent_to_l1", {}) or {})
                self._l2 = dict(_d.get("intent_to_l2", {}) or {})
                self._loaded = True
                return bool(self._l1)
            except Exception as e:
                self._error = "%s: %s" % (type(e).__name__, e)
                self._loaded = True
                return False

    def lookup(self, intent: str) -> tuple[str, str]:
        """查询意图的层次归属。

        Returns:
            (l1_category, l2_category)；未收录时返回 ("", "")
        """
        self.load()
        return self._l1.get(intent, ""), self._l2.get(intent, "")

    @property
    def error(self) -> str:
        return self._error


# ==================================================================
# 通道 ① 关键词
# ==================================================================
def ch_keyword(qica: Any, text: str, words: set, ctx: dict) -> dict[str, float]:
    """关键词通道：复用 QICA 现有规则表 + 长词权重 + 模糊匹配。"""
    scores = _zero_scores()
    try:
        rules = list(getattr(qica, "_intent_rules", []) or [])
        for rule in rules:
            intent = rule.get("intent_type", "")
            if intent not in scores:
                continue
            hit = 0.0
            for kw in rule.get("keywords", []):
                if qica._kw_match(kw, text, words) or qica._fuzzy_kw_match(kw, words):
                    hit += qica._kw_weight(kw)
            if hit > 0:
                # 规则优先级参与加权（高优先级规则更可信）
                prio = float(rule.get("priority", 1))
                scores[intent] += hit * (1.0 + prio * 0.1)
    except Exception as e:
        ctx.setdefault("errors", []).append("keyword: %s: %s" % (type(e).__name__, e))
    return _norm_scores(scores)


# ==================================================================
# 通道 ② 语义向量
# ==================================================================
def ch_semantic(qica: Any, text: str, words: set, ctx: dict) -> dict[str, float]:
    """语义向量通道：问题向量 vs 24 个意图原型向量的余弦相似度。

    ★这是解决「关键词匹配不到」的核心通道，权重最高（0.25）。

    ★主线第3批 任务2(P1-24) 语义鸿沟修复：
      bge-small-zh 的余弦被严重压缩——同类均值 0.46、异类均值 0.42，几乎重叠，
      任何绝对阈值都切不开（见 VectorEncoder.apply_score_gate 注释）。
      旧实现直接对 24 意图余弦做 min-max 归一化，使「本查询相对最像的意图」恒=1.0，
      哪怕其绝对余弦仅 0.45（属异类区间）——导致语义宽泛的「知识查询」原型成为黑洞，
      把规则查阅/技术推理/概念解释等一律吸走（回归实测 21 错分中 ~15 错归知识查询）。

      修复：在相对排序（min-max）之外，乘上「绝对置信度」
      raw_conf = clamp((cos - FLOOR)/(CEIL - FLOOR), 0, 1)，
      使「绝对余弦高（真正匹配）」的意图保留满权，而「仅是相对最像、绝对余弦低」的
      意图被显著压低，让真正匹配的意图浮出。FLOOR/CEIL 取语料实测的异类/同类基线，
      可用 config.QICA_SEMANTIC_CONF_FLOOR / _CEIL 调整；关闭
      ENABLE_QICA_SEMANTIC_CONF_AWARE 则完全退回旧 min-max（向后兼容）。
    """
    store = IntentPrototypeStore.get_instance()
    sims = store.similarities(ctx.get("vector"))
    if not sims:
        ctx.setdefault("errors", []).append(
            "semantic: 原型库不可用(%s)" % (store.error or "unknown"))
        return _zero_scores()
    # ★原始余弦（未归一化）：供上层判定「语义高置信覆盖关键词规则误命中」
    ctx["semantic_raw"] = sims

    if not getattr(config, "ENABLE_QICA_SEMANTIC_CONF_AWARE", True):
        return _norm_scores(sims)

    _norm = _norm_scores(sims)
    _floor = float(getattr(config, "QICA_SEMANTIC_CONF_FLOOR", 0.42))
    _ceil = float(getattr(config, "QICA_SEMANTIC_CONF_CEIL", 0.58))
    _span = max(1e-6, _ceil - _floor)
    _out: dict[str, float] = {}
    for k, v in sims.items():
        _rc = max(0.0, min(1.0, (float(v) - _floor) / _span))
        _out[k] = _norm.get(k, 0.0) * _rc
    return _out


# ==================================================================
# 通道 ③ 扩展思维
# ==================================================================
def ch_expansion(qica: Any, text: str, words: set, ctx: dict) -> dict[str, float]:
    """扩展思维通道：在语义空间做 1 跳关联扩展后再判意图。

    实现：把问题向量与「关键词通道 top3 意图的原型向量」做加权融合，
    得到扩展向量（相当于沿关联图谱走 1 跳），再算与原型的相似度。
    这是关联图谱在语义空间上的投影，避免引入共振引擎的耦合与延迟。
    """
    store = IntentPrototypeStore.get_instance()
    if not store.available:
        return _zero_scores()
    try:
        import numpy as np
        v = ctx.get("vector")
        if v is None:
            return _zero_scores()
        v = np.asarray(v, dtype="float32")
        n = float(np.linalg.norm(v))
        if n < 1e-8:
            return _zero_scores()
        v = v / n

        kw_scores = ctx.get("kw_scores") or {}
        top = sorted(kw_scores.items(), key=lambda kv: -kv[1])[:3]
        top = [(k, s) for k, s in top if s > 0]
        if not top:
            # 无关键词信号时退化为纯语义（与通道② 一致，交由权重稀释）
            return _norm_scores(store.similarities(v))

        names = store._names
        ext = v.copy()
        w_sum = 1.0
        for intent, s in top:
            if intent not in names:
                continue
            idx = names.index(intent)
            w = 0.3 * float(s)
            ext = ext + w * store._matrix[idx]
            w_sum += w
        ext = ext / w_sum
        return _norm_scores(store.similarities(ext))
    except Exception as e:
        ctx.setdefault("errors", []).append("expansion: %s: %s" % (type(e).__name__, e))
        return _zero_scores()


# ==================================================================
# 通道 ④ 实体识别
# ==================================================================
def ch_entity(qica: Any, text: str, words: set, ctx: dict) -> dict[str, float]:
    """实体识别通道：人名/系统名/时间/地点等实体 → 意图映射。"""
    scores = _zero_scores()
    try:
        entities = qica._extract_entities(text) if hasattr(qica, "_extract_entities") else []
        has_person = any(e in text for e in _ENTITY_PERSON)
        has_system = any(e in text for e in _ENTITY_SYSTEM)

        if has_person:
            scores["关系查询"] += 0.6
            scores["身份确认"] += 0.3
        if has_system:
            scores["知识查询"] += 0.5
            scores["身份确认"] += 0.3
        if entities:
            # 现有 CORE_ENTITIES 命中即视为有明确指代对象 → 提升身份/关系/知识类
            scores["身份确认"] += 0.2
            scores["知识查询"] += 0.2

        # 时间实体
        if re.search(r"\d+\s*(点|时|分|号|日|月|年)", text) or any(
                w in text for w in ("现在几点", "什么时候", "多久", "日程", "提醒")):
            scores["时间日程"] += 0.7
    except Exception as e:
        ctx.setdefault("errors", []).append("entity: %s: %s" % (type(e).__name__, e))
    return _norm_scores(scores)


# ==================================================================
# 通道 ⑤ 句式分析
# ==================================================================
def ch_sentence(qica: Any, text: str, words: set, ctx: dict) -> dict[str, float]:
    """句式分析通道：疑问/祈使/陈述/否定/追问 → 意图映射。"""
    scores = _zero_scores()
    try:
        stype = qica._detect_sentence_type(text) if hasattr(qica, "_detect_sentence_type") else "statement"
        negation = bool(ctx.get("has_negation"))

        if stype == "question":
            # ★主线第3批 任务2(P1-24)：补 规则查阅 —— 此前疑问句提升名单仅 6 个意图，
            #   规则查阅（"你的运行规则是什么"等）是疑问句却无句式信号，完全依赖语义通道，
            #   被宽泛的「知识查询」原型吸走（回归实测 规则查阅 准确率仅 0.50）。
            #   平铺 +0.5 对所有疑问句生效，不破坏同类疑问句的相对排序，仅让规则查阅
            #   在自身疑问句上与知识查询公平竞争。
            for k in ("知识查询", "概念解释", "关系查询", "身份确认", "状态查询",
                      "健康检查", "规则查阅"):
                scores[k] += 0.5
        elif stype == "imperative":
            for k in ("系统命令", "任务执行", "搜索获取", "请求帮助"):
                scores[k] += 0.6
        else:
            scores["情感表达"] += 0.4
            scores["一般对话"] += 0.3

        if negation:
            scores["否定质疑"] += 0.8

        # 追问澄清：短句 + 追问词
        if len(text) <= 12 and any(w in text for w in ("然后", "为什么", "具体", "什么意思", "接着")):
            scores["追问澄清"] += 0.7
    except Exception as e:
        ctx.setdefault("errors", []).append("sentence: %s: %s" % (type(e).__name__, e))
    return _norm_scores(scores)


# ==================================================================
# 通道 ⑥ 上下文
# ==================================================================
def ch_context(qica: Any, text: str, words: set, ctx: dict) -> dict[str, float]:
    """上下文通道：上轮意图延续 + 追问承接。"""
    scores = _zero_scores()
    try:
        prev = ctx.get("prev_intent") or ""
        if prev and prev in scores:
            scores[prev] += 0.6
            # 同路径的相邻意图给部分延续分
            prev_path = INTENT_TO_PATH.get(prev)
            for k in ALL_INTENTS:
                if k != prev and INTENT_TO_PATH.get(k) == prev_path:
                    scores[k] += 0.2
        # 追问信号（承接上轮）
        if ctx.get("is_followup"):
            if prev:
                scores[prev] += 0.3
            scores["追问澄清"] += 0.3
    except Exception as e:
        ctx.setdefault("errors", []).append("context: %s: %s" % (type(e).__name__, e))
    return _norm_scores(scores)


# ==================================================================
# 通道 ⑦ 复杂度
# ==================================================================
def ch_complexity(qica: Any, text: str, words: set, ctx: dict) -> dict[str, float]:
    """复杂度通道：词汇丰富度/长度/结构/抽象度 → 处理深度。"""
    scores = _zero_scores()
    try:
        length = len(text)
        # 词汇丰富度
        uniq = len(set(text))
        richness = (uniq / length) if length else 0.0
        len_score = min(1.0, length / 50.0)
        # 结构复杂度：连接词 + 分句
        struct_hits = sum(1 for m in _STRUCT_MARKERS if m in text)
        struct_score = min(1.0, struct_hits / 3.0)
        # 抽象度
        abs_hits = sum(1 for m in _ABSTRACT_MARKERS if m in text)
        abs_score = min(1.0, abs_hits / 2.0)

        complexity = (0.25 * richness + 0.25 * len_score
                      + 0.25 * struct_score + 0.25 * abs_score)

        scores["深度分析"] += complexity
        scores["创造性思考"] += complexity * 0.8
        scores["技术推理"] += complexity * 0.8
        # 低复杂度 → 轻量意图
        scores["一般对话"] += (1.0 - complexity) * 0.6
        scores["情感问候"] += (1.0 - complexity) * 0.5
        scores["追问澄清"] += (1.0 - complexity) * 0.4
    except Exception as e:
        ctx.setdefault("errors", []).append("complexity: %s: %s" % (type(e).__name__, e))
    return _norm_scores(scores)


# ==================================================================
# 通道 ⑧ 历史反馈
# ==================================================================
def ch_feedback(qica: Any, text: str, words: set, ctx: dict) -> dict[str, float]:
    """历史反馈通道：各意图的历史成功率修正（反馈学习关闭时恒为中性）。"""
    scores = _zero_scores()
    try:
        if not getattr(config, "ENABLE_QICA_FEEDBACK_LEARNING", False):
            # 中性：所有意图等分，融合时不影响排序
            return {k: 0.5 for k in ALL_INTENTS}
        store = get_feedback_store()
        any_data = False
        for intent in ALL_INTENTS:
            _st = store.stats(intent)
            _total = int(_st.get("total", 0))
            if _total > 0:
                any_data = True
                scores[intent] = int(_st.get("ok", 0)) / _total
            else:
                scores[intent] = 0.5
        if not any_data:
            return {k: 0.5 for k in ALL_INTENTS}
    except Exception as e:
        ctx.setdefault("errors", []).append("feedback: %s: %s" % (type(e).__name__, e))
        return {k: 0.5 for k in ALL_INTENTS}
    return _norm_scores(scores)


CHANNELS: dict[str, Callable[[Any, str, set, dict], dict[str, float]]] = {
    "keyword": ch_keyword,
    "semantic": ch_semantic,
    "expansion": ch_expansion,
    "entity": ch_entity,
    "sentence": ch_sentence,
    "context": ch_context,
    "complexity": ch_complexity,
    "feedback": ch_feedback,
}


# ========== 关系类问题信号词（意图精修用） ==========
_RELATION_WORDS = ("关系", "是谁", "什么人", "认识", "创造者", "设计者", "开发者", "主人")


# ==================================================================
# ★P1-45 语言学意图精修规则（可解释 / 可测试 / 不扩句式通道名单）
# ------------------------------------------------------------------
# 设计原则：
#   1) 仅当「当前意图」落在 _CONFUSABLE 兜底/易混集合时才改写，绝不覆盖已明确的意图；
#   2) 每条规则都是显式语言学标记（人名+关系词 / 为什么+需要 / 对比词 / 技术动词+对象 …），
#      可被单测逐条覆盖；
#   3) 与 run_channels 内的 refine_intent（融合路径）共用同一份规则，单一事实来源。
# ==================================================================
_CONFUSABLE = (
    "知识查询", "一般对话", "概念解释", "追问澄清",
)

def linguistic_refine_intent(text, current_intent, intent_scores=None):
    """语言学意图精修：返回 (new_intent, reason) 或 (None, None)。

    Args:
        text: 已归一化的问题文本
        current_intent: 当前已判定的意图（融合或关键词路径均可）
        intent_scores: 可选，融合意图分 dict（预留，当前规则未依赖）

    Returns:
        (new_intent, reason): 命中规则且当前意图可改写时；否则 (None, None)
    """
    if current_intent not in _CONFUSABLE:
        return None, None
    t = text or ""

    # 规则1：人名 + 关系词 → 关系查询
    if any(p in t for p in _ENTITY_PERSON) and any(w in t for w in _RELATION_WORDS):
        return "关系查询", "person+relation"

    # 规则2：为什么 + 需要/应该/必须/得 → 技术推理
    if "为什么" in t and any(w in t for w in ("需要", "应该", "必须", "得")):
        return "技术推理", "why+need"

    # 规则3：对比词（区别/差异/不同/对比/相比/比较/比）→ 对比分析
    if any(w in t for w in ("区别", "差异", "不同", "对比", "相比", "比较", "比")):
        return "对比分析", "contrast"

    # 规则4：技术动词 + 技术对象 → 技术推理
    if (any(v in t for v in ("设计", "重构", "实现", "搭建", "开发", "部署",
                              "优化", "编写", "架构"))
            and any(o in t for o in ("系统", "模块", "架构", "代码", "功能",
                                    "组件", "接口", "服务"))):
        return "技术推理", "tech-verb+object"

    # 规则5：身份类词 + 怎么/如何/是什么/是谁 → 身份确认
    if (any(w in t for w in ("构成", "形成", "诞生", "产生", "来历", "身份"))
            and any(q in t for q in ("怎么", "如何", "是什么", "是谁"))):
        return "身份确认", "identity-ask"

    # 规则6：触发/规则 + 什么情况/如何/怎么/什么时候/条件 → 规则查阅
    if (("触发" in t or "规则" in t)
            and any(q in t for q in ("什么情况", "如何", "怎么", "什么时候", "条件"))):
        return "规则查阅", "trigger-rule"

    # 规则7：之前提到/记得/上次 + 什么/内容/哪/怎么 → 记忆回想
    if (any(w in t for w in ("之前提到", "之前说", "刚才说", "记得", "上次", "前面说"))
            and any(q in t for q in ("什么", "内容", "哪", "怎么"))):
        return "记忆回想", "recall"

    return None, None


def refine_intent(text: str, result: dict, semantic_raw: dict | None = None) -> dict:
    """意图精修：人名 + 关系词 同时出现时判定为「关系查询」。

    ★修正 rule_fast_identity 关键词含 "关系" 造成的误命中：
      实测 "你和小林是什么关系？" 融合得 身份确认 0.596 / 关系查询 0.575，
      以 0.02 分之差错判，而星轨拍板的期望标签为「关系查询」。

    Args:
        text: 已归一化的问题文本
        result: fuse() 的输出（原地修改并返回）
        semantic_raw: 语义通道原始余弦，用于重算 top_raw

    Returns:
        精修后的 result（含 refined 标记，便于日志追溯）
    """
    try:
        has_person = any(p in text for p in _ENTITY_PERSON)
        has_relation = any(w in text for w in _RELATION_WORDS)
        if not (has_person and has_relation):
            return result
        if result.get("top_intent") == "关系查询":
            return result

        result["top_intent"] = "关系查询"
        result["top_path"] = INTENT_TO_PATH.get("关系查询", "identity")
        result["top_score"] = max(
            float(result.get("top_score", 0.0)),
            float(result.get("intent_scores", {}).get("关系查询", 0.0)),
        )
        result["method"] = INTENT_TO_METHOD.get("关系查询", "rule_reason")
        result["refined"] = True
        # 重算 top_raw，保证「语义高置信覆盖规则」判定拿到关系查询的真实余弦
        if semantic_raw:
            result["top_raw"] = float(semantic_raw.get("关系查询", 0.0))
    except Exception:
        # 精修为增强项，失败不得影响主流程
        return result
    return result


# ==================================================================
# 融合决策
# ==================================================================
def channel_contributions(channel_scores: dict[str, dict[str, float]],
                          weights: dict[str, float],
                          top_intent: str) -> dict[str, float]:
    """计算各通道对「胜出意图」的加权贡献占比（求和=1）。

    纯函数，无副作用，供 fuse() 遥测与 158 语料贡献度分析脚本复用。
    贡献定义：channel c 对 top_intent 的加权值 = w_c * score_c[top_intent]，
    各通道该值归一化后得到占比（便于跨语料聚合）。
    """
    num: dict[str, float] = {}
    den = 0.0
    for name, scores in channel_scores.items():
        w = float(weights.get(name, 0.0))
        if w <= 0 or not scores:
            continue
        c = w * float(scores.get(top_intent, 0.0))
        if c > 0:
            num[name] = c
            den += c
    if den <= 1e-8:
        return {}
    return {n: (c / den) for n, c in num.items()}


def fuse(channel_scores: dict[str, dict[str, float]]) -> dict[str, Any]:
    """8 通道加权融合 → 意图分数 → 6 路径分数 → top1/top2 → method。

    Returns:
        {
          "intent_scores": {意图: 融合分},
          "path_scores": {路径: 分},
          "top_intent": str, "top_score": float,
          "second_path": str|None, "second_score": float,
          "margin": float, "need_explore": bool,
          "method": str|None,
        }
    """
    weights = dict(getattr(config, "QICA_CHANNEL_WEIGHTS", {}) or {})
    if not weights:
        weights = {k: 1.0 for k in CHANNELS}

    total_w = 0.0
    fused = _zero_scores()
    for name, scores in channel_scores.items():
        w = float(weights.get(name, 0.0))
        if w <= 0 or not scores:
            continue
        total_w += w
        for intent, s in scores.items():
            if intent in fused:
                fused[intent] += w * float(s)

    if total_w > 1e-8:
        fused = {k: v / total_w for k, v in fused.items()}

    # 意图 → 路径聚合（取该路径下最高意图分）
    path_scores: dict[str, float] = {}
    path_intent: dict[str, str] = {}
    for intent, s in fused.items():
        p = INTENT_TO_PATH.get(intent, "knowledge_retrieve")
        if s > path_scores.get(p, -1.0):
            path_scores[p] = s
            path_intent[p] = intent

    ranked = sorted(path_scores.items(), key=lambda kv: -kv[1])
    top_path, top_score = (ranked[0] if ranked else ("knowledge_retrieve", 0.0))
    second_path, second_score = (ranked[1] if len(ranked) > 1 else (None, 0.0))
    margin = top_score - second_score

    margin_thr = float(getattr(config, "QICA_FUSION_MARGIN", 0.1))
    top_intent = path_intent.get(top_path, "一般对话")
    # ★优先用意图自身的 method 映射（保留 health_check / meta_cognitive_report 等特殊 method），
    #   路径 method 仅作为兜底
    _method = INTENT_TO_METHOD.get(top_intent) or PATH_TO_METHOD.get(top_path) or "knowledge_retrieve"
    # ★主线第4批 任务1(P1-43)：认知计算扩域 + 简单问题快速路径（灰度，与 QICA 侧一致）
    if getattr(config, "ENABLE_QICA_COGNITIVE_BROADEN", False):
        if top_intent in set(getattr(config, "QICA_COGNITIVE_BROADEN_INTENTS", ())):
            _method = "cognitive_compute"
    if getattr(config, "ENABLE_QICA_FAST_PATH_SIMPLE", False):
        if top_intent in set(getattr(config, "QICA_FAST_PATH_SIMPLE_INTENTS", ())):
            _method = "rule_reason"
    method = _method

    # ★P1-41：附带 L1/L2 层次归属（可选字段，不参与打分、不改变分类流程）
    try:
        _l1, _l2 = IntentHierarchy.get_instance().lookup(top_intent)
    except Exception:
        _l1, _l2 = "", ""

    # ★主线第5批 任务3+4：QICA 8 通道贡献度遥测（预埋 PHASE18 器官关联图谱）
    #   按「各通道对胜出意图的加权贡献占比」记录到 phase18_signals，
    #   供星轨在运行时聚合 158 语料的多通道贡献分布。关闭开关时零开销。
    if getattr(config, "ENABLE_QICA_CHANNEL_TELEMETRY", False):
        try:
            _contrib = channel_contributions(channel_scores, weights, top_intent)
            if _contrib:
                from nucleus.telemetry.phase18_signals import get_phase18_signals
                get_phase18_signals().record_qica_channel_contrib(
                    contributions=_contrib,
                    top_intent=top_intent,
                    top_score=top_score,
                )
        except Exception:
            # 遥测为增强项，任何异常不得影响主融合流程
            pass

    return {
        "intent_scores": fused,
        "path_scores": path_scores,
        "path_intent": path_intent,
        "top_path": top_path,
        "top_intent": top_intent,
        "top_score": top_score,
        "second_path": second_path,
        "second_score": second_score,
        "margin": margin,
        "need_explore": bool(second_path) and margin < margin_thr,
        "method": method,
        "l1_category": _l1,
        "l2_category": _l2,
    }


def run_channels(qica: Any, text: str, anchor: dict[str, Any],
                 vector: Any, log: Callable[[Any, str], None] | None = None,
                 max_workers: int = 8) -> dict[str, Any]:
    """并行执行 8 通道并融合。

    Args:
        qica: QICA 实例（复用其 _kw_match / _extract_entities 等方法）
        text: 已归一化的问题文本
        anchor: 当前 anchor（提供 has_negation 等字段）
        vector: 预先编码好的问题向量（通道②③ 复用，避免重复编码）
        log: 日志函数 (level, msg)
        max_workers: 线程池大小

    Returns:
        fuse() 的结果 + "channels" 原始分 + "errors"
    """
    _log = log or _default_log
    words = qica._get_segmented_words(text) if hasattr(qica, "_get_segmented_words") else set()

    ctx: dict[str, Any] = {
        "vector": vector,
        "has_negation": bool(anchor.get("has_negation", False)),
        "sentence_type": anchor.get("sentence_type", ""),
        "prev_intent": anchor.get("_prev_intent", ""),
        "is_followup": bool(anchor.get("_is_followup", False)),
        "errors": [],
    }

    channel_scores: dict[str, dict[str, float]] = {}

    # 关键词通道先算（扩展通道依赖其结果）
    try:
        channel_scores["keyword"] = ch_keyword(qica, text, words, ctx)
        ctx["kw_scores"] = channel_scores["keyword"]
    except Exception as e:
        ctx["errors"].append("keyword(pre): %s: %s" % (type(e).__name__, e))
        channel_scores["keyword"] = _zero_scores()
        ctx["kw_scores"] = channel_scores["keyword"]

    rest = [n for n in CHANNELS if n != "keyword"]
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {n: ex.submit(CHANNELS[n], qica, text, words, ctx) for n in rest}
            for n, f in futs.items():
                try:
                    channel_scores[n] = f.result()
                except Exception as e:
                    ctx["errors"].append("%s: %s: %s" % (n, type(e).__name__, e))
                    channel_scores[n] = _zero_scores()
    except Exception as e:
        ctx["errors"].append("executor: %s: %s" % (type(e).__name__, e))
        for n in rest:
            channel_scores.setdefault(n, _zero_scores())

    result = fuse(channel_scores)
    result["channels"] = channel_scores
    result["errors"] = ctx["errors"]
    # ★语义原始余弦：供上层判定「语义高置信覆盖关键词规则误命中」
    _raw = ctx.get("semantic_raw") or {}
    result["top_raw"] = float(_raw.get(result["top_intent"], 0.0))

    # ★意图精修（人名+关系词 → 关系查询），精修后同步重算 top_raw
    if refine_intent(text, result, _raw).get("refined"):
        _log(None, "[QICA][多通道] 意图精修 → 关系查询（人名+关系词）")

    if ctx["errors"]:
        _log(None, "[QICA][多通道] 通道异常: %s" % "; ".join(ctx["errors"]))

    _log(None, (
        "[QICA][多通道] 意图=%s 路径=%s 分=%.3f | 次选=%s 分=%.3f "
        "差值=%.3f%s | method=%s"
    ) % (
        result["top_intent"], result["top_path"], result["top_score"],
        result["second_path"] or "-", result["second_score"],
        result["margin"], " →触发探索" if result["need_explore"] else "",
        result["method"],
    ))
    return result


# 编码器可用性缓存：避免「模型不可用」时每次分类都空等加载超时
#   checked: 是否已判定过；ok: 判定结果；ts: 判定时间戳
_ENCODER_STATE = {"checked": False, "ok": False, "ts": 0.0}
_ENCODER_COOLDOWN = 300.0   # 判定不可用后的冷却秒数（期内直接返回 None）


def encode_text(text: str, wait_timeout: float = 60.0) -> Any:
    """编码问题文本（供通道②③ 复用一次）。失败返回 None。

    ★坑：VectorEncoder 是后台线程懒加载，is_available() 首次调用只触发加载、
      不等待，直接返回 False。故首次调用需轮询等待就绪。
    ★保护：若判定不可用，冷却期内不再重复等待，避免拖慢分类链路。
    """
    import time

    try:
        from nucleus.semantic.VectorEncoder import get_vector_encoder
    except Exception:
        return None

    st = _ENCODER_STATE
    now = time.time()
    if st["checked"] and not st["ok"] and (now - st["ts"]) < _ENCODER_COOLDOWN:
        return None

    try:
        enc = get_vector_encoder()
        if not enc.is_available():
            # 首次触发后台加载，轮询等待就绪
            _t0 = time.time()
            while time.time() - _t0 < wait_timeout:
                if enc.is_available():
                    break
                if enc.status().get("state") == "disabled":
                    st.update(checked=True, ok=False, ts=time.time())
                    return None
                time.sleep(0.2)

        if not enc.is_available():
            st.update(checked=True, ok=False, ts=time.time())
            return None

        vec = enc.encode_one(text)
        st.update(checked=True, ok=True, ts=time.time())
        return vec
    except Exception:
        st.update(checked=True, ok=False, ts=time.time())
        return None
