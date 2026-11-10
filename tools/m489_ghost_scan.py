#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m489_ghost_scan.py — 静态幽灵订阅扫描（489-2）。

逻辑：对 organs/ 下每个器官类，取其 `get_resonance_conditions()` 声明的订阅事件
（订阅面），再用 AST 解析其 `on_pulse` / `_handle_pulse_safe` 里真正处理该事件的
分支（处理面）。幽灵订阅 = 订阅了某事件，但 on_pulse 对该事件没有任何处理分支，
且非通配订阅（event_types 含 "*" 或为空 → 排除，按规则不判幽灵）。

输出：data/reports/ghost_subscriptions.json（含 ghosts / wildcard_subscribers /
subscribed_digest_knowledge 复核段，以及三器官已知幽灵的复核）。

不依赖活体：纯 AST + const.py 静态解析，离线可跑。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nucleus._silent_except import silent_exc
from nucleus.const import DigestEvent

_DK = DigestEvent.KNOWLEDGE  # 事件字符串须经 const.py 常量引用

REPO_ROOT = Path(__file__).resolve().parent.parent
ORGANS_DIR = REPO_ROOT / "organs"
CONST_PATH = REPO_ROOT / "nucleus" / "const.py"
OUT_PATH = REPO_ROOT / "data" / "reports" / "ghost_subscriptions.json"

# 任务书点名的「已知 3 幽灵」器官（用于复核段高亮）
KNOWN_GHOST_ORGANS = {"PulseLegs", "PulseBloodVessel", "PulseMetricsCollector"}


# ---------------------------------------------------------------------------
# const 值解析（解析 const.py，避免 import 副作用）
# ---------------------------------------------------------------------------
def _literal_value(node):
    try:
        return ast.literal_eval(node)
    except Exception as _e:
        silent_exc(_e, where="m489_ghost_scan::_literal_value", level="debug")
        return None


def load_const_values() -> dict[str, str]:
    """解析 const.py 的字符串常量。

    关键点：类体内的常量（如 class DigestEvent: KNOWLEDGE 映射到 digest 类事件字符串）
    在 AST 里是 target 为 Name 的赋值，必须按类名加前缀才能得到
    "DigestEvent.KNOWLEDGE" 这种可被 resolve_const_ref 查到的 key。
    同时兼容 AnnAssign（注解赋值）与模块级 Attribute 赋值。
    """
    values: dict[str, str] = {}
    if not CONST_PATH.exists():
        return values
    try:
        tree = ast.parse(CONST_PATH.read_text(encoding="utf-8"))
    except Exception as _e:
        silent_exc(_e, where="m489_ghost_scan::load_const_values", level="debug")
        return values

    def _add(targets, value, prefix: str = ""):
        val = _literal_value(value)
        if not isinstance(val, str):
            return
        for tgt in targets:
            if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name):
                values[f"{tgt.value.id}.{tgt.attr}"] = val
            elif isinstance(tgt, ast.Name):
                key = f"{prefix}.{tgt.id}" if prefix else tgt.id
                values[key] = val

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            _add(node.targets, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            _add([node.target], node.value)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.Assign):
                    _add(item.targets, item.value, prefix=node.name)
                elif isinstance(item, ast.AnnAssign) and item.value is not None:
                    _add([item.target], item.value, prefix=node.name)
    return values


def resolve_const_ref(node, const_values: dict[str, str]):
    """把 DigestEvent.KNOWLEDGE 这类属性引用解析成字符串值。"""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    parts.reverse()
    full = ".".join(parts)
    if full in const_values:
        return const_values[full]
    if len(parts) >= 2:
        short = ".".join(parts[-2:])
        if short in const_values:
            return const_values[short]
    return None


def node_to_event_ids(node, const_values: dict[str, str]) -> list[str]:
    """把一个表达式节点解析成一个或多个事件 id（字符串字面量或 const 值）。"""
    out: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append(node.value)
    elif isinstance(node, ast.Attribute):
        v = resolve_const_ref(node, const_values)
        if v is not None:
            out.append(v)
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for el in node.elts:
            out.extend(node_to_event_ids(el, const_values))
    return out


# ---------------------------------------------------------------------------
# 订阅面提取
# ---------------------------------------------------------------------------
def _dict_literal(node):
    """解析 {'event_types': [...], 'organ_name': '...'} 这类字典字面量。"""
    if not isinstance(node, ast.Dict):
        return None
    d: dict[str, object] = {}
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            d[k.value] = v
    return d


def extract_subscriptions(func_node) -> list[dict]:
    """从 get_resonance_conditions 的 return 列表里提取订阅。"""
    subs: list[dict] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return) and isinstance(node.value, (ast.List, ast.Tuple)):
            for el in node.value.elts:
                d = _dict_literal(el)
                if d is None:
                    continue
                et_node = d.get("event_types")
                organ = d.get("organ_name")
                organ_name = None
                if isinstance(organ, ast.Constant) and isinstance(organ.value, str):
                    organ_name = organ.value
                if et_node is None:
                    # 无 event_types → 视为通配
                    subs.append({"organ_name": organ_name, "event_types": ["*"],
                                 "wildcard": True})
                    continue
                if isinstance(et_node, ast.Constant) and et_node.value == "*":
                    subs.append({"organ_name": organ_name, "event_types": ["*"],
                                 "wildcard": True})
                    continue
                ets = node_to_event_ids(et_node, load_const_values())
                if not ets or "*" in ets:
                    # 解析不出具体事件（如动态表达式）或含通配"*" → 保守按通配排除
                    subs.append({"organ_name": organ_name,
                                 "event_types": (ets if ets else ["*"]),
                                 "wildcard": True})
                    continue
                subs.append({"organ_name": organ_name, "event_types": ets,
                             "wildcard": False})
    return subs


# ---------------------------------------------------------------------------
# 处理面提取（AST）
# ---------------------------------------------------------------------------
class _HandlerScan(ast.NodeVisitor):
    def __init__(self, const_values: dict[str, str]):
        self.const_values = const_values
        self.handled: set[str] = set()
        self.prefixes: set[str] = set()
        self.dynamic = False

    def visit_Compare(self, node):
        for op, other in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.Is)):
                ids = node_to_event_ids(node.left, self.const_values)
                if not ids:
                    ids = node_to_event_ids(other, self.const_values)
                self.handled.update(ids)
            elif isinstance(op, (ast.In, ast.NotIn)):
                # event_type in (...)/[...]/set → 解析元素
                ids = node_to_event_ids(other, self.const_values)
                if ids:
                    self.handled.update(ids)
                else:
                    # event_type in some_var（集合/字典）→ 视为动态分发
                    self.dynamic = True
        self.generic_visit(node)

    def visit_Call(self, node):
        # event_type.startswith("prefix.") → 前缀匹配
        if isinstance(node.func, ast.Attribute) and node.func.attr == "startswith":
            if node.args:
                pre = _literal_value(node.args[0])
                if isinstance(pre, str):
                    self.prefixes.add(pre)
        # dict[event_type] / handlers[event_type]() → 动态分发
        if isinstance(node.func, ast.Subscript):
            self.dynamic = True
        self.generic_visit(node)


def extract_handled(func_node, const_values: dict[str, str]):
    scanner = _HandlerScan(const_values)
    scanner.visit(func_node)
    return scanner


# ---------------------------------------------------------------------------
# 器官类发现 + 主流程
# ---------------------------------------------------------------------------
def iter_organ_classes():
    if not ORGANS_DIR.exists():
        return
    for path in sorted(ORGANS_DIR.rglob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception as _e:
            silent_exc(_e, where="m489_ghost_scan::iter_organ_classes", level="debug")
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if "get_resonance_conditions" in methods or "on_pulse" in methods:
                yield node, path


def scan_all():
    const_values = load_const_values()
    const_values_for_use = const_values  # 闭包用
    organs = []
    ghosts = []
    wildcard_subs = []
    digest_review = []

    for cls, path in iter_organ_classes():
        rel = str(path.relative_to(REPO_ROOT))
        # 订阅面
        subs = []
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and item.name == "get_resonance_conditions":
                subs = extract_subscriptions(item)
                break
        # 处理面
        handled = set()
        prefixes = set()
        dynamic = False
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and item.name in ("on_pulse", "_handle_pulse_safe"):
                sc = extract_handled(item, const_values_for_use)
                handled |= sc.handled
                prefixes |= sc.prefixes
                dynamic = dynamic or sc.dynamic

        subscribed_all: list[str] = []
        for s in subs:
            for et in s["event_types"]:
                if s.get("wildcard"):
                    wildcard_subs.append({
                        "organ": cls.name, "file": rel,
                        "event_types": s["event_types"],
                    })
                else:
                    subscribed_all.append(et)

        # 严格幽灵：订阅(非通配) - 处理面 - 前缀匹配
        organ_ghosts: list[str] = []
        for et in set(subscribed_all):
            handled_flag = et in handled
            if not handled_flag:
                for p in prefixes:
                    if et.startswith(p):
                        handled_flag = True
                        break
            if dynamic:
                handled_flag = True  # 动态分发保守视为已处理
            if not handled_flag:
                organ_ghosts.append(et)
                ghosts.append({
                    "organ": cls.name, "file": rel, "event": et,
                    "reason": "subscribed_but_no_on_pulse_branch",
                })

        # DK 复核段（含已知 3 幽灵器官）
        subs_dk = [et for et in subscribed_all
                   if et == const_values.get("DigestEvent.KNOWLEDGE")]
        if cls.name in KNOWN_GHOST_ORGANS or subs_dk:
            digest_review.append({
                "organ": cls.name, "file": rel,
                "subscribes_digest_knowledge": bool(subs_dk),
                "has_branch": (_DK in handled) or dynamic,
                "note": ("wildcard_excluded" if any(s.get("wildcard") for s in subs)
                         else ("handled_branch" if _DK in handled or dynamic
                               else "no_branch_GHOST")),
            })

        organs.append({
            "organ": cls.name, "file": rel,
            "subscribed": sorted(set(subscribed_all)),
            "wildcard": any(s.get("wildcard") for s in subs),
            "handled": sorted(handled),
            "prefixes": sorted(prefixes),
            "dynamic_dispatch": dynamic,
            "ghosts": sorted(organ_ghosts),
        })

    return {
        "ghosts": ghosts,
        "wildcard_subscribers": wildcard_subs,
        "subscribed_digest_knowledge": digest_review,
        "organs": organs,
    }


def main():
    result = scan_all()
    result["generated_at"] = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
    result["scanned_organs"] = len(result["organs"])
    result["summary"] = {
        "ghost_count": len(result["ghosts"]),
        "wildcard_subscriber_count": len(result["wildcard_subscribers"]),
        "known_3_review": [d for d in result["subscribed_digest_knowledge"]
                            if d["organ"] in KNOWN_GHOST_ORGANS],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        from nucleus.data.write_guard import guard_write
        _allowed = guard_write(str(OUT_PATH), explicit=False)
    except Exception as _e:
        silent_exc(_e, where="m489_ghost_scan::main[guard]", level="debug")
        _allowed = True
    if not _allowed:
        alt = REPO_ROOT / "tmp" / "ghost_subscriptions.json"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"[m489] write_guard 拦截，落到 {alt}")
    else:
        OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"[m489] 已写出 {OUT_PATH}")

    print(f"[m489] 扫描器官 {result['scanned_organs']} 个，"
          f"严格幽灵 {result['summary']['ghost_count']} 条，"
          f"通配订阅者 {result['summary']['wildcard_subscriber_count']} 个")
    print("[m489] 已知 3 幽灵器官复核：")
    for d in result["summary"]["known_3_review"]:
        print(f"  - {d['organ']:24s} 订阅DK={d['subscribes_digest_knowledge']} "
              f"有分支={d['has_branch']} 结论={d['note']}")
    return result


if __name__ == "__main__":
    main()
