# -*- coding: utf-8 -*-
"""
CodeBackupManager.py —— 代码备份管理器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 代码修改前的自动备份管理
机制: 基于CodeBackupManager类实现，包含5个核心方法
定位: 推理支撑层
"""

import os
import shutil
import time
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json



class CodeBackupManager(SilentLogMixin):
    """代码文件备份管理器"""

    def __init__(self, project_root: str):
        self._project_root = project_root
        self._backup_dir = os.path.join(project_root, "data", "code_backups")
        os.makedirs(self._backup_dir, exist_ok=True)
        self._max_backups = 20  # 最多保留20份备份

    def create_backup(self, files: list[str], reason: str = "",
                      source: str = "") -> str | None:
        """
        创建一次备份会话。

        Args:
            files: 需要备份的文件绝对路径列表
            reason: 备份原因（为什么修改）
            source: 来源诊断类型

        Returns:
            备份目录路径，如果失败返回None
        """
        if not files:
            return None

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(self._backup_dir, f"backup_{timestamp}")
        os.makedirs(backup_dir, exist_ok=True)

        manifest = {
            "created_at": time.time(),
            "created_at_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
            "source": source,
            "project_root": self._project_root,
            "files": [],
        }

        for file_path in files:
            if not os.path.exists(file_path):
                continue
            rel_path = os.path.relpath(file_path, self._project_root)
            target_path = os.path.join(backup_dir, rel_path)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            shutil.copy2(file_path, target_path)
            manifest["files"].append({
                "rel_path": rel_path,
                "abs_path": file_path,
                "size_bytes": os.path.getsize(file_path),
            })

        if not manifest["files"]:
            shutil.rmtree(backup_dir, ignore_errors=True)
            return None

        # 保存清单
        manifest_path = os.path.join(backup_dir, "manifest.json")
        safe_write_json(manifest_path, manifest, indent=2)

        # 清理过期备份
        self._cleanup_old_backups()

        return backup_dir

    def restore_backup(self, backup_dir: str) -> dict[str, Any]:
        """
        从备份恢复所有文件。

        Args:
            backup_dir: 备份目录路径

        Returns:
            恢复结果
        """
        manifest_path = os.path.join(backup_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            return {"success": False, "reason": "备份清单不存在"}

        manifest = safe_read_json(manifest_path, default={})

        restored = []
        failed = []
        for file_info in manifest.get("files", []):
            rel_path = file_info.get("rel_path", "")
            backup_file = os.path.join(backup_dir, rel_path)
            target_file = os.path.join(self._project_root, rel_path)

            if not os.path.exists(backup_file):
                failed.append(rel_path)
                continue

            try:
                os.makedirs(os.path.dirname(target_file), exist_ok=True)
                shutil.copy2(backup_file, target_file)
                restored.append(rel_path)
            except Exception:
                failed.append(rel_path)

        return {
            "success": len(failed) == 0,
            "restored": restored,
            "failed": failed,
        }

    def list_backups(self) -> list[dict[str, Any]]:
        """列出所有可用备份"""
        backups = []
        for name in os.listdir(self._backup_dir):
            backup_dir = os.path.join(self._backup_dir, name)
            if not os.path.isdir(backup_dir):
                continue
            manifest_path = os.path.join(backup_dir, "manifest.json")
            if not os.path.exists(manifest_path):
                continue
            try:
                manifest = safe_read_json(manifest_path, default={})
                backups.append({
                    "dir": backup_dir,
                    "name": name,
                    "created_at": manifest.get("created_at_str", ""),
                    "reason": manifest.get("reason", ""),
                    "source": manifest.get("source", ""),
                    "file_count": len(manifest.get("files", [])),
                })
            except Exception as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")

        backups.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return backups

    def _cleanup_old_backups(self):
        """清理超过上限的旧备份"""
        backups = []
        for name in os.listdir(self._backup_dir):
            backup_dir = os.path.join(self._backup_dir, name)
            if os.path.isdir(backup_dir):
                backups.append((os.path.getmtime(backup_dir), backup_dir))

        backups.sort(reverse=True)
        for _, backup_dir in backups[self._max_backups:]:
            shutil.rmtree(backup_dir, ignore_errors=True)