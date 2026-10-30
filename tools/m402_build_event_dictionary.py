# -*- coding: utf-8 -*-
"""402 刀1：事件字典生成器（L2 语义软着陆 · 只落台账不阻断热路径）。

背景
----
洞鉴 400 + 烛微 401：`nucleus/const.py` 里有 200+ 条事件常量，但**没有 schema**、
没有 payload 形状、没有 producers/consumers。发方 misspelling 一个 key，
收方永远收不到，且没有任何机制会报错。

本工具的产出
------------
`data/contracts/event_dictionary.json`：每个事件一条记录，含
  class / version / payload / producers / consumers / required_keys

★T0 实测修正（票面数字与本机不符，详见交付报告偏差清单）
--------------------------------------------------------
  · 票面「223 条事件」= 189 条**字面量**事件常量 + 34 条非事件枚举
    （PulseLayer/ViewMode/OrganStatus/ErrorCode/LogLevel/PulseIntent）。
    189 之外还有 16 条 `Event` 注册表的**类引用别名**（`HEART_BEAT = HeartEvent.BEAT`），
    故真实事件常量条目 205 条 / **唯一值 185 个**。
  · 票面「4 组重复」只对**字面量层面**成立（189 - 185 = 4：`chat.message`
    /`curiosity.tick`/`digest.knowledge`/`inference.result`）。把 16 条别名计入后
    重复组变成 19 组，其中 **17 组是注册表刻意复用**（注释明写"单一事实来源，
    不重复定义值"），**真撞车只有 2 组**：`inference.result`、
    `controller.search_stage_completed`（三源）。
  · 票面「223 - 85 = 138 未发」= 实测 **110 已发 / 75 未发**；且三类里的
    「仅测试发」在本项目是**空集**（tests 只用 `ping`/`demo.*` 等虚构事件名）。
  · 票面「register_condition 反向补 consumers」**会漏掉全部器官订阅** ——
    器官走 `get_resonance_conditions()`（main.py 遍历批量注册），
    直调 `register_condition` 的只有 15 处对话/UI 模块 ⇒ 本工具**双源合并**。

只读设计
--------
本模块**不进热路径**：不被 main / organs / nucleus 生产代码 import，
只由 `tools` 侧与测试调用。生成失败不影响框架运行。
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

SCHEMA = "event_dictionary/402.v1"

#: 目录级排除（★只排"结构性"目录，不按业务名排 —— 见 401 批教训）
_SKIP_DIR_NAMES = frozenset({
    "__pycache__", "node_modules", "venv", ".venv", "site-packages",
    ".git", ".release-tmp", ".tmp_backup", ".pytest_tmp", "code_backups",
    "docker_build_ctx",
})

#: ★文件级排除：备份文件是**平铺在目录里**的（`.bak_batch347_X.py`），
#:   只靠目录名过滤会漏 —— T0 第一版探针就被它污染过。
_SKIP_FILE_PREFIX = (".bak", "tmp_", "~$")

#: 事件发射入口 → 事件实参在第几个位置参数
_EMIT_EVENT_POS = {
    "_emit": 0,
    "tap_publish": 0,
    "emit_event": 0,
    "publish": 0,
    "emit": 0,
}

#: 器官名形态：`core.emit("耳朵", Event.X)` 的第一实参是**器官**不是事件。
_ORGAN_NAME_RE = re.compile(r"^[\u4e00-\u9fff]{1,8}$")

#: never_emitted 三分类的注释线索
_RESERVED_HINTS = ("预留", "预埋", "待接线", "待接入", "拟接线", "协议未启用", "PHASE")
_DEPRECATED_HINTS = ("废弃", "已摘除", "死常量", "不再使用", "已移除", "笔误遗留")


# ============================ 基础设施 ============================

def iter_py(root: str, *, include_tests: bool = False,
            include_tmp: bool = False):
    """遍历生产面 .py（默认排除 tests/tmp 与一切备份件）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in _SKIP_DIR_NAMES]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            if any(fn.startswith(p) for p in _SKIP_FILE_PREFIX):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, root).replace("\\", "/")
            if not include_tests and rel.startswith("tests/"):
                continue
            if not include_tmp and rel.startswith("tmp/"):
                continue
            yield rel, fp


def _read(fp: str) -> str:
    return io.open(fp, encoding="utf-8", errors="replace").read()


#: ★AST 解析缓存。本工具要跑三遍全库（发射面 / 订阅面 / tests 面），
#:   而 `ast.parse` 是绝对大头（实测 13s → 5s，见交付报告）。
#:   键用 `(root, rel)`：同一进程内跨用例复用不会串味（root 不同即隔离）。
_TREE_CACHE: dict = {}


def clear_tree_cache() -> None:
    _TREE_CACHE.clear()


def _parse_cached(root: str, rel: str, fp: str):
    key = (root, rel)
    if key in _TREE_CACHE:
        return _TREE_CACHE[key]
    tree = _parse(rel, fp)
    _TREE_CACHE[key] = tree
    return tree


def _parse(rel: str, fp: str):
    try:
        return ast.parse(_read(fp), filename=fp)
    except SyntaxError as _e:
        silent_exc(_e, where="m402_build_event_dictionary._parse:%s" % rel, level="debug")
        return None


# ============================ 1) 常量表 ============================

def collect_constants(root: str) -> dict:
    """从 `nucleus/const.py` 抽出全部 `*Event` 类常量。

    Returns: {
      "entries": [{"class","name","value","lineno","resolve_from"}],
      "non_event_classes": {类名: 条数},     # ★票面 223 的水分就在这
      "const_lines": [...],
    }
    """
    fp = os.path.join(root, "nucleus", "const.py")
    src = _read(fp)
    tree = ast.parse(src, filename=fp)
    lines = src.splitlines()

    per_class = {}          # cls -> [(name, value_or_None, lineno, ref)]
    non_event = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        items = []
        n_str = 0
        for b in node.body:
            if not (isinstance(b, ast.Assign) and len(b.targets) == 1
                    and isinstance(b.targets[0], ast.Name)):
                continue
            tgt = b.targets[0].id
            v = b.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                items.append((tgt, v.value, b.lineno, ""))
                n_str += 1
            elif isinstance(v, ast.Attribute):
                items.append((tgt, None, b.lineno, ast.unparse(v)))
        if node.name.endswith("Event"):
            per_class[node.name] = items
        elif n_str:
            non_event[node.name] = n_str

    # 解析类引用（`HEART_BEAT = HeartEvent.BEAT`）
    flat = {}
    for cls, items in per_class.items():
        for nm, val, ln, ref in items:
            if val is not None:
                flat["%s.%s" % (cls, nm)] = val

    entries = []
    for cls, items in per_class.items():
        for nm, val, ln, ref in items:
            if val is None:
                val = flat.get(ref)
            entries.append({
                "class": cls,
                "name": nm,
                "value": val,
                "lineno": ln,
                "resolve_from": ref,
                "comment": _near_comment(lines, ln),
            })
    return {"entries": entries, "non_event_classes": non_event,
            "const_lines": lines}


def _near_comment(lines, lineno: int, up: int = 3) -> str:
    """取定义行上方 up 行的注释上下文（用于判预留/废弃）。"""
    try:
        seg = lines[max(0, lineno - up - 1):lineno]
    except (IndexError, TypeError) as exc:
        silent_exc(exc, where="m402_build_event_dictionary._near_comment:%s" % lineno,
                   level="debug")
        return ""
    return "\n".join(x.strip() for x in seg if x.strip().startswith("#"))


# ============================ 2) 发射面 ============================

def _event_arg_index(func) -> int:
    """判定事件实参位置。

    ★这是本工具最易错的一处：`PulseCore.emit(source_organ, event_type, ...)`
      的事件在**第二**个位置参数，而器官内部 `self._emit(event_type, ...)`
      在第一。用正则统一抓第一实参，会把 86 处 `core.emit("耳朵", Event.X)`
      全部算成"未发射"（T0 实测：错判 84 → 真值更低）。
    """
    attr = getattr(func, "attr", None)
    if attr == "emit":
        recv = getattr(func.value, "id", "") if isinstance(func, ast.Attribute) else ""
        if recv in ("self", "cls", ""):
            return 0
        return 1        # core.emit / _pc.emit / backend.emit —— 器官在前
    return _EMIT_EVENT_POS.get(attr or "", 0)


def _const_ref(node) -> str | None:
    """把 `Event.X` / `HeartEvent.BEAT` 还原成 (类, 名) 字符串键。"""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return "%s.%s" % (node.value.id, node.attr)
    return None


def _dict_keys(node) -> list | None:
    if isinstance(node, ast.Dict):
        out = []
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.append(k.value)
        return out
    return None


def scan_emits(root: str) -> dict:
    """扫生产面全部发射调用，返回 {事件值: {...}}。

    ★T0 第三处修正：事件实参可能是**关键字**而非位置 ——
      `self.pulse_core.emit(source_organ=..., event_type=ChatEvent.X, payload={...})`
      全库 68 处。只处理位置参数会把 `chat.user_presence_detected` 这类
      明明在发的件判成"零发射"（实测一度把契约断裂虚报成 73 个）。
      ⇒ 优先取关键字，其次位置参数。
    """
    out = defaultdict(lambda: {"producers": [], "payload_keys": [],
                               "dynamic_payload": 0, "sites": 0})
    unknown = Counter()
    _KW_EVENT = ("event_type", "name", "event")

    for rel, fp in iter_py(root):
        if rel == "nucleus/const.py":
            continue
        tree = _parse_cached(root, rel, fp)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", None)
            fname = getattr(node.func, "id", None)
            if attr not in _EMIT_EVENT_POS and fname not in _EMIT_EVENT_POS:
                continue

            # ---- ① 关键字形态优先 ----
            arg = None
            payload_node = None
            for kw in node.keywords:
                if kw.arg in _KW_EVENT and arg is None:
                    arg = kw.value
                if kw.arg == "payload":
                    payload_node = kw.value
            # ---- ② 位置形态兜底 ----
            if arg is None:
                idx = _event_arg_index(node.func)
                if len(node.args) <= idx:
                    unknown["no_event_arg"] += 1
                    continue
                arg = node.args[idx]
                pidx = idx + 1
                if payload_node is None and len(node.args) > pidx:
                    payload_node = node.args[pidx]

            ref = _const_ref(arg)
            literal = None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literal = arg.value
            elif ref is None:
                unknown["dynamic_event(%s)" % type(arg).__name__] += 1
                continue
            key = ("REF", ref) if ref else ("LIT", literal)

            keys = _dict_keys(payload_node) if payload_node is not None else None
            if payload_node is not None and keys is None \
                    and not isinstance(payload_node, ast.Constant):
                out[key]["dynamic_payload"] += 1
            out[key]["producers"].append(rel)
            out[key]["sites"] += 1
            if keys:
                out[key]["payload_keys"].extend(keys)
    return {"by_key": out, "unknown": unknown}


# ============================ 3) 订阅面 ============================

def scan_consumers(root: str) -> dict:
    """双源收集 consumers。

    源 A：器官 `get_resonance_conditions()` 里声明的事件（main.py 遍历批量注册）
    源 B：直接 `register_condition(event_types=[...])` 调用（对话/UI 模块）
    """
    by_key = defaultdict(list)

    for rel, fp in iter_py(root):
        if rel == "nucleus/const.py":
            continue
        tree = _parse_cached(root, rel, fp)
        if tree is None:
            continue
        for node in ast.walk(tree):
            # ---- 源 A：器官订阅声明 ----
            if isinstance(node, ast.FunctionDef) and node.name == "get_resonance_conditions":
                organ = _organ_of_class(tree, node)
                for sub in ast.walk(node):
                    ref = _const_ref(sub)
                    if ref:
                        by_key[("REF", ref)].append(organ or rel)
                    elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        v = sub.value
                        if v == "*" or v.endswith(".*"):
                            by_key[("PAT", v)].append(organ or rel)
                        elif "." in v and not v.startswith(("/", "data", "nucleus")):
                            by_key[("LIT", v)].append(organ or rel)
            # ---- 源 B：直接注册 ----
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "register_condition":
                for kw in node.keywords:
                    if kw.arg != "event_types":
                        continue
                    if not isinstance(kw.value, (ast.List, ast.Tuple)):
                        continue
                    for e in kw.value.elts:
                        ref = _const_ref(e)
                        if ref:
                            by_key[("REF", ref)].append(rel)
                        elif isinstance(e, ast.Constant) and isinstance(e.value, str):
                            v = e.value
                            if v == "*" or v.endswith(".*"):
                                by_key[("PAT", v)].append(rel)
                            else:
                                by_key[("LIT", v)].append(rel)
    return by_key


def _organ_of_class(tree, fn) -> str | None:
    """找该函数所属类的**类名**（consumers 的稳定唯一标识）。

    ★T0 实测：全库 consumers 里没有一个是中文 `organ_name` 字面量 ——
      器官的 `organ_name` 都在 `__init__` 或 mixin 里赋值，不是类体字面量。
      早期版本优先取类体 `organ_name` 字面量，会让迷你工程返回 `'心脏'`、
      真实工程返回 `PulseHeart`，两套口径不一致。统一为**类名优先**：
      类名唯一、跨批次稳定、可直接 grep 到文件；显示名不进 consumers 键。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and fn in node.body:
            return node.name
    return None


# ============================ 4) 组装 ============================

_LIT_EVENT_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")


def _looks_like_event(s: str) -> bool:
    """字面量是否像事件名（小写点分）——用于剔除路径/模块名等噪声。"""
    return bool(s) and len(s) <= 64 and bool(_LIT_EVENT_RE.match(s))


def _expand_pat(pat: str, all_values) -> list:
    if pat == "*":
        return list(all_values)
    if pat.endswith(".*"):
        pre = pat[:-1]
        return [v for v in all_values if v.startswith(pre)]
    return [pat]


def build_dictionary(root: str, *, max_payload_samples: int = 12) -> dict:
    clear_tree_cache()          # ★每次重建都清：源码可能刚改过，缓存会 stale
    const = collect_constants(root)
    entries = const["entries"]

    # 值 → 别名
    aliases = defaultdict(list)          # value -> ["Cls.NAME", ...]
    ref_to_value = {}
    for e in entries:
        if e["value"] is None:
            continue
        ref_to_value["%s.%s" % (e["class"], e["name"])] = e["value"]
        k = "%s.%s" % (e["class"], e["name"])
        if k not in aliases[e["value"]]:
            aliases[e["value"]].append(k)

    all_values = set(aliases)
    emit_scan = scan_emits(root)
    emit = emit_scan["by_key"]
    cons = scan_consumers(root)

    # 解析 producers / consumers 到值
    producers = defaultdict(set)
    payload_keys = defaultdict(list)
    dynamic_payload = Counter()
    for (kind, k), rec in emit.items():
        v = ref_to_value.get(k) if kind == "REF" else k
        if v is None or v not in all_values:
            continue
        for r in rec["producers"]:
            producers[v].add(r)
        payload_keys[v].extend(rec["payload_keys"])
        dynamic_payload[v] += rec["dynamic_payload"]

    # ★consumers 必须区分「精确订阅」与「通配订阅」：
    #   只算精确订阅会把 `chat.*` 顺带能收到的事件也当成"有专人订阅"，
    #   让「有订阅者却零发射 = 契约断裂」这个诊断指标虚高。
    consumers = defaultdict(set)        # 精确
    consumers_wild = defaultdict(set)   # 通配（`*` / `x.*`）
    for (kind, k), orgs in cons.items():
        if kind == "PAT":
            targets = [(v, True) for v in _expand_pat(k, all_values)]
        elif kind == "REF":
            targets = [(ref_to_value.get(k), False)]
        else:
            targets = [(k, False)]
        for v, wild in targets:
            if not v or v not in all_values:
                continue
            for o in orgs:
                (consumers_wild if wild else consumers)[v].add(o)

    # ★tests 侧发射（用于区分"仅测试发"）
    test_emit = defaultdict(int)
    for rel, fp in iter_py(root, include_tests=True):
        if not rel.startswith("tests/"):
            continue
        tree = _parse_cached(root, rel, fp)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", None)
            if attr not in _EMIT_EVENT_POS:
                continue
            idx = _event_arg_index(node.func)
            if len(node.args) <= idx:
                continue
            arg = node.args[idx]
            ref = _const_ref(arg)
            v = ref_to_value.get(ref) if ref else (
                arg.value if isinstance(arg, ast.Constant) else None)
            if v in all_values:
                test_emit[v] += 1

    # ★字面量事件：全库在发/在订，但 const.py 里**没有对应常量**。
    #   这正是票面担心的"拼错 key 无人报错"的温床 —— 两侧都是裸字符串，
    #   任何静态检查都看不见；只落台账，不阻断。
    literal_only = {}
    for (kind, k), rec in emit.items():
        if kind != "LIT" or k in all_values or not _looks_like_event(k):
            continue
        literal_only.setdefault(k, {"producers": [], "consumers": [],
                                    "consumers_wildcard": [], "payload": [],
                                    "has_constant": False})
        literal_only[k]["producers"] = sorted(set(rec["producers"]))
        literal_only[k]["payload"] = sorted(set(rec["payload_keys"]))
    for (kind, k), orgs in cons.items():
        if kind != "LIT" or k in all_values or not _looks_like_event(k):
            continue
        literal_only.setdefault(k, {"producers": [], "consumers": [],
                                    "consumers_wildcard": [], "payload": [],
                                    "has_constant": False})
        literal_only[k]["consumers"] = sorted(set(orgs))
    for k, r in literal_only.items():
        # ★只标**静态**孤儿：事件名可存进 config 再动态发射（`config.py:119`
        #   `"event_type": "snapshot.auto_save"` → `PulseHeart.py:1333`），
        #   静态扫不到。已知假阳性都出在这类"配置驱动发射"上，故名字带 static。
        r["static_orphan"] = bool(r["consumers"]) and not r["producers"]

    # 逐值组装
    events = {}
    for v in sorted(all_values):
        pk = payload_keys.get(v) or []
        cnt = Counter(pk)
        req = sorted([k for k, c in cnt.items() if c >= 2]) if cnt else []
        rec = {
            "class": sorted({a.split(".")[0] for a in aliases[v]}),
            "const_names": aliases[v],
            "version": 1,
            "payload": sorted(cnt),
            "payload_samples": cnt.most_common(max_payload_samples),
            "payload_dynamic": int(dynamic_payload.get(v, 0)),
            "producers": sorted(producers.get(v, ())),
            "consumers": sorted(consumers.get(v, ())),
            "consumers_wildcard": sorted(consumers_wild.get(v, ())),
            "required_keys": req,
            "never_emitted": v not in producers,
            "test_emit_count": int(test_emit.get(v, 0)),
        }
        events[v] = rec

    # never_emitted 分桶
    # ★T0 实测修正：票面要求的三类里「仅测试发」在本项目是**空集** ——
    #   tests 里的 emit 全是虚构事件名（`ping`/`x`/`a.one`/`demo.*`），
    #   没有任何**真实**事件只被测试发射。故按诊断价值重分为四类：
    #     orphan_consumer  有**精确**订阅却零发射 ⇒ 契约断裂（刀2 死信的核心输入）
    #     wildcard_only    只有通配订阅（`chat.*`/`*`）⇒ 顺带能收，非专人契约
    #     reserved         无任何订阅也零发射 ⇒ 真预留/未接线
    #     deprecated       有废弃线索（注释 / 同义事件已在发）
    never = [v for v, r in events.items() if r["never_emitted"]]
    buckets = {"orphan_consumer": [], "wildcard_only": [], "deprecated": [],
               "reserved": [], "test_only": []}
    why = {}
    for v in never:
        if events[v]["test_emit_count"] > 0:
            buckets["test_only"].append(v)
            why[v] = "tests 目录里有发射调用（生产零发射）"
            continue
        ctx = ""
        for e in entries:
            if e["value"] == v:
                ctx += (e.get("comment") or "") + "\n"
        if any(h in ctx for h in _DEPRECATED_HINTS):
            buckets["deprecated"].append(v)
            why[v] = "const.py 定义处注释含废弃线索"
            continue
        norm = re.sub(r"^(?:controller|system|organ)\.", "", v)
        twin = [x for x in all_values
                if x != v and re.sub(r"^(?:controller|system|organ)\.", "", x) == norm
                and producers.get(x)]
        if twin:
            buckets["deprecated"].append(v)
            why[v] = "同义事件 %s 已在生产发射 ⇒ 本名为旧名/废弃名" % twin[0]
            continue
        if events[v]["consumers"]:
            buckets["orphan_consumer"].append(v)
            why[v] = ("有 %d 个**精确**订阅方却零发射 ⇒ 契约断裂：订阅方永远收不到"
                      % len(events[v]["consumers"]))
            continue
        if events[v]["consumers_wildcard"]:
            buckets["wildcard_only"].append(v)
            why[v] = ("仅被通配订阅覆盖（%d 处）⇒ 顺带能收到，非专人契约；零发射属未接线"
                      % len(events[v]["consumers_wildcard"]))
            continue
        buckets["reserved"].append(v)
        why[v] = "既无发射也无订阅 ⇒ 预留或未接线"

    for v in never:
        events[v]["never_reason"] = why.get(v, "")
        events[v]["never_bucket"] = next(
            (b for b in ("test_only", "deprecated", "orphan_consumer",
                         "wildcard_only", "reserved") if v in buckets[b]), "reserved")

    # 重复值分组
    dups = {}
    for v, al in aliases.items():
        if len(al) > 1:
            dups[v] = al
    # 区分「刻意复用」与「真撞车」：别名里含 `Event.` 的视为注册表复用
    reuse, colliding = {}, {}
    for v, al in dups.items():
        has_registry = any(a.startswith("Event.") for a in al)
        if has_registry and len(al) == 2:
            reuse[v] = al
        else:
            colliding[v] = al

    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "generated_by": "tools/m402_build_event_dictionary.py",
        "source": "nucleus/const.py",
        "totals": {
            "constant_entries": len(entries),
            "unique_events": len(all_values),
            "emitted_in_prod": len(all_values) - len(never),
            "never_emitted": len(never),
            "duplicate_value_groups": len(dups),
            "duplicate_reuse": len(reuse),
            "duplicate_colliding": len(colliding),
            # ★票面 223 的真实构成：189 字面量事件 + 34 非事件枚举
            "literal_event_constants": len([e for e in entries if not e["resolve_from"]]),
            "alias_event_constants": len([e for e in entries if e["resolve_from"]]),
            "non_event_enum_constants": sum(const["non_event_classes"].values()),
            "non_event_classes": const["non_event_classes"],
            "ticket_223_decomposition": (
                "票面 223 = 189 字面量事件常量 + 34 非事件枚举；"
                "205 = 189 + 16 条 Event 注册表类引用别名；唯一值 185"),
            # ★裸字符串事件：无常量、只有字面量在发/在订
            "literal_only_events": len(literal_only),
            "literal_only_static_orphan": len(
                [1 for r in literal_only.values() if r["static_orphan"]]),
        },
        "never_emitted_breakdown": {
            "counts": {k: len(x) for k, x in buckets.items()},
            "note": ("票面三类中的「仅测试发」在本项目为空集（tests 只用虚构事件名）；"
                     "改按诊断价值分四类，orphan_consumer 即契约断裂（刀2 死信输入）"),
            "test_only": sorted(buckets["test_only"]),
            "deprecated": sorted(buckets["deprecated"]),
            "orphan_consumer": sorted(buckets["orphan_consumer"]),
            "wildcard_only": sorted(buckets["wildcard_only"]),
            "reserved": sorted(buckets["reserved"]),
        },
        "duplicates": {
            "reuse_by_registry": reuse,
            "colliding": colliding,
        },
        # ★诚实边界：静态分析做不到什么，必须写清楚，否则读数会被高估
        "static_limits": {
            "dynamic_emit_sites": sum(
                v for k, v in emit_scan["unknown"].items() if k.startswith("dynamic_event")),
            "dynamic_emit_detail": dict(emit_scan["unknown"]),
            "note": ("这些发射点的事件实参是变量/调用/下标（如 "
                     "`self._emit(task_info['event_type'], ...)`），静态无法归属到具体事件 "
                     "⇒ never_emitted / orphan_consumer 的判定以此为**已知盲区**："
                     "被判为「零发射」的事件，仍可能由 dynamic_emit_sites 所示的发射点覆盖。"),
            "payload_unavailable": ("payload 为变量而非字典字面量时，key 列表为空（记 "
                                    "payload_dynamic 计数），不代表该事件没有 payload。"),
            "consumers_scope": ("consumers 只含**静态可解析**的订阅声明：器官 "
                                "`get_resonance_conditions()` 内的事件常量、以及 "
                                "`register_condition(event_types=[...])` 的字面量/常量。"
                                "运行时动态拼接的 event_types 不在内。键统一用**类名**"
                                "（无类可归时退化为文件相对路径），organ_name 显示名不入键。"),
            "literal_only_scope": ("literal_only 段收的是**裸字符串事件**："
                                   "发射/订阅两侧都写 `\"a.b\"`、const.py 里没有常量。"
                                   "只认小写点分形态（`^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$`），"
                                   "路径与模块名已被过滤；仍可能有少量非事件噪声。"
                                   "`static_orphan=True` **已知含假阳性**：事件名可先存进 "
                                   "config 再动态发射（`config.py:119` 的 "
                                   "`snapshot.auto_save` → `PulseHeart.py:1333`），静态扫不到。"),
        },
        "literal_only": literal_only,
        "events": events,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="402 刀1：生成事件字典（只读台账）")
    ap.add_argument("--root", default=_ROOT)
    ap.add_argument("--out", default=os.path.join(_ROOT, "data", "contracts",
                                                  "event_dictionary.json"))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    d = build_dictionary(args.root)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=False)
        f.write("\n")

    if not args.quiet:
        t = d["totals"]
        print("=== 事件字典已生成 ===")
        print("  输出            : %s" % args.out)
        print("  常量条目        : %d" % t["constant_entries"])
        print("  唯一事件        : %d" % t["unique_events"])
        print("  生产已发射      : %d" % t["emitted_in_prod"])
        print("  从未发射        : %d" % t["never_emitted"])
        _bc = d["never_emitted_breakdown"]["counts"]
        print("     契约断裂(有精确订阅却零发射) : %d" % _bc.get("orphan_consumer", 0))
        print("     仅通配订阅                  : %d" % _bc.get("wildcard_only", 0))
        print("     预留未接线(无订阅)          : %d" % _bc.get("reserved", 0))
        print("     废弃未清                    : %d" % _bc.get("deprecated", 0))
        print("     仅测试发                    : %d" % _bc.get("test_only", 0))
        print("  重复值组        : %d（刻意复用 %d / 真撞车 %d）"
              % (t["duplicate_value_groups"], t["duplicate_reuse"],
                 t["duplicate_colliding"]))
        print("  非事件枚举(水分): %d  %s"
              % (t["non_event_enum_constants"], t["non_event_classes"]))
        print("  裸字符串事件    : %d 条（无常量；其中静态孤儿 %d）"
              % (t["literal_only_events"], t["literal_only_static_orphan"]))
        if d["duplicates"]["colliding"]:
            print("  ★真撞车明细：")
            for v, al in d["duplicates"]["colliding"].items():
                print("     %s <= %s" % (v, al))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
