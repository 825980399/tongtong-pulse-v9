# -*- coding: utf-8 -*-
"""
code_analysis_layers.py —— 代码分析层级

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 分层代码结构解析，支持深度/浅层分析模式
机制: 基于CodeAnalysisLayers类实现，包含10个核心方法
定位: 代码学习支撑层
"""

import ast
import os
from typing import Any


class CodeAnalysisLayers:
    """四层串联代码分析引擎"""

    def __init__(self):
        self._ast_cache: dict[str, tuple[float, ast.Module]] = {}
        self._source_cache: dict[str, str] = {}

    def analyze(self, file_path: str, node_pool=None) -> dict[str, Any]:
        """
        对指定Python文件执行四层串联分析。
        
        Args:
            file_path: .py文件绝对路径
            node_pool: 可选，用于第四层知识库交叉验证
        
        Returns:
            {
                "layer1_ast_structure": {...},
                "layer2_call_dataflow": {...},
                "layer3_semantic_hints": [...],
                "layer4_verification_hints": [...],
            }
        """
        if not os.path.exists(file_path):
            return {"error": f"文件不存在: {file_path}"}

        source, tree = self._get_ast(file_path)

        layer1 = self._layer1_ast_structure(tree, source, file_path)
        layer2 = self._layer2_call_dataflow(tree, layer1)
        layer3 = self._layer3_semantic_hints(layer1, layer2)
        layer4 = self._layer4_verification(layer1, layer2, layer3, node_pool)

        return {
            "file_path": file_path,
            "layer1_ast_structure": layer1,
            "layer2_call_dataflow": layer2,
            "layer3_semantic_hints": layer3,
            "layer4_verification_hints": layer4,
        }

    # ========== 缓存 ==========

    def _get_ast(self, file_path: str) -> tuple[str, ast.Module]:
        mtime = os.path.getmtime(file_path)
        if file_path in self._ast_cache:
            cached_mtime, cached_tree = self._ast_cache[file_path]
            if cached_mtime == mtime:
                return self._source_cache[file_path], cached_tree

        with open(file_path, encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source)
        self._ast_cache[file_path] = (mtime, tree)
        self._source_cache[file_path] = source
        return source, tree

    # ========== 第一层：AST精确结构 ==========

    def _layer1_ast_structure(self, tree: ast.Module, source: str,
                              file_path: str) -> dict[str, Any]:
        """提取函数、类、导入的精确结构信息"""
        methods = []
        classes = []
        imports = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append(self._extract_method_info(node, source))
            elif isinstance(node, ast.ClassDef):
                classes.append(self._extract_class_info(node))
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(f"from {node.module} import ...")

        return {
            "methods": methods,
            "classes": classes,
            "imports": list(set(imports)),
            "method_count": len(methods),
            "class_count": len(classes),
        }

    def _extract_method_info(self, node: ast.FunctionDef, source: str) -> dict[str, Any]:
        """从AST函数节点提取完整方法信息"""
        args = []
        for arg in node.args.args:
            if arg.arg == 'self':
                continue
            arg_info = {"name": arg.arg}
            if arg.annotation is not None:
                arg_info["annotation"] = ast.unparse(arg.annotation)
            args.append(arg_info)

        # 装饰器
        decorators = [ast.unparse(d) for d in node.decorator_list]

        # docstring
        doc = ast.get_docstring(node) or ""

        # 参数默认值
        defaults = []
        for default in node.args.defaults:
            defaults.append(ast.unparse(default))

        return {
            "name": node.name,
            "args": args,
            "arg_names": [a["name"] for a in args],
            "defaults": defaults,
            "decorators": decorators,
            "doc": doc[:200],
            "line_number": node.lineno,
            "end_line": node.end_lineno,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "return_annotation": ast.unparse(node.returns) if node.returns else None,
        }

    def _extract_class_info(self, node: ast.ClassDef) -> dict[str, Any]:
        """从AST类节点提取类信息"""
        bases = [ast.unparse(b) for b in node.bases]
        doc = ast.get_docstring(node) or ""
        method_names = [
            n.name for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        return {
            "name": node.name,
            "bases": bases,
            "doc": doc[:150],
            "line_number": node.lineno,
            "method_names": method_names,
        }

    # ========== 第二层：调用链与数据流 ==========

    def _layer2_call_dataflow(self, tree: ast.Module, layer1: dict) -> dict[str, Any]:
        """提取方法调用图和实例属性读写关系"""
        call_graph: dict[str, list[str]] = {}
        data_deps: dict[str, dict[str, list[str]]] = {}

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_name = node.name
                calls = self._extract_calls(node)
                reads, writes = self._extract_attribute_access(node)

                call_graph[method_name] = calls
                data_deps[method_name] = {
                    "reads": list(reads),
                    "writes": list(writes),
                }

        return {
            "call_graph": call_graph,
            "data_dependencies": data_deps,
        }

    def _extract_calls(self, func_node: ast.FunctionDef) -> list[str]:
        """提取方法体内调用的方法名（基于AST Call节点）"""
        calls = set()
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    # self.xxx() 或 obj.xxx() —— 统一取裸方法名，避免 self. 前缀导致消费方失配
                    calls.add(node.func.attr)
                elif isinstance(node.func, ast.Name):
                    # 直接调用函数名
                    calls.add(node.func.id)
        return sorted(calls)

    def _extract_attribute_access(self, func_node: ast.FunctionDef):
        """提取实例属性的读写"""
        reads = set()
        writes = set()

        for node in ast.walk(func_node):
            if isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == 'self':
                    attr_name = node.attr
                    # 判断是读还是写
                    parent = getattr(node, 'parent', None)
                    if parent is None:
                        reads.add(attr_name)
                    elif isinstance(parent, ast.Assign) and parent.targets and node in parent.targets or isinstance(parent, ast.AugAssign) and parent.target == node:
                        writes.add(attr_name)
                    else:
                        reads.add(attr_name)

        return reads, writes

    # ========== 第三层：语义启发式推断 ==========

    def _layer3_semantic_hints(self, layer1: dict, layer2: dict) -> list[dict[str, Any]]:
        """基于命名、装饰器、调用关系推断方法职责"""
        hints = []
        methods = layer1.get("methods", [])
        for method in methods:
            name = method["name"]
            hints.append({
                "method": name,
                "hints": self._infer_semantic_hints(method, layer2),
            })
        return hints

    def _infer_semantic_hints(self, method: dict, layer2: dict) -> list[str]:
        """单个方法的语义推断"""
        name = method["name"]
        hints = []

        # 1. 脉冲入口检测
        if name.startswith("on_"):
            hints.append("脉冲入口")

        # 2. 生命周期方法
        if name in ("start", "stop", "boot", "shutdown"):
            hints.append("生命周期")

        # 3. 定时任务
        if any(kw in name for kw in ("_timer", "_interval", "_schedule", "_heartbeat")):
            hints.append("定时任务")

        # 4. 工具/辅助方法
        if name.startswith("_") and not name.startswith("__"):
            hints.append("内部辅助")

        # 5. 查询/获取
        if name.startswith(("get_", "query", "find", "has_")):
            hints.append("查询/获取")

        # 6. 设置/注入
        if name.startswith(("set_", "inject", "register")):
            hints.append("依赖注入/设置")

        # 7. 处理器
        if name.startswith(("_handle", "handle")):
            hints.append("事件处理")

        # 8. 发射脉冲检测
        if "emit" in name or "publish" in name:
            hints.append("发射脉冲")

        # 9. 清理/校验
        if any(kw in name for kw in ("_clean", "_check", "_validate", "_verify")):
            hints.append("校验/清理")

        # 10. 默认
        if not hints:
            hints.append("通用逻辑")

        return hints

    # ========== 第四层：知识库交叉验证 ==========

    def _layer4_verification(self, layer1: dict, layer2: dict, layer3: list,
                             node_pool=None) -> list[dict[str, Any]]:
        """
        将解析结果与已有知识节点对比。
        当前版本仅做简单提示，完整验证逻辑后续接入终身学习引擎Hub。
        """
        hints = []
        methods = layer1.get("methods", [])
        for method in methods:
            name = method["name"]
            doc = method.get("doc", "")
            suggestions = []

            if not doc:
                suggestions.append("无docstring，建议提交大模型深度分析")
            if len(method.get("arg_names", [])) > 5:
                suggestions.append("参数过多，建议重构")

            if suggestions:
                hints.append({
                    "method": name,
                    "suggestions": suggestions,
                })

        return hints