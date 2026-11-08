"""★479：静态检查解释器选择的门控单测。

## 病的现场

用户控制台（重启前）反复出现：

    [342tool] ruff stderr 非空（61 字节...）: D:\\Program Files\\Python314t\\python.exe: No module named ruff
    [342tool] bandit stderr 非空（63 字节...）: ... Python314t ...: No module named bandit
    [342tool] ruff stdout 为空（rc=1 ...）⇒ 本通道 0 条，已记账

`tooling_runner` 里命令写成裸 `"python"`，PATH 解析到 3.14t；而当时 3.14t 没装
ruff/bandit/mypy ⇒ **三个通道永远 0 条**（还白花扫描时间）。

已装（2026-11-08）：ruff 0.17.4 / bandit 1.9.4 / mypy 2.4.1（有 cp314-cp314t wheel）。

## 本测试守什么

  ① 解释器解析的优先级（config 显式 > 当前解释器 > 回落）；
  ② `compileall` 必须用**运行时解释器**、三个静态工具用**解析出来的解释器**；
  ③ 代码里不得再出现裸 `"python"` 命令（防回潮）；
  ④ **端到端**：真的能扫出问题、也能在干净文件上报 0（证明通道不是空转）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.tooling_runner import (
    ToolingRunner,
    _m479_probe_static_tools,
    _m479_resolve_tool_python,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestResolve:
    def test_01_returns_existing_file(self):
        _py = _m479_resolve_tool_python()
        assert isinstance(_py, str) and os.path.isfile(_py), _py

    def test_02_explicit_config_wins(self, monkeypatch):
        """config.TOOLING_PYTHON 显式指定且存在 ⇒ 最高优先级。"""
        import config
        monkeypatch.setattr(config, "TOOLING_PYTHON", sys.executable)
        assert _m479_resolve_tool_python() == sys.executable

    def test_03_falls_through_when_explicit_missing(self, monkeypatch):
        """★反证：显式路径不存在 ⇒ 不能返回它（否则又是「No module named」）。"""
        import config
        monkeypatch.setattr(config, "TOOLING_PYTHON",
                            os.path.join(ROOT, "tmp", "__no_such_py__.exe"))
        _py = _m479_resolve_tool_python()
        assert os.path.isfile(_py), _py
        assert _py != os.path.join(ROOT, "tmp", "__no_such_py__.exe")

    def test_04_current_interpreter_preferred_when_has_tools(self):
        """当前解释器装了工具 ⇒ 应返回它（用户要求「主用 3.14t」）。"""
        if not _m479_probe_static_tools(sys.executable):
            pytest.skip("当前解释器没有静态工具，跳过优先性断言")
        assert _m479_resolve_tool_python() == sys.executable

    def test_05_probe_detects_missing(self):
        """★反证：不存在的解释器 ⇒ 探测必须 False。"""
        assert _m479_probe_static_tools(
            os.path.join(ROOT, "tmp", "__no_such_py__.exe")) is False

    def test_06_probe_empty_string_false(self):
        assert _m479_probe_static_tools("") is False


class TestWiring:
    """接线防回潮：命令必须用解析出来的解释器，不能再是裸 "python"。"""

    def _src(self) -> str:
        from pathlib import Path
        return (Path(ROOT) / "nucleus" / "tooling_runner.py").read_text(
            encoding="utf-8", errors="replace")

    def test_10_no_bare_python_command(self):
        _src = self._src()
        assert '"python", "-m"' not in _src, "还有裸 \"python\" 命令（会解析到错的解释器）"

    def test_11_static_tools_use_self_py(self):
        _src = self._src()
        assert _src.count('self._py, "-m"') == 3, "ruff/mypy/bandit 三处都要用 self._py"

    def test_12_compileall_uses_runtime_py(self):
        """compileall 语义是「真实运行环境能否编译」⇒ 必须用 sys.executable。"""
        _src = self._src()
        assert _src.count('self._runtime_py, "-m"') == 1

    def test_13_runner_fields(self):
        _r = ToolingRunner(ROOT)
        assert os.path.isfile(_r._py)
        assert _r._runtime_py == sys.executable


class TestEndToEnd:
    """★端到端：通道必须**真的**能出数据（这是「0 条」是否可信的前提）。"""

    def test_20_clean_file_zero(self, tmp_path):
        _p = tmp_path / "clean_479.py"
        _p.write_text("def f():\n    return 1\n", encoding="utf-8")
        _r = ToolingRunner(ROOT)
        assert _r.run_ruff_check([str(_p)]) == []

    def test_21_dirty_file_detected(self, tmp_path):
        """造一个必被 ruff 抓的文件（未使用 import + 未定义名）。"""
        _p = tmp_path / "dirty_479.py"
        _p.write_text("import os\nimport sys\n\n\ndef f():\n    return undefined_name\n",
                      encoding="utf-8")
        _r = ToolingRunner(ROOT)
        _items = _r.run_ruff_check([str(_p)])
        assert len(_items) > 0, (
            "ruff 通道仍然 0 条 —— 说明解释器还是没装 ruff，"
            f"当前 _py={_r._py}")
        assert all(isinstance(i, dict) for i in _items)

    def test_22_channel_flag_recorded(self, tmp_path):
        """通道记账必须写下来（这是「本通道 0 条」与「本通道没跑到」的分界）。"""
        _p = tmp_path / "clean2_479.py"
        _p.write_text("x = 1\n", encoding="utf-8")
        _r = ToolingRunner(ROOT)
        _r.run_ruff_check([str(_p)])
        _flags = _r.m360_tool_flags()
        assert "ruff" in _flags


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
