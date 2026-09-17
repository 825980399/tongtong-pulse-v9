# -*- coding: utf-8 -*-
"""KnowledgeQualityAnalyzer.py —— 知识质量分析器（自我认知引擎·第五维）

版本: v10 PulseNet · 主线第23批
关联债务: P3-4（知识一致性 / 覆盖率 / 老化分析）

【定位】
- 自我认知引擎前四维是「大脑**结构与运行**」（代码静态 / 数据文件 / 运行时动态 / 代码结构）；
- 本模块补的是「大脑**内容**」—— 全库知识体系的质量体检。

【与已有模块的分工（★T0 核实结论，不重复造轮子）】
- ``nucleus/knowledge/KnowledgeQualityScorer`` 是**单节点内容质量**评分器（信息密度/关键词/
  来源信任/长度/分类，微观）→ 本模块在「知识深度」子项**复用**其思想，但不重复实现；
- 本模块做**全库体系级**分析（宏观）：一致性 / 覆盖率 / 老化。

【数据源（T0 实测）】
优先 ``data/knowledge/parquet/evol_level=L{1,2,3}/`` —— 现成分区，pyarrow 全读约 1.1s，
10496 节点 × 28 列；回退 ``data/knowledge/pulse_l1_snapshot.json``（小）；两者皆不可用时
用 ``set_nodes()`` 注入（测试路径）。**绝不整体 load 主快照（406MB）。**

【设计约束（任务书 §四）】
只读、不改任何生产知识数据；不依赖大模型（规则/关键词/统计，可复现）；
复杂语义矛盾标记 ``needs_review=True`` 交由人工确认。
"""

from __future__ import annotations

import glob
import os
import re
import time
from collections import Counter, defaultdict
from typing import Any

__all__ = ["KnowledgeQualityAnalyzer", "get_knowledge_quality_analyzer"]

# ======================================================================
# 常量（阈值集中在此，便于裁决与调参）
# ======================================================================
AGING_DAYS_THRESHOLD = 180.0      # 超过该天龄 → 「可能过时」
DORMANT_DAYS_THRESHOLD = 90.0     # 超过该天数无激活 → 「休眠知识」
LOW_DENSITY_ABS_MIN = 5           # 领域节点数下限（绝对）
LOW_DENSITY_RATIO = 0.002         # 领域节点数占全库比例下限（0.2%，实测 1% 过严）
NUMERIC_CONFLICT_TOL = 0.05       # 数值冲突容差（相对差 > 5% 才算）
DEF_JACCARD_MAX = 0.55            # 同概念两个定义相似度低于此 → 定义不一致
DEEP_RATIO_TARGET = 0.80          # 深度满分目标：L2/L3 占比
BREADTH_TARGET = 8                # 广度满分目标：领域数

_WEIGHT_CONSISTENCY = 0.30
_WEIGHT_COVERAGE = 0.30
_WEIGHT_AGING = 0.20
_WEIGHT_DEPTH = 0.10
_WEIGHT_BREADTH = 0.10

_NEG_WORDS = ("不是", "并非", "不属于", "不能", "不会", "无法", "没有", "不")

# 知识树路径首段 = 领域
_PATH_SPLIT_RE = re.compile(r"[/\\]+")
# 「A 是 B」/「A 为 B」
_POS_PATTERNS = (
    re.compile(r"([\u4e00-\u9fa5A-Za-z_][\u4e00-\u9fa5A-Za-z0-9_]{1,29}?)(?:是指|指的是|定义为|是|为)"),
)
# 否定式「A 不是 B」
_NEG_PATTERN = re.compile(
    r"([\u4e00-\u9fa5A-Za-z_][\u4e00-\u9fa5A-Za-z0-9_]{1,29}?)(?:不是|并非|不属于)")
# 数值：「指标 达到/为 80 %」
# ★第23批 T1 修正：连接词由**可选**改为**必需** —— 原写法等于「任意文本+数字」都命中，
#   实测把「（由30条相关知识归纳）」解析成指标「条相关知识归纳由」、把年份 2026 当数值。
_NUM_PATTERN = re.compile(
    r"([\u4e00-\u9fa5A-Za-z_]{2,12}?)\s*(?:达到|约为|约|为|是|=|:|：)\s*"
    r"(\d+(?:\.\d+)?)\s*(%|％|秒|分钟|小时|天|次|个|倍|MB|GB|KB|TB)?")
#: 指标名中的虚词 → 判定为伪指标（过滤「条相关知识归纳由」这类噪声）
_METRIC_STOPWORDS = ("的", "了", "由", "在", "和", "与", "或", "把", "被", "让")
# 因果：「A 导致 B」
_CAUSE_PATTERN = re.compile(
    r"([\u4e00-\u9fa5A-Za-z_][^，。；;\n]{1,29}?)(?:导致|引起|会造成|引发)"
    r"([\u4e00-\u9fa5A-Za-z_][^，。；;\n]{1,29})")
# 版本号 v9.5 / 9.5
_VER_PATTERN = re.compile(r"\bv?(\d{1,2})\.(\d{1,2})(?:\.\d{1,2})?\b")


# ======================================================================
# 纯函数工具（便于单测）
# ======================================================================
def _norm_text(text: Any) -> str:
    """规范化文本：去空白、去标点，用于比较。"""
    if not isinstance(text, str):
        text = str(text or "")
    return re.sub(r"[\s，。、；：！？,.;:!?\"'“”‘’()（）\[\]【】]", "", text)


def _jaccard(a: str, b: str) -> float:
    """字符二元组 Jaccard 相似度（0-1，无需模型，可复现）。"""
    a, b = _norm_text(a), _norm_text(b)
    if not a or not b:
        return 0.0
    ga = {a[i:i + 2] for i in range(max(1, len(a) - 1))}
    gb = {b[i:i + 2] for i in range(max(1, len(b) - 1))}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _domain_of(space_path: Any) -> str:
    """从 space_path 取领域（首段）；空/根 → ``未分类``。"""
    _raw = str(space_path or "").strip()
    if not _raw or _raw == "/":
        return "未分类"
    _parts = [p.strip() for p in _PATH_SPLIT_RE.split(_raw) if p.strip()]
    return _parts[0] if _parts else "未分类"


def _node_ts(node: dict) -> float:
    """节点「来源/内容时间」：优先 source_timestamp > source_time > acquired_time > created_at。"""
    for _k in ("source_timestamp", "source_time", "acquired_time", "created_at"):
        _v = node.get(_k)
        if isinstance(_v, (int, float)) and _v > 0:
            return float(_v)
    return 0.0


def _node_updated_ts(node: dict) -> float:
    """节点「最后更新时间」：updated_at > created_at。"""
    for _k in ("updated_at", "created_at"):
        _v = node.get(_k)
        if isinstance(_v, (int, float)) and _v > 0:
            return float(_v)
    return 0.0


def _node_last_active_ts(node: dict) -> float:
    """节点「最后激活时间」：last_activated > created_at。"""
    for _k in ("last_activated", "created_at"):
        _v = node.get(_k)
        if isinstance(_v, (int, float)) and _v > 0:
            return float(_v)
    return 0.0


def _severity_of(nodes: list[dict]) -> str:
    """按涉及节点的最高演化层级判严重度。"""
    _lv = {str(n.get("evol_level", "")).upper() for n in nodes}
    if "L3" in _lv:
        return "high"
    if "L2" in _lv:
        return "medium"
    return "low"


# ======================================================================
# 主类
# ======================================================================
class KnowledgeQualityAnalyzer:
    """全库知识质量分析器（第五维）。

    Args:
        project_root: 项目根（默认按本文件上溯 3 层）。
        parquet_dir: parquet 分区目录（默认 ``data/knowledge/parquet``）。
        l1_snapshot: L1 快照路径（默认 ``data/knowledge/pulse_l1_snapshot.json``）。
        now: 注入「当前时间」（测试用；None 取 ``time.time()``）。
    """

    def __init__(self, project_root: str | None = None,
                 parquet_dir: str | None = None,
                 l1_snapshot: str | None = None,
                 now: float | None = None) -> None:
        _root = project_root or os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.project_root = _root
        self.parquet_dir = parquet_dir or os.path.join(_root, "data", "knowledge", "parquet")
        self.l1_snapshot = l1_snapshot or os.path.join(
            _root, "data", "knowledge", "pulse_l1_snapshot.json")
        self._now = float(now) if now else time.time()
        self._injected: list[dict] | None = None
        self._parquet_cache: list[dict] | None = None

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def set_nodes(self, nodes: list[dict] | None) -> KnowledgeQualityAnalyzer:
        """注入节点集（测试路径；None 表示恢复自动加载）。"""
        self._injected = list(nodes) if nodes is not None else None
        return self

    def load_nodes(self, source: str = "auto") -> list[dict]:
        """加载知识节点。

        Args:
            source: ``auto`` / ``parquet`` / ``l1`` / ``injected``。

        Returns:
            节点 dict 列表（加载失败返回空列表，绝不抛异常）。
        """
        if source in ("auto", "injected") and self._injected is not None:
            return self._injected
        if source in ("auto", "parquet"):
            _p = self._load_from_parquet()
            if _p:
                return _p
        if source in ("auto", "l1"):
            return self._load_from_l1_snapshot()
        return []

    def _load_from_parquet(self) -> list[dict]:
        """从 ``evol_level=L*`` 分区读节点（pyarrow；不可用时返回空）。"""
        if self._parquet_cache is not None:
            return self._parquet_cache
        _out: list[dict] = []
        try:
            import pyarrow.parquet as _pq  # 局部导入：缺失时静默回退
        except Exception:
            return _out
        try:
            _files = sorted(glob.glob(os.path.join(self.parquet_dir, "evol_level=*", "*.parquet")))
            for _f in _files:
                _lv = ""
                for _seg in _f.replace("\\", "/").split("/"):
                    if _seg.startswith("evol_level="):
                        _lv = _seg.split("=", 1)[1]
                        break
                _tbl = _pq.read_table(_f)
                for _row in _tbl.to_pylist():
                    if _lv:
                        _row.setdefault("evol_level", _lv)
                    _out.append(_row)
        except Exception:
            return []
        self._parquet_cache = _out
        return _out

    def _load_from_l1_snapshot(self) -> list[dict]:
        """从 L1 快照读节点（小文件，可安全整体读）。"""
        try:
            import json
            if not os.path.exists(self.l1_snapshot):
                return []
            with open(self.l1_snapshot, encoding="utf-8") as _f:
                _d = json.load(_f)
            _nodes = _d.get("nodes") if isinstance(_d, dict) else _d
            if isinstance(_nodes, dict):
                return list(_nodes.values())
            return list(_nodes or [])
        except Exception:
            return []

    # ------------------------------------------------------------------
    # T1 一致性
    # ------------------------------------------------------------------
    def analyze_consistency(self, nodes: list[dict] | None = None) -> dict[str, Any]:
        """检测知识节点间的矛盾/冲突，产出 0-100 一致性评分。"""
        _nodes = self._pick(nodes)
        _total = len(_nodes)
        if _total < 2:
            return {"score": 100.0, "total_nodes": _total, "conflicts": [],
                    "by_type": {}, "stats": {"total": 0, "needs_review": 0}}
        _conflicts: list[dict] = []
        _conflicts += self._detect_direct_contradiction(_nodes)
        _conflicts += self._detect_numeric_conflict(_nodes)
        _conflicts += self._detect_causal_contradiction(_nodes)
        _conflicts += self._detect_definition_inconsistency(_nodes)
        _dedup = self._dedup_conflicts(_conflicts)
        _involved = {i for c in _dedup for i in c.get("node_ids", [])}
        _by_type = Counter(c["type"] for c in _dedup)
        _score = max(0.0, 100.0 - (len(_involved) / _total) * 100.0 * 2.0)
        return {
            "score": round(_score, 2),
            "total_nodes": _total,
            "conflicts": _dedup,
            "by_type": dict(_by_type),
            "stats": {
                "total": len(_dedup),
                "involved_nodes": len(_involved),
                "needs_review": sum(1 for c in _dedup if c.get("needs_review")),
                "severity": dict(Counter(c["severity"] for c in _dedup)),
            },
        }

    def _pick(self, nodes: list[dict] | None) -> list[dict]:
        return list(nodes) if nodes is not None else self.load_nodes()

    def _detect_direct_contradiction(self, nodes: list[dict]) -> list[dict]:
        """同一主体既有「A 是 X」又有「A 不是 X」→ 直接矛盾。"""
        _pos: dict[str, list[tuple[str, int]]] = defaultdict(list)
        _neg: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for _i, _n in enumerate(nodes):
            _txt = _norm_text(_n.get("value"))
            if not _txt:
                continue
            for _pat in _POS_PATTERNS:
                for _m in _pat.finditer(_txt):
                    _subj = _m.group(1)
                    _obj = _txt[_m.end():_m.end() + 40]
                    if _subj and _obj and len(_subj) >= 2:
                        _pos[_subj].append((_obj, _i))
            for _m in _NEG_PATTERN.finditer(_txt):
                _subj = _m.group(1)
                _obj = _txt[_m.end():_m.end() + 40]
                if _subj and _obj and len(_subj) >= 2:
                    _neg[_subj].append((_obj, _i))
        _out: list[dict] = []
        for _subj, _pos_list in _pos.items():
            if _subj not in _neg:
                continue
            for _pobj, _pi in _pos_list:
                for _nobj, _ni in _neg[_subj]:
                    if _pi == _ni:
                        continue
                    _sim = _jaccard(_pobj, _nobj)
                    if _sim >= 0.34:      # 同一论断的正反两面
                        _out.append(self._mk_conflict(
                            "direct_contradiction", [_pi, _ni], nodes,
                            f"「{_subj}是{_pobj[:20]}」与「{_subj}不是{_nobj[:20]}」互斥",
                            confidence=round(min(1.0, _sim + 0.3), 2)))
        return _out

    def _detect_numeric_conflict(self, nodes: list[dict]) -> list[dict]:
        """同一指标出现显著不同的数值 → 数值冲突。"""
        _metric: dict[str, list[tuple[float, str, int]]] = defaultdict(list)
        for _i, _n in enumerate(nodes):
            _txt = _norm_text(_n.get("value"))
            if not _txt:
                continue
            _seen: set[str] = set()
            for _m in _NUM_PATTERN.finditer(_txt):
                _name = _m.group(1)
                _num = float(_m.group(2))
                _unit = _m.group(3) or ""
                if len(_name) < 2 or _name in _seen:
                    continue
                # ★第23批 T1 修正：三重噪声过滤（年份 / 虚词指标名）
                if not _unit and _num == int(_num) and 1900 <= _num <= 2100:
                    continue      # 无单位整数且落在年份区间 → 几乎必是年份
                if any(_w in _name for _w in _METRIC_STOPWORDS):
                    continue      # 指标名含虚词 → 非真实指标
                _seen.add(_name)
                _metric[_name].append((_num, _unit, _i))
        _out: list[dict] = []
        for _name, _vals in _metric.items():
            if len(_vals) < 2:
                continue
            _idx = {v[2] for v in _vals}
            if len(_idx) < 2:
                continue
            _nums = [v[0] for v in _vals]
            _lo, _hi = min(_nums), max(_nums)
            if _lo <= 0 and _hi <= 0:
                continue
            _rel = (_hi - _lo) / max(abs(_hi), 1e-9)
            if _rel <= NUMERIC_CONFLICT_TOL:
                continue
            _ids = sorted(_idx)[:6]
            _out.append(self._mk_conflict(
                "numeric_conflict", _ids, nodes,
                f"指标「{_name}」存在冲突数值 {_lo} 与 {_hi}（相对差 {_rel:.0%}）",
                confidence=round(min(1.0, _rel), 2)))
        return _out

    def _detect_causal_contradiction(self, nodes: list[dict]) -> list[dict]:
        """同一「因」推出互斥的「果」（其中一方含否定）→ 因果矛盾。"""
        _cause: dict[str, list[tuple[str, int, bool]]] = defaultdict(list)
        for _i, _n in enumerate(nodes):
            _txt = _norm_text(_n.get("value"))
            if not _txt:
                continue
            for _m in _CAUSE_PATTERN.finditer(_txt):
                _c = _m.group(1)
                _e = _m.group(2)
                if len(_c) < 2 or len(_e) < 2:
                    continue
                _negated = any(w in _e for w in _NEG_WORDS)
                _cause[_c].append((_e, _i, _negated))
        _out: list[dict] = []
        for _c, _items in _cause.items():
            if len(_items) < 2:
                continue
            for _i1 in range(len(_items)):
                for _i2 in range(_i1 + 1, len(_items)):
                    _e1, _n1, _neg1 = _items[_i1]
                    _e2, _n2, _neg2 = _items[_i2]
                    if _n1 == _n2 or _neg1 == _neg2:
                        continue    # 必须一正一反
                    _sim = _jaccard(_e1, _e2)
                    if _sim >= 0.2:
                        _out.append(self._mk_conflict(
                            "causal_contradiction", [_n1, _n2], nodes,
                            f"「{_c}」既导致「{_e1[:18]}」又导致「{_e2[:18]}」（一正一反）",
                            confidence=round(min(1.0, _sim + 0.4), 2)))
        return _out

    def _detect_definition_inconsistency(self, nodes: list[dict]) -> list[dict]:
        """同一概念存在差异较大的多个定义 → 定义不一致。"""
        _defs: dict[str, list[tuple[str, int]]] = defaultdict(list)
        _def_re = re.compile(
            r"([\u4e00-\u9fa5A-Za-z_][\u4e00-\u9fa5A-Za-z0-9_]{1,19}?)"
            r"(?:是指|指的是|定义为)([^。；;\n]{5,80})")
        for _i, _n in enumerate(nodes):
            _txt = _norm_text(_n.get("value"))
            if not _txt:
                continue
            for _m in _def_re.finditer(_txt):
                _defs[_m.group(1)].append((_m.group(2), _i))
        _out: list[dict] = []
        for _term, _items in _defs.items():
            if len(_items) < 2:
                continue
            # ★第23批修正：同一「概念」只保留最严重的一条 —— 原实现会为 N 个节点
            #   产生 N*(N-1)/2 条重复项（实测同一概念重复占满 TOP 问题列表）。
            _worst: dict | None = None
            for _i1 in range(len(_items)):
                for _i2 in range(_i1 + 1, len(_items)):
                    _d1, _n1 = _items[_i1]
                    _d2, _n2 = _items[_i2]
                    if _n1 == _n2:
                        continue
                    _sim = _jaccard(_d1, _d2)
                    if _sim >= DEF_JACCARD_MAX:
                        continue
                    _cand = self._mk_conflict(
                        "definition_inconsistency", [_n1, _n2], nodes,
                        f"概念「{_term}」有两种差异较大的定义（相似度 {_sim:.0%}）",
                        confidence=round(1.0 - _sim, 2), needs_review=True)
                    if _worst is None or _cand["confidence"] > _worst["confidence"]:
                        _worst = _cand
            if _worst is not None:
                _out.append(_worst)
        return _out

    @staticmethod
    def _mk_conflict(ctype: str, idxs: list[int], nodes: list[dict],
                     detail: str, confidence: float = 0.5,
                     needs_review: bool = False) -> dict:
        _involved = [nodes[i] for i in idxs if 0 <= i < len(nodes)]
        return {
            "type": ctype,
            "node_ids": [str(n.get("node_id", "")) for n in _involved],
            "domains": sorted({_domain_of(n.get("space_path")) for n in _involved}),
            "severity": _severity_of(_involved),
            "confidence": confidence,
            "detail": detail,
            "needs_review": needs_review,
        }

    @staticmethod
    def _dedup_conflicts(conflicts: list[dict]) -> list[dict]:
        """按 (type, 节点集合) 去重，保留置信度最高者。"""
        _best: dict[tuple, dict] = {}
        for _c in conflicts:
            _key = (_c["type"], tuple(sorted(_c.get("node_ids", []))))
            if _key not in _best or _c.get("confidence", 0) > _best[_key].get("confidence", 0):
                _best[_key] = _c
        return sorted(_best.values(), key=lambda c: -c.get("confidence", 0))

    # ------------------------------------------------------------------
    # T2 覆盖率
    # ------------------------------------------------------------------
    def analyze_coverage(self, nodes: list[dict] | None = None) -> dict[str, Any]:
        """领域/深度/关联/时间四维覆盖分析，产出 0-100 覆盖率评分。"""
        _nodes = self._pick(nodes)
        _total = len(_nodes)
        if _total == 0:
            return {"score": 0.0, "total_nodes": 0, "domains": {}, "domain_counts": {},
                    "blind_spots": [], "islands": [], "level_dist": {},
                    "shallow_domains": [], "time_dist": {}, "sub_scores": {}}

        # ① 领域覆盖
        _dom_counts = Counter(_domain_of(n.get("space_path")) for n in _nodes)
        _min_nodes = max(LOW_DENSITY_ABS_MIN, _total * LOW_DENSITY_RATIO)
        _blind = [{"domain": d, "nodes": c,
                   "suggestion": "补充该领域的采集/推导入口（当前样本过少，难以支撑检索）"}
                  for d, c in _dom_counts.items() if c < _min_nodes]
        _blind.sort(key=lambda x: x["nodes"])
        _domain_score = 100.0 if not _dom_counts else max(
            0.0, 100.0 - len(_blind) / len(_dom_counts) * 100.0)

        # ② 深度覆盖（L1/L2/L3）
        _lv = Counter(str(n.get("evol_level") or "L1").upper() for n in _nodes)
        _deep_cnt = _lv.get("L2", 0) + _lv.get("L3", 0)
        _deep_ratio = _deep_cnt / _total
        _depth_score = max(0.0, min(100.0, _deep_ratio / DEEP_RATIO_TARGET * 100.0))
        _shallow: list[dict] = []
        for _d in _dom_counts:
            _d_nodes = [n for n in _nodes if _domain_of(n.get("space_path")) == _d]
            _d_lv = Counter(str(n.get("evol_level") or "L1").upper() for n in _d_nodes)
            if _d_lv.get("L2", 0) + _d_lv.get("L3", 0) == 0 and len(_d_nodes) >= LOW_DENSITY_ABS_MIN:
                _shallow.append({"domain": _d, "l1_only_nodes": len(_d_nodes),
                                 "suggestion": "该领域只有原始知识(L1)，缺少提炼(L2/L3)"})

        # ③ 关联覆盖（孤岛）
        _islands = [{"node_id": str(n.get("node_id", "")),
                     "domain": _domain_of(n.get("space_path")),
                     "value_brief": str(n.get("value", ""))[:40]}
                    for n in _nodes
                    if not _has_any_link(n)]
        _link_ratio = 1.0 - (len(_islands) / _total)
        # 孤岛率 0 → 100 分；≥60% → 0 分（线性）
        _assoc_score = max(0.0, min(100.0, (1.0 - len(_islands) / _total - 0.4) / 0.6 * 100.0))
        if _link_ratio >= 0.999:
            _assoc_score = 100.0

        # ④ 时间覆盖（近 90 天是否有新注入）
        _recent = 0
        _cut = self._now - 90 * 86400
        _time_dist: Counter = Counter()
        for _n in _nodes:
            _ts = _node_updated_ts(_n)
            if _ts <= 0:
                _time_dist["无时间戳"] += 1
                continue
            _bucket = f"{(self._now - _ts) / 86400 // 30:.0f}月内"
            _time_dist[_bucket] += 1
            if _ts >= _cut:
                _recent += 1
        _time_score = min(100.0, _recent / _total * 100.0 / 0.5)  # 50% 近 90 天 → 满分

        _score = (_domain_score * 0.35 + _depth_score * 0.20
                  + _assoc_score * 0.30 + _time_score * 0.15)
        return {
            "score": round(_score, 2),
            "total_nodes": _total,
            "domains": sorted(_dom_counts.keys()),
            "domain_counts": dict(_dom_counts.most_common()),
            "blind_spots": _blind,
            "shallow_domains": _shallow,
            "islands": _islands[:50],
            "island_count": len(_islands),
            "level_dist": dict(_lv),
            "time_dist": dict(_time_dist),
            "recent_90d": _recent,
            "sub_scores": {
                "domain": round(_domain_score, 2),
                "depth": round(_depth_score, 2),
                "association": round(_assoc_score, 2),
                "time": round(_time_score, 2),
            },
        }

    # ------------------------------------------------------------------
    # T3 老化
    # ------------------------------------------------------------------
    def analyze_aging(self, nodes: list[dict] | None = None,
                      consistency: dict | None = None) -> dict[str, Any]:
        """时效性 / 版本关联 / 访问频率 / 矛盾驱动 四类老化检测。"""
        _nodes = self._pick(nodes)
        _total = len(_nodes)
        if _total == 0:
            return {"score": 100.0, "total_nodes": 0, "aged_nodes": [],
                    "by_type": {}, "stats": {}}
        _cur_ver = self._current_version()
        _aged: list[dict] = []
        for _n in _nodes:
            _types: list[str] = []
            _ts = _node_updated_ts(_n) or _node_ts(_n)
            _age_days = (self._now - _ts) / 86400 if _ts > 0 else -1.0
            _ts_inferred = False
            if _age_days < 0:
                _ts_inferred = True
                _age_days = 0.0
            if _age_days > AGING_DAYS_THRESHOLD:
                _types.append("obsolescence")
            _la = _node_last_active_ts(_n)
            _idle_days = (self._now - _la) / 86400 if _la > 0 else _age_days
            if int(_n.get("activation_count") or 0) == 0 and _idle_days > DORMANT_DAYS_THRESHOLD:
                _types.append("dormant")
            if self._references_old_version(_n, _cur_ver):
                _types.append("version_stale")
            if _types:
                _aged.append({
                    "node_id": str(_n.get("node_id", "")),
                    "domain": _domain_of(_n.get("space_path")),
                    "age_days": round(_age_days, 1),
                    "idle_days": round(_idle_days, 1),
                    "types": _types,
                    "action": _suggest_action(_types),
                    "timestamp_inferred": _ts_inferred,
                    "value_brief": str(_n.get("value", ""))[:40],
                })
        # 矛盾驱动老化（依赖 T1）
        _conf = consistency if consistency is not None else self.analyze_consistency(_nodes)
        _conf_ids = {i for c in _conf.get("conflicts", []) for i in c.get("node_ids", [])}
        if _conf_ids:
            _known = {a["node_id"] for a in _aged}
            for _n in _nodes:
                _nid = str(_n.get("node_id", ""))
                if _nid in _conf_ids and _nid not in _known:
                    _ts = _node_updated_ts(_n) or _node_ts(_n)
                    _aged.append({
                        "node_id": _nid,
                        "domain": _domain_of(_n.get("space_path")),
                        "age_days": round((self._now - _ts) / 86400, 1) if _ts else -1.0,
                        "idle_days": -1.0,
                        "types": ["conflict_driven"],
                        "action": "review",
                        "timestamp_inferred": _ts <= 0,
                        "value_brief": str(_n.get("value", ""))[:40],
                    })
        _by_type: Counter = Counter()
        for _a in _aged:
            for _t in _a["types"]:
                _by_type[_t] += 1
        _aged_ratio = len(_aged) / _total
        _score = max(0.0, 100.0 - _aged_ratio * 100.0 * 1.5)
        return {
            "score": round(_score, 2),
            "total_nodes": _total,
            "current_version": _cur_ver,
            "aged_nodes": _aged[:80],
            "aged_count": len(_aged),
            "by_type": dict(_by_type),
            "stats": {
                "obsolescence": _by_type.get("obsolescence", 0),
                "dormant": _by_type.get("dormant", 0),
                "version_stale": _by_type.get("version_stale", 0),
                "conflict_driven": _by_type.get("conflict_driven", 0),
                "inferred_timestamp": sum(1 for a in _aged if a.get("timestamp_inferred")),
            },
        }

    def _current_version(self) -> tuple[int, int]:
        """当前框架版本（用于「引用旧版本」判定）：config.KNOWLEDGE_CURRENT_VERSION → 默认 9.5。"""
        try:
            import config as _cfg
            _v = getattr(_cfg, "KNOWLEDGE_CURRENT_VERSION", None)
            if isinstance(_v, str):
                _m = _VER_PATTERN.search(_v)
                if _m:
                    return int(_m.group(1)), int(_m.group(2))
        except Exception:
            pass
        return 9, 5

    @staticmethod
    def _references_old_version(node: dict, current: tuple[int, int]) -> bool:
        """节点内容引用了低于当前版本的版本号 → 可能失效。"""
        _txt = str(node.get("value", ""))
        if not _txt:
            return False
        for _m in _VER_PATTERN.finditer(_txt):
            _mj, _mn = int(_m.group(1)), int(_m.group(2))
            if _mj < 1:          # 过滤 0.x 之类噪声
                continue
            if (_mj, _mn) < current:
                return True
        return False

    # ------------------------------------------------------------------
    # T4 综合评分
    # ------------------------------------------------------------------
    def analyze_overall(self, nodes: list[dict] | None = None) -> dict[str, Any]:
        """一致性30% + 覆盖率30% + 老化20% + 深度10% + 广度10%。"""
        _nodes = self._pick(nodes)
        _cons = self.analyze_consistency(_nodes)
        _cov = self.analyze_coverage(_nodes)
        _aging = self.analyze_aging(_nodes, consistency=_cons)
        _depth = _cov["sub_scores"].get("depth", 0.0)
        _breadth = min(100.0, len(_cov.get("domains", [])) / BREADTH_TARGET * 100.0)
        _score = (_cons["score"] * _WEIGHT_CONSISTENCY
                  + _cov["score"] * _WEIGHT_COVERAGE
                  + _aging["score"] * _WEIGHT_AGING
                  + _depth * _WEIGHT_DEPTH
                  + _breadth * _WEIGHT_BREADTH)
        _score = max(0.0, min(100.0, _score))
        return {
            "score": round(_score, 2),
            "level": _grade(_score),
            "total_nodes": len(_nodes),
            "dimensions": {
                "consistency": _cons["score"],
                "coverage": _cov["score"],
                "aging": _aging["score"],
                "depth": round(_depth, 2),
                "breadth": round(_breadth, 2),
            },
            "weights": {
                "consistency": _WEIGHT_CONSISTENCY,
                "coverage": _WEIGHT_COVERAGE,
                "aging": _WEIGHT_AGING,
                "depth": _WEIGHT_DEPTH,
                "breadth": _WEIGHT_BREADTH,
            },
            "consistency": _cons,
            "coverage": _cov,
            "aging": _aging,
            "generated_at": _iso(self._now),
        }

    def generate_report(self, nodes: list[dict] | None = None) -> dict[str, Any]:
        """生成结构化知识质量报告（兼容 SelfAwarenessEngine 的 dict 契约）。"""
        _overall = self.analyze_overall(nodes)
        _cons, _cov, _aging = _overall["consistency"], _overall["coverage"], _overall["aging"]
        _top: list[dict] = []
        for _c in _cons.get("conflicts", [])[:5]:
            _top.append({"kind": "冲突", "detail": _c["detail"],
                         "severity": _c["severity"], "type": _c["type"]})
        for _b in _cov.get("blind_spots", [])[:3]:
            _top.append({"kind": "盲区", "detail": f"领域「{_b['domain']}」仅 {_b['nodes']} 个节点",
                         "severity": "medium", "type": "blind_spot"})
        for _a in _aging.get("aged_nodes", [])[:3]:
            _top.append({"kind": "老化",
                         "detail": f"节点 {_a['node_id'][:24]} 天龄 {_a['age_days']} 天，"
                                   f"类型={'/'.join(_a['types'])}",
                         "severity": "medium", "type": _a["types"][0]})
        return {
            "section": "knowledge_quality",
            "score": _overall["score"],
            "level": _overall["level"],
            "dimensions": _overall["dimensions"],
            "weights": _overall["weights"],
            "summary": (
                f"知识质量 {_overall['score']}/100（{_overall['level']}）｜"
                f"一致性 {_cons['score']}｜覆盖率 {_cov['score']}｜老化 {_aging['score']}｜"
                f"节点 {_overall['total_nodes']} 个｜冲突 {_cons['stats']['total']} 处｜"
                f"盲区领域 {len(_cov.get('blind_spots', []))} 个｜"
                f"孤岛节点 {_cov.get('island_count', 0)} 个｜"
                f"老化节点 {_aging.get('aged_count', 0)} 个"),
            "top_issues": _top,
            "suggestions": _build_suggestions(_cons, _cov, _aging),
            "stats": {
                "conflicts": _cons["stats"],
                "islands": _cov.get("island_count", 0),
                "blind_spots": len(_cov.get("blind_spots", [])),
                "aged": _aging.get("aged_count", 0),
                "level_dist": _cov.get("level_dist", {}),
            },
            "generated_at": _overall["generated_at"],
        }

    @staticmethod
    def compare_profiles(baseline: Any, current: Any) -> dict[str, Any]:
        """对比两次知识质量结果（接口与 SelfAwarenessEngine.compare_profiles 一致）。

        Returns:
            ``{"improved": [...], "degraded": [...], "unchanged": [...], "delta": {...}}``
        """
        _b = _extract_scores(baseline)
        _c = _extract_scores(current)
        if not _b or not _c:
            return {"improved": [], "degraded": [], "unchanged": [], "delta": {}}
        _improved, _degraded, _unchanged = [], [], []
        _delta: dict[str, float] = {}
        for _k in sorted(set(_b) & set(_c)):
            _d = round(_c[_k] - _b[_k], 2)
            _delta[_k] = _d
            if _d > 0.5:
                _improved.append(_k)
            elif _d < -0.5:
                _degraded.append(_k)
            else:
                _unchanged.append(_k)
        return {"improved": _improved, "degraded": _degraded,
                "unchanged": _unchanged, "delta": _delta}


# ======================================================================
# 辅助函数
# ======================================================================
def _has_any_link(node: dict) -> bool:
    """节点是否存在任何关联（linked_nodes / hebbian_weight / cooccurrence）。"""
    _ln = node.get("linked_nodes")
    if isinstance(_ln, (list, tuple, set)) and len(_ln) > 0:
        return True
    try:
        if float(node.get("hebbian_weight") or 0) > 0:
            return True
        if int(node.get("cooccurrence_count") or 0) > 0:
            return True
    except Exception:
        pass
    _sr = node.get("semantic_relations")
    return bool(isinstance(_sr, (list, tuple)) and len(_sr) > 0)


def _suggest_action(types: list[str]) -> str:
    """按老化类型给建议操作（更新/归档/删除/复核）。"""
    if "conflict_driven" in types:
        return "review"
    if "version_stale" in types:
        return "update"
    if "dormant" in types and "obsolescence" in types:
        return "archive_or_delete"
    if "obsolescence" in types:
        return "update_or_archive"
    return "review"


def _grade(score: float) -> str:
    """等级划分：优秀≥85 / 良好70-84 / 一般50-69 / 较差30-49 / 危险<30。"""
    if score >= 85:
        return "优秀"
    if score >= 70:
        return "良好"
    if score >= 50:
        return "一般"
    if score >= 30:
        return "较差"
    return "危险"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _extract_scores(data: Any) -> dict[str, float]:
    """从报告/profile 中抽取可对比的分数（容错 dict / 对象两种形态）。"""
    if data is None:
        return {}
    _d = data
    if not isinstance(_d, dict):
        _d = getattr(data, "to_dict", lambda: {})() or {}
        if hasattr(data, "knowledge_quality"):
            _kq = data.knowledge_quality
            if isinstance(_kq, dict):
                _d.setdefault("knowledge_quality", _kq)
    _kq = _d.get("knowledge_quality") if isinstance(_d, dict) else None
    if isinstance(_kq, dict):
        _d = _kq
    if not isinstance(_d, dict):
        return {}
    _out: dict[str, float] = {}
    if isinstance(_d.get("score"), (int, float)):
        _out["score"] = float(_d["score"])
    _dims = _d.get("dimensions")
    if isinstance(_dims, dict):
        for _k, _v in _dims.items():
            if isinstance(_v, (int, float)):
                _out[f"dim_{_k}"] = float(_v)
    return _out


def _build_suggestions(cons: dict, cov: dict, aging: dict) -> list[str]:
    """生成改进建议（按优先级）。"""
    _out: list[str] = []
    if cons.get("stats", {}).get("total"):
        _high = cons["stats"].get("severity", {}).get("high", 0)
        _out.append(f"优先复核 {_high} 处高危知识冲突（涉及 L3 永久锁定节点）" if _high
                    else f"复核 {cons['stats']['total']} 处知识冲突")
    for _b in cov.get("blind_spots", [])[:3]:
        _out.append(f"补充领域「{_b['domain']}」的知识采集（当前仅 {_b['nodes']} 个节点）")
    if cov.get("shallow_domains"):
        _out.append(f"对 {len(cov['shallow_domains'])} 个「只有 L1 无提炼」的领域触发 L2/L3 归纳")
    if cov.get("island_count"):
        _out.append(f"为 {cov['island_count']} 个孤岛节点建立关联（提升检索可达性）")
    if aging.get("stats", {}).get("version_stale"):
        _out.append(f"更新 {aging['stats']['version_stale']} 个引用旧版本的知识节点")
    if aging.get("stats", {}).get("dormant"):
        _out.append(f"评估 {aging['stats']['dormant']} 个休眠知识是否归档")
    return _out


_analyzer_singleton: KnowledgeQualityAnalyzer | None = None


def get_knowledge_quality_analyzer(**kwargs: Any) -> KnowledgeQualityAnalyzer:
    """进程级单例（参数仅在首次创建时生效）。"""
    global _analyzer_singleton
    if _analyzer_singleton is None:
        _analyzer_singleton = KnowledgeQualityAnalyzer(**kwargs)
    return _analyzer_singleton


def reset_knowledge_quality_analyzer() -> None:
    """重置单例（测试用）。"""
    global _analyzer_singleton
    _analyzer_singleton = None
