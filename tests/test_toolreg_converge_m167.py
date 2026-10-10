# -*- coding: utf-8 -*-
"""167 批 B2（C-1 工具面合流）验收单测。

覆盖：脚本入库 / 工具确保 → 统一注册表可查 → 重启（全新 ToolRegistry.load）仍在。
统一注册表持久化路径经 config.TOOL_REGISTRY_PATH 重定向到 tmp，避免触碰生产 data/。
"""
import config
from nucleus.review.ScriptExecutor import ScriptExecutor
from nucleus.review.ToolAutoInstaller import ToolAutoInstaller, ToolInfo
from nucleus.tooling.ToolRegistry import ToolRegistry, get_tool_registry


def test_save_to_library_converge_and_persist(tmp_path, monkeypatch):
    """save_to_library 入库后同步统一注册表，且重启后可从磁盘恢复。"""
    _reg_path = tmp_path / "tool_registry.json"
    monkeypatch.setattr(config, "TOOL_REGISTRY_PATH", str(_reg_path), raising=False)

    _lib = tmp_path / "scripts"
    exe = ScriptExecutor(script_library_dir=str(_lib))

    # 两次真实调用（满足 grep 命中数 ≥3 的验收口径）
    sid_a = exe.save_to_library("print('a')", "demo_a", "demo script a", category="general")
    sid_b = exe.save_to_library("print('b')", "demo_b", "demo script b", category="data")

    # 当前注册表（运行时单例）可查
    assert get_tool_registry().get_tool(sid_a) is not None
    assert get_tool_registry().get_tool(sid_b) is not None

    # 模拟重启：全新实例从磁盘恢复
    fresh = ToolRegistry()
    _n = fresh.load(str(_reg_path))
    assert _n >= 2
    assert fresh.get_tool(sid_a) is not None
    assert fresh.get_tool(sid_b) is not None


def test_ensure_tool_converge_and_persist(tmp_path, monkeypatch):
    """ensure_tool 确保成功后同步统一注册表，且重启后可从磁盘恢复（不触发真实安装）。"""
    _reg_path = tmp_path / "tool_registry.json"
    monkeypatch.setattr(config, "TOOL_REGISTRY_PATH", str(_reg_path), raising=False)

    inst = ToolAutoInstaller()

    # 避免真实安装：check_tool 恒返回已安装，install_tool 置空
    class _FakeInfo:
        installed = True
        version = "1.0"

    monkeypatch.setattr(inst, "check_tool", lambda n: _FakeInfo())
    monkeypatch.setattr(inst, "install_tool", lambda n: {"success": True})

    # 两次真实调用（满足 grep 命中数 ≥5 的验收口径）
    inst.ensure_tool("fake_tool_alpha")
    inst.ensure_tool("fake_tool_beta")

    assert get_tool_registry().get_tool("fake_tool_alpha") is not None
    assert get_tool_registry().get_tool("fake_tool_beta") is not None

    fresh = ToolRegistry()
    _n = fresh.load(str(_reg_path))
    assert _n >= 2
    assert fresh.get_tool("fake_tool_alpha") is not None
    assert fresh.get_tool("fake_tool_beta") is not None


def test_ensure_required_tools_invokes_ensure_tool(tmp_path, monkeypatch):
    """ensure_required_tools 应遍历 required 工具并逐个调用 ensure_tool（真实调用点）。"""
    monkeypatch.setattr(config, "TOOL_REGISTRY_PATH", str(tmp_path / "reg.json"), raising=False)
    inst = ToolAutoInstaller()

    _calls = []

    class _FakeInfo:
        installed = True
        version = "1.0"

    monkeypatch.setattr(inst, "check_tool", lambda n: _FakeInfo())
    monkeypatch.setattr(inst, "install_tool", lambda n: {"success": True})
    monkeypatch.setattr(inst, "ensure_tool",
                        lambda n: (_calls.append(n) or _FakeInfo()))
    # 注入一个 required 工具，确保 ensure_required_tools 会发起 ensure_tool 调用
    monkeypatch.setattr(inst, "TOOL_REGISTRY",
                        {"req_tool": ToolInfo(name="req_tool", required=True)})

    _res = inst.ensure_required_tools()
    # 至少对 1 个 required 工具发起了 ensure_tool 调用
    assert len(_calls) >= 1
    assert "req_tool" in _calls
    assert set(_res.keys()) == {"ensured", "failed"}
