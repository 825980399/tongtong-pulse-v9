# -*- coding: utf-8 -*-
"""162批刀10 · ToolRegistry 持久化（C-1 收尾）门控单测。

验收：
- 注册表写入 → 重启（新实例 load）后仍在；
- register_tool(overwrite=True) 可重绑 executor / 更新描述；
- load 不恢复 executor（置 None，由 register_default_tools/ensure_tool 重绑）；
- 持久化文件剔除不可序列化的 executor 字段。
"""
import json
import os

from nucleus.tooling.ToolRegistry import ToolRegistry


def test_registry_persist_across_restart(tmp_path):
    """验收核心：注册表写入 → 重启（新实例 load）后仍在。"""
    _p = str(tmp_path / "tool_registry.json")
    _r1 = ToolRegistry()
    assert _r1.register_tool(name="custom_x", description="用户工具",
                             capabilities=["x"], category="user") is True
    assert _r1.save(_p) is True
    assert os.path.isfile(_p)
    # 模拟重启：全新实例 load
    _r2 = ToolRegistry()
    _n = _r2.load(_p)
    assert _n >= 1
    _t = _r2.get_tool("custom_x")
    assert _t is not None, "重启后注册表应仍在"
    assert _t["category"] == "user"
    assert _t["description"] == "用户工具"
    # executor 不持久化（置 None）
    assert _r2._tools["custom_x"]["executor"] is None


def test_overwrite_rebinds_executor_and_updates_desc():
    """overwrite=True 应重绑 executor + 更新描述，且不新增重复条目。"""
    _r = ToolRegistry()
    def _exec(**kw):
        return "ok"
    assert _r.register_tool(name="t", description="v1", capabilities=["a"],
                             executor=_exec) is True

    def _exec2(**kw):
        return "ok2"
    assert _r.register_tool(name="t", description="v2", capabilities=["a", "b"],
                             executor=_exec2, overwrite=True) is True
    # 仍是同一工具（未新增）
    assert len(_r.list_tools()) == 1
    # 描述更新、executor 重绑
    assert _r._tools["t"]["description"] == "v2"
    assert _r.execute("t")["result"] == "ok2"


def test_load_missing_file_returns_zero():
    _r = ToolRegistry()
    assert _r.load("data/__nonexistent_tool_registry__.json") == 0


def test_save_excludes_executor(tmp_path):
    """持久化文件不得包含不可序列化的 executor 字段。"""
    _p = str(tmp_path / "tool_registry.json")
    _r = ToolRegistry()
    _r.register_tool(name="with_exec", description="d", capabilities=["c"],
                     executor=lambda **kw: 1)
    assert _r.save(_p) is True
    _d = json.loads(open(_p, encoding="utf-8").read())
    assert "executor" not in _d["tools"]["with_exec"], "executor 应被剔除"
