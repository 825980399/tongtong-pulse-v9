# -*- coding: utf-8 -*-
"""
DataQualityGuard.py —— 数据质量守卫

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 知识节点写入前的质量检查与过滤
机制: 基于DataQualityGuard类实现，包含10个核心方法
定位: 知识治理层
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from collections.abc import Iterable

from nucleus.knowledge.DuplicateNodeDetector import (

    FLAG_DUPLICATE,
    DuplicateNodeDetector,
)
from nucleus.knowledge.PollutionTagger import (
    FLAG_CLEAN,
    FLAG_POLLUTED,
    FLAG_SUSPECT,
    PollutionTagger,
)
from nucleus.logger import get_module_logger

__all__ = ["DataQualityGuard", "get_data_quality_guard", "reset_data_quality_guard"]

_logger = get_module_logger("DataQualityGuard")

# 测试隔离：经验库落盘路径重定向用（与 ExperiencePollutionGuard._ISO_BASE_DIR 同思路）
_ISO_BASE_DIR: str | None = None


class DataQualityGuard:
    """统一脏数据治理入口（facade：编排三个检测器，不重写它们的逻辑）。"""

    def __init__(self, tagger_config: dict[str, Any] | None = None,
                 dup_threshold: float = 0.8, log_fn=None):
        self._tagger = PollutionTagger(config=tagger_config, log_fn=log_fn)
        self._dup_detector = DuplicateNodeDetector(threshold=dup_threshold)
        self._log_fn = log_fn
        # 统一统计
        self._stats: dict[str, Any] = {
            "experience": None,
            "nodes": None,
            "duplicates": None,
        }

    # ---------------- 经验库 ----------------

    def scan_experience_entries(self, entries: Iterable[Any]) -> dict[str, Any]:
        """检测一批经验条目（**纯内存，不写盘**，可测试、可离线用）。"""
        from nucleus.evolution.ExperiencePollutionGuard import detect
        _flags = Counter()
        _n = 0
        for _it in entries:
            _n += 1
            if not isinstance(_it, dict):
                _flags["polluted"] += 1
                continue
            _flag, _reason = detect(_it)
            _flags[_flag] += 1
            if _flag == "ok":
                for _k in ("polluted", "quality_flag", "pollution_reason", "quality_weight"):
                    _it.pop(_k, None)
            else:
                from nucleus.evolution.ExperiencePollutionGuard import (
                    WEIGHT_POLLUTED, WEIGHT_SUSPECT,
                )
                _it["polluted"] = True
                _it["quality_flag"] = _flag
                _it["pollution_reason"] = _reason
                _it["quality_weight"] = (WEIGHT_POLLUTED if _flag == "polluted"
                                         else WEIGHT_SUSPECT)
        _bad = _flags["polluted"] + _flags["suspect"]
        return {
            "total": _n,
            "polluted": _flags["polluted"],
            "suspect": _flags["suspect"],
            "clean": _flags["ok"],
            "pollution_rate": round(_bad / _n, 4) if _n else 0.0,
        }

    def scan_experience_files(self, rel_paths: tuple[str, ...] | None = None,
                              mark: bool = False) -> list[dict[str, Any]]:
        """扫描真实经验库文件（写盘需 mark=True，默认只读）。"""
        from nucleus.evolution.ExperiencePollutionGuard import scan_all
        return scan_all(rels=rel_paths or (), mark=mark)

    # ---------------- 知识节点污染 ----------------

    def scan_nodes(self, nodes: Iterable[Any], mark: bool = True) -> dict[str, Any]:
        """扫描知识节点，把 quality_flag / quality_reason 写到节点上（只标记不删除）。

        ★与 PollutionTagger.scan_nodes 的差别：那个对 dict 节点**不写回**（只在
          `not isinstance(_n, dict)` 时写），而这里统一写回 dict，保证从流式快照
          读出来的 dict 节点也能被打标（这是"统一写 quality_flag"的关键）。
        """
        _flags = Counter()
        _reasons: Counter = Counter()
        _n = 0
        for _node in nodes:
            _n += 1
            try:
                _flag, _reason = self._tagger.classify(_node)
            except Exception:
                continue
            _flags[_flag] += 1
            if _reason:
                _reasons[_reason.split(":", 1)[0]] += 1
            if mark:
                self._write_flag(_node, _flag, _reason)
        _stats = {
            "total": _n,
            "polluted": _flags.get(FLAG_POLLUTED, 0),
            "suspect": _flags.get(FLAG_SUSPECT, 0),
            "clean": _flags.get(FLAG_CLEAN, 0),
            "reasons": dict(_reasons),
        }
        self._stats["nodes"] = _stats
        return _stats

    # ---------------- 重复节点 ----------------

    def find_duplicates(self, nodes: Iterable[Any], path_prefix: str | None = None,
                        legacy_prefixes: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """找出重复节点分组（复用第一批 DuplicateNodeDetector）。"""
        return self._dup_detector.find_groups(
            nodes, path_prefix=path_prefix, legacy_prefixes=legacy_prefixes)

    def mark_duplicates(self, groups: Iterable[dict[str, Any]]) -> int:
        """把重复方标为 suspect（只标记不删除）。"""
        return self._dup_detector.mark(groups, flag=FLAG_DUPLICATE)

    # ---------------- 统一汇总 ----------------

    def scan_all(self, nodes: Iterable[Any] | None = None,
                 entries: Iterable[Any] | None = None,
                 path_prefix: str | None = None,
                 legacy_prefixes: Iterable[str] | None = None) -> dict[str, Any]:
        """统一扫描：经验库 + 知识节点污染 + 重复节点，返回一份汇总统计。"""
        _out: dict[str, Any] = {}
        if entries is not None:
            _out["experience"] = self.scan_experience_entries(entries)
        if nodes is not None:
            _nodes = list(nodes)
            _out["nodes"] = self.scan_nodes(_nodes, mark=True)
            _groups = self.find_duplicates(_nodes, path_prefix=path_prefix,
                                           legacy_prefixes=legacy_prefixes)
            _out["duplicates"] = {
                "groups": len(_groups),
                "marked": self.mark_duplicates(_groups),
            }
        self._stats.update(_out)
        return _out

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    # ---------------- 单节点轻量检查（主线第12批 T4/P2-31） ----------------

    def check_node(self, node: Any, context: str = "") -> dict[str, Any]:
        """单节点质量检查（**只判定、不写标记**，热路径安全）。

        用于「知识写入前 / 胃消化后 / 快照保存前」三个调用点做轻量质量观测，
        不做全量扫描（全量扫描仍由 scan_all 承担，避免高频路径变重）。

        日志策略：按 `_check_sample_every`（默认 20）采样输出 INFO，
        避免逐节点刷屏；同时维护计数器供 get_stats() 观测。

        Returns:
            {"flag": clean/suspect/polluted, "reason": str,
             "checked": int(累计检查数), "logged": bool}
        """
        self._check_counter = getattr(self, "_check_counter", 0) + 1
        _flag, _reason = FLAG_CLEAN, ""
        try:
            _flag, _reason = self._tagger.classify(node)
        except Exception:
            _flag, _reason = FLAG_CLEAN, "检查异常(降级clean)"
        _bad = _flag in (FLAG_SUSPECT, FLAG_POLLUTED)
        try:
            self._check_bad_counter = getattr(self, "_check_bad_counter", 0) + (1 if _bad else 0)
        except Exception:
            pass
        _every = getattr(self, "_check_sample_every", 20)
        _logged = False
        if _bad or (self._check_counter % max(1, _every) == 0):
            _logged = True
            try:
                _logger.info(
                    f"[DataQualityGuard] 质量检查#{self._check_counter}"
                    f"({context or '常规'}): {_flag}"
                    f"{' - ' + _reason[:60] if _reason else ''}"
                    f"（累计异常 {getattr(self, '_check_bad_counter', 0)}/"
                    f"{self._check_counter}）")
            except Exception:
                pass
        return {"flag": _flag, "reason": _reason,
                "checked": self._check_counter, "logged": _logged}

    def get_check_stats(self) -> dict[str, Any]:
        """返回单节点检查累计统计（供观测/测试）。"""
        return {
            "checked": getattr(self, "_check_counter", 0),
            "bad": getattr(self, "_check_bad_counter", 0),
            "sample_every": getattr(self, "_check_sample_every", 20),
        }


    # ---------------- 撤销标记 ----------------

    @staticmethod
    def clear_flag(node: Any) -> bool:
        """把节点的显式标记撤销回 clean（标记可逆，避免永久粘死）。"""
        try:
            if isinstance(node, dict):
                node["quality_flag"] = FLAG_CLEAN
                node.pop("quality_reason", None)
                node.pop("duplicate_of", None)
            else:
                node.quality_flag = FLAG_CLEAN
                if hasattr(node, "quality_reason"):
                    node.quality_reason = ""
                if hasattr(node, "duplicate_of"):
                    node.duplicate_of = None
            return True
        except Exception:
            return False

    # ---------------- 内部 ----------------

    def _write_flag(self, node: Any, flag: str, reason: str) -> None:
        """统一把标记写回节点（dict 与对象都支持）。"""
        if flag == FLAG_CLEAN:
            return
        try:
            if isinstance(node, dict):
                node["quality_flag"] = flag
                node["quality_reason"] = reason
            else:
                node.quality_flag = flag
                node.quality_reason = reason
        except Exception:
            pass


# ========== 模块级单例 ==========
_guard: DataQualityGuard | None = None


def get_data_quality_guard(tagger_config: dict[str, Any] | None = None,
                           dup_threshold: float = 0.8,
                           log_fn=None) -> DataQualityGuard:
    global _guard
    if _guard is None:
        _guard = DataQualityGuard(tagger_config=tagger_config,
                                  dup_threshold=dup_threshold, log_fn=log_fn)
    return _guard


def reset_data_quality_guard():
    global _guard
    _guard = None
