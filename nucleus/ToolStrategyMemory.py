# -*- coding: utf-8 -*-
"""
ToolStrategyMemory.py —— 工具策略记忆

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 工具使用策略的学习与记忆
机制: 基于ToolStrategyMemory类实现，包含9个核心方法
定位: 学习记忆层
"""

import os
import threading
import time
from collections import defaultdict
from typing import Any

from nucleus._silent_except import silent_exc
from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus.logger import get_module_logger

_logger = get_module_logger(__name__)

_module_logger = get_module_logger("ToolStrategyMemory")


class ToolStrategyMemory:
    """工具调用策略经验记忆。

    数据结构:
        self._data = {
            "<category>": {
                "search":    {"ok": 3, "fail": 1},
                "trending":  {"ok": 0, "fail": 2},
                ...
            }
        }
    """

    _MIN_SAMPLES = 2       # 建议某策略所需的最小样本数
    _MIN_SUCCESS_RATE = 0.6  # 建议所需的最低成功率
    _CONFIDENCE_BASE = 3   # 有足够证据的总样本下限

    def __init__(self, base_dir: str | None = None):
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: {"ok": 0, "fail": 0})
        )
        # 数据文件目录（默认项目 data 目录）
        if base_dir is None:
            base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        self._save_dir = base_dir
        self._save_path = os.path.join(base_dir, "tool_strategy_memory.json")
        self._last_save = 0.0
        self._load()

    # ========== 类别规范化 ==========

    def _categorize(self, task: str) -> str:
        """从任务/方向文本提取类别 key。

        取核心主题（去除常见功能词后的前 4 字），保证同类任务能聚类。
        无法提取时退回完整字符串前 8 字。
        """
        if not task:
            return "_unknown"
        _stop = ["相关", "内容", "搜索", "帮我", "请问", "什么", "如何", "怎么",
                 "一个", "一种", "关于", "一下", "这个", "那个", "的", "和"]
        _core = ""
        for ch in str(task):
            if ch in _stop:
                break
            _core += ch
        if not _core:
            _core = str(task)[:4]
        return _core[:4]

    # ========== 记录 ==========

    def record(self, task: str, strategy: str, success: bool,
               error_type: str | None = None) -> None:
        """记录一次策略调用的成败。

        Args:
            task: 任务/方向描述
            strategy: 策略名（search/trending/explore/builtin）
            success: 是否成功
            error_type: 失败归因（capability/info/policy，可选）
        """
        if not strategy:
            return
        _cat = self._categorize(task)
        _is_first_record = False
        with self._lock:
            _entry = self._data[_cat][strategy]
            _before = _entry.get("ok", 0) + _entry.get("fail", 0)
            _is_first_record = (_before == 0)
            if success:
                _entry["ok"] += 1
            else:
                _entry["fail"] += 1
            if error_type:
                _err_key = f"err_{error_type}"
                _entry[_err_key] = _entry.get(_err_key, 0) + 1
        # ★L2日志补全：记忆文件/类目首次创建时打 INFO（低调，后台可观测策略记忆是否工作）
        if _is_first_record:
            _module_logger.info(
                f"策略记忆: 类目[{_cat}]策略[{strategy}]首次记录 "
                f"({'成功' if success else '失败'}"
                + (f",归因{error_type}" if error_type else "") + ")")
        # 节流持久化：最多每 30 秒写一次盘
        _now = time.time()
        if _now - self._last_save > 30:
            self.save()

    # ========== 建议 ==========

    def suggest(self, task: str) -> str | None:
        """返回该类任务历史成功率最高的策略；经验不足时返回 None。

        判定条件（避免单次成功就锚定）：
          - 该类目总样本 >= _CONFIDENCE_BASE
          - 候选策略样本 >= _MIN_SAMPLES 且成功率 >= _MIN_SUCCESS_RATE
        """
        _cat = self._categorize(task)
        with self._lock:
            _strategies = self._data.get(_cat)
            if not _strategies:
                return None
            _total = sum(e.get("ok", 0) + e.get("fail", 0) for e in _strategies.values())
            if _total < self._CONFIDENCE_BASE:
                return None
            _best = None
            _best_score = 0.0
            for _name, _entry in _strategies.items():
                _ok = _entry.get("ok", 0)
                _fail = _entry.get("fail", 0)
                _samples = _ok + _fail
                if _samples < self._MIN_SAMPLES:
                    continue
                _rate = _ok / _samples if _samples else 0.0
                if _rate >= self._MIN_SUCCESS_RATE and _rate > _best_score:
                    _best = _name
                    _best_score = _rate
            # ★L2日志补全：命中经验建议时 DEBUG（后台可观测经验复用）
            if _best:
                _module_logger.debug(
                    f"策略记忆: 类目[{_cat}]建议策略[{_best}] 成功率={_best_score:.2f}")
            return _best

    def get_stats(self) -> dict[str, Any]:
        """统计信息（供日志/调试）"""
        with self._lock:
            return {
                "categories": len(self._data),
                "total_records": sum(
                    sum(e.get("ok", 0) + e.get("fail", 0) for e in s.values())
                    for s in self._data.values()
                ),
                "data_file": self._save_path,
            }

    # ========== 持久化 ==========

    def _load(self) -> None:
        try:
            if os.path.exists(self._save_path):
                _raw = safe_read_json(self._save_path, default={})
                for _cat, _strategies in (_raw or {}).items():
                    for _name, _entry in _strategies.items():
                        self._data[_cat][_name] = _entry
        except Exception as e:
            silent_exc(e, "ToolStrategyMemory.py:175:_load", level="warning")

    def save(self) -> None:
        try:
            _first_create = not os.path.exists(self._save_path)
            os.makedirs(self._save_dir, exist_ok=True)
            with self._lock:
                _dump = {c: dict(s) for c, s in self._data.items()}
            safe_write_json(self._save_path, _dump, indent=1)
            self._last_save = time.time()
            # ★L2日志补全：记忆文件首次创建时 INFO（后台可观测记忆落地）
            if _first_create:
                _module_logger.info(f"策略记忆已创建: {self._save_path}")
        except Exception as e:
            _logger.warning(f"异常已忽略（需关注）: {type(e).__name__}: {e}")


# ========== 模块级单例 ==========
_memory: ToolStrategyMemory | None = None
_memory_lock = threading.Lock()


def get_tool_strategy_memory() -> ToolStrategyMemory:
    """获取 ToolStrategyMemory 单例（跨模块共享记忆）"""
    global _memory
    if _memory is None:
        with _memory_lock:
            if _memory is None:
                _memory = ToolStrategyMemory()
    return _memory


