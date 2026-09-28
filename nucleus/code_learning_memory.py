# -*- coding: utf-8 -*-
"""nucleus/code_learning_memory —— 代码学习已检测问题记忆（主线第56批 T5/P2-395）

问题背景
--------
代码学习系统反复检测相同问题（如 ``bare_return_none_in_except`` 在 PulseLung
被在 15:14/15:41/16:24... 多次检出），根因是**没有记忆已检测的问题** → 浪费
大模型调用额度与计算资源，降低学习效率。

★本模块（方案B：持久化）解决
-----------------------------
* 记录已检测问题（文件路径 + 行号 + 问题类型）到 ``data/code_learning/checked_issues.json``
* 下次扫描：``should_skip`` 对「已检测 + 文件未修改 + 未过期」的问题返回 True → 跳过
* 文件被修改过（mtime 变化）→ 重新检测该文件
* 记忆过期（默认 7 天）→ 重新检测
* ``skipped_count`` 记录跳过数量（日志可统计）
* 不影响新问题检测（新问题从未记录 → should_skip 返回 False）

API
---
* CheckedIssueMemory(storage_path=None, expiry_days=7)
  - should_skip(file_path, line, issue_type) -> bool
  - record(file_path, line, issue_type) -> None
  - recorded_count() -> int
  - clear() -> None
* default_storage_path() -> str

存储路径可注入（测试用临时目录），默认落在 ``<项目根>/data/code_learning/``。
"""
from __future__ import annotations
from nucleus._silent_except import silent_exc

import io
import json
import os
import time

__all__ = ["CheckedIssueMemory", "default_storage_path"]

DEFAULT_EXPIRY_DAYS = 7
_SECONDS_PER_DAY = 86400


def default_storage_path() -> str:
    """默认存储路径：``<项目根>/data/code_learning/checked_issues.json``。"""
    _here = os.path.dirname(os.path.abspath(__file__))  # nucleus/
    _root = os.path.dirname(_here)                      # 项目根
    return os.path.join(_root, "data", "code_learning", "checked_issues.json")


class CheckedIssueMemory:
    """★已检测问题持久化记忆（方案B）。

    storage_path: JSON 文件路径（默认 default_storage_path()）
    expiry_days:  记忆有效天数，超过则重新检测（默认 7）
    """

    def __init__(self, storage_path: str | None = None,
                 expiry_days: int = DEFAULT_EXPIRY_DAYS):
        self.storage_path = storage_path or default_storage_path()
        self.expiry_days = expiry_days
        self.skipped_count = 0
        self._data: dict | None = None

    # ------------------------------------------------------------------
    @staticmethod
    def _key(file_path: str, line, issue_type: str) -> str:
        """记忆键：用 ``::`` 分隔，避免 Windows 路径中的 ``:`` 歧义。"""
        return f"{file_path}::{line}::{issue_type}"

    @staticmethod
    def _mtime_of(file_path: str) -> float:
        try:
            return float(os.path.getmtime(file_path))
        except OSError:
            return 0.0

    def _load(self) -> None:
        if self._data is not None:
            return
        try:
            if os.path.exists(self.storage_path):
                with io.open(self.storage_path, "r", encoding="utf-8") as f:
                    _d = json.load(f)
                if isinstance(_d, dict) and isinstance(_d.get("issues"), dict):
                    self._data = _d
                else:
                    self._data = {"issues": {}}
            else:
                self._data = {"issues": {}}
        except Exception:
            # 加载失败不得阻断检测流程（降级为无记忆）
            self._data = {"issues": {}}

    def _save(self) -> None:
        try:
            _dir = os.path.dirname(self.storage_path)
            if _dir:
                os.makedirs(_dir, exist_ok=True)
            _tmp = self.storage_path + ".tmp"
            with io.open(_tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(_tmp, self.storage_path)
        except Exception as e:
            # 持久化失败不得阻断检测流程
            silent_exc(e, "code_learning_memory.py:103:_save", level="warning")

    # ------------------------------------------------------------------
    def should_skip(self, file_path: str, line, issue_type: str) -> bool:
        """★已检测 + 文件未修改 + 未过期 → 跳过（True）。

        任一条件不满足（新问题 / 文件已改 / 已过期）→ 返回 False（重新检测）。
        """
        if not file_path:
            return False
        self._load()
        _e = self._data.get("issues", {}).get(
            self._key(file_path, line, issue_type))
        if not _e:
            return False
        if (time.time() - float(_e.get("checked_at", 0))
                > self.expiry_days * _SECONDS_PER_DAY):
            return False  # 过期 → 重新检测
        if self._mtime_of(file_path) != float(_e.get("mtime", -1)):
            return False  # 文件已修改 → 重新检测
        self.skipped_count += 1
        return True

    def record(self, file_path: str, line, issue_type: str) -> None:
        """★记录一次检测（持久化到磁盘）。"""
        if not file_path:
            return
        self._load()
        self._data.setdefault("issues", {})[
            self._key(file_path, line, issue_type)] = {
            "file": file_path,
            "line": line,
            "type": issue_type,
            "mtime": self._mtime_of(file_path),
            "checked_at": time.time(),
        }
        self._save()

    def recorded_count(self) -> int:
        self._load()
        return len(self._data.get("issues", {}))

    def clear(self) -> None:
        self._data = {"issues": {}}
        self._save()
