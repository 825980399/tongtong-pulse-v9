# -*- coding: utf-8 -*-
"""
SelfVerifier.py —— 自我验证器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 推理结论的自我验证与检查
机制: 基于SelfVerifier类实现，包含9个核心方法
定位: 推理验证层
"""

import os
from typing import Any

from nucleus.const import LogLevel
from nucleus.logging.SilentLogMixin import SilentLogMixin  # ★P0-1: 幽灵_log兜底
from nucleus.data.DataAccessLayer import safe_write_json
from nucleus.data.DataAccessLayer import safe_read_json



class SelfVerifier(SilentLogMixin):
    """框架自我验证器"""

    def __init__(self, project_root: str):
        self._project_root = project_root
        self._patch_dir = os.path.join(project_root, "data", "patches")
        self._verify_file = os.path.join(self._patch_dir, "pending_verification.json")
        os.makedirs(self._patch_dir, exist_ok=True)

    def mark_pending(self, applied_count: int):
        """补丁应用后写入待验证标记"""
        data = {
            "applied_count": applied_count,
            "verified": False,
            "attempts": 0,
            "last_attempt_at": 0,
            "rolled_back": False,  # ★修复: 每次待验证只自动回退一次，防误退历史好补丁
        }
        safe_write_json(self._verify_file, data, indent=2)

    def has_rolled_back(self) -> bool:
        """★新增: 本轮待验证是否已执行过自动回退"""
        if not os.path.exists(self._verify_file):
            return False
        try:
            data = safe_read_json(self._verify_file, default={})
            return bool(data.get("rolled_back", False))
        except Exception as e:
            print(f"[WARNING] SelfVerifier.py:43: {type(e).__name__}: {e}")
            return False

    def mark_rolled_back(self):
        """★新增: 标记本轮已回退过（后续验证失败不再自动回退，转人工告警）"""
        data = {}
        if os.path.exists(self._verify_file):
            try:
                data = safe_read_json(self._verify_file, default={})
            except (ValueError, OSError) as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        data["rolled_back"] = True
        safe_write_json(self._verify_file, data, indent=2)

    def has_pending(self) -> bool:
        """检查是否有待验证的补丁"""
        if not os.path.exists(self._verify_file):
            return False
        try:
            data = safe_read_json(self._verify_file, default={})
            return not data.get("verified", False) and data.get("attempts", 0) < 3
        except Exception as e:
            print(f"[WARNING] SelfVerifier.py:64: {type(e).__name__}: {e}")
            return False

    def clear_pending(self):
        """验证通过后清除标记"""
        try:
            os.remove(self._verify_file)
        except OSError:
            pass

    def increment_attempt(self):
        """验证失败时增加尝试次数"""
        data = {}
        if os.path.exists(self._verify_file):
            try:
                data = safe_read_json(self._verify_file, default={})
            except (ValueError, OSError) as e:
                self._log(LogLevel.WARNING, f"异常已忽略（需关注）: {type(e).__name__}: {e}")
        data["attempts"] = data.get("attempts", 0) + 1
        data["last_attempt_at"] = os.path.getmtime(self._verify_file) if os.path.exists(self._verify_file) else 0
        safe_write_json(self._verify_file, data, indent=2)

    def run_verification(self, framework) -> bool:
        """
        执行三关自我验证。
        返回True表示全部通过。
        """
        return self.run_verification_detailed(framework)["passed"]

    def run_verification_detailed(self, framework) -> dict[str, Any]:
        """
        ★进化闭环升级(阶段C): 执行三关验证并返回详细结果（含每关通过情况与失败详情）。

        返回:
            {"passed": bool, "checks": {"organs_online": bool, ...},
             "detail": {"organs_online": str, ...}}
        """
        checks = {}
        detail = {}

        # 第一关：器官在线检查
        try:
            online = sum(
                1 for o in framework.organs.values()
                if o is not None and getattr(o, "is_running", False)
            )
            total = len(framework.organs)
            checks["organs_online"] = online >= max(50, total - 3)
            detail["organs_online"] = f"{online}/{total} 在线"
        except Exception as _e:
            checks["organs_online"] = False
            detail["organs_online"] = f"异常: {_e}"

        # 第二关：知识节点加载检查
        try:
            total_nodes = framework.node_pool.count() if framework.node_pool else 0
            checks["knowledge_loaded"] = total_nodes > 0
            detail["knowledge_loaded"] = f"{total_nodes} 个节点"
        except Exception as _e:
            checks["knowledge_loaded"] = False
            detail["knowledge_loaded"] = f"异常: {_e}"

        # 第三关：基础推理检查
        try:
            rule_answer = None
            if hasattr(framework, "inner_world") and framework.inner_world:
                rule_answer = framework.inner_world.rule_reason("你是谁", "系统")
            checks["basic_reasoning"] = rule_answer is not None and len(str(rule_answer)) > 0
            detail["basic_reasoning"] = "有答案" if checks["basic_reasoning"] else "无答案"
        except Exception as _e:
            checks["basic_reasoning"] = False
            detail["basic_reasoning"] = f"异常: {_e}"

        return {
            "passed": all(checks.values()),
            "checks": checks,
            "detail": detail,
        }