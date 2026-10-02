# -*- coding: utf-8 -*-
"""
CodeReviewEngine.py —— 代码审查引擎

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 自动化代码审查与质量评估
机制: 基于CodeIssue类实现，包含10个核心方法
定位: 代码审查层
"""

from nucleus.data.path_utils import safe_relpath as _safe_relpath  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
from nucleus.data.exclude_dirs import prune  # ★157-E T-框架-1：统一排除集（单一真相集）
from config import TIMEOUT_CONFIG
import json
import os
import subprocess
import time
from dataclasses import dataclass, field

from nucleus.logger import get_module_logger
from config import EXTERNAL_CALL_TIMEOUTS
from nucleus._silent_except import silent_exc


_logger = get_module_logger("CodeReviewEngine")


@dataclass
class CodeIssue:
    """单个代码问题"""
    file_path: str           # 文件相对路径
    line: int                # 行号
    column: int              # 列号
    rule: str                # 规则ID（如F821、E501）
    severity: str            # 严重程度：P0/P1/P2/P3
    message: str             # 问题描述
    source: str              # 来源工具：ruff/pyright
    fix_available: bool = False  # 是否可自动修复
    fix_suggestion: str = ""     # 修复建议


@dataclass
class ReviewResult:
    """审查结果"""
    total_files: int = 0          # 审查文件数
    total_issues: int = 0         # 总问题数
    issues_by_severity: dict = field(default_factory=dict)  # 按严重程度分组
    issues: list = field(default_factory=list)              # 问题列表
    review_time: float = 0.0      # 审查耗时（秒）
    tools_used: list = field(default_factory=list)  # 使用的工具

    def get_p0_issues(self) -> list:
        """获取P0级问题"""
        return [i for i in self.issues if i.severity == "P0"]

    def get_p1_issues(self) -> list:
        """获取P1级问题"""
        return [i for i in self.issues if i.severity == "P1"]

    def get_summary(self) -> str:
        """获取摘要"""
        lines = [
            f"代码审查完成: {self.total_files}个文件, {self.total_issues}个问题, 耗时{self.review_time:.1f}秒",
            f"工具: {', '.join(self.tools_used)}",
        ]
        for sev in ["P0", "P1", "P2", "P3"]:
            count = self.issues_by_severity.get(sev, 0)
            if count > 0:
                lines.append(f"  {sev}: {count}个")
        return "\n".join(lines)


class CodeReviewEngine:
    """代码审查引擎"""

    # 规则严重程度映射
    RUFF_SEVERITY_MAP = {
        # P0: 严重错误（未定义名称、语法错误）
        "F821": "P0", "F822": "P0", "F823": "P0", "E999": "P0",
        "F811": "P0",  # 重定义
        # P1: 潜在bug
        "F841": "P1", "B009": "P1", "B010": "P1", "B011": "P1",
        "B012": "P1", "B013": "P1", "B014": "P1", "B015": "P1",
        "B016": "P1", "B017": "P1", "B018": "P1", "B019": "P1",
        "B020": "P1", "B021": "P1", "B022": "P1", "B023": "P1",
        "B024": "P1", "B025": "P1", "B026": "P1", "B027": "P1",
        "B028": "P1", "B029": "P1", "B030": "P1", "B031": "P1",
        "B032": "P1", "B033": "P1",
        "SIM115": "P1",  # 文件未用上下文管理器
        "LOG015": "P1",  # 根logger调用
        # P2: 风格和性能
        "E": "P2", "W": "P2", "F": "P2", "I": "P2", "UP": "P2",
        "C": "P2", "PERF": "P2", "RUF": "P2", "PIE": "P2",
        "PL": "P2", "SIM": "P2",
    }

    PYRIGHT_SEVERITY_MAP = {
        "error": "P1",
        "warning": "P2",
        "information": "P3",
    }

    def __init__(self, project_root: str | None = None):
        """
        初始化代码审查引擎

        Args:
            project_root: 项目根目录，默认使用当前文件的上级目录
        """
        if project_root is None:
            # nucleus/review/CodeReviewEngine.py -> 项目根目录
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.project_root = project_root
        self._ruff_available = self._check_tool("ruff")
        self._pyright_available = self._check_tool("pyright")
        self._last_review_time = 0
        self._cached_result = None
        _logger.info(f"代码审查引擎初始化: ruff={'可用' if self._ruff_available else '不可用'}, "
                     f"pyright={'可用' if self._pyright_available else '不可用'}")

    def _check_tool(self, tool_name: str) -> bool:
        """检查工具是否可用"""
        try:
            result = subprocess.run(
                [tool_name, "--version"], check=False,
                capture_output=True, text=True, timeout=TIMEOUT_CONFIG['subprocess_default'],
                encoding="utf-8", errors="replace",
                cwd=self.project_root
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            silent_exc(e, where="nucleus.review.CodeReviewEngine::_check_tool L132")
            return False

    def _get_severity(self, rule: str, source: str) -> str:
        """根据规则ID获取严重程度"""
        if source == "ruff":
            # 精确匹配
            if rule in self.RUFF_SEVERITY_MAP:
                return self.RUFF_SEVERITY_MAP[rule]
            # 前缀匹配
            for prefix, sev in self.RUFF_SEVERITY_MAP.items():
                if rule.startswith(prefix):
                    return sev
            return "P3"
        elif source == "pyright":
            return self.PYRIGHT_SEVERITY_MAP.get(rule, "P3")
        return "P3"

    def review_with_ruff(self, file_path: str | None = None) -> list:
        """
        使用RUFF审查代码

        Args:
            file_path: 指定文件路径，None表示审查整个项目

        Returns:
            问题列表
        """
        if not self._ruff_available:
            _logger.warning("RUFF不可用，跳过")
            return []

        issues = []
        try:
            cmd = ["ruff", "check", "--output-format", "json"]
            # ★修复：排除tmp目录（临时测试文件，不是正式代码）
            if not file_path:
                cmd.extend(["--exclude", "tmp"])
            if file_path:
                cmd.append(file_path)
            else:
                cmd.append(".")

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=EXTERNAL_CALL_TIMEOUTS["subprocess_long"], cwd=self.project_root, check=False,
                encoding="utf-8", errors="replace"
            )

            if result.stdout.strip():
                data = json.loads(result.stdout)
                for item in data:
                    issue = CodeIssue(
                        file_path=_safe_relpath(item.get("filename", ""), self.project_root),
                        line=item.get("location", {}).get("row", 0),
                        column=item.get("location", {}).get("column", 0),
                        rule=item.get("code", ""),
                        severity=self._get_severity(item.get("code", ""), "ruff"),
                        message=item.get("message", ""),
                        source="ruff",
                        fix_available=(item.get("fix") or {}).get("applicable", False),
                    )
                    issues.append(issue)

        except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            _logger.error(f"RUFF审查失败: {e}")

        return issues

    def review_with_pyright(self, file_path: str | None = None) -> list:
        """
        使用Pyright审查代码

        Args:
            file_path: 指定文件路径，None表示审查整个项目

        Returns:
            问题列表
        """
        if not self._pyright_available:
            _logger.warning("Pyright不可用，跳过")
            return []

        issues = []
        try:
            cmd = ["pyright", "--outputjson"]
            if file_path:
                cmd.append(file_path)

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=300, cwd=self.project_root, check=False,
                encoding="utf-8", errors="replace"
            )

            if result.stdout.strip():
                data = json.loads(result.stdout)
                for diag in data.get("generalDiagnostics", []):
                    severity = diag.get("severity", "information")
                    _diag_file = _safe_relpath(diag.get("file", ""), self.project_root)
                    # ★修复：排除tmp目录的结果（临时测试文件，不是正式代码）
                    if _diag_file.startswith("tmp" + os.sep) or _diag_file == "tmp":
                        continue
                    issue = CodeIssue(
                        file_path=_diag_file,
                        line=diag.get("range", {}).get("start", {}).get("line", 0) + 1,
                        column=diag.get("range", {}).get("start", {}).get("character", 0),
                        rule=diag.get("rule", ""),
                        severity=self._get_severity(severity, "pyright"),
                        message=diag.get("message", ""),
                        source="pyright",
                    )
                    issues.append(issue)

        except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
            _logger.error(f"Pyright审查失败: {e}")

        return issues

    def review(self, file_path: str | None = None, use_ruff: bool = True,
               use_pyright: bool = True, incremental: bool = True) -> ReviewResult:
        """
        执行完整代码审查

        Args:
            file_path: 指定文件路径，None表示审查整个项目
            use_ruff: 是否使用RUFF
            use_pyright: 是否使用Pyright（较慢，大项目建议只在需要时启用）
            incremental: 是否增量审查（只审查最近修改的文件）

        Returns:
            审查结果
        """
        start_time = time.time()
        result = ReviewResult()
        all_issues = []

        # 增量审查：只审查最近1小时修改的文件
        if incremental and file_path is None:
            modified_files = self._get_recently_modified_files(hours=1)
            if modified_files:
                _logger.info(f"增量审查: {len(modified_files)}个最近修改的文件")
                for f in modified_files:
                    if use_ruff:
                        all_issues.extend(self.review_with_ruff(f))
                    if use_pyright:
                        all_issues.extend(self.review_with_pyright(f))
                result.total_files = len(modified_files)
            else:
                _logger.info("无最近修改的文件，跳过增量审查")
                result.review_time = time.time() - start_time
                return result
        else:
            # 全量审查
            if use_ruff:
                all_issues.extend(self.review_with_ruff(file_path))
                result.tools_used.append("ruff")
            if use_pyright:
                all_issues.extend(self.review_with_pyright(file_path))
                result.tools_used.append("pyright")
            result.total_files = 1 if file_path else "all"

        # 汇总结果
        result.issues = all_issues
        result.total_issues = len(all_issues)
        for issue in all_issues:
            result.issues_by_severity[issue.severity] = \
                result.issues_by_severity.get(issue.severity, 0) + 1

        result.review_time = time.time() - start_time
        self._last_review_time = time.time()
        self._cached_result = result

        _logger.info(result.get_summary())
        return result

    def _get_recently_modified_files(self, hours: int = 1) -> list:
        """获取最近修改的Python文件"""
        modified = []
        cutoff = time.time() - hours * 3600
        for root, dirs, files in os.walk(self.project_root):
            # ★157-E T-框架-1：改用统一排除集 prune()（单一真相集，排除副本/缓存/备份/tmp/.aionclaw-tmp 等）
            dirs[:] = prune(dirs)
            for f in files:
                if f.endswith(".py"):
                    path = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(path)
                        if mtime > cutoff:
                            modified.append(_safe_relpath(path, self.project_root))
                    except OSError as e:
                        silent_exc(e, where="nucleus.review.CodeReviewEngine::_get_recently_modified_files L325")
        return modified

    def get_top_issues(self, n: int = 10, severity: str | None = None) -> list:
        """获取最严重的前N个问题"""
        if self._cached_result is None:
            return []
        issues = self._cached_result.issues
        if severity:
            issues = [i for i in issues if i.severity == severity]
        # 按严重程度排序
        sev_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        issues.sort(key=lambda x: (sev_order.get(x.severity, 9), x.file_path, x.line))
        return issues[:n]

    def generate_fix_plan(self, issues: list | None = None) -> list:
        """
        根据问题列表生成修复计划

        Args:
            issues: 问题列表，None使用最近一次审查结果

        Returns:
            修复计划列表，每项包含文件、问题、修复建议
        """
        if issues is None:
            if self._cached_result is None:
                return []
            issues = self._cached_result.get_p0_issues() + self._cached_result.get_p1_issues()

        plan = []
        for issue in issues:
            plan.append({
                "file": issue.file_path,
                "line": issue.line,
                "rule": issue.rule,
                "severity": issue.severity,
                "message": issue.message,
                "fix_suggestion": issue.fix_suggestion or self._get_fix_suggestion(issue),
                "auto_fixable": issue.fix_available,
            })
        return plan

    def _get_fix_suggestion(self, issue: CodeIssue) -> str:
        """根据问题类型生成修复建议"""
        suggestions = {
            "F821": "检查变量是否正确定义，或添加type: ignore",
            "F841": "删除未使用的变量，或添加_前缀",
            "E501": "拆分长行或增加行长度配置",
            "I001": "使用ruff check --fix自动排序导入",
            "UP009": "删除多余的UTF-8编码声明",
            "SIM115": "使用with语句管理文件资源",
            "LOG015": "使用get_module_logger获取模块级logger",
            "reportPossiblyUnboundVariable": "在使用前初始化变量默认值",
            "reportIncompatibleMethodOverride": "检查基类方法签名，保持一致",
        }
        return suggestions.get(issue.rule, "请人工审查后修复")


# 单例实例
_engine = None

def get_code_review_engine() -> CodeReviewEngine:
    """获取代码审查引擎单例"""
    global _engine
    if _engine is None:
        _engine = CodeReviewEngine()
    return _engine
