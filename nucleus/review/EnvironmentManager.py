# -*- coding: utf-8 -*-
"""
EnvironmentManager.py —— 环境管理器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 执行环境的管理与隔离
机制: 基于PackageInfo类实现，包含10个核心方法
定位: 环境管理层
"""

import importlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from config import EXTERNAL_CALL_TIMEOUTS, TIMEOUT_CONFIG
from nucleus._silent_except import silent_exc
from nucleus.logger import get_module_logger

_logger = get_module_logger("EnvironmentManager")


@dataclass
class PackageInfo:
    """包信息"""
    name: str
    version: str = ""
    installed: bool = False
    required: bool = False  # 是否为框架必需
    category: str = "optional"  # core/optional/dev
    install_command: str = ""


@dataclass
class EnvironmentStatus:
    """环境状态"""
    python_version: str = ""
    executable: str = ""
    total_packages: int = 0
    missing_required: list = field(default_factory=list)
    missing_optional: list = field(default_factory=list)
    outdated: list = field(default_factory=list)
    health_score: int = 100  # 0-100
    last_check: float = 0.0


class EnvironmentManager:
    """自主环境管理引擎"""

    # 框架核心依赖（必须安装）
    CORE_PACKAGES = [
        "numpy", "orjson", "pyyaml", "requests",
    ]

    # 可选依赖（功能增强，缺失时降级）
    OPTIONAL_PACKAGES = {
        "opencv-python": "视觉处理",
        "mediapipe": "人脸检测",
        "torch": "深度学习推理",
        "transformers": "NLP模型",
        "pillow": "图像处理",
        "aiohttp": "异步HTTP",
        "psutil": "系统监控",
        "pyarrow": "Parquet存储",
        "pandas": "数据处理",
        "scikit-learn": "机器学习",
    }

    # 安装白名单（只允许安装这些包）
    INSTALL_WHITELIST = set(CORE_PACKAGES + list(OPTIONAL_PACKAGES.keys()))

    def __init__(self, project_root: str | None = None):
        """
        初始化环境管理器

        Args:
            project_root: 项目根目录
        """
        if project_root is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.project_root = project_root
        self._status = EnvironmentStatus()
        self._install_history: list = []
        self._pip_available = self._check_pip()
        _logger.info(f"环境管理器初始化: Python={sys.version.split()[0]}, pip={'可用' if self._pip_available else '不可用'}")

    def _check_pip(self) -> bool:
        """检查pip是否可用"""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "--version"],
                capture_output=True, text=True, timeout=TIMEOUT_CONFIG['subprocess_default'], check=False,
                encoding="utf-8", errors="replace"
            )
            return result.returncode == 0
        except Exception as e:
            silent_exc(e, where="nucleus.review.EnvironmentManager::_check_pip L103")
            return False

    def check_package(self, package_name: str) -> PackageInfo:
        """
        检查单个包是否安装

        Args:
            package_name: 包名

        Returns:
            包信息
        """
        info = PackageInfo(name=package_name)
        try:
            # 尝试导入
            import_name = package_name.replace("-", "_")
            # 特殊处理
            if package_name == "opencv-python":
                import_name = "cv2"
            elif package_name == "scikit-learn":
                import_name = "sklearn"
            elif package_name == "pyyaml":
                import_name = "yaml"

            mod = importlib.import_module(import_name)
            info.installed = True
            info.version = getattr(mod, "__version__", "unknown")
        except ImportError:
            info.installed = False
        except Exception as e:
            _logger.debug(f"检查包 {package_name} 异常: {e}")
            info.installed = False

        info.required = package_name in self.CORE_PACKAGES
        info.category = "core" if info.required else "optional"
        info.install_command = f"{sys.executable} -m pip install {package_name}"
        return info

    def check_environment(self, full_scan: bool = False) -> EnvironmentStatus:
        """
        检查完整环境状态

        Args:
            full_scan: 是否全量扫描所有可选包

        Returns:
            环境状态
        """
        status = EnvironmentStatus()
        status.python_version = sys.version.split()[0]
        status.executable = sys.executable
        status.last_check = time.time()

        # 1. 检查核心依赖
        missing_core = []
        for pkg in self.CORE_PACKAGES:
            info = self.check_package(pkg)
            if not info.installed:
                missing_core.append(pkg)
                _logger.warning(f"核心依赖缺失: {pkg}")

        status.missing_required = missing_core

        # 2. 检查可选依赖（全量扫描时）
        if full_scan:
            missing_optional = []
            for pkg, desc in self.OPTIONAL_PACKAGES.items():
                info = self.check_package(pkg)
                if not info.installed:
                    missing_optional.append({"name": pkg, "description": desc})
            status.missing_optional = missing_optional

        # 3. 计算健康分数
        score = 100
        score -= len(missing_core) * 10  # 每个缺失核心包扣10分
        score -= len(status.missing_optional) * 2  # 每个缺失可选包扣2分
        status.health_score = max(0, score)

        self._status = status
        _logger.info(f"环境检查完成: 健康度={status.health_score}分, "
                     f"核心缺失={len(missing_core)}, 可选缺失={len(status.missing_optional)}")
        return status

    def install_package(self, package_name: str, version: str | None = None,
                        verify: bool = True) -> dict:
        """
        安装包（白名单验证）

        Args:
            package_name: 包名
            version: 版本号，None表示最新版
            verify: 是否验证安装成功

        Returns:
            安装结果 {success, package, version, error}
        """
        result = {"success": False, "package": package_name, "version": "", "error": ""}

        # 1. 白名单验证
        if package_name not in self.INSTALL_WHITELIST:
            result["error"] = f"包 {package_name} 不在安装白名单中，拒绝安装"
            _logger.warning(result["error"])
            return result

        # 2. 检查是否已安装
        info = self.check_package(package_name)
        if info.installed:
            result["success"] = True
            result["version"] = info.version
            result["error"] = "already_installed"
            _logger.info(f"包 {package_name} 已安装（版本={info.version}），跳过")
            return result

        # 3. 执行安装
        install_spec = package_name if not version else f"{package_name}=={version}"
        _logger.info(f"开始安装: {install_spec}")

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pip", "install", install_spec, "--quiet"],
                capture_output=True, text=True, timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_long"], check=False,
                encoding="utf-8", errors="replace"
            )

            if proc.returncode != 0:
                result["error"] = proc.stderr[-200:] if proc.stderr else "安装失败"
                _logger.error(f"安装失败: {package_name} - {result['error']}")
                return result

            # 4. 验证安装
            if verify:
                time.sleep(1)  # 等待安装完成
                info = self.check_package(package_name)
                if not info.installed:
                    result["error"] = "安装后验证失败，无法导入"
                    _logger.error(result["error"])
                    return result
                result["version"] = info.version

            result["success"] = True
            self._install_history.append({
                "package": package_name,
                "version": result["version"],
                "time": time.time(),
                "success": True,
            })
            _logger.info(f"安装成功: {package_name}=={result['version']}")

        except subprocess.TimeoutExpired:
            result["error"] = "安装超时（120秒）"
            _logger.error(result["error"])
        except Exception as e:
            result["error"] = str(e)
            _logger.error(f"安装异常: {e}")

        return result

    def auto_install_missing(self, include_optional: bool = False) -> dict:
        """
        自动安装所有缺失的依赖

        Args:
            include_optional: 是否包含可选依赖

        Returns:
            安装结果汇总
        """
        status = self.check_environment(full_scan=include_optional)
        results = {"installed": [], "failed": [], "skipped": []}

        # 安装核心依赖
        for pkg in status.missing_required:
            result = self.install_package(pkg)
            if result["success"]:
                results["installed"].append(pkg)
            else:
                results["failed"].append({"package": pkg, "error": result["error"]})

        # 安装可选依赖
        if include_optional:
            for item in status.missing_optional:
                pkg = item["name"]
                result = self.install_package(pkg)
                if result["success"]:
                    results["installed"].append(pkg)
                else:
                    results["failed"].append({"package": pkg, "error": result["error"]})

        _logger.info(f"自动安装完成: 成功={len(results['installed'])}, "
                     f"失败={len(results['failed'])}")
        return results

    def ensure_import(self, module_name: str, package_name: str | None = None) -> Any:
        """
        确保模块可导入，缺失时自动安装

        Args:
            module_name: 导入名（如cv2、numpy）
            package_name: pip包名（如opencv-python），None时用module_name

        Returns:
            导入的模块，失败返回None
        """
        try:
            return importlib.import_module(module_name)
        except ImportError:
            pkg = package_name or module_name
            _logger.info(f"模块 {module_name} 缺失，尝试自动安装 {pkg}")
            result = self.install_package(pkg)
            if result["success"]:
                try:
                    return importlib.import_module(module_name)
                except ImportError:
                    _logger.error(f"安装后仍无法导入: {module_name}")
                    return None
            return None

    def get_install_history(self, limit: int = 50) -> list:
        """获取安装历史"""
        return self._install_history[-limit:]

    def get_status_summary(self) -> str:
        """获取状态摘要"""
        s = self._status
        lines = [
            f"环境状态: Python={s.python_version}, 健康度={s.health_score}分",
            f"核心依赖缺失: {len(s.missing_required)}个",
        ]
        if s.missing_required:
            lines.append(f"  {', '.join(s.missing_required)}")
        if s.missing_optional:
            lines.append(f"可选依赖缺失: {len(s.missing_optional)}个")
        return "\n".join(lines)


# 单例实例
_manager = None

def get_environment_manager() -> EnvironmentManager:
    """获取环境管理器单例"""
    global _manager
    if _manager is None:
        _manager = EnvironmentManager()
    return _manager
