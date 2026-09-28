# -*- coding: utf-8 -*-
"""
DiffArchiver.py —— 差异归档器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 代码变更差异的归档与版本管理
机制: 基于DiffArchiver类实现，包含9个核心方法
定位: 进化管理层
"""

from __future__ import annotations

import difflib
import os
import time
from typing import Any

from nucleus.const import LogLevel
from nucleus.data.DataAccessLayer import safe_read_json, safe_write_json
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底


class DiffArchiver(SilentLogMixin):
    """补丁 diff 归档与全链路溯源记录器。"""

    def __init__(self, project_root: str):
        self._project_root = project_root
        self._patch_dir = os.path.join(project_root, "data", "evolution", "patches")
        self._index_file = os.path.join(project_root, "data", "evolution", "trace_index.json")
        os.makedirs(self._patch_dir, exist_ok=True)

    # ========== 生成 unified diff ==========

    def generate_diff(self, patch_id: str, file_path: str,
                      original_code: str, modified_code: str) -> str:
        """
        生成标准 unified diff 文本，并归档到 <patch_id>.diff。

        返回 diff 文本（也写入磁盘）。
        """
        _diff = self._unified_diff(file_path, original_code, modified_code)
        _diff_file = os.path.join(self._patch_dir, f"{patch_id}.diff")
        try:
            with open(_diff_file, "w", encoding="utf-8") as _f:
                _f.write(_diff)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        return _diff

    def _unified_diff(self, file_path: str, original_code: str,
                      modified_code: str) -> str:
        """用 difflib 生成标准 unified diff（含文件头，兼容 git diff 风格）。"""
        _orig_lines = original_code.splitlines(keepends=True)
        _mod_lines = modified_code.splitlines(keepends=True)
        _diff_lines = list(difflib.unified_diff(
            _orig_lines, _mod_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="\n",
        ))
        if not _diff_lines:
            return f"# 无差异: {file_path}\n"
        return "".join(_diff_lines)

    # ========== 全链路溯源 ==========

    def record_trace(self, patch_id: str, trace: dict[str, Any]) -> None:
        """
        记录一个补丁的全链路溯源，归档到 <patch_id>.trace.json，
        并更新总索引 trace_index.json。

        trace 结构（各环节可选，缺省为空）:
            {
                "patch_id": str,
                "file": str,
                "method": str,
                "issue": {"type": str, "description": str, "source": str},
                "location": {"file": str, "line": int, "method": str},
                "change": {"diff_file": str, "summary": str},
                "test": {"script": str, "passed": bool, "output": str},
                "apply": {"status": str, "backup_path": str, "applied_at": float},
                "verify": {"health_before": float, "health_after": float, "effective": bool},
                "timeline": {"found_at": float, "patched_at": float, "tested_at": float, "applied_at": float},
            }
        """
        _trace = dict(trace)
        _trace.setdefault("patch_id", patch_id)
        _trace.setdefault("recorded_at", time.time())

        # 1. 写入单条溯源
        _trace_file = os.path.join(self._patch_dir, f"{patch_id}.trace.json")
        try:
            safe_write_json(_trace_file, _trace, indent=2)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        # 2. 更新总索引
        self._update_index(_trace)

    def _update_index(self, trace: dict[str, Any]) -> None:
        """把一条溯源追加到总索引（按时间倒序）。"""
        _index = self._load_index()
        _entry = {
            "patch_id": trace.get("patch_id", ""),
            "file": trace.get("file", ""),
            "method": trace.get("method", ""),
            "issue_type": (trace.get("issue") or {}).get("type", ""),
            "summary": (trace.get("change") or {}).get("summary", ""),
            "test_passed": (trace.get("test") or {}).get("passed", None),
            "apply_status": (trace.get("apply") or {}).get("status", ""),
            "recorded_at": trace.get("recorded_at", time.time()),
        }
        # 去重：同一 patch_id 覆盖
        _index = [e for e in _index if e.get("patch_id") != _entry["patch_id"]]
        _index.append(_entry)
        _index.sort(key=lambda e: e.get("recorded_at", 0), reverse=True)
        try:
            safe_write_json(self._index_file, _index, indent=2)
        except Exception as e:
            self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

    def _load_index(self) -> list[dict[str, Any]]:
        if not os.path.exists(self._index_file):
            return []
        try:
            return safe_read_json(self._index_file, default={})
        except Exception as e:
            print(f"[WARNING] DiffArchiver.py:125: {type(e).__name__}: {e}")
            return []

    # ========== 查询 ==========

    def get_trace(self, patch_id: str) -> dict[str, Any] | None:
        """读取单个补丁的全链路溯源。"""
        _trace_file = os.path.join(self._patch_dir, f"{patch_id}.trace.json")
        if not os.path.exists(_trace_file):
            return None
        try:
            return safe_read_json(_trace_file, default={})
        except Exception as e:
            print(f"[WARNING] DiffArchiver.py:137: {type(e).__name__}: {e}")
            return None

    def list_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        """列出溯源索引（按时间倒序）。"""
        return self._load_index()[:limit]


# ========== 便捷函数 ==========

def get_diff_archiver(project_root: str) -> DiffArchiver:
    """获取 DiffArchiver 实例。"""
    return DiffArchiver(project_root)


if __name__ == "__main__":
    # 自测：生成一个 diff 并记录溯源
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _archiver = DiffArchiver(_root)
    _orig = "def foo():\n    x = 1\n    return x\n"
    _mod = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"
    _diff = _archiver.generate_diff("test_patch", "organs/test.py", _orig, _mod)
    print("=== diff ===")
    print(_diff)
    _archiver.record_trace("test_patch", {
        "file": "organs/test.py",
        "method": "foo",
        "issue": {"type": "test", "description": "自测", "source": "manual"},
        "location": {"file": "organs/test.py", "line": 1, "method": "foo"},
        "change": {"summary": "新增变量 y 并返回 x+y"},
        "test": {"passed": True},
    })
    print("=== 溯源索引 ===")
    for _e in _archiver.list_traces(5):
        print(f"  {_e['patch_id']} {_e['file']} - {_e['summary']}")
