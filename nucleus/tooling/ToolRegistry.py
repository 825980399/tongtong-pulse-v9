# -*- coding: utf-8 -*-
"""
ToolRegistry.py —— 工具注册表

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 外部工具的注册与能力声明
机制: 基于ToolRegistry类实现，包含10个核心方法
定位: 工具管理层
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from nucleus._silent_except import silent_exc



class ToolRegistry:
    """通用工具注册表。"""

    def __init__(self) -> None:
        # 工具注册表：name → tool_info
        self._tools: dict[str, dict[str, Any]] = {}
        # 使用统计：name → {"calls": n, "success": n, "failed": n, "total_time": float}
        self._stats: dict[str, dict[str, Any]] = {}
        # 工具能力关键词索引：keyword → [tool_name, ...]
        self._capability_index: dict[str, list[str]] = {}

    # ========== 工具注册 ==========

    def register_tool(
        self,
        name: str,
        description: str,
        capabilities: list[str],
        executor: Callable[..., Any] | None = None,
        params: dict[str, Any] | None = None,
        returns: str = "",
        category: str = "general",
    ) -> bool:
        """注册一个工具。

        Args:
            name: 工具名称（唯一标识）
            description: 工具能力描述
            capabilities: 能力关键词列表（用于匹配）
            executor: 执行函数（可选，注册时不绑定也可以后续execute时传入）
            params: 参数说明
            returns: 返回值说明
            category: 工具分类（code_check/search/execution/evolution等）

        Returns:
            True=注册成功，False=名称已存在
        """
        if name in self._tools:
            return False

        self._tools[name] = {
            "name": name,
            "description": description,
            "capabilities": list(capabilities),
            "executor": executor,
            "params": params or {},
            "returns": returns,
            "category": category,
            "registered_at": time.time(),
        }
        self._stats[name] = {"calls": 0, "success": 0, "failed": 0, "total_time": 0.0}

        # 建立能力关键词索引
        for _cap in capabilities:
            _key = _cap.lower().strip()
            if _key not in self._capability_index:
                self._capability_index[_key] = []
            if name not in self._capability_index[_key]:
                self._capability_index[_key].append(name)

        return True

    def unregister_tool(self, name: str) -> bool:
        """注销工具。"""
        if name not in self._tools:
            return False
        del self._tools[name]
        self._stats.pop(name, None)
        for _names in self._capability_index.values():
            if name in _names:
                _names.remove(name)
        return True

    # ========== 工具发现 ==========

    def find_tool(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """根据能力描述查找工具。

        Args:
            query: 能力描述关键词（如"检查代码质量"、"搜索网页"）
            limit: 最多返回数量

        Returns:
            匹配的工具信息列表（按匹配度排序）
        """
        if not query or not self._tools:
            return []

        _query_lower = query.lower()
        _scores: dict[str, float] = {}

        # 1. 能力关键词精确匹配
        for _cap, _names in self._capability_index.items():
            if _cap in _query_lower or _query_lower in _cap:
                for _name in _names:
                    _scores[_name] = _scores.get(_name, 0) + 2.0

        # 2. 描述文本模糊匹配
        for _name, _tool in self._tools.items():
            _desc_lower = _tool["description"].lower()
            # 关键词重叠度
            _query_words = set(_query_lower.split())
            _desc_words = set(_desc_lower.split())
            _overlap = len(_query_words & _desc_words)
            if _overlap > 0:
                _scores[_name] = _scores.get(_name, 0) + _overlap * 0.5
            # 包含查询词
            if _query_lower in _desc_lower:
                _scores[_name] = _scores.get(_name, 0) + 1.0

        # 3. 按匹配度排序
        _sorted = sorted(_scores.items(), key=lambda x: x[1], reverse=True)
        _result = []
        for _name, _score in _sorted[:limit]:
            if _name in self._tools:
                _tool = dict(self._tools[_name])
                _tool["match_score"] = round(_score, 2)
                _tool.pop("executor", None)  # 不返回executor
                _result.append(_tool)

        return _result

    def list_tools(self, category: str | None = None) -> list[dict[str, Any]]:
        """列出所有工具（或指定分类的工具）。"""
        _result = []
        for _tool in self._tools.values():
            if category and _tool["category"] != category:
                continue
            _t = dict(_tool)
            _t.pop("executor", None)
            _result.append(_t)
        return _result

    def get_tool(self, name: str) -> dict[str, Any] | None:
        """获取工具信息。"""
        if name not in self._tools:
            return None
        _t = dict(self._tools[name])
        _t.pop("executor", None)
        return _t

    # ========== 工具执行 ==========

    def execute(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """执行工具。

        Args:
            name: 工具名称
            **kwargs: 工具参数

        Returns:
            {"status": "success"/"failed", "result": Any, "error": str, "duration_ms": float}
        """
        if name not in self._tools:
            return {"status": "failed", "result": None, "error": f"工具不存在: {name}", "duration_ms": 0}

        _tool = self._tools[name]
        _executor = _tool.get("executor")
        if _executor is None:
            return {"status": "failed", "result": None, "error": f"工具未绑定executor: {name}", "duration_ms": 0}

        _start = time.time()
        try:
            _result = _executor(**kwargs)
            _duration = (time.time() - _start) * 1000
            self._stats[name]["calls"] += 1
            self._stats[name]["success"] += 1
            self._stats[name]["total_time"] += _duration
            return {"status": "success", "result": _result, "error": "", "duration_ms": round(_duration, 1)}
        except Exception as _e:
            _duration = (time.time() - _start) * 1000
            self._stats[name]["calls"] += 1
            self._stats[name]["failed"] += 1
            self._stats[name]["total_time"] += _duration
            return {"status": "failed", "result": None, "error": str(_e), "duration_ms": round(_duration, 1)}

    # ========== 统计与查询 ==========

    def get_stats(self, name: str | None = None) -> dict[str, Any]:
        """获取工具使用统计。"""
        if name:
            if name not in self._stats:
                return {}
            _s = dict(self._stats[name])
            _s["success_rate"] = round(_s["success"] / _s["calls"], 3) if _s["calls"] > 0 else 0
            _s["avg_duration_ms"] = round(_s["total_time"] / _s["calls"], 1) if _s["calls"] > 0 else 0
            return _s
        # 全部统计
        _all = {}
        for _name, _s in self._stats.items():
            _all[_name] = {
                "calls": _s["calls"],
                "success": _s["success"],
                "failed": _s["failed"],
                "success_rate": round(_s["success"] / _s["calls"], 3) if _s["calls"] > 0 else 0,
                "avg_duration_ms": round(_s["total_time"] / _s["calls"], 1) if _s["calls"] > 0 else 0,
            }
        return _all

    def get_best_tool(self, query: str) -> str | None:
        """获取匹配度最高且成功率最高的工具。"""
        _candidates = self.find_tool(query, limit=3)
        if not _candidates:
            return None
        # 匹配度*0.6 + 成功率*0.4 综合排序
        _scored = []
        for _t in _candidates:
            _name = _t["name"]
            _stats = self.get_stats(_name)
            _success_rate = _stats.get("success_rate", 0.5)
            _combined = _t["match_score"] * 0.6 + _success_rate * 0.4
            _scored.append((_name, _combined))
        _scored.sort(key=lambda x: x[1], reverse=True)
        return _scored[0][0] if _scored else None

    def has_capability(self, query: str) -> bool:
        """检查是否有工具具备指定能力。"""
        return len(self.find_tool(query, limit=1)) > 0


# ========== 单例 ==========

_tool_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """获取工具注册表单例。"""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry



# ========== 默认工具注册 ==========

def register_default_tools() -> None:
    """注册框架默认工具（启动时调用一次）。"""
    _registry = get_tool_registry()

    # 代码质量检查工具
    try:
        from nucleus.tooling_runner import get_tooling_runner
        _runner = get_tooling_runner()
        _registry.register_tool(
            name="compile_check",
            description="Python代码编译检查，检测语法错误和编译问题",
            capabilities=["代码检查", "编译", "语法错误", "静态分析"],
            executor=lambda **kw: _runner.run_compile_check(),
            params={"files": "要检查的文件列表（None=全部）"},
            returns="问题列表",
            category="code_check",
        )
        _registry.register_tool(
            name="ruff_check",
            description="Python代码风格和质量检查，检测PEP8违规、复杂度、安全隐患",
            capabilities=["代码检查", "静态分析", "风格", "安全扫描", "ruff"],
            executor=lambda **kw: _runner.run_ruff_check(kw.get("files")),
            params={"files": "要检查的文件列表"},
            returns="问题列表",
            category="code_check",
        )
        _registry.register_tool(
            name="mypy_check",
            description="Python类型检查，检测类型注解错误和类型不匹配",
            capabilities=["代码检查", "类型检查", "静态分析", "mypy"],
            executor=lambda **kw: _runner.run_mypy_check(),
            params={},
            returns="问题列表",
            category="code_check",
        )
        _registry.register_tool(
            name="bandit_check",
            description="Python安全扫描，检测常见安全漏洞和风险代码",
            capabilities=["代码检查", "安全扫描", "漏洞检测", "bandit"],
            executor=lambda **kw: _runner.run_bandit_check(kw.get("files")),
            params={"files": "要检查的文件列表"},
            returns="安全问题列表",
            category="code_check",
        )
    except Exception as e:
        silent_exc(e, where="nucleus.tooling.ToolRegistry::register_default_tools L307")

    # 外部命令执行工具
    try:
        from nucleus.external_executor import ExternalExecutor
        _ext = ExternalExecutor()
        _registry.register_tool(
            name="external_command",
            description="执行外部系统命令，支持超时控制和安全沙箱",
            capabilities=["命令执行", "系统调用", "外部工具", "shell"],
            executor=lambda **kw: _ext.execute(kw.get("command", ""), timeout=kw.get("timeout", 60)),
            params={"command": "命令字符串", "timeout": "超时秒数"},
            returns="执行结果(stdout/stderr/returncode)",
            category="execution",
        )
    except Exception as e:
        silent_exc(e, where="nucleus.tooling.ToolRegistry::register_default_tools L323")


if __name__ == "__main__":
    # 自测
    register_default_tools()
    reg = get_tool_registry()
    print(f"已注册工具: {len(reg.list_tools())}个")
    for t in reg.list_tools():
        print(f"  - {t['name']}: {t['description'][:50]}")
    print(f"\n查找'代码检查': {[t['name'] for t in reg.find_tool('代码检查')]}")
    print(f"查找'安全扫描': {[t['name'] for t in reg.find_tool('安全扫描')]}")
    print(f"最佳工具'代码质量': {reg.get_best_tool('代码质量')}")
