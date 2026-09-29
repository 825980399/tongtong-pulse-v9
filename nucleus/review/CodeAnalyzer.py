# -*- coding: utf-8 -*-
"""
CodeAnalyzer.py —— 代码分析器

版本: v10 PulseNet
设计: 内部协作者、内部协作者、内部协作者
日期: 2026年9月11日

职责: 代码静态分析与结构理解
机制: 基于FunctionInfo类实现，包含10个核心方法
定位: 代码审查层
"""

from nucleus.data.path_utils import safe_relpath as _safe_relpath  # ★第55批 T3（跨盘安全，同盘行为与 os.path.relpath 一致）
import ast
import json
import os
import time
from dataclasses import dataclass, field

from nucleus.logger import get_module_logger


_logger = get_module_logger("CodeAnalyzer")


@dataclass
class FunctionInfo:
    """函数信息"""
    name: str
    line: int
    end_line: int
    args: list = field(default_factory=list)
    docstring: str = ""
    complexity: int = 0  # 圈复杂度
    is_async: bool = False
    is_method: bool = False
    decorators: list = field(default_factory=list)


@dataclass
class ClassInfo:
    """类信息"""
    name: str
    line: int
    end_line: int
    methods: list = field(default_factory=list)  # FunctionInfo列表
    bases: list = field(default_factory=list)
    docstring: str = ""
    decorators: list = field(default_factory=list)


@dataclass
class ImportInfo:
    """导入信息"""
    module: str
    name: str = ""  # from module import name
    alias: str = ""
    line: int = 0


@dataclass
class FileAnalysis:
    """文件分析结果"""
    file_path: str
    total_lines: int = 0
    code_lines: int = 0  # 非空非注释行
    comment_lines: int = 0
    blank_lines: int = 0
    functions: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    total_complexity: int = 0
    avg_function_length: float = 0.0
    has_syntax_error: bool = False
    syntax_error: str = ""
    analyzed_at: float = 0.0


class CodeAnalyzer:
    """代码文件分析引擎"""

    def __init__(self, project_root: str | None = None):
        """
        初始化代码分析器

        Args:
            project_root: 项目根目录
        """
        if project_root is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.project_root = project_root
        self._cache: dict = {}  # file_path -> (mtime, FileAnalysis)
        _logger.info("代码分析器初始化")

    def _calculate_complexity(self, node: ast.AST) -> int:
        """计算圈复杂度（简化版）"""
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.And, ast.Or,
                                  ast.ExceptHandler, ast.With, ast.Assert)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity

    def _get_docstring(self, node: ast.AST) -> str:
        """获取文档字符串"""
        return ast.get_docstring(node) or ""

    def _analyze_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef,
                          is_method: bool = False) -> FunctionInfo:
        """分析函数定义"""
        args = []
        for arg in node.args.args:
            args.append(arg.arg)

        return FunctionInfo(
            name=node.name,
            line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            args=args,
            docstring=self._get_docstring(node),
            complexity=self._calculate_complexity(node),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_method=is_method,
            decorators=[ast.unparse(d) for d in node.decorator_list],
        )

    def _analyze_class(self, node: ast.ClassDef) -> ClassInfo:
        """分析类定义"""
        methods = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(self._analyze_function(item, is_method=True))

        bases = [ast.unparse(b) for b in node.bases]
        return ClassInfo(
            name=node.name,
            line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            methods=methods,
            bases=bases,
            docstring=self._get_docstring(node),
            decorators=[ast.unparse(d) for d in node.decorator_list],
        )

    def analyze_file(self, file_path: str, use_cache: bool = True) -> FileAnalysis:
        """
        分析单个Python文件

        Args:
            file_path: 文件路径（相对或绝对）
            use_cache: 是否使用缓存

        Returns:
            文件分析结果
        """
        abs_path = file_path if os.path.isabs(file_path) else os.path.join(self.project_root, file_path)

        # 缓存检查
        if use_cache and abs_path in self._cache:
            cached_mtime, cached_result = self._cache[abs_path]
            try:
                current_mtime = os.path.getmtime(abs_path)
                if current_mtime == cached_mtime:
                    return cached_result
            except OSError:
                pass

        result = FileAnalysis(file_path=_safe_relpath(abs_path, self.project_root))
        result.analyzed_at = time.time()

        try:
            with open(abs_path, encoding="utf-8") as f:
                source = f.read()
        except (OSError, UnicodeDecodeError) as e:
            result.has_syntax_error = True
            result.syntax_error = str(e)
            return result

        # 统计行数
        lines = source.split("\n")
        result.total_lines = len(lines)
        for line in lines:
            stripped = line.strip()
            if not stripped:
                result.blank_lines += 1
            elif stripped.startswith("#"):
                result.comment_lines += 1
            else:
                result.code_lines += 1

        # AST解析
        try:
            tree = ast.parse(source, filename=abs_path)
        except SyntaxError as e:
            result.has_syntax_error = True
            result.syntax_error = str(e)
            return result

        # 提取函数、类、导入
        # 先收集类中的方法，避免重复统计
        class_methods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                result.classes.append(self._analyze_class(node))
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        class_methods.add(id(item))

        # 只统计顶层函数（不在类中的函数）
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if id(node) not in class_methods:
                    result.functions.append(self._analyze_function(node))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    result.imports.append(ImportInfo(
                        module=alias.name, alias=alias.asname or "", line=node.lineno
                    ))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    result.imports.append(ImportInfo(
                        module=module, name=alias.name,
                        alias=alias.asname or "", line=node.lineno
                    ))

        # 计算总复杂度
        all_funcs = list(result.functions)
        for cls in result.classes:
            all_funcs.extend(cls.methods)
        result.total_complexity = sum(f.complexity for f in all_funcs)

        # 平均函数长度
        if all_funcs:
            total_length = sum(f.end_line - f.line + 1 for f in all_funcs)
            result.avg_function_length = total_length / len(all_funcs)

        # 更新缓存
        try:
            self._cache[abs_path] = (os.path.getmtime(abs_path), result)
        except OSError:
            pass

        return result

    def analyze_directory(self, dir_path: str, pattern: str = "*.py",
                          recursive: bool = True, max_files: int = 100) -> list:
        """
        分析目录下的所有Python文件

        Args:
            dir_path: 目录路径
            pattern: 文件匹配模式
            recursive: 是否递归
            max_files: 最大文件数

        Returns:
            文件分析结果列表
        """
        abs_dir = dir_path if os.path.isabs(dir_path) else os.path.join(self.project_root, dir_path)
        results = []

        if recursive:
            for root, dirs, files in os.walk(abs_dir):
                if "__pycache__" in root or ".git" in root:
                    continue
                for f in files:
                    if f.endswith(".py"):
                        if len(results) >= max_files:
                            break
                        results.append(self.analyze_file(os.path.join(root, f)))
        else:
            for f in os.listdir(abs_dir):
                if f.endswith(".py"):
                    if len(results) >= max_files:
                        break
                    results.append(self.analyze_file(os.path.join(abs_dir, f)))

        return results

    def find_function(self, file_path: str, function_name: str) -> FunctionInfo | None:
        """在文件中查找指定函数"""
        analysis = self.analyze_file(file_path)
        for func in analysis.functions:
            if func.name == function_name:
                return func
        for cls in analysis.classes:
            for method in cls.methods:
                if method.name == function_name:
                    return method
        return None

    def get_complex_functions(self, file_path: str, threshold: int = 10) -> list:
        """获取复杂度超过阈值的函数"""
        analysis = self.analyze_file(file_path)
        complex_funcs = []
        for func in analysis.functions:
            if func.complexity >= threshold:
                complex_funcs.append(func)
        for cls in analysis.classes:
            for method in cls.methods:
                if method.complexity >= threshold:
                    complex_funcs.append(method)
        return complex_funcs

    def get_file_summary(self, file_path: str) -> dict:
        """获取文件摘要信息"""
        analysis = self.analyze_file(file_path)
        return {
            "file": analysis.file_path,
            "total_lines": analysis.total_lines,
            "code_lines": analysis.code_lines,
            "functions": len(analysis.functions),
            "classes": len(analysis.classes),
            "methods": sum(len(c.methods) for c in analysis.classes),
            "total_complexity": analysis.total_complexity,
            "avg_function_length": round(analysis.avg_function_length, 1),
            "has_syntax_error": analysis.has_syntax_error,
        }

    def get_project_summary(self, max_files: int = 200) -> dict:
        """获取项目整体摘要"""
        results = self.analyze_directory(".", max_files=max_files)
        total_lines = sum(r.total_lines for r in results)
        total_code = sum(r.code_lines for r in results)
        total_funcs = sum(len(r.functions) for r in results)
        total_classes = sum(len(r.classes) for r in results)
        total_complexity = sum(r.total_complexity for r in results)
        syntax_errors = sum(1 for r in results if r.has_syntax_error)

        return {
            "total_files": len(results),
            "total_lines": total_lines,
            "code_lines": total_code,
            "total_functions": total_funcs,
            "total_classes": total_classes,
            "total_complexity": total_complexity,
            "avg_complexity_per_file": round(total_complexity / max(len(results), 1), 1),
            "syntax_errors": syntax_errors,
            "analyzed_at": time.time(),
        }

    def export_to_json(self, analysis: FileAnalysis, output_path: str | None = None) -> str:
        """导出分析结果为JSON"""
        data = {
            "file_path": analysis.file_path,
            "total_lines": analysis.total_lines,
            "code_lines": analysis.code_lines,
            "functions": [{"name": f.name, "line": f.line, "complexity": f.complexity,
                           "args": f.args} for f in analysis.functions],
            "classes": [{"name": c.name, "line": c.line,
                         "methods": [m.name for m in c.methods]} for c in analysis.classes],
            "imports": [{"module": i.module, "name": i.name} for i in analysis.imports],
            "total_complexity": analysis.total_complexity,
        }
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(json_str)
        return json_str


# 单例实例
_analyzer = None

def get_code_analyzer() -> CodeAnalyzer:
    """获取代码分析器单例"""
    global _analyzer
    if _analyzer is None:
        _analyzer = CodeAnalyzer()
    return _analyzer
