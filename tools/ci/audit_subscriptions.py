# -*- coding: utf-8 -*-
"""第107批 T-107b（D172）：订阅关系静态审计工具。

扫描全项目 `emit(...)` 与 `get_resonance_conditions()`，交叉比对找出：
  - 死订阅（订阅了但全项目无人发射，且非通配兜底）
  - 孤儿发射（发射了但无任何器官订阅）
  - 零发射器官（注册了共振条件但自身从不 emit）
输出订阅健康报告（JSON + 文本），供清理决策使用。

用法：python tools/ci/audit_subscriptions.py [--json out.json]
"""
from __future__ import annotations

import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXCLUDE_DIRS = {"tmp", "data", "logs", ".git", "__pycache__", "node_modules", ".venv", "venv", ".workbuddy", "backups"}
EXCLUDE_PREFIXES = (".bak",)


def _is_excluded(rel: str) -> bool:
    parts = rel.split(os.sep)
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if rel != "." and any(rel.startswith(p) for p in EXCLUDE_PREFIXES):
        return True
    return False


def build_event_map() -> dict[str, str]:
    """反射 nucleus.const 中所有事件类，得到 `Class.MEMBER` -> 字符串值 的映射。"""
    sys.path.insert(0, ROOT)
    import importlib
    mod = importlib.import_module("nucleus.const")
    m: dict[str, str] = {}
    for cls_name in dir(mod):
        cls = getattr(mod, cls_name)
        if not isinstance(cls, type):
            continue
        if cls is type(mod):
            continue
        for k, v in vars(cls).items():
            if k.isupper() and isinstance(v, str):
                m[f"{cls_name}.{k}"] = v
    return m


def resolve_event(node: ast.AST, event_map: dict[str, str]) -> str | None:
    """把一个 AST 节点解析成事件字符串。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute):
        # 形如 HeartEvent.BEAT
        if isinstance(node.value, ast.Name):
            key = f"{node.value.id}.{node.attr}"
            if key in event_map:
                return event_map[key]
            return f"<unresolved:{key}>"
        # 形如 something.HEART 之类，尽力而为
        return f"<attr:{ast.dump(node)[:40]}>"
    if isinstance(node, ast.Name):
        return f"<name:{node.id}>"
    return None


def _match(pattern: str, name: str) -> bool:
    """简化通配匹配（复用 EventBus 语义的子集）：'**' 通配全部；'a.*' 匹配单层。"""
    if pattern == name:
        return True
    if pattern == "**" or pattern == "*":
        return True
    ps = pattern.split(".")
    ns = name.split(".")
    if len(ps) != len(ns):
        # 仅当 pattern 末段为 * 或 ** 才跨层
        if ps[-1] in ("*", "**"):
            return ps[:-1] == ns[: len(ps) - 1]
        return False
    for a, b in zip(ps, ns):
        if a in ("*", "**"):
            continue
        if a != b:
            return False
    return True


def iter_py_files():
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in EXCLUDE_DIRS and not d.startswith(".bak")]
        rel = os.path.relpath(dp, ROOT)
        if _is_excluded(rel):
            continue
        for f in fn:
            if f.endswith(".py"):
                yield os.path.join(dp, f)


def extract_emit_events(tree: ast.AST, event_map: dict[str, str]) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            if fname != "emit":
                continue
            # 事件类型：第 2 个位置参数，或关键字 event_type
            et = None
            if len(node.args) >= 2:
                et = resolve_event(node.args[1], event_map)
            for kw in node.keywords:
                if kw.arg == "event_type":
                    et = resolve_event(kw.value, event_map)
            if et and not et.startswith("<"):
                out.add(et)
    return out


def extract_subscriptions(tree: ast.AST, event_map: dict[str, str], file: str) -> list[dict]:
    subs: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_resonance_conditions":
            organ = os.path.basename(os.path.dirname(file))
            # 尽力从 return 的 list[dict] 里取 event_types
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and isinstance(child.value, (ast.List, ast.Tuple)):
                    for item in child.value.elts:
                        if not isinstance(item, ast.Dict):
                            continue
                        ets = None
                        organ_name = organ
                        for k, v in zip(item.keys, item.values):
                            if isinstance(k, ast.Constant) and k.value == "event_types":
                                ets = [resolve_event(e, event_map) for e in v.elts]
                            if isinstance(k, ast.Constant) and k.value == "organ_name":
                                on = resolve_event(v, event_map)
                                if on and not on.startswith("<"):
                                    organ_name = on
                        if ets:
                            for e in ets:
                                if e and not e.startswith("<"):
                                    subs.append({"organ": organ_name, "event": e, "file": file})
    return subs


def extract_has_emit(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            fname = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            if fname == "emit":
                return True
    return False


def main():
    event_map = build_event_map()
    emitted: set[str] = set()
    subscriptions: list[dict] = []
    organ_files: dict[str, dict] = {}  # organ dir -> {emits:bool, subs:int}

    for fp in iter_py_files():
        try:
            src = open(fp, "r", encoding="utf-8").read()
            tree = ast.parse(src)
        except Exception as e:
            print(f"# parse skip {fp}: {e}", file=sys.stderr)
            continue
        emitted |= extract_emit_events(tree, event_map)
        subs = extract_subscriptions(tree, event_map, fp)
        subscriptions.extend(subs)
        organ = os.path.basename(os.path.dirname(fp))
        info = organ_files.setdefault(organ, {"emits": False, "subs": 0, "files": set()})
        info["files"].add(fp)
        if extract_has_emit(tree):
            info["emits"] = True
        info["subs"] += len(subs)

    # 交叉比对
    dead_subs: list[dict] = []
    for s in subscriptions:
        live = s["event"] in emitted or any(_match(s["event"], e) for e in emitted)
        if not live:
            dead_subs.append(s)

    # 孤儿发射：emitted 中没有任何订阅匹配
    orphan_emits = []
    for e in sorted(emitted):
        if not any(_match(s["event"], e) for s in subscriptions):
            orphan_emits.append(e)

    # 零发射器官：有订阅但自身文件从不 emit
    zero_emit = []
    for organ, info in organ_files.items():
        if info["subs"] > 0 and not info["emits"]:
            zero_emit.append(organ)

    report = {
        "emitted_event_count": len(emitted),
        "subscription_count": len(subscriptions),
        "dead_subscription_count": len(dead_subs),
        "dead_subscriptions": dead_subs,
        "orphan_emit_count": len(orphan_emits),
        "orphan_emits": orphan_emits,
        "zero_emit_organ_count": len(zero_emit),
        "zero_emit_organs": zero_emit,
        "emitted_events_sample": sorted(emitted)[:60],
    }

    txt = []
    txt.append("=" * 60)
    txt.append("订阅健康报告（第107批 T-107b / D172）")
    txt.append("=" * 60)
    txt.append(f"发射事件种类数: {report['emitted_event_count']}")
    txt.append(f"订阅条目数:     {report['subscription_count']}")
    txt.append(f"死订阅数:       {report['dead_subscription_count']}  "
               f"(占订阅 {report['dead_subscription_count']*100//max(1,report['subscription_count'])}%)")
    txt.append(f"孤儿发射数:     {report['orphan_emit_count']}")
    txt.append(f"零发射器官数:   {report['zero_emit_organ_count']} -> {zero_emit}")
    txt.append("-" * 60)
    txt.append("死订阅明细（订阅了但全项目无人发射）:")
    for s in dead_subs:
        txt.append(f"  - {s['organ']:20s} <- {s['event']}   [{os.path.relpath(s['file'], ROOT)}]")
    txt.append("-" * 60)
    txt.append("孤儿发射明细（发射了但无器官订阅）:")
    for e in orphan_emits:
        txt.append(f"  - {e}")
    print("\n".join(txt))

    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n# JSON -> {out}")

    return report


if __name__ == "__main__":
    main()
