# -*- coding: utf-8 -*-
"""
PollutionTagger.py —— 污染标记器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 标记知识库中的污染与低质量节点
机制: 基于PollutionTagger类实现，包含10个核心方法
定位: 知识治理层
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from typing import Any


try:
    # 正常导入路径（框架内）：走统一日志系统
    from nucleus.logger import get_module_logger

    _logger = get_module_logger("PollutionTagger")
except Exception:  # pragma: no cover
    # ★兜底：本文件末尾自带 `python PollutionTagger.py` 自测入口，
    #   直接运行时 sys.path 里没有项目根，`import nucleus...` 会失败。
    #   此时退回标准 logging，保证自测入口不被这条导入卡死。
    import logging

    _logger = logging.getLogger("PollutionTagger")

__all__ = [
    "FLAG_CLEAN",
    "FLAG_POLLUTED",
    "FLAG_SUSPECT",
    "PollutionTagger",
    "get_shared_tagger",
]

FLAG_CLEAN = "clean"
FLAG_SUSPECT = "suspect"
FLAG_POLLUTED = "polluted"

# 降权系数（config.POLLUTION_TAGGING_CONFIG 可覆盖）
DEFAULT_FACTORS = {FLAG_CLEAN: 1.0, FLAG_SUSPECT: 0.7, FLAG_POLLUTED: 0.3}

_MARK_PREFIX = "[已标记错误"
_RE_QUESTION = re.compile(r'^(什么是|什么是|如何|为什么|怎么|谁|哪个|能否|是否|哪里)|[？?]\s*$')
_RE_INVALID_EXP = re.compile(r'(未验证|无法判断|未知结论|不确定结论|无效经验|解析失败)')


class PollutionTagger:
    """污染节点标记器（无状态、线程安全）。"""

    def __init__(self, config: dict[str, Any] | None = None, log_fn=None):
        _cfg = config or {}
        self._factors = {
            FLAG_CLEAN: 1.0,
            FLAG_SUSPECT: float(_cfg.get("suspect_factor", DEFAULT_FACTORS[FLAG_SUSPECT])),
            FLAG_POLLUTED: float(_cfg.get("polluted_factor", DEFAULT_FACTORS[FLAG_POLLUTED])),
        }
        self._max_depth = int(_cfg.get("max_path_depth", 7))
        self._long_seed = int(_cfg.get("long_seed_threshold", 500))
        self._log_fn = log_fn
        # 统计
        self._scanned = 0
        self._counts = {FLAG_CLEAN: 0, FLAG_SUSPECT: 0, FLAG_POLLUTED: 0}
        self._reasons: dict[str, int] = {}
        # ★P2-25：显式标记命中的计数，以及"已打过降权日志"的 node_id 集合
        #   （降权在检索热路径上，按 node_id 去重，避免每轮检索重复刷屏）
        self._explicit_hits = 0
        self._logged_nodes: set[str] = set()
        # node_id → flag 缓存（共振引擎热路径避免重复判定）
        self._cache: dict[str, tuple[str, str]] = {}

    # ========== 判定 ==========

    def classify(self, node: Any) -> tuple[str, str]:
        """判定节点质量等级。

        Args:
            node: PulseNode 对象 或 节点 dict

        Returns:
            (flag, reason)，flag ∈ clean/suspect/polluted
        """
        if node is None:
            return FLAG_CLEAN, "空节点"

        _d = node if isinstance(node, dict) else self._to_dict(node)
        _nid = str(_d.get("node_id", "") or "")
        if _nid and _nid in self._cache:
            return self._cache[_nid]

        _flag, _reason = self._classify_uncached(_d)
        if _nid:
            # 缓存上限保护（避免长会话内存膨胀）
            if len(self._cache) > 20000:
                self._cache.clear()
            self._cache[_nid] = (_flag, _reason)
        return _flag, _reason

    def _classify_uncached(self, _d: dict) -> tuple[str, str]:
        # ★P2-25（2026-09-09 第二批任务1）：**显式质量标记优先** —— 修复假闭环第 5 例。
        #   原实现只按正文/路径/关键词现算，**从不读节点上的 quality_flag**，
        #   于是"标记"写进去了却没有任何消费方：第七批 ENABLE_POLLUTION_TAGGING 以来
        #   所有污染节点标记从未真正降权（实测快照 9961 节点中 29 个 suspect +
        #   7 个 polluted 全部处于"已标记但未生效"状态）。
        #   现加短路：显式 quality_flag ∈ {suspect, polluted} → 直接返回，不再现算。
        #   ⚠ 零冲突：值为 clean 或字段缺失时**完全走原有现算路径**，与之前逐字节一致；
        #      实测 9925/9961 为 clean，均不受影响。
        #   ⚠ 已知副作用：标记写入后会"粘住"——内容后来修好了也不会自动恢复，
        #      需显式改回 clean（`DataQualityGuard.clear_flag()` 提供该能力）。
        _explicit = str(_d.get("quality_flag", "") or "").strip().lower()
        if _explicit in (FLAG_SUSPECT, FLAG_POLLUTED):
            self._explicit_hits += 1
            _why = str(_d.get("quality_reason", "") or "").strip()
            return _explicit, (f"E1:显式标记({_explicit})"
                               + (f" {_why}" if _why else ""))

        _v = str(_d.get("value", "") or "")
        _path = str(_d.get("space_path", "") or "/")
        _depth = self.path_depth(_path)

        # ---- polluted ----
        if _MARK_PREFIX in _v:
            return FLAG_POLLUTED, "P1:已被错误标记工具标记"
        if "【种子记忆】" in _v and len(_v) > self._long_seed:
            return FLAG_POLLUTED, f"P2:过长种子记忆节点(len={len(_v)})"
        if _depth > 10:
            return FLAG_POLLUTED, f"P3:路径严重过深({_depth}层)"
        if _v.startswith("我了解到") and len(_v) > 200:
            return FLAG_POLLUTED, "P4:过长的自言自语节点"

        # ---- suspect ----
        if _v.startswith("我了解到") or "此刻的我——" in _v:
            return FLAG_SUSPECT, "S1:自言自语/独白节点"
        if _depth > self._max_depth:
            return FLAG_SUSPECT, f"S2:路径过深({_depth}层)>{self._max_depth}"
        if _RE_QUESTION.search(_v) and "/问答" not in _path and len(_v) < 120:
            return FLAG_SUSPECT, "S3:问题被误存为知识节点"
        if "/推理经验" in _path and _RE_INVALID_EXP.search(_v):
            return FLAG_SUSPECT, "S4:推理经验库中的无效经验"
        _kws = [k for k in (_d.get("keywords") or []) if k and len(str(k)) >= 2]
        if len(_v.strip()) < 8 and not _kws:
            return FLAG_SUSPECT, "S5:内容过短且无有效关键词"

        return FLAG_CLEAN, ""

    # ========== 扫描 ==========

    def scan_pool(self, node_pool, log_fn=None, apply_to_node: bool = True) -> dict[str, Any]:
        """全量扫描节点池，把 quality_flag 写到节点对象上（**不删除任何节点**）。

        Args:
            node_pool: PulseNodePool 实例（需实现 iter_all_nodes / all_nodes / get_all_nodes）
            log_fn:    日志回调 (level, msg)
            apply_to_node: 是否把 flag 写回节点对象的 quality_flag 属性

        Returns:
            统计字典 {scanned, polluted, suspect, clean, reasons}
        """
        _log = log_fn or self._log_fn
        _nodes = self._iter_nodes(node_pool)
        _t0 = time.time()

        for _n in _nodes:
            try:
                _flag, _reason = self.classify(_n)
            except Exception:
                continue
            self._scanned += 1
            self._counts[_flag] = self._counts.get(_flag, 0) + 1
            if _reason:
                _key = _reason.split(":", 1)[0]
                self._reasons[_key] = self._reasons.get(_key, 0) + 1

            if apply_to_node and not isinstance(_n, dict):
                try:
                    if getattr(_n, "quality_flag", FLAG_CLEAN) != _flag:
                        _n.quality_flag = _flag
                        _n.quality_reason = _reason
                except Exception:
                    pass

            if _flag != FLAG_CLEAN and _log is not None:
                _nid = str((_n if isinstance(_n, dict) else getattr(_n, "node_id", "")) or "")
                try:
                    _log("INFO", f"[污染标记] 节点{_nid[:18]} 标记={_flag} "
                                 f"原因={_reason} 路径={str(_n.get('space_path') if isinstance(_n, dict) else getattr(_n, 'space_path', ''))[:30]}")
                except Exception:
                    pass

        _stats = self.get_stats()
        _stats["elapsed_ms"] = int((time.time() - _t0) * 1000)
        if _log is not None:
            try:
                _log("INFO", f"[污染标记] 扫描完成: 共{_stats['scanned']}个节点，"
                             f"polluted={_stats['polluted']}, suspect={_stats['suspect']}, "
                             f"clean={_stats['clean']}（只标记不删除）")
            except Exception:
                pass
        return _stats

    def scan_nodes(self, nodes: Iterable[Any], log_fn=None, apply_to_node: bool = True) -> dict[str, Any]:
        """扫描任意节点可迭代对象（不依赖节点池接口）。"""
        _log = log_fn or self._log_fn
        for _n in nodes:
            try:
                _flag, _reason = self.classify(_n)
            except Exception:
                continue
            self._scanned += 1
            self._counts[_flag] = self._counts.get(_flag, 0) + 1
            if _reason:
                _key2 = _reason.split(":", 1)[0]
                self._reasons[_key2] = self._reasons.get(_key2, 0) + 1
            if apply_to_node and not isinstance(_n, dict):
                try:
                    if getattr(_n, "quality_flag", FLAG_CLEAN) != _flag:
                        _n.quality_flag = _flag
                        _n.quality_reason = _reason
                except Exception:
                    pass
            if _flag != FLAG_CLEAN and _log is not None:
                try:
                    _log("INFO", f"[污染标记] 标记={_flag} 原因={_reason}")
                except Exception:
                    pass
        return self.get_stats()

    # ========== 降权 ==========

    def downweight_factor(self, node: Any) -> float:
        """检索降权系数（clean=1.0 / suspect=0.7 / polluted=0.3）。

        ★P2-25：本方法此前是**死路**——它调 classify()，而 classify() 不读
        quality_flag，所以外部写进去的标记永远不会体现在系数上。短路修好之后，
        这里补一条 DEBUG 日志让"降权真的发生"这件事**可被观测**（假闭环的第
        一道防线就是可观测）。
        仅在**显式标记触发**的降权上打印，并按 node_id 去重——
        检索热路径每轮都会走到这里，不去重会刷屏。
        """
        _flag, _ = self.classify(node)
        _f = self._factors.get(_flag, 1.0)
        if _f < 1.0:
            try:
                _d = node if isinstance(node, dict) else self._to_dict(node)
                _explicit = str(_d.get("quality_flag", "") or "").strip().lower()
                if _explicit in (FLAG_SUSPECT, FLAG_POLLUTED):
                    _nid = str(_d.get("node_id", ""))[:18]
                    if _nid and _nid not in self._logged_nodes:
                        if len(self._logged_nodes) > 2000:
                            self._logged_nodes.clear()
                        self._logged_nodes.add(_nid)
                        _logger.debug(
                            f"[污染降权] 节点{_nid} quality_flag={_explicit} "
                            f"降权系数={_f}（显式标记生效）")
            except Exception as _e:
                _logger.debug(f"[污染降权] 日志记录失败（不影响检索）: {_e}")
        return _f

    # ========== 工具 ==========

    @staticmethod
    def path_depth(path: str) -> int:
        """路径深度（"/a/b/c" → 3；空/根 → 0）。"""
        if not path:
            return 0
        return len([p for p in str(path).split("/") if p])

    def get_stats(self) -> dict[str, Any]:
        return {
            "scanned": self._scanned,
            "polluted": self._counts.get(FLAG_POLLUTED, 0),
            "suspect": self._counts.get(FLAG_SUSPECT, 0),
            "clean": self._counts.get(FLAG_CLEAN, 0),
            "reasons": dict(self._reasons),
            "factors": dict(self._factors),
            # ★P2-25：显式标记命中数。>0 才说明"外部写入的标记"真的被读到了——
            #   这是判断短路是否生效的直接证据（假闭环自检指标）。
            "explicit_hits": self._explicit_hits,
        }

    def reset_stats(self):
        self._scanned = 0
        self._counts = {FLAG_CLEAN: 0, FLAG_SUSPECT: 0, FLAG_POLLUTED: 0}
        self._reasons = {}
        self._explicit_hits = 0

    # ========== 内部 ==========

    @staticmethod
    def _to_dict(node: Any) -> dict:
        """把 PulseNode 转为轻量 dict（避免 to_dict 的全量开销）。"""
        if hasattr(node, "to_dict"):
            try:
                return node.to_dict()
            except Exception:
                pass
        return {
            "node_id": getattr(node, "node_id", ""),
            "value": getattr(node, "value", ""),
            "space_path": getattr(node, "space_path", "/"),
            "keywords": getattr(node, "keywords", []),
        }

    @staticmethod
    def _iter_nodes(node_pool) -> list:
        """从节点池取全量节点（多接口兼容，取不到返回空列表）。"""
        for _meth in ("iter_all_nodes", "get_all_nodes", "all_nodes"):
            _f = getattr(node_pool, _meth, None)
            if callable(_f):
                try:
                    return list(_f())
                except Exception:
                    continue
        # 兜底：直接读内部池（只读，不修改）
        _out = []
        for _attr in ("_hot", "_warm", "_cold"):
            _pool = getattr(node_pool, _attr, None)
            if isinstance(_pool, dict):
                _out.extend(_pool.values())
        return _out


# ========== 模块级共享实例 ==========
_tagger: PollutionTagger | None = None


def get_shared_tagger(config: dict[str, Any] | None = None, log_fn=None) -> PollutionTagger:
    """获取共享标记器（配置变化时重建）。"""
    global _tagger
    if _tagger is None:
        _tagger = PollutionTagger(config=config, log_fn=log_fn)
    return _tagger


def reset_shared_tagger():
    global _tagger
    _tagger = None


# ========== 自测 ==========
if __name__ == "__main__":
    print("=== PollutionTagger 自测 ===\n")
    _t = PollutionTagger()

    _cases = [
        ({"node_id": "n1", "value": "正常的知识内容，讲清楚一个概念", "keywords": ["概念"],
          "space_path": "/科技/AI"}, FLAG_CLEAN),
        ({"node_id": "n2", "value": "[已标记错误-待清理] 特斯拉共振是物理现象", "keywords": [],
          "space_path": "/自我/知识/共振引擎"}, FLAG_POLLUTED),
        ({"node_id": "n3", "value": "【种子记忆】" + "很长的内容" * 120, "keywords": [],
          "space_path": "/自我/状态"}, FLAG_POLLUTED),
        ({"node_id": "n4", "value": "我了解到我自己正在思考" + "啊" * 250, "keywords": [],
          "space_path": "/自我/状态"}, FLAG_POLLUTED),
        ({"node_id": "n5", "value": "我了解到我在思考", "keywords": [], "space_path": "/自我/状态"}, FLAG_SUSPECT),
        ({"node_id": "n6", "value": "什么是共振引擎？", "keywords": [], "space_path": "/自我/知识/共振引擎"}, FLAG_SUSPECT),
        ({"node_id": "n7", "value": "正常的知识", "keywords": ["x"],
          "space_path": "/a/b/c/d/e/f/g/h/i"}, FLAG_SUSPECT),   # 9层 > 7
        ({"node_id": "n8", "value": "正常知识", "keywords": ["x"],
          "space_path": "/" + "/".join(f"l{i}" for i in range(12))}, FLAG_POLLUTED),  # 12层 > 10
        ({"node_id": "n9", "value": "未验证的推测结论", "keywords": ["推测"],
          "space_path": "/推理经验/因果"}, FLAG_SUSPECT),
    ]

    _ok = 0
    for _node, _expect in _cases:
        _flag, _reason = _t.classify(_node)
        _good = _flag == _expect
        _ok += 1 if _good else 0
        print(f"  {'✅' if _good else '❌'} {_node['node_id']}: 期望={_expect} 实际={_flag} ({_reason})")

    # 降权系数
    _f_clean = _t.downweight_factor(_cases[0][0])
    _f_pol = _t.downweight_factor(_cases[1][0])
    _f_sus = _t.downweight_factor(_cases[4][0])
    print(f"\n降权系数: clean={_f_clean}, suspect={_f_sus}, polluted={_f_pol}")
    assert _f_clean == 1.0 and abs(_f_pol - 0.3) < 1e-6 and abs(_f_sus - 0.7) < 1e-6

    # 扫描统计
    _stats = _t.scan_nodes([c[0] for c in _cases], log_fn=lambda l, m: None)
    print(f"扫描统计: { {k: v for k, v in _stats.items() if k != 'factors'} }")
    assert _stats["scanned"] == len(_cases)
    assert _stats["polluted"] == 4 and _stats["suspect"] == 4 and _stats["clean"] == 1

    print(f"\n✅ 自测全部通过（{_ok}/{len(_cases)} 分类用例）")
