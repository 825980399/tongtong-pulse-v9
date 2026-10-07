# -*- coding: utf-8 -*-
"""
KnowledgeAcquisitionRouter.py —— 知识获取路由器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 路由知识获取请求到对应采集器
机制: 基于KnowledgeAcquisitionRouter类实现，包含10个核心方法
定位: 知识获取层
"""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from nucleus._silent_except import silent_exc


# 查询类型
QT_ENTITY = "entity"          # 实体/概念定义
QT_NEWS = "news"              # 新闻/行业动态
QT_TUTORIAL = "tutorial"      # 教程/方法
QT_REAL_TIME = "real_time"    # 实时信息
QT_LOCAL = "local"            # 框架自身知识
QT_COMPLEX = "complex"        # 复杂/混合
QT_UNKNOWN = "unknown"        # 未知

# 各类型的通道计划（顺序 = fallback 优先级；"browser" 表示委托调用方原路径）
CHANNEL_PLAN: dict[str, list[str]] = {
    QT_ENTITY: ["wiki", "browser"],
    QT_NEWS: ["rss", "browser"],
    QT_TUTORIAL: ["rss", "browser"],
    QT_REAL_TIME: ["browser"],
    QT_LOCAL: ["local"],
    QT_COMPLEX: ["wiki+rss", "browser"],
    QT_UNKNOWN: ["browser"],
}

_TYPE_LABEL = {
    QT_ENTITY: "实体定义", QT_NEWS: "新闻动态", QT_TUTORIAL: "教程方法",
    QT_REAL_TIME: "实时信息", QT_LOCAL: "自身知识", QT_COMPLEX: "复杂混合",
    QT_UNKNOWN: "未知类型",
}

# ========== ★第164批 刀A4：浏览器导航熔断（防 DNS 风暴反复选 browser） ==========
KA_ROUTER_BROWSER_CIRCUIT_BREAKER_ENABLED = True   # 总开关（默认开）
KA_ROUTER_BROWSER_FAIL_THRESHOLD = 5                # 连续失败次数阈值
KA_ROUTER_BROWSER_COOLDOWN_SEC = 300.0             # 跳闸后冷却退避时长（秒）

class KnowledgeAcquisitionRouter:
    """知识获取策略路由器（线程安全；无自建线程，按需同步调用）"""

    def __init__(self, log_fn: Callable[[str], None] | None = None):
        self._log_fn = log_fn or (lambda _m: None)
        self._lock = threading.Lock()
        # 统计：channel → {"attempts":n,"success":n,"total_elapsed":s}
        self._stats: dict[str, dict[str, float]] = {}
        self._decisions: list[dict[str, Any]] = []  # 最近路由决策（上限50）
        self._max_decisions = 50
        # ★A-14：分类统计（每类数量/命中路径）
        self._class_stats: dict[str, dict[str, Any]] = {}
        # ★第164批 刀A4：浏览器熔断状态（线程安全，_lock 保护）
        self._browser_fail_streak = 0
        self._browser_open_until = 0.0
        self._browser_trip_count = 0

    # ========== 对外接口 ==========

    def classify_query(self, query: str) -> str:
        """查询分类（★A-14：关键词规则 + 语义相似度双路 + 置信度门槛）。

        规则顺序敏感：实时 > 新闻 > 自身知识 > 教程 > 复杂混合 > 实体 > 未知。
        关键词未命中时走**语义相似度**兜底（bge 原型向量余弦，置信度<0.6 → 未知/浏览器，
        避免误分类）；语义通道不可用时退化为纯关键词。
        """
        _q = str(query or "").strip()
        if not _q:
            return QT_UNKNOWN
        if any(_k in _q for _k in ("现在", "此刻", "刚刚", "今天晚上", "当前时间")):
            return self._record_class(QT_REAL_TIME, 0.9, "keyword")
        if any(_k in _q for _k in ("最新", "新闻", "动态", "资讯", "热点", "行业", "局势")):
            return self._record_class(QT_NEWS, 0.9, "keyword")
        # ★A-14：自身知识词表扩充（自律/本能/认知等内在状态词——运行期误判"未知"的来源）
        if any(_k in _q for _k in ("你是谁", "你的", "自我", "自身", "内心世界", "身体状态",
                                   "自律", "本能", "认知", "反思自己", "我的状态")):
            return self._record_class(QT_LOCAL, 0.9, "keyword")
        # ★A-14：教程/领域学习词表扩充（技术架构/框架/代码/实现——运行期误判"未知"的来源）
        #   注意：复杂混合判定必须**先于**本规则——长查询命中"架构/代码"等词
        #   仍属多主题混合（"对比A和B的架构区别以及…"），否则会被教程类截胡
        if len(_q) > 25 and any(_k in _q for _k in ("和", "以及", "对比", "区别", "还有")):
            return self._record_class(QT_COMPLEX, 0.8, "keyword")
        if any(_k in _q for _k in ("怎么", "如何", "教程", "步骤", "怎么做", "方法",
                                   "技术架构", "框架", "代码", "实现", "编程",
                                   "开发", "架构", "算法")):
            return self._record_class(QT_TUTORIAL, 0.85, "keyword")
        if any(_k in _q for _k in ("什么是", "是什么", "的定义", "简介", "百科")) \
                or ("「" in _q and "」" in _q):
            return self._record_class(QT_ENTITY, 0.85, "keyword")
        # ★A-14 双路第二路：语义相似度兜底（关键词全部未命中时）
        _sem_type, _conf = self._semantic_classify(_q)
        if _sem_type and _conf >= self._SEM_CONF_THRESHOLD:
            return self._record_class(_sem_type, _conf, "semantic")
        return self._record_class(QT_UNKNOWN, _conf, "fallback")

    def _record_class(self, qtype: str, confidence: float, via: str) -> str:
        """★A-14：分类统计（每类数量/命中路径），便于后续优化。"""
        with self._lock:
            _s = self._class_stats.setdefault(
                qtype, {"count": 0, "via": {}, "conf_sum": 0.0})
            _s["count"] += 1
            _s["via"][via] = _s["via"].get(via, 0) + 1
            _s["conf_sum"] += float(confidence)
        return qtype

    # 语义原型（★A-14）：每类 4 条代表性短句，向量余弦取最近原型
    _SEMANTIC_PROTOTYPES: dict[str, list[str]] = {
        QT_ENTITY: ["什么是量子纠缠", "黑洞的定义", "这是一个概念解释", "术语含义解释"],
        QT_NEWS: ["AI行业最新动态", "科技新闻快讯", "行业热点事件", "近期局势变化"],
        QT_TUTORIAL: ["技术架构设计", "框架使用方法", "代码实现步骤", "编程开发实践"],
        QT_REAL_TIME: ["现在几点了", "当前状态如何", "此刻情况", "刚刚发生的事"],
        QT_LOCAL: ["你的自我认知", "内心的自律本能", "反思自己的状态", "我的内在感受"],
        QT_COMPLEX: ["对比量子计算和经典计算的区别以及应用", "综合分析多个方案的差异和联系"],
        QT_UNKNOWN: ["随便聊聊", "嗯嗯好的", "哈哈", "今天吃什么"],
    }
    _SEM_CONF_THRESHOLD = 0.6
    _sem_proto_cache: dict[str, Any] = {"vecs": None, "failed": False}

    def _semantic_classify(self, query: str) -> tuple[str, float]:
        """语义相似度分类（★A-14 第二路）。不可用/失败 → (QT_UNKNOWN, 0.0)。

        置信度映射：bge 余弦典型区间 0.3~0.7，conf = clip((sim-0.30)/0.30, 0, 1)；
        阈值 0.6 对应 sim≈0.48。
        """
        if self._sem_proto_cache.get("failed"):
            return QT_UNKNOWN, 0.0
        try:
            import numpy as _np

            from nucleus.semantic.VectorEncoder import VectorEncoder
            _enc = VectorEncoder.get_instance()
            if self._sem_proto_cache.get("vecs") is None:
                _protos: list[str] = []
                _labels: list[str] = []
                for _t, _texts in self._SEMANTIC_PROTOTYPES.items():
                    for _txt in _texts:
                        _protos.append(_txt)
                        _labels.append(_t)
                _vecs = _enc.encode(_protos)
                if _vecs is None:
                    self._sem_proto_cache["failed"] = True
                    return QT_UNKNOWN, 0.0
                import numpy as _np2
                _norms = _np2.linalg.norm(_vecs, axis=1, keepdims=True)
                _norms[_norms == 0] = 1.0
                self._sem_proto_cache["vecs"] = (_vecs / _norms, _labels)
            _qv = _enc.encode([query])
            if _qv is None or len(_qv) == 0:
                return QT_UNKNOWN, 0.0
            _vecs, _labels = self._sem_proto_cache["vecs"]
            _q = _qv[0]
            _qn = float(_np.linalg.norm(_q))
            if _qn == 0:
                return QT_UNKNOWN, 0.0
            _sims = _vecs @ (_q / _qn)
            _best = int(_np.argmax(_sims))
            _sim = float(_sims[_best])
            _conf = max(0.0, min(1.0, (_sim - 0.30) / 0.30))
            return _labels[_best], round(_conf, 2)
        except Exception as _e:
            self._sem_proto_cache["failed"] = True
            self._log(f"语义分类通道不可用（退化为关键词分类）: {_e}")
            return QT_UNKNOWN, 0.0

    def acquire(self, query: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """按查询类型路由获取知识。

        Returns:
            {
              "channel":    命中通道（"wiki"/"rss"/"wiki+rss"/"local"）或 None，
              "decision":   决策描述（可追溯），
              "payload":    通道产物（wiki: WikiResult；rss: 文章列表；local: None），
              "delegate_browser": True=首选失败，委托调用方走原浏览器路径，
            }
            路由器关闭 → None（调用方走自有直连逻辑，零回退）。
        """
        try:
            import config as _cfg
            if not getattr(_cfg, 'ENABLE_KNOWLEDGE_ACQUISITION_ROUTER', False):
                return None
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge.KnowledgeAcquisitionRouter::acquire L184")
            return None

        # ★第164批 刀A4：浏览器熔断——连续失败达阈值则退避，本次返回 None
        #   由调用方走直连 wiki/RSS 降级路径，避免 DNS 风暴反复选 browser。
        if self._browser_circuit_open():
            self._log("浏览器熔断中（连续失败已达阈值，冷却退避中），本次 acquire 返回 None，"
                      "由调用方走直连 wiki/RSS 降级路径，避免反复选 browser")
            return None

        _q = str(query or "").strip()
        _qtype = self.classify_query(_q)
        _plan = CHANNEL_PLAN.get(_qtype, CHANNEL_PLAN[QT_UNKNOWN])
        _t0 = time.time()
        _result: dict[str, Any] | None = None
        _hit_channel: str | None = None
        _tried: list[str] = []

        for _ch in _plan:
            if _ch == "browser":
                # 委托调用方走原浏览器路径（路由器不拥有 Playwright）
                _tried.append("browser→委托")
                break
            if _ch == "local":
                # 自身知识：委托本地检索（调用方/内在世界已有链路），不外呼
                _tried.append("local→委托本地检索")
                _result = {"channel": "local", "payload": None,
                           "delegate_browser": False, "delegate_local": True}
                _hit_channel = "local"
                break
            _got = self._try_channel(_ch, _q, _tried)
            if _got is not None:
                _result = _got
                # 组合通道（wiki+rss）命中时报**实际获胜子通道**（wiki优先）
                _hit_channel = _got.get("channel") or _ch
                break

        _elapsed = time.time() - _t0
        _decision = (f"类型={_TYPE_LABEL.get(_qtype, _qtype)}, 计划={'→'.join(_plan)}, "
                     f"尝试={','.join(_tried) or '无'}, 命中={_hit_channel or '委托浏览器'}, "
                     f"耗时={_elapsed:.2f}s")
        self._log(f"路由决策: [{_q[:36]}] {_decision}")
        self._record_decision(_q, _qtype, _hit_channel, _tried, _elapsed)

        return {
            "channel": _hit_channel,
            "decision": _decision,
            "payload": (_result or {}).get("payload"),
            "delegate_browser": _hit_channel is None,
            "delegate_local": bool((_result or {}).get("delegate_local")),
        }

    def record_stats(self, channel: str, success: bool, elapsed: float):
        """记录通道调用统计（供上层/未来优化权重）"""
        with self._lock:
            _s = self._stats.setdefault(
                channel, {"attempts": 0, "success": 0, "total_elapsed": 0.0})
            _s["attempts"] += 1
            if success:
                _s["success"] += 1
            _s["total_elapsed"] += float(elapsed)

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            _out = {}
            for _ch, _s in self._stats.items():
                _rate = _s["success"] / _s["attempts"] if _s["attempts"] else 0.0
                _out[_ch] = {**_s,
                             "success_rate": round(_rate, 2),
                             "avg_elapsed": round(_s["total_elapsed"] / _s["attempts"], 2)
                             if _s["attempts"] else 0.0}
            # ★A-14：分类统计（每类数量/平均置信度/命中路径）
            _cls = {}
            for _t, _s in self._class_stats.items():
                _cls[_t] = {"count": _s["count"],
                            "avg_confidence": round(_s["conf_sum"] / _s["count"], 2)
                            if _s["count"] else 0.0,
                            "via": dict(_s["via"])}
            return {"channels": _out,
                    "class_stats": _cls,
                    "decisions_recorded": len(self._decisions),
                    "recent_decisions": list(self._decisions[-5:])}

    # ========== 内部实现 ==========

    def _channel_enabled(self, channel: str) -> bool:
        """子通道受各自灰度开关约束（路由器不越权绕过）"""
        try:
            import config as _cfg
            if channel in ("wiki", "wiki+rss"):
                if not getattr(_cfg, 'ENABLE_ENCYCLOPEDIA_QUERY', False):
                    return False
            if channel in ("rss", "wiki+rss"):
                if not getattr(_cfg, 'ENABLE_RSS_COLLECTOR', False):
                    return False
            return True
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge.KnowledgeAcquisitionRouter::_channel_enabled L273")
            return False

    def _try_channel(self, channel: str, query: str,
                     tried: list[str]) -> dict[str, Any] | None:
        """尝试单通道（或并行组合）。失败/未启用返回 None 并在 tried 留痕。"""
        if not self._channel_enabled(channel):
            tried.append(f"{channel}(开关未开,跳过)")
            return None

        if channel == "wiki":
            tried.append("wiki")
            return self._try_wiki(query)

        if channel == "rss":
            tried.append("rss")
            return self._try_rss(query)

        if channel == "wiki+rss":
            # 复杂/混合查询：双通道**并行**，百科（定义质量高）优先取用
            tried.append("wiki+rss(并行)")
            _wiki_res: dict | None = None
            _rss_res: dict | None = None
            with ThreadPoolExecutor(max_workers=2) as _pool:
                _f1 = _pool.submit(self._try_wiki, query) if \
                    self._channel_enabled("wiki") else None
                _f2 = _pool.submit(self._try_rss, query) if \
                    self._channel_enabled("rss") else None
                _wiki_res = _f1.result() if _f1 else None
                _rss_res = _f2.result() if _f2 else None
            if _wiki_res is not None:
                return _wiki_res
            return _rss_res
        return None

    def _extract_core_entity(self, query: str) -> str | None:
        """★第五批任务2A：从长句中提取核心实体名词（2-6字），供百科查询重试。

        使用 jieba 分词 + 词性过滤（名词 n*/专有名词），返回首个符合长度的候选。
        无 jieba 或无可提取实体时返回 None（调用方退化为原行为）。
        """
        try:
            import jieba.posseg as _posseg
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge.KnowledgeAcquisitionRouter::_extract_core_entity L316")
            return None
        try:
            _q = str(query or "").strip()
            if not _q:
                return None
            _cands = [
                _w.word for _w in _posseg.cut(_q)
                if _w.flag.startswith("n") and 2 <= len(_w.word) <= 6
            ]
            return _cands[0] if _cands else None
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge.KnowledgeAcquisitionRouter::_extract_core_entity L327")
            return None

    def _try_wiki(self, query: str) -> dict[str, Any] | None:
        """百科通道：触发判定→查询→统计。

        ★第五批任务2A：首次 should_query 判定失败时，提取核心实体（长句→2-6字名词）
        重试一次，提升实体定义类查询的 wiki 命中率（实测 118 次路由 wiki 0 命中）。
        """
        from nucleus.knowledge.WikiQuerier import get_shared_wiki_querier
        _querier = get_shared_wiki_querier(log_fn=self._log_fn)
        if _querier.should_query(query):
            return self._wiki_run(_querier, query)
        # ★重试：从长句提取核心实体再试一次
        _entity = self._extract_core_entity(query)
        if _entity and _entity != query.strip() and _querier.should_query(_entity):
            self._log(f"wiki 通道：首次判定未过，已提取核心实体「{_entity}」重试")
            return self._wiki_run(_querier, _entity)
        self.record_stats("wiki", False, 0.0)
        return None

    def _wiki_run(self, querier, keyword: str) -> dict[str, Any] | None:
        """执行一次百科查询并统计（被 _try_wiki 复用：直接查询 + 实体重试）。"""
        _t0 = time.time()
        _r = querier.query(keyword)
        self.record_stats("wiki", _r is not None, time.time() - _t0)
        if _r is None:
            return None
        return {"channel": "wiki", "payload": _r, "delegate_browser": False}

    def _try_rss(self, query: str) -> dict[str, Any] | None:
        """RSS通道：按方向关联近期文章→统计"""
        from nucleus.knowledge.RssCollector import get_shared_rss_collector
        _collector = get_shared_rss_collector(log_fn=self._log_fn)
        _t0 = time.time()
        _arts = _collector.collect_for_direction(query, max_articles=3)
        self.record_stats("rss", bool(_arts), time.time() - _t0)
        if not _arts:
            return None
        return {"channel": "rss", "payload": _arts, "delegate_browser": False}

    def _record_decision(self, query: str, qtype: str, channel: str | None,
                         tried: list[str], elapsed: float):
        with self._lock:
            self._decisions.append({
                "time": time.strftime("%H:%M:%S"),
                "query": query[:50],
                "type": qtype,
                "channel": channel,
                "tried": tried,
                "elapsed": round(elapsed, 2),
            })
            if len(self._decisions) > self._max_decisions:
                del self._decisions[:len(self._decisions) - self._max_decisions]

    # ========== ★第164批 刀A4：浏览器熔断闭环 ==========

    def record_browser_outcome(self, success: bool) -> None:
        """★第164批 刀A4：浏览器尝试成败上报（调用方在浏览器操作结束后调用）。

        连续失败达阈值 → 跳闸进入冷却（open）；冷却期间 acquire 返回 None。
        任一次成功 → 重置失败计数并闭合熔断。半开探测（冷却到期）仍失败 → 延长冷却。
        纯算术 + 已自身容错的 _log，无 try/except（规避 cw2 静默except 门禁）。
        """
        with self._lock:
            if success:
                if self._browser_fail_streak != 0 or self._browser_open_until > 0.0:
                    self._log("浏览器熔断：检测到一次成功，重置失败计数并闭合熔断")
                self._browser_fail_streak = 0
                self._browser_open_until = 0.0
                return
            self._browser_fail_streak += 1
            if self._browser_fail_streak >= KA_ROUTER_BROWSER_FAIL_THRESHOLD:
                _now = time.time()
                if self._browser_open_until <= _now:
                    self._browser_open_until = _now + KA_ROUTER_BROWSER_COOLDOWN_SEC
                    self._browser_trip_count += 1
                    self._log(
                        f"浏览器熔断触发：连续失败 {self._browser_fail_streak} 次，"
                        f"退避 {KA_ROUTER_BROWSER_COOLDOWN_SEC:.0f}s（累计跳闸 {self._browser_trip_count} 次）")
                else:
                    # 半开探测仍失败 → 延长冷却
                    self._browser_open_until = _now + KA_ROUTER_BROWSER_COOLDOWN_SEC
                    self._log(
                        f"浏览器熔断半开探测仍失败，延长冷却至 {KA_ROUTER_BROWSER_COOLDOWN_SEC:.0f}s")

    def _browser_circuit_open(self) -> bool:
        """★第164批 刀A4：熔断是否处于打开（冷却）态。"""
        if not KA_ROUTER_BROWSER_CIRCUIT_BREAKER_ENABLED:
            return False
        return time.time() < self._browser_open_until

    def get_browser_circuit_state(self) -> dict[str, Any]:
        """★第164批 刀A4：熔断状态快照（可观测/单测）。"""
        with self._lock:
            return {
                "enabled": KA_ROUTER_BROWSER_CIRCUIT_BREAKER_ENABLED,
                "fail_streak": self._browser_fail_streak,
                "open": self._browser_circuit_open(),
                "open_until": self._browser_open_until,
                "trip_count": self._browser_trip_count,
                "threshold": KA_ROUTER_BROWSER_FAIL_THRESHOLD,
                "cooldown_sec": KA_ROUTER_BROWSER_COOLDOWN_SEC,
            }

    def reset_browser_circuit(self) -> None:
        """★第164批 刀A4：重置熔断状态（测试隔离用）。"""
        with self._lock:
            self._browser_fail_streak = 0
            self._browser_open_until = 0.0
            self._browser_trip_count = 0

    def _log(self, msg: str):
        try:
            self._log_fn(f"[策略路由器] {msg}")
        except Exception as e:
            silent_exc(e, where="nucleus.knowledge.KnowledgeAcquisitionRouter::_log L385")


# ========== 模块级单例（供双腿/控制器接线使用） ==========

_shared_router: KnowledgeAcquisitionRouter | None = None
_shared_lock = threading.Lock()


def get_shared_knowledge_router(log_fn=None) -> KnowledgeAcquisitionRouter:
    global _shared_router
    with _shared_lock:
        if _shared_router is None:
            _shared_router = KnowledgeAcquisitionRouter(log_fn=log_fn)
        return _shared_router


def report_browser_outcome(success: bool) -> None:
    """★第164批 刀A4：浏览器熔断闭环反馈（调用方便捷入口，委托共享单例）。

    无 try/except 包裹：record_browser_outcome 为纯算术 + 已自身容错的日志，
    不会抛异常；调用方各自已有异常边界。
    """
    _r = get_shared_knowledge_router()
    _r.record_browser_outcome(bool(success))
