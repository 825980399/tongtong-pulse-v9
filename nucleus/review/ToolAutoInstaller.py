# -*- coding: utf-8 -*-
"""
ToolAutoInstaller.py —— 工具自动安装器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 依赖工具的自动检测与安装
机制: 基于ToolInfo类实现，包含10个核心方法
定位: 工具管理层
"""

from config import TIMEOUT_CONFIG
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from nucleus.logger import get_module_logger
from config import EXTERNAL_CALL_TIMEOUTS


_logger = get_module_logger("ToolAutoInstaller")


@dataclass
class ToolInfo:
    """工具信息"""
    name: str                    # 工具名称
    command: str = ""            # 命令行调用名
    pip_package: str = ""        # pip包名（如果是Python包）
    version_command: str = ""    # 版本查询命令
    required: bool = False       # 是否必需
    category: str = "general"    # 分类：code_review/data_processing/network/system
    description: str = ""        # 描述
    install_method: str = "pip"  # 安装方式：pip/system/manual
    installed: bool = False
    version: str = ""
    last_used: float = 0.0
    use_count: int = 0


@dataclass
class ToolCapability:
    """工具能力"""
    tool_name: str
    capability: str       # 能力描述（如"代码审查"、"类型检查"）
    command_template: str  # 命令模板（如"ruff check {file}"）
    output_format: str = "text"  # 输出格式：text/json
    success_pattern: str = ""    # 成功判断模式


class ToolAutoInstaller:
    """运行时工具自主安装引擎"""

    # 预定义工具库
    TOOL_REGISTRY = {
        # 代码审查工具
        "ruff": ToolInfo(
            name="ruff", command="ruff", pip_package="ruff",
            version_command="ruff --version",
            category="code_review", description="极速Python linter",
            install_method="pip",
        ),
        "pyright": ToolInfo(
            name="pyright", command="pyright", pip_package="pyright",
            version_command="pyright --version",
            category="code_review", description="Python类型检查器",
            install_method="pip",
        ),
        "pylint": ToolInfo(
            name="pylint", command="pylint", pip_package="pylint",
            version_command="pylint --version",
            category="code_review", description="Python代码分析器",
            install_method="pip",
        ),
        "mypy": ToolInfo(
            name="mypy", command="mypy", pip_package="mypy",
            version_command="mypy --version",
            category="code_review", description="静态类型检查器",
            install_method="pip",
        ),
        # 数据处理工具
        "pandas": ToolInfo(
            name="pandas", command="", pip_package="pandas",
            version_command="",
            category="data_processing", description="数据分析库",
            install_method="pip",
        ),
        "numpy": ToolInfo(
            name="numpy", command="", pip_package="numpy",
            version_command="",
            category="data_processing", description="数值计算库",
            install_method="pip",
        ),
        # 网络工具
        "curl": ToolInfo(
            name="curl", command="curl", pip_package="",
            version_command="curl --version",
            category="network", description="HTTP客户端",
            install_method="system",
        ),
        # 系统工具
        "git": ToolInfo(
            name="git", command="git", pip_package="",
            version_command="git --version",
            category="system", description="版本控制",
            install_method="system",
        ),
    }

    # 工具能力注册
    CAPABILITIES = {
        "code_review": [
            ToolCapability("ruff", "代码风格检查", "ruff check {file} --output-format json", "json"),
            ToolCapability("pyright", "类型检查", "pyright {file} --outputjson", "json"),
        ],
    }

    def __init__(self, project_root: str | None = None):
        """
        初始化工具安装器

        Args:
            project_root: 项目根目录
        """
        if project_root is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.project_root = project_root
        self._installed_cache: dict = {}
        self._install_history: list = []
        _logger.info(f"工具安装器初始化: 注册表中有{len(self.TOOL_REGISTRY)}个工具")

    def check_tool(self, tool_name: str) -> ToolInfo:
        """
        检查工具是否安装

        Args:
            tool_name: 工具名称

        Returns:
            工具信息
        """
        if tool_name not in self.TOOL_REGISTRY:
            _logger.warning(f"工具未注册: {tool_name}")
            return ToolInfo(name=tool_name, installed=False)

        tool = self.TOOL_REGISTRY[tool_name]

        # 缓存检查
        if tool_name in self._installed_cache:
            cached_time, cached_result = self._installed_cache[tool_name]
            if time.time() - cached_time < 300:  # 5分钟缓存
                return cached_result

        # 检查命令行工具
        if tool.command:
            tool.installed = shutil.which(tool.command) is not None
        # 检查Python包
        elif tool.pip_package:
            try:
                __import__(tool.pip_package.replace("-", "_"))
                tool.installed = True
            except ImportError:
                tool.installed = False

        # 获取版本
        if tool.installed and tool.version_command:
            try:
                result = subprocess.run(
                    tool.version_command.split(),
                    capture_output=True, text=True, timeout=TIMEOUT_CONFIG['subprocess_default'], check=False,
                    encoding="utf-8", errors="replace"
                )
                if result.returncode == 0:
                    tool.version = result.stdout.strip().split("\n")[0][:50]
            except Exception:
                pass

        # 更新缓存
        self._installed_cache[tool_name] = (time.time(), tool)
        return tool

    def install_tool(self, tool_name: str, verify: bool = True) -> dict:
        """
        安装工具

        Args:
            tool_name: 工具名称
            verify: 是否验证安装成功

        Returns:
            安装结果 {success, tool, version, error}
        """
        result = {"success": False, "tool": tool_name, "version": "", "error": ""}

        if tool_name not in self.TOOL_REGISTRY:
            result["error"] = f"工具未注册: {tool_name}"
            _logger.warning(result["error"])
            return result

        tool = self.TOOL_REGISTRY[tool_name]

        # 检查是否已安装
        current = self.check_tool(tool_name)
        if current.installed:
            result["success"] = True
            result["version"] = current.version
            result["error"] = "already_installed"
            _logger.info(f"工具已安装: {tool_name} ({current.version})")
            return result

        # 只支持pip安装
        if tool.install_method != "pip" or not tool.pip_package:
            result["error"] = f"工具 {tool_name} 不支持自动安装（方式={tool.install_method}）"
            _logger.warning(result["error"])
            return result

        # 执行安装
        _logger.info(f"开始安装工具: {tool_name} (pip install {tool.pip_package})")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", tool.pip_package, "--quiet"],
                capture_output=True, text=True, timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_long"], check=False,
                encoding="utf-8", errors="replace"
            )

            if proc.returncode != 0:
                result["error"] = proc.stderr[-200:] if proc.stderr else "安装失败"
                _logger.error(f"安装失败: {tool_name} - {result['error']}")
                return result

            # 验证安装
            if verify:
                time.sleep(1)
                self._installed_cache.pop(tool_name, None)  # 清除缓存
                installed = self.check_tool(tool_name)
                if not installed.installed:
                    result["error"] = "安装后验证失败"
                    _logger.error(result["error"])
                    return result
                result["version"] = installed.version

            result["success"] = True
            self._install_history.append({
                "tool": tool_name,
                "version": result["version"],
                "time": time.time(),
                "success": True,
            })
            _logger.info(f"安装成功: {tool_name}=={result['version']}")

        except subprocess.TimeoutExpired:
            result["error"] = "安装超时（120秒）"
            _logger.error(result["error"])
        except Exception as e:
            result["error"] = str(e)
            _logger.error(f"安装异常: {e}")

        return result

    def ensure_tool(self, tool_name: str) -> ToolInfo:
        """
        确保工具可用，缺失时自动安装

        Args:
            tool_name: 工具名称

        Returns:
            工具信息
        """
        tool = self.check_tool(tool_name)
        if not tool.installed:
            _logger.info(f"工具 {tool_name} 缺失，尝试自动安装")
            self.install_tool(tool_name)
            tool = self.check_tool(tool_name)
        return tool

    def get_tools_by_category(self, category: str) -> list:
        """按分类获取工具列表"""
        return [t for t in self.TOOL_REGISTRY.values() if t.category == category]

    def get_missing_tools(self, include_optional: bool = True) -> list:
        """获取所有缺失的工具"""
        missing = []
        for name, tool in self.TOOL_REGISTRY.items():
            if tool.required or include_optional:
                checked = self.check_tool(name)
                if not checked.installed:
                    missing.append({
                        "name": name,
                        "category": tool.category,
                        "description": tool.description,
                        "install_method": tool.install_method,
                    })
        return missing

    def auto_install_missing(self, categories: list | None = None) -> dict:
        """
        自动安装缺失的工具

        Args:
            categories: 指定分类，None表示所有分类

        Returns:
            安装结果汇总
        """
        results = {"installed": [], "failed": [], "skipped": []}

        for name, tool in self.TOOL_REGISTRY.items():
            if categories and tool.category not in categories:
                continue
            if tool.install_method != "pip":
                results["skipped"].append(name)
                continue

            checked = self.check_tool(name)
            if not checked.installed:
                result = self.install_tool(name)
                if result["success"]:
                    results["installed"].append(name)
                else:
                    results["failed"].append({"name": name, "error": result["error"]})

        _logger.info(f"自动安装完成: 成功={len(results['installed'])}, "
                     f"失败={len(results['failed'])}, 跳过={len(results['skipped'])}")
        return results

    def get_capabilities(self, category: str | None = None) -> list:
        """获取可用的工具能力"""
        caps = []
        for cat, cat_caps in self.CAPABILITIES.items():
            if category and cat != category:
                continue
            for cap in cat_caps:
                tool = self.check_tool(cap.tool_name)
                if tool.installed:
                    caps.append({
                        "tool": cap.tool_name,
                        "capability": cap.capability,
                        "command_template": cap.command_template,
                        "output_format": cap.output_format,
                    })
        return caps

    def get_install_history(self, limit: int = 50) -> list:
        """获取安装历史"""
        return self._install_history[-limit:]

    def get_status_summary(self) -> str:
        """获取状态摘要"""
        total = len(self.TOOL_REGISTRY)
        installed = sum(1 for name in self.TOOL_REGISTRY if self.check_tool(name).installed)
        lines = [
            f"工具状态: {installed}/{total} 已安装",
        ]
        for category in ["code_review", "data_processing", "network", "system"]:
            tools = self.get_tools_by_category(category)
            inst = sum(1 for t in tools if self.check_tool(t.name).installed)
            lines.append(f"  {category}: {inst}/{len(tools)}")
        return "\n".join(lines)


# 单例实例
_installer = None

def get_tool_installer() -> ToolAutoInstaller:
    """获取工具安装器单例"""
    global _installer
    if _installer is None:
        _installer = ToolAutoInstaller()
    return _installer
