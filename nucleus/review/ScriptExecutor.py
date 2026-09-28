# -*- coding: utf-8 -*-
"""
ScriptExecutor.py —— 脚本执行器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 外部脚本的安全执行与结果收集
机制: 基于ScriptResult类实现，包含10个核心方法
定位: 执行管理层
"""

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from nucleus.logger import get_module_logger


# ★8-2：复用主沙箱的资源限制模型（CPU/内存/文件描述符/进程数上限 + 内存看门狗）
from nucleus.security.sandbox_limits import execute_code_in_subprocess

_logger = get_module_logger("ScriptExecutor")


@dataclass
class ScriptResult:
    """脚本执行结果"""
    success: bool = False
    return_code: int = -1
    stdout: str = ""
    stderr: str = ""
    execution_time: float = 0.0
    output_data: Any = None  # 脚本输出的结构化数据（如果有）
    error: str = ""


@dataclass
class ScriptMeta:
    """脚本元数据"""
    script_id: str = ""
    name: str = ""
    description: str = ""
    category: str = "general"  # general/data_processing/code_analysis/environment
    created_at: float = 0.0
    last_used: float = 0.0
    use_count: int = 0
    success_count: int = 0
    tags: list = field(default_factory=list)
    script_path: str = ""


class ScriptExecutor:
    """脚本执行引擎"""

    # 危险操作黑名单（脚本中禁止出现）
    DANGEROUS_PATTERNS = [
        "os.system(", "subprocess.call(", "subprocess.run(",
        "os.remove(", "os.unlink(", "os.rmdir(", "shutil.rmtree(",
        "eval(", "exec(", "__import__(",
        "open(",  # 文件写入需要特殊处理
        "socket.", "requests.", "urllib.",  # 网络操作
    ]

    # ★S7修复（P0 安全）：高危模式命中即**拒绝执行**。
    #   原实现把所有危险模式都塞进 result["warnings"]（见 validate_script），
    #   而 execute() 仅在 validation["valid"] 为 False 时拒绝（:204-208），
    #   valid 又只由「语法错误」决定 —— 故黑名单纯装饰，
    #   含 os.system( / subprocess.run( / eval( 的脚本照样被执行。
    #   配合「无环境隔离 + 继承父进程全部环境变量」，构成完整的 RCE 链路
    #   （实测可取得 uid=0 权限，并读到 160 个继承的环境变量）。
    #   现分级：高危阻断 / 中危仅告警（文件读写与网络请求是常见合法需求，
    #   一刀切阻断会误伤大量正常脚本，故保持在告警级）。
    BLOCKING_PATTERNS = [
        # 命令执行
        "os.system(", "subprocess.call(", "subprocess.run(",
        "subprocess.Popen(", "subprocess.check_output(", "subprocess.check_call(",
        "os.execv(", "os.execve(", "os.fork(", "os.spawn",
        # 动态代码执行
        "eval(", "exec(", "compile(", "__import__(",
        # 破坏性文件操作
        "os.remove(", "os.unlink(", "os.rmdir(", "shutil.rmtree(",
        # 网络与反弹shell
        "socket.", "pty.spawn",
        # 常见绕过手法（字符串拼接/属性访问隐藏真实调用）
        "importlib.import_module", "getattr(__builtins__",
        "builtins.__dict__", "__builtins__.__dict__",
    ]

    # ★S7：中危模式——允许执行但记录告警（不做阻断，避免误伤）
    WARNING_PATTERNS = [
        "open(", "requests.", "urllib.", "os.rename(", "os.mkdir(",
    ]

    # ★S7：子进程环境变量白名单。
    #   原实现直接继承父进程全部环境（实测 160 个变量），
    #   其中包含 LLM/企业微信等服务的 API Key 与 Token，
    #   脚本一旦被执行即可随命令执行结果一并外泄。
    #   此处仅透传运行 Python 解释器所必需的最小集合。
    ENV_ALLOWLIST = [
        # 跨平台通用
        "PATH", "PYTHONHOME", "PYTHONPATH", "PYTHONIOENCODING",
        "PYTHONUTF8", "PYTHONHASHSEED", "PYTHONNOUSERSITE",
        "HOME", "USER", "USERPROFILE", "LANG", "LC_ALL", "TZ",
        # Linux/macOS
        "TMPDIR", "LD_LIBRARY_PATH", "SHELL",
        # Windows（缺失会导致 Python 无法定位系统 DLL）
        "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
        "PATHEXT", "COMSPEC", "APPDATA", "LOCALAPPDATA",
        "PROGRAMFILES", "PROGRAMFILES(X86)", "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE", "USERDOMAIN", "COMPUTERNAME",
    ]

    # 允许的安全模块
    SAFE_MODULES = {
        "os", "sys", "json", "re", "math", "time", "datetime",
        "collections", "itertools", "functools", "typing",
        "pathlib", "string", "hashlib", "random",
    }

    def __init__(self, script_library_dir: str | None = None):
        """
        初始化脚本执行引擎

        Args:
            script_library_dir: 脚本库目录，默认在tools/script_library/
        """
        if script_library_dir is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            script_library_dir = os.path.join(project_root, "tools", "script_library")
        self.script_library_dir = script_library_dir
        os.makedirs(self.script_library_dir, exist_ok=True)
        # ★S8修复（P0 功能缺陷）：此处原为
        #     self._script_cache: dict = field(default_factory=dict)
        #     self._execution_history: list = field(default_factory=list)
        #   field() 是 dataclasses 的**类字段描述符**，只能在 dataclass 类体内使用
        #   （本文件 55 行 `tags: list = field(default_factory=list)` 才是正确用法）。
        #   在 __init__ 中调用它返回的是 dataclasses.Field 对象，而非 dict/list，
        #   导致 execute() 执行到「记录执行历史」的
        #     self._execution_history.append({...})
        #   时抛出 AttributeError: 'Field' object has no attribute 'append'
        #   —— 即本引擎一经调用必然崩溃。
        #   当前该实例由 main.py:335 创建但全库零使用，故该缺陷尚未在生产暴露；
        #   一旦有调用方接线即 100% 触发，故在此先行修正。
        self._script_cache: dict = {}
        self._execution_history: list = []
        _logger.info(f"脚本执行引擎初始化: 脚本库={self.script_library_dir}")

    def generate_script(self, task_description: str, template: str | None = None,
                        input_params: dict | None = None) -> str:
        """
        根据任务描述生成脚本

        Args:
            task_description: 任务描述
            template: 脚本模板，None使用通用模板
            input_params: 输入参数

        Returns:
            生成的脚本代码
        """
        if template:
            script = template
            if input_params:
                for key, value in input_params.items():
                    script = script.replace(f"{{{{{key}}}}}", repr(value))
        else:
            # 通用脚本模板
            script = self._build_generic_script(task_description, input_params)

        return script

    def _build_generic_script(self, task_description: str, params: dict | None = None) -> str:
        """构建通用脚本"""
        params_json = json.dumps(params or {}, ensure_ascii=False, indent=2)
        return f'''# -*- coding: utf-8 -*-
"""
自动生成脚本
任务: {task_description}
生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}
"""
import json
import sys
import os

# 输入参数
PARAMS = {params_json}

def main():
    """主函数"""
    result = {{"success": False, "data": None, "error": None}}
    try:
        # TODO: 在这里实现任务逻辑
        result["success"] = True
        result["data"] = {{"message": "脚本执行成功"}}
    except Exception as e:
        result["error"] = str(e)
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
'''

    def validate_script(self, script_code: str) -> dict:
        """
        验证脚本安全性和语法

        Args:
            script_code: 脚本代码

        Returns:
            验证结果 {valid, errors, warnings}
        """
        result = {"valid": True, "errors": [], "warnings": []}

        # 1. 语法检查
        try:
            compile(script_code, "<script>", "exec")
        except SyntaxError as e:
            result["valid"] = False
            result["errors"].append(f"语法错误: {e}")
            return result

        # 2. 危险操作检查
        # ★S7修复：分级处置——
        #   BLOCKING_PATTERNS 命中 → 写入 errors，令 valid=False，execute() 将拒绝执行；
        #   WARNING_PATTERNS  命中 → 仅记录告警，不阻断（避免误伤正常文件读写）。
        #   原实现对所有模式一视同仁地只写 warnings，导致黑名单形同虚设。
        for pattern in self.BLOCKING_PATTERNS:
            if pattern in script_code:
                result["valid"] = False
                result["errors"].append(
                    f"包含被禁止的高危操作: {pattern}（该操作可导致命令执行或文件破坏，已拒绝执行）")
        for pattern in self.WARNING_PATTERNS:
            if pattern in script_code:
                result["warnings"].append(f"包含需留意的敏感操作: {pattern}")
        # 保留旧字段的告警输出，兼容依赖 DANGEROUS_PATTERNS 的调用方
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in script_code and pattern not in self.BLOCKING_PATTERNS:
                result["warnings"].append(f"包含潜在危险操作: {pattern}")

        # 3. 导入检查（只允许安全模块）
        import re
        imports = re.findall(r'^import\s+(\w+)', script_code, re.MULTILINE)
        imports += re.findall(r'^from\s+(\w+)', script_code, re.MULTILINE)
        for imp in imports:
            if imp not in self.SAFE_MODULES:
                result["warnings"].append(f"导入非安全模块: {imp}")

        if result["errors"]:
            result["valid"] = False

        return result

    def _build_safe_env(self) -> dict[str, str]:
        """★S7：构造子进程的最小环境变量集（白名单制）。

        默认继承父进程的全部环境变量会把 LLM / 企业微信等服务的
        API Key、Token 一并暴露给被执行脚本（实测继承 160 个变量）。
        白名单制即「默认 deny，显式 allow」，新增变量必须显式加入
        ENV_ALLOWLIST，避免凭证随新配置悄悄泄漏。
        """
        _env = {}
        for _key, _val in os.environ.items():
            if _key.upper() in {k.upper() for k in self.ENV_ALLOWLIST}:
                _env[_key] = _val
        # 兜底：连 PATH 都没有时子进程几乎无法启动，补一个最小 PATH
        if not _env.get("PATH"):
            _env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
        # 强制 UTF-8 输出，避免 Windows(GBK) 下脚本 print 非 ASCII 字符崩溃
        _env.setdefault("PYTHONIOENCODING", "utf-8")
        return _env

    def execute(self, script_code: str, timeout: int = 30,
                capture_output: bool = True, work_dir: str | None = None) -> ScriptResult:
        """
        执行脚本

        Args:
            script_code: 脚本代码
            timeout: 超时时间（秒）
            capture_output: 是否捕获输出
            work_dir: 工作目录

        Returns:
            执行结果
        """
        result = ScriptResult()

        # 1. 验证脚本
        validation = self.validate_script(script_code)
        if not validation["valid"]:
            result.error = f"脚本验证失败: {'; '.join(validation['errors'])}"
            # ★T4修复（P2，星轨 N3）：安全门拒绝高危脚本是**预期业务行为**，
            #   不是系统错误。原用 ERROR 级别，导致两个后果：
            #   1) 污染错误统计（50分钟运行出现 4 个 ERROR，全是本处拦截日志）；
            #   2) 更严重的——LogAnalyzer 会把 ERROR 日志当作代码问题采集
            #      （LogAnalyzer.py:124-142 的 ERROR/CRITICAL 分支），
            #      使这些「正常拦截记录」进入自主进化的问题队列，
            #      而它们既无 file 也无 method，修复流程必然静默跳过，
            #      最终表现为「发现N个问题，修复0个，通过率0%」（星轨 N1）。
            #   降级为 WARNING：仍醒目可追溯，但不再被当作系统错误与代码缺陷。
            _logger.warning(result.error)
            return result

        # 2. 写入临时文件
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".py", prefix="script_")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(script_code)

            # 3. 执行
            start_time = time.time()
            try:
                # ★8-2加固：复用 nucleus.security.sandbox_limits 的资源限制模型，
                #   与主沙箱 PulseCodeSandbox 同一套子进程隔离 + 资源上限。
                #   相比 S7 的裸 subprocess.run，新增三道防线：
                #     ① CPU 时间上限（RLIMIT_CPU，Linux/macOS）
                #     ② 内存上限（RLIMIT_AS / Windows ctypes 看门狗轮询）
                #     ③ 文件描述符 / 进程数上限（RLIMIT_NOFILE / NPROC）
                #   allow_full_builtins=True 保留脚本的 import 能力（ScriptExecutor
                #   允许 import SAFE_MODULES 安全模块，与主沙箱的受限 builtins 不同），
                #   高危操作已在 validate_script 的 BLOCKING_PATTERNS 阶段被阻断。
                _safe_env = self._build_safe_env()
                _res = execute_code_in_subprocess(
                    script_code,
                    timeout_seconds=timeout,
                    child_env=_safe_env,
                    cwd=work_dir or self.script_library_dir,
                    source_label="<script>",
                    allow_full_builtins=True,
                )
                result.return_code = _res.returncode
                result.stdout = _res.stdout or ""
                result.stderr = _res.stderr or ""
                if _res.limit_hit == "wall_clock":
                    result.error = f"脚本执行超时（{timeout}秒）"
                    result.success = False
                elif _res.limit_hit == "cpu":
                    result.error = "脚本执行超过 CPU 时间上限（30 秒）"
                    result.success = False
                elif _res.limit_hit == "memory":
                    result.error = "脚本执行超过内存上限（512MB）"
                    result.success = False
                else:
                    result.success = _res.returncode == 0
            except subprocess.TimeoutExpired:
                result.error = f"脚本执行超时（{timeout}秒）"
                _logger.error(result.error)
            except Exception as e:
                result.error = f"执行异常: {e}"
                _logger.error(result.error)

            result.execution_time = time.time() - start_time

            # 4. 解析输出（如果是JSON）
            if result.stdout.strip():
                try:
                    result.output_data = json.loads(result.stdout.strip())
                except (json.JSONDecodeError, ValueError):
                    pass  # 不是JSON输出，保留原始文本

            # 5. 记录执行历史
            self._execution_history.append({
                "time": time.time(),
                "success": result.success,
                "execution_time": result.execution_time,
                "error": result.error,
            })
            # 历史记录上限
            if len(self._execution_history) > 1000:
                self._execution_history = self._execution_history[-500:]

        finally:
            # 清理临时文件
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        if result.success:
            _logger.debug(f"脚本执行成功: {result.execution_time:.2f}秒")
        else:
            _logger.warning(f"脚本执行失败: {result.error or result.stderr[:100]}")

        return result

    def save_to_library(self, script_code: str, name: str, description: str = "",
                        category: str = "general", tags: list | None = None) -> str:
        """
        保存脚本到脚本库

        Args:
            script_code: 脚本代码
            name: 脚本名称
            description: 脚本描述
            category: 分类
            tags: 标签

        Returns:
            脚本ID
        """
        script_id = f"{category}_{name}_{int(time.time())}"
        script_path = os.path.join(self.script_library_dir, f"{script_id}.py")

        # 添加元数据头
        header = f'''# -*- coding: utf-8 -*-
"""
脚本名称: {name}
描述: {description}
分类: {category}
标签: {', '.join(tags or [])}
创建时间: {time.strftime("%Y-%m-%d %H:%M:%S")}
"""
'''
        full_script = header + script_code

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(full_script)

        # 保存元数据
        meta = ScriptMeta(
            script_id=script_id,
            name=name,
            description=description,
            category=category,
            created_at=time.time(),
            tags=tags or [],
            script_path=script_path,
        )
        self._script_cache[script_id] = meta

        _logger.info(f"脚本已保存到库: {name} ({script_id})")
        return script_id

    def load_from_library(self, script_id: str) -> str:
        """从脚本库加载脚本"""
        script_path = os.path.join(self.script_library_dir, f"{script_id}.py")
        if not os.path.exists(script_path):
            _logger.error(f"脚本不存在: {script_id}")
            return ""
        with open(script_path, encoding="utf-8") as f:
            return f.read()

    def list_scripts(self, category: str | None = None) -> list:
        """列出脚本库中的脚本"""
        scripts = []
        for f in os.listdir(self.script_library_dir):
            if f.endswith(".py"):
                script_id = f[:-3]
                meta = self._script_cache.get(script_id)
                if meta is None:
                    meta = ScriptMeta(script_id=script_id, name=script_id)
                if category is None or meta.category == category:
                    scripts.append({
                        "id": script_id,
                        "name": meta.name,
                        "category": meta.category,
                        "use_count": meta.use_count,
                    })
        return scripts

    def execute_from_library(self, script_id: str, timeout: int = 30) -> ScriptResult:
        """从脚本库加载并执行"""
        script_code = self.load_from_library(script_id)
        if not script_code:
            return ScriptResult(success=False, error=f"脚本不存在: {script_id}")

        # 更新使用计数
        if script_id in self._script_cache:
            # GIL-dependent atomic increment (safe on CPython 3.11/3.12, review before free-threaded migration)
            self._script_cache[script_id].use_count += 1
            self._script_cache[script_id].last_used = time.time()

        return self.execute(script_code, timeout=timeout)

    def get_stats(self) -> dict:
        """获取执行统计"""
        total = len(self._execution_history)
        success = sum(1 for h in self._execution_history if h["success"])
        avg_time = sum(h["execution_time"] for h in self._execution_history) / max(total, 1)
        return {
            "total_executions": total,
            "success_count": success,
            "success_rate": success / max(total, 1),
            "avg_execution_time": avg_time,
            "library_size": len(os.listdir(self.script_library_dir)),
        }


# 单例实例
_executor = None

def get_script_executor() -> ScriptExecutor:
    """获取脚本执行引擎单例"""
    global _executor
    if _executor is None:
        _executor = ScriptExecutor()
    return _executor
