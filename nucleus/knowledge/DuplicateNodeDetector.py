# -*- coding: utf-8 -*-
"""
DuplicateNodeDetector.py —— 重复节点检测器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 检测知识库中的重复与相似节点
机制: 基于DuplicateNodeDetector类实现，包含10个核心方法
定位: 知识治理层
"""

from __future__ import annotations

import re
from typing import Any
from collections.abc import Iterable


__all__ = [
    "FLAG_DUPLICATE",
    "DuplicateNodeDetector",
    "find_duplicate_groups",
    "mark_duplicates",
]

# 打在重复节点上的质量标记（沿用 PollutionTagger 既有词表，便于将来统一降权）
FLAG_DUPLICATE = "suspect"

_RE_TOKEN = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9._-]*")
_MIN_KEYWORDS = 3          # 关键词少于此数时，退化为正文分词
_MIN_SHARED_TOKENS = 2     # 倒排索引候选对最少共享 token 数


def _keywords(node: Any) -> set[str]:
    """取节点关键词集合（小写去空白）。"""
    _raw = node.get("keywords") if isinstance(node, dict) else getattr(node, "keywords", None)
    if not _raw:
        return set()
    if isinstance(_raw, str):
        _raw = [_raw]
    return {str(k).strip().lower() for k in _raw if str(k).strip()}


def _value(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("value", "") or "")
    return str(getattr(node, "value", "") or "")


def _space_path(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("space_path", "") or "")
    return str(getattr(node, "space_path", "") or "")


def _node_id(node: Any) -> str:
    if isinstance(node, dict):
        return str(node.get("node_id", "") or "")
    return str(getattr(node, "node_id", "") or "")


def tokens_of(node: Any) -> set[str]:
    """节点特征 token 集合：优先用关键词，关键词太少时退化为正文分词。"""
    _kw = _keywords(node)
    if len(_kw) >= _MIN_KEYWORDS:
        return _kw
    return {t.lower() for t in _RE_TOKEN.findall(_value(node)) if len(t) >= 2}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class DuplicateNodeDetector:
    """重复节点检测器（无状态、线程安全，纯内存，不做任何 I/O）。"""

    def __init__(self, threshold: float = 0.8):
        self.threshold = float(threshold)

    # ---------------- 检测 ----------------

    def find_groups(self, nodes: Iterable[Any],
                    path_prefix: str | None = None,
                    legacy_prefixes: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """找出重复节点分组。

        Args:
            nodes:           节点 dict 或带属性的节点对象
            path_prefix:     只检测该路径前缀下的节点（None = 全量）
            legacy_prefixes: 旧路径前缀列表；分组内优先把旧路径节点判为"重复方"

        Returns:
            [{"keep": node, "duplicates": [node...], "similarity": float,
              "tokens": set}, ...]
        """
        _pool = [n for n in nodes
                 if not path_prefix or _space_path(n).startswith(path_prefix)]
        _legacy = tuple(legacy_prefixes or ())
        # 关键词/Jaccard 近重复需要跨路径比对：若指定了 path_prefix，
        # 仍需在**全量**里找对照节点，否则"旧路径唯一节点"永远比不出重复。
        _universe = list(nodes)
        _pool_ids = {id(n) for n in _pool}

        _tok = {id(n): tokens_of(n) for n in _universe}
        _tok_pool = {id(n): _tok[id(n)] for n in _pool}

        # 倒排索引：token → 节点列表（只为减少候选对数量）
        _index: dict[str, list[Any]] = {}
        for n in _universe:
            for t in _tok[id(n)]:
                _index.setdefault(t, []).append(n)

        # 生成候选对：pool 节点 × 与之共享 >= _MIN_SHARED_TOKENS 个 token 的节点
        _pairs: set[tuple[int, int]] = set()
        for n in _pool:
            _counter: dict[int, int] = {}
            for t in _tok_pool[id(n)]:
                for m in _index.get(t, ()):
                    if m is n:
                        continue
                    _k = id(m)
                    _counter[_k] = _counter.get(_k, 0) + 1
            for _k, _c in _counter.items():
                if _c >= _MIN_SHARED_TOKENS:
                    _pairs.add(tuple(sorted((id(n), _k))))

        # 并查集聚合
        _parent: dict[int, int] = {id(n): id(n) for n in _universe}

        def _find(x):
            while _parent[x] != x:
                _parent[x] = _parent[_parent[x]]
                x = _parent[x]
            return x

        def _union(a, b):
            ra, rb = _find(a), _find(b)
            if ra != rb:
                _parent[rb] = ra

        _by_id = {id(n): n for n in _universe}
        _sim: dict[tuple[int, int], float] = {}
        for a, b in _pairs:
            s = jaccard(_tok[a], _tok[b])
            if s >= self.threshold:
                _sim[(a, b)] = s
                _union(a, b)

        _groups: dict[int, list[Any]] = {}
        for n in _universe:
            _groups.setdefault(_find(id(n)), []).append(n)

        _out = []
        for _members in _groups.values():
            if len(_members) < 2:
                continue
            _sims = [v for k, v in _sim.items()
                     if k[0] in {id(x) for x in _members}
                     and k[1] in {id(x) for x in _members}]
            _keep = self._pick_keeper(_members, _legacy, _pool_ids)
            _out.append({
                "members": _members,
                "keep": _keep,
                # ★只把「待清理范围内」的节点判为重复方：
                #   范围外的对照节点（如规范路径节点）只用来证明重复成立，
                #   绝不能被顺带标记 —— 否则一次清理会误伤全库。
                "duplicates": [m for m in _members
                               if id(m) in _pool_ids and id(m) != id(_keep)],
                "similarity": round(max(_sims) if _sims else 1.0, 4),
            })
        return [g for g in _out if g["duplicates"]]

    @staticmethod
    def _pick_keeper(members: list[Any], legacy_prefixes: tuple[str, ...],
                     pool_ids: set[int] | None = None) -> Any:
        """挑选保留方。

        优先级：① 不在清理范围内（范围外的对照节点优先保留）
               → ② 非旧路径 → ③ 路径更浅（更规范）→ ④ 正文更长 → ⑤ node_id 稳定序
        """
        _pool = pool_ids or set()

        def _is_legacy(n):
            p = _space_path(n)
            return any(p.startswith(pre) for pre in legacy_prefixes) if legacy_prefixes else False

        return sorted(
            members,
            key=lambda n: (
                id(n) in _pool,                      # False(0) 排前 → 范围外节点优先保留
                _is_legacy(n),                       # False(0) 排前 → 非旧路径优先保留
                len([x for x in _space_path(n).split("/") if x]),
                -len(_value(n)),
                _node_id(n),
            ),
        )[0]

    # ---------------- 标记 ----------------

    @staticmethod
    def mark(groups: Iterable[dict[str, Any]],
             flag: str = FLAG_DUPLICATE,
             reason_prefix: str = "D1:重复节点") -> int:
        """给重复方打标记（**不删除任何节点**）。

        写入字段：
          quality_flag   = flag（沿用 clean/suspect/polluted 词表）
          quality_reason = "D1:重复节点(与 node:xxx 内容重复)"
          duplicate_of   = 保留方的 node_id（便于回溯）

        Returns:
            被标记的节点数
        """
        _n = 0
        for g in groups:
            _keep = g.get("keep")
            _kid = _node_id(_keep)
            for _dup in g.get("duplicates", []):
                _reason = f"{reason_prefix}(与 {_kid} 内容重复)" if _kid else reason_prefix
                if isinstance(_dup, dict):
                    _dup["quality_flag"] = flag
                    _dup["quality_reason"] = _reason
                    _dup["duplicate_of"] = _kid
                else:
                    try:
                        _dup.quality_flag = flag
                        _dup.quality_reason = _reason
                        _dup.duplicate_of = _kid
                    except Exception:
                        continue
                _n += 1
        return _n


def find_duplicate_groups(nodes: Iterable[Any], path_prefix: str | None = None,
                          legacy_prefixes: Iterable[str] | None = None,
                          threshold: float = 0.8) -> list[dict[str, Any]]:
    """模块级快捷函数。"""
    return DuplicateNodeDetector(threshold).find_groups(
        nodes, path_prefix=path_prefix, legacy_prefixes=legacy_prefixes)


def mark_duplicates(groups: Iterable[dict[str, Any]],
                    flag: str = FLAG_DUPLICATE) -> int:
    """模块级快捷函数：只标记不删除。"""
    return DuplicateNodeDetector.mark(groups, flag=flag)
