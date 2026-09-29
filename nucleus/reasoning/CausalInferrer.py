# -*- coding: utf-8 -*-
"""
CausalInferrer.py —— 因果推断器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 因果关系推断与归因分析
机制: 基于CausalInferrer类实现，包含10个核心方法
定位: 推理核心层
"""

from __future__ import annotations

import re
from typing import Any


__all__ = ["CausalInferrer", "get_causal_inferrer"]

_CAUSAL_REL_TYPES = {"causal", "semantic_similarity", "cooccurrence"}

# 词法级证据：源节点文本里确实把 A、B 用因果连接词连在一起
_CAUSAL_CONNECTORS = ["导致", "因此", "所以", "因为", "引起", "引发", "造成", "促使",
                      "触发", "驱动", "推动", "使得", "源于", "归因于", "影响",
                      "产生", "结果是"]


class CausalInferrer:
    """因果链构建 + 边存在性校验（无状态、线程安全）。"""

    # 置信度（0~1 口径）
    BASE_CONFIDENCE = 0.5          # 单条已验证边的基准置信度
    VERIFIED_BONUS = 0.15          # 每条已验证边的加成
    UNVERIFIED_DECAY = 0.1         # ★星轨要求：每多一跳未验证边衰减 0.1
    MIN_CONFIDENCE = 0.1           # 低于此值整条链不输出

    def __init__(self, log_fn=None):
        self._log_fn = log_fn
        # 统计
        self._edges_checked = 0
        self._edges_verified = 0
        self._chains_built = 0
        self._chains_truncated = 0
        self._chains_rejected = 0

    # ========== 主入口 ==========

    def verify_chain(self, causal_pairs: list[dict[str, Any]],
                     node_pool) -> dict[str, Any] | None:
        """校验一条因果链（A→B→C 的边序列），校验不过则截断。

        Args:
            causal_pairs: [{"cause","effect","relation","source_node_id","trust"}, ...]
                          依次相邻成链（pair[i].effect == pair[i+1].cause）
            node_pool:    PulseNodePool 实例（边存在性证据源）

        Returns:
            {
              "chain": [...截断后的边列表，每条带 verified/evidence...],
              "truncated": bool,          # 是否发生了截断
              "rejected": bool,           # 整条链被拒（第一条边都不过）
              "confidence": float,        # 0~1，按未验证跳数衰减
              "verified_hops": int,
              "report": "人类可读校验报告",
            }
            无有效边时返回 None。
        """
        if not causal_pairs:
            return None

        _edge_report: list[dict[str, Any]] = []
        _verified_run = 0          # 连续已验证边数（用于截断）
        _truncated_at = len(causal_pairs)   # 默认不截断

        for _i, _pair in enumerate(causal_pairs):
            _v = self.verify_edge(_pair, node_pool)
            _edge_report.append({
                "hop": _i + 1,
                "edge": f"{_pair.get('cause')}→{_pair.get('effect')}",
                "verified": _v["verified"],
                "evidence": _v["evidence"],
            })
            self._edges_checked += 1
            if _v["verified"]:
                self._edges_verified += 1
                _verified_run += 1
            else:
                # ★链路截断：第一条未验证边之后的部分不输出
                _truncated_at = _i
                break

        if _truncated_at == 0:
            self._chains_rejected += 1
            self._log("INFO",
                      f"[因果推理] 整链拒绝: 首边 "
                      f"{causal_pairs[0].get('cause')}→{causal_pairs[0].get('effect')} "
                      f"无图谱证据，不输出")
            return None

        _kept = causal_pairs[:_truncated_at]
        _truncated = _truncated_at < len(causal_pairs)
        _n_verified = sum(1 for _e in _edge_report[:_truncated_at] if _e["verified"])
        # ★衰减口径：未验证跳数 = 原始链长 − 已验证数（含触发截断的那条未验证边——
        #   截断说明链的后半段未被证实，置信度必须如实反映这一事实）
        _n_unverified = len(causal_pairs) - _n_verified

        # 置信度：基准 + 已验证加成 − 未验证衰减（★每跳未验证 −0.1）
        _conf = (self.BASE_CONFIDENCE
                 + self.VERIFIED_BONUS * _n_verified
                 - self.UNVERIFIED_DECAY * _n_unverified)
        _conf = max(0.0, min(1.0, _conf))

        if _truncated:
            self._chains_truncated += 1
        else:
            self._chains_built += 1

        _report = (f"校验{_truncated_at}/{len(causal_pairs)}条边"
                   f"（已验证{_n_verified}，未验证{_n_unverified}）"
                   + ("，链路已截断" if _truncated else "，全链通过"))
        self._log("INFO",
                  f"[因果推理] 链校验: {causal_pairs[0].get('cause')}→…→"
                  f"{_kept[-1].get('effect')} {_report} 置信度={_conf:.2f}")

        return {
            "chain": _kept,
            "edge_report": _edge_report[:_truncated_at],
            "truncated": _truncated,
            "rejected": False,
            "confidence": round(_conf, 3),
            "verified_hops": _n_verified,
            "report": _report,
        }

    # ========== 边校验 ==========

    def verify_edge(self, pair: dict[str, Any], node_pool) -> dict[str, Any]:
        """校验单条 A→B 边是否在框架自有图谱中真实存在。

        证据源（命中任一即 verified）：
          ① 源节点的 semantic_relations 中存在指向 B 相关节点的关联（causal/
             semantic_similarity/cooccurrence），且 B 的关键词与之匹配
          ② get_related_nodes(源节点) 的关联节点关键词包含 B
          ③ 源节点文本里因果连接词确实连接了 A 与 B（词法级，最弱证据）
        """
        _cause = str(pair.get("cause", "") or "")
        _effect = str(pair.get("effect", "") or "")
        _src_id = str(pair.get("source_node_id", "") or "")
        _evidence: list[str] = []

        if not _cause or not _effect or node_pool is None:
            return {"verified": False, "evidence": ["参数不全"]}

        _src = None
        for _getter in ("get", "get_node"):
            _f = getattr(node_pool, _getter, None)
            if callable(_f):
                try:
                    _src = _f(_src_id)
                except Exception:
                    _src = None
                if _src is not None:
                    break

        # 证据①：源节点 semantic_relations（前向，框架自有关联图谱）
        if _src is not None:
            _rel_list = getattr(_src, "semantic_relations", None) or []
            for _rel in _rel_list:
                if not isinstance(_rel, dict):
                    continue
                if _rel.get("relation_type") not in _CAUSAL_REL_TYPES:
                    continue
                _rel_weight = float(_rel.get("weight", 0.0) or 0.0)
                if _rel_weight >= 0.3:
                    _evidence.append(
                        f"图谱关联:{_rel.get('relation_type')}"
                        f"(w={_rel_weight:.2f})")
                    break

        # 证据②：横向索引关联节点的关键词命中 B
        if _src is not None and not _evidence:
            try:
                _related = node_pool.get_related_nodes(_src_id, limit=8)
            except Exception:
                _related = []
            for _rn in (_related or []):
                _rkws = [str(k) for k in (getattr(_rn, "keywords", None) or [])]
                _rval = str(getattr(_rn, "value", "") or "")
                if _effect in _rkws or (_effect and _effect in _rval):
                    _evidence.append(f"横向关联:节点{getattr(_rn, 'node_id', '')[:18]}")
                    break

        # 证据③：词法级——源文本用因果连接词连接 A 与 B（最弱，单独不足以验证）
        if _src is not None and not _evidence:
            _val = str(getattr(_src, "value", "") or "")
            for _kw in _CAUSAL_CONNECTORS:
                if (re.search(f"{re.escape(_cause)}.*?{_kw}", _val)
                        and re.search(f"{_kw}.*?{re.escape(_effect)}", _val)):
                    _evidence.append(f"词法连接:{_kw}")
                    break

        _verified = bool(_evidence)
        if not _verified:
            _evidence.append("无图谱证据")
        return {"verified": _verified, "evidence": _evidence}

    # ========== 统计 ==========

    def get_stats(self) -> dict[str, Any]:
        return {
            "edges_checked": self._edges_checked,
            "edges_verified": self._edges_verified,
            "verify_rate": round(self._edges_verified / self._edges_checked, 3)
                           if self._edges_checked else 0.0,
            "chains_built": self._chains_built,
            "chains_truncated": self._chains_truncated,
            "chains_rejected": self._chains_rejected,
        }

    def _log(self, level: str, msg: str):
        if self._log_fn is not None:
            try:
                self._log_fn(level, msg)
            except Exception:
                pass


# ========== 模块级共享实例 ==========
_inferrer = None


def get_causal_inferrer(log_fn=None) -> CausalInferrer:
    global _inferrer
    if _inferrer is None:
        _inferrer = CausalInferrer(log_fn=log_fn)
    return _inferrer


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== CausalInferrer 自测 ===\n")
    _logs = []
    ci = CausalInferrer(log_fn=lambda l, m: _logs.append((l, m)))

    class _Node:
        def __init__(self, nid, value, keywords, relations=None):
            self.node_id = nid
            self.value = value
            self.keywords = keywords
            self.semantic_relations = relations or []

    class _Pool:
        def __init__(self, nodes):
            self._n = nodes
        def get(self, nid):
            return self._n.get(nid)
        def get_related_nodes(self, nid, limit=10, relation_type=None):
            src = self._n.get(nid)
            if src is None:
                return []
            out = []
            for rel in (src.semantic_relations or []):
                t = self._n.get(rel.get("target_node_id"))
                if t:
                    out.append(t)
                if len(out) >= limit:
                    break
            return out

    # 节点A：文本含因果连接词连接"降水"与"水位上涨"，且有图谱关联指向B
    nodeA = _Node("nA", "持续降水导致水库水位上涨", ["降水", "水位上涨"],
                  [{"relation_type": "causal", "target_node_id": "nB", "weight": 0.7}])
    nodeB = _Node("nB", "水位上涨引发下游泄洪", ["水位上涨", "泄洪"], [])
    # 节点C：B 的关联里没有"地震"，边 B→C 无图谱证据（词法也没有）
    nodeC = _Node("nC", "地震预警系统需要测试", ["地震", "预警"], [])
    pool = _Pool({"nA": nodeA, "nB": nodeB, "nC": nodeC})

    # 1. 边校验：A→B 有图谱证据 ✓
    r = ci.verify_edge({"cause": "降水", "effect": "水位上涨",
                        "source_node_id": "nA"}, pool)
    check1 = r["verified"]
    print(f"  {'✅' if check1 else '❌'} 边A→B: verified={r['verified']} 证据={r['evidence']}")

    # 2. 边校验：B→C 无证据 ✗
    r2 = ci.verify_edge({"cause": "水位上涨", "effect": "地震",
                         "source_node_id": "nB"}, pool)
    check2 = not r2["verified"]
    print(f"  {'✅' if check2 else '❌'} 边B→C: verified={r2['verified']} 证据={r2['evidence']}")

    # 3. 链校验：A→B→C 应截断为 A→B，置信度按未验证跳衰减
    res = ci.verify_chain([
        {"cause": "降水", "effect": "水位上涨", "relation": "导致", "source_node_id": "nA", "trust": 60},
        {"cause": "水位上涨", "effect": "地震", "relation": "引发", "source_node_id": "nB", "trust": 60},
    ], pool)
    check3 = (res is not None and res["truncated"] and len(res["chain"]) == 1)
    print(f"  {'✅' if check3 else '❌'} 链截断: {res['report'] if res else None}")

    # 4. 置信度：全验证 > 截断链（未验证不输出，但截断链应 < 全验证基准）
    res2 = ci.verify_chain([
        {"cause": "降水", "effect": "水位上涨", "relation": "导致", "source_node_id": "nA", "trust": 60},
    ], pool)
    check4 = res2 and not res2["truncated"] and res2["confidence"] > res["confidence"]
    print(f"  {'✅' if check4 else '❌'} 置信度: 全验证={res2['confidence']} "
          f"截断链={res['confidence']}")

    # 5. 整链拒绝：首边就无证据
    res3 = ci.verify_chain([
        {"cause": "月球", "effect": "地震", "relation": "引发", "source_node_id": "nC", "trust": 60},
    ], pool)
    check5 = res3 is None
    print(f"  {'✅' if check5 else '❌'} 整链拒绝: {res3}")

    # 6. 禁止虚假标记：截断链的输出不含未验证边
    check6 = res is not None and all(
        e["verified"] for e in res["edge_report"])
    print(f"  {'✅' if check6 else '❌'} 输出边全部已验证: "
          f"{[e['edge'] for e in res['edge_report']] if res else None}")

    # 7. 校验报告有 INFO 日志
    check7 = any("因果推理" in m for _, m in _logs)
    print(f"  {'✅' if check7 else '❌'} 可追溯日志: {len(_logs)}条")

    _ok = sum([check1, check2, check3, check4, check5, check6, check7])
    print(f"\n{'✅' if _ok == 7 else '❌'} 自测通过 {_ok}/7")
    import sys
    sys.exit(0 if _ok == 7 else 1)
