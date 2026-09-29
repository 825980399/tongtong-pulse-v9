# -*- coding: utf-8 -*-
"""
organ_assembler.py —— 器官装配器

版本: v10 PulseNet
设计: 路灯、小林、星轨
日期: 2026年9月11日

职责: 器官实例化与依赖注入装配
机制: 基于OrganAssembler类实现，包含10个核心方法
定位: 器官管理层
"""

from __future__ import annotations

import importlib
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any


# 框架基础组件符号名（非器官，装配时由框架直接提供，不参与拓扑排序）。
# 这些是「被注入的公共服务」，不属于 organs/ 扫描的器官注册表。
FRAMEWORK_COMPONENTS: frozenset[str] = frozenset({
    "node_pool", "knowledge_tree", "frequency_codec", "resonance_engine",
    "snapshot", "info_field", "pulse_core", "experience_pool",
    "self_inspector", "evolution_sandbox", "insight_board", "stream_miner",
    "sensor", "expression", "context_snapshot", "companion_bridge",
    "autonomous_deriver", "oscillon_monitor", "reasoning_pool",
    # QICA 虽以 _create_organ 创建并存于 organs，但位于 nucleus/qica 而非 organs/，
    # 不在 ORGAN_META 扫描范围，按「组件」对待。
    "qica",
    # 框架实例自身（set_framework_ref 注入），非器官也非可装配组件。
    "framework",
    # 三级安全沙箱核心引擎（main.py 内联创建，非器官）。
    "sandbox_core",
})


class OrganAssembler:
    """声明式器官装配器。

    阶段2 用法（只校验不装配）:
        assembler = OrganAssembler(metas, components=FRAMEWORK_COMPONENTS)
        report = assembler.validate()
    """

    def __init__(self, metas: list[Any], components: set[str] | frozenset[str] | None = None):
        """
        Args:
            metas: OrganMeta 列表（来自 OrganLoader.scan_organs_directory()）
            components: 已知框架组件符号名集合（非器官）。缺省使用 FRAMEWORK_COMPONENTS。
        """
        self.metas = list(metas)
        self.components: set[str] = set(components) if components is not None else set(FRAMEWORK_COMPONENTS)
        self._organ_names: set[str] = {m.name for m in self.metas}

    # ========================================================================
    # 依赖图构建
    # ========================================================================

    def _resolve_dep_kind(self, dep_name: str) -> str:
        """判断依赖符号类型：'organ'（器官）/'component'（组件）/'unknown'（未声明）。"""
        if dep_name in self._organ_names:
            return "organ"
        if dep_name in self.components:
            return "component"
        return "unknown"

    def build_dependency_graph(self) -> dict[str, Any]:
        """
        从 ORGAN_META 解析依赖图。

        Returns:
            {
                "hard_edges": {器官名: set(依赖的器官名)},   # extra_deps 中的器官间硬依赖
                "soft_wiring": {器官名: [{target, setter}]}, # post_wiring 软依赖
                "missing_deps": [(器官名, 未知符号)],        # extra_deps 引用未知符号
                "missing_targets": [(器官名, 未知符号)],     # post_wiring 引用未知符号
            }
        """
        hard_edges: dict[str, set[str]] = {name: set() for name in self._organ_names}
        soft_wiring: dict[str, list[dict[str, str]]] = {}
        missing_deps: list[tuple[str, str]] = []
        missing_targets: list[tuple[str, str]] = []

        for m in self.metas:
            # 1. 硬依赖（extra_deps）
            for dep_name in (m.extra_deps or {}).values():
                kind = self._resolve_dep_kind(dep_name)
                if kind == "organ":
                    hard_edges[m.name].add(dep_name)
                elif kind == "unknown":
                    missing_deps.append((m.name, dep_name))

            # 2. 软依赖（post_wiring）
            for wiring in (m.post_wiring or []):
                target = wiring.get("target", "")
                if not target:
                    continue
                soft_wiring.setdefault(m.name, []).append(wiring)
                kind = self._resolve_dep_kind(target)
                if kind == "unknown":
                    missing_targets.append((m.name, target))

        return {
            "hard_edges": hard_edges,
            "soft_wiring": soft_wiring,
            "missing_deps": missing_deps,
            "missing_targets": missing_targets,
        }

    # ========================================================================
    # 拓扑排序 + 循环检测（Kahn 算法）
    # ========================================================================

    def _detect_cycles(self, hard_edges: dict[str, set[str]]) -> list[list[str]]:
        """
        用 Kahn 算法检测 extra_deps 硬依赖图中的循环。

        Returns:
            环列表；每个环是「参与环的器官名」列表（去重排序）。
        """
        # 入度 = 硬依赖数量（依赖必须先于自身创建）
        indegree: dict[str, int] = {name: len(hard_edges[name]) for name in hard_edges}
        # 反向邻接表：依赖 -> 依赖它的器官
        dependents: dict[str, list[str]] = defaultdict(list)
        for name, deps in hard_edges.items():
            for dep in deps:
                dependents[dep].append(name)

        queue = deque([name for name, deg in indegree.items() if deg == 0])
        visited: set[str] = set()

        while queue:
            node = queue.popleft()
            visited.add(node)
            for dependent in dependents[node]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)

        # 拓扑排序后仍有入度 > 0 的节点 → 参与循环
        remaining = [name for name, deg in indegree.items() if deg > 0]
        return self._extract_cycles(remaining, dependents)

    @staticmethod
    def _extract_cycles(nodes: list[str], dependents: dict[str, list[str]]) -> list[list[str]]:
        """从「仍在环上」的节点集合中提取各个连通环。"""
        node_set = set(nodes)
        cycles: list[list[str]] = []
        visited: set[str] = set()

        for start in sorted(node_set):
            if start in visited:
                continue
            # BFS 收集与 start 连通的剩余节点（它们在同一个环或相邻环）
            stack = [start]
            component: list[str] = []
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                component.append(node)
                for dep in dependents[node]:
                    if dep in node_set and dep not in visited:
                        stack.append(dep)
            cycles.append(sorted(component))

        return cycles

    # ========================================================================
    # 校验入口
    # ========================================================================

    def validate(self) -> dict[str, Any]:
        """
        校验 ORGAN_META 依赖声明的完整性与合法性（只读，不装配）。

        检测项:
            1. 未声明的硬依赖（extra_deps 引用既非器官也非已知组件）
            2. 未声明的循环依赖（器官间 extra_deps 成环，应改为 post_wiring）
            3. post_wiring 的 target 引用未知符号
            4. 声明完整性统计

        Returns:
            校验报告 dict，包含 ok/errors/warnings/stats。
        """
        graph = self.build_dependency_graph()
        hard_edges: dict[str, set[str]] = graph["hard_edges"]
        missing_deps: list[tuple[str, str]] = graph["missing_deps"]
        missing_targets: list[tuple[str, str]] = graph["missing_targets"]

        cycles = self._detect_cycles(hard_edges)

        # 统计
        hard_organ_edges = sum(len(deps) for deps in hard_edges.values())
        soft_wiring_count = sum(len(w) for w in graph["soft_wiring"].values())
        declared_extra_deps = sum(len(m.extra_deps or {}) for m in self.metas)

        errors: list[str] = []
        warnings: list[str] = []

        if missing_deps:
            errors.append(f"未声明的硬依赖 {len(missing_deps)} 处: "
                          f"{[(o, d) for o, d in missing_deps]}")
        if cycles:
            errors.append(f"硬依赖成环 {len(cycles)} 处（应改为 post_wiring）: {cycles}")
        if missing_targets:
            warnings.append(f"post_wiring 引用未知符号 {len(missing_targets)} 处: "
                            f"{[(o, t) for o, t in missing_targets]}")

        # ★always_online 语义一致性校验（技术债治理）：
        # always_online=True 表示「生命线核心器官，不可被 feature_flag 关闭」，
        # 故核心器官不应同时声明 feature_flag（否则「核心不可关」与「可开关降级」矛盾）。
        # always_online=False 表示「可降级器官」，应带 feature_flag 承载开关语义。
        ao_flag_conflict = [
            m.name for m in self.metas
            if m.always_online and m.feature_flag
        ]
        ao_noflag_conflict = [
            m.name for m in self.metas
            if not m.always_online and not m.feature_flag
        ]
        if ao_flag_conflict:
            errors.append(
                f"always_online=True 却声明 feature_flag 的矛盾组合 {len(ao_flag_conflict)} 处"
                f"（核心器官不可被开关降级，应移除其一）: {ao_flag_conflict}")
        if ao_noflag_conflict:
            warnings.append(
                f"always_online=False 却未声明 feature_flag 的冗余标注 {len(ao_noflag_conflict)} 处"
                f"（可降级器官建议补 feature_flag）: {ao_noflag_conflict}")

        # setter 命名规约检查（软依赖 setter 应以 set_ 开头）
        bad_setters = []
        for organ, wirings in graph["soft_wiring"].items():
            for w in wirings:
                setter = w.get("setter", "")
                if not setter.startswith("set_"):
                    bad_setters.append((organ, setter))
        if bad_setters:
            warnings.append(f"post_wiring setter 命名不规范 {len(bad_setters)} 处: {bad_setters}")

        return {
            "ok": not errors,
            "organ_count": len(self.metas),
            "errors": errors,
            "warnings": warnings,
            "stats": {
                "declared_extra_deps": declared_extra_deps,
                "hard_organ_edges": hard_organ_edges,
                "soft_wiring_count": soft_wiring_count,
                "cycle_count": len(cycles),
                "missing_dep_count": len(missing_deps),
                "missing_target_count": len(missing_targets),
                "always_online_count": sum(1 for m in self.metas if m.always_online),
                "degradable_count": sum(1 for m in self.metas if not m.always_online),
            },
            "cycles": cycles,
        }

    # ========================================================================
    # 拓扑排序（供 assemble 使用）
    # ========================================================================

    def _topological_order(self, hard_edges: dict[str, set[str]]) -> list[str]:
        """
        Kahn 拓扑排序，返回「依赖在前、依赖方在后」的创建顺序。

        若存在环（理论上 validate() 已拦下），返回按剩余节点拼接的顺序，
        并让 assemble() 在遇到未就绪依赖时降级为 post_wiring 晚绑定。
        """
        indegree: dict[str, int] = {name: len(hard_edges[name]) for name in hard_edges}
        dependents: dict[str, list[str]] = defaultdict(list)
        for name, deps in hard_edges.items():
            for dep in deps:
                dependents[dep].append(name)

        queue = deque([name for name, deg in indegree.items() if deg == 0])
        order: list[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for dependent in dependents[node]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    queue.append(dependent)

        # 环内剩余节点（理论上不存在）追加到末尾，靠 post_wiring 兜底
        remaining = [name for name, deg in indegree.items() if deg > 0]
        order.extend(sorted(remaining))
        return order

    # ========================================================================
    # 声明式装配（阶段3）
    # ========================================================================

    def assemble(
        self,
        framework: Any,
        component_resolver: Callable[[str], Any] | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        声明式装配器官（阶段3：替换 main.py 的硬编码 _create_organ）。

        装配流程（四阶段）:
            Phase 1  拓扑排序 → 创建无环硬依赖的器官（含 extra_deps 注入）
            Phase 2  post_wiring 晚绑定 → 回填前向引用/环依赖
            Phase 3  FEATURE 降级 → 开关关闭的器官置 None，下游容忍 None

        Args:
            framework: 框架实例，需提供:
                - framework._create_organ(organ_class, name, **extra_deps)
                - framework.config.FEATURE（dict，可为空）
            component_resolver: 非器官组件解析器（callable 或 dict），
                name -> instance | None。用于解析 extra_deps/post_wiring 中的组件符号。

        Returns:
            {器官中文名: 器官实例或 None}
        """
        feature = getattr(getattr(framework, "config", None), "FEATURE", {}) or {}

        def _resolve(name: str) -> Any:
            """解析符号名：器官实例优先，否则走组件解析器。"""
            if name in created:
                return created[name]
            if component_resolver is None:
                return None
            if callable(component_resolver):
                return component_resolver(name)
            return component_resolver.get(name)

        meta_by_name = {m.name: m for m in self.metas}
        graph = self.build_dependency_graph()
        hard_edges: dict[str, set[str]] = graph["hard_edges"]
        soft_wiring: dict[str, list[dict[str, str]]] = graph["soft_wiring"]

        created: dict[str, Any] = {}

        # ===== Phase 1: 拓扑排序 + 创建器官 =====
        order = self._topological_order(hard_edges)
        for name in order:
            meta = meta_by_name[name]
            # FEATURE 降级：开关关闭 → 不创建，置 None。
            # ★always_online 语义（技术债治理）：always_online=True 表示生命线核心器官，
            # 不可被 feature_flag 关闭。故降级判断需排除核心器官——
            # 即使核心器官误声明了 feature_flag（validate 会报错），也强制创建。
            # always_online=False 表示可降级器官，正常走 feature_flag 降级。
            if (not meta.always_online
                    and meta.feature_flag
                    and not feature.get(meta.feature_flag, True)):
                created[name] = None
                self._set_attr(framework, meta, None)
                continue

            # 解析硬依赖（extra_deps）
            extra_deps: dict[str, Any] = {}
            for attr_key, dep_name in (meta.extra_deps or {}).items():
                resolved = _resolve(dep_name)
                if resolved is not None:
                    extra_deps[attr_key] = resolved

            # 动态导入类并创建
            try:
                module = importlib.import_module(meta.module_path)
                organ_class = getattr(module, meta.class_name)
                organ = framework.create_organ(organ_class, meta.name, **extra_deps)
            except Exception as e:
                framework.log("ERROR", f"声明式装配失败 {meta.name}: {e}")
                organ = None

            created[name] = organ
            self._set_attr(framework, meta, organ)

        # ===== Phase 2: post_wiring 晚绑定 =====
        for name in [m.name for m in self.metas]:
            organ = created.get(name)
            if organ is None:
                continue
            for wiring in soft_wiring.get(name, []):
                target_name = wiring.get("target", "")
                setter = wiring.get("setter", "")
                if not target_name or not setter:
                    continue
                target = _resolve(target_name)
                if target is None:
                    continue  # 依赖尚未就绪（如 FEATURE 关闭），容忍 None
                setter_fn = getattr(organ, setter, None)
                if setter_fn:
                    try:
                        setter_fn(target)
                    except Exception as e:
                        framework.log("WARNING", f"post_wiring 失败 {name}.{setter}: {e}")

        return created

    @staticmethod
    def _set_attr(framework: Any, meta: Any, organ: Any) -> None:
        """把器官实例写回框架英文属性名（self.heart / self.cortex 等）。"""
        if meta.attr_name:
            try:
                setattr(framework, meta.attr_name, organ)
            except Exception:
                pass
