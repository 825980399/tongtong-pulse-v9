#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""★492-7：意图样本**建议标注**表（只读输入，绝不写真值）。

- 读 ``data/eval/intent_samples.jsonl``；
- 用 ``QICA.INTENT_RULES`` 的 keywords → intent_type（按 priority 降序）给
  ``suggested_intent`` 建议值，合法值域用 ``IntentChannels.INTENT_TO_METHOD`` 的键校验；
- **绝不写 ``expected_intent``**（452/457 批留白纪律：机器不猜标注），
  也不提供 ``--inplace``，绝不回写原样本文件。

输出默认 stdout（jsonl）；``--out`` 可指定独立建议表，落盘经 guard_write 守卫。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nucleus._silent_except import silent_exc  # noqa: E402

_DEFAULT_IN = os.path.join(_ROOT, "data", "eval", "intent_samples.jsonl")
_QICA_SRC = os.path.join(_ROOT, "nucleus", "qica", "QICA.py")


def _load_rules(path: str):
    """AST 静态解析 QICA.INTENT_RULES 的 keywords / intent_type / priority。

    不 import 活体 QICA：脚本是离线只读工具，import 会带起分类器与配置副作用。
    """
    rules = []
    try:
        with open(path, encoding="utf-8", errors="replace") as _f:
            _tree = ast.parse(_f.read(), filename=path)
    except Exception as _e:
        silent_exc(_e, where="m492_annotate_suggest::_load_rules parse", level="debug")
        return rules
    for _node in ast.walk(_tree):
        if not isinstance(_node, ast.Assign):
            continue
        _is_rules = any(
            isinstance(_t, ast.Name) and _t.id == "INTENT_RULES"
            for _t in _node.targets)
        if not _is_rules or not isinstance(_node.value, (ast.List, ast.Tuple)):
            continue
        for _el in _node.value.elts:
            if not isinstance(_el, ast.Dict):
                continue
            _row = {}
            for _k, _v in zip(_el.keys, _el.values):
                if not isinstance(_k, ast.Constant) or not isinstance(_k.value, str):
                    continue
                try:
                    _row[_k.value] = ast.literal_eval(_v)
                except Exception as _e:
                    silent_exc(_e, where="m492_annotate_suggest::_load_rules literal",
                               level="debug")
                    _row[_k.value] = None
            if _row.get("keywords") and _row.get("intent_type"):
                rules.append(_row)
    rules.sort(key=lambda r: -int(r.get("priority") or 0)
               if isinstance(r.get("priority"), int) else 0)
    return rules


def _valid_intents():
    try:
        from nucleus.qica.IntentChannels import INTENT_TO_METHOD
        return set(dict(INTENT_TO_METHOD).keys())
    except Exception as _e:
        silent_exc(_e, where="m492_annotate_suggest::_valid_intents", level="debug")
        return set()


def suggest(query: str, rules, valid) -> str:
    """关键词命中（高 priority 优先）且在合法意图值域内才给建议，否则空串。"""
    _q = str(query or "")
    for _r in rules:
        for _kw in _r.get("keywords") or []:
            if str(_kw) and str(_kw) in _q:
                _it = str(_r.get("intent_type") or "")
                return _it if (not valid or _it in valid) else ""
    return ""


def collect(in_path: str, rules, valid):
    rows = []
    try:
        with open(in_path, encoding="utf-8", errors="replace") as _f:
            for _line in _f:
                if not _line.strip():
                    continue
                try:
                    _row = json.loads(_line)
                except ValueError as _je:
                    silent_exc(_je, where="m492_annotate_suggest::collect 坏行",
                               level="debug")
                    continue
                if not isinstance(_row, dict):
                    continue
                _before = dict(_row)
                _row["suggested_intent"] = suggest(
                    _row.get("query", ""), rules, valid)
                # 硬约束：只新增 suggested_intent，expected_intent 逐字不变
                if set(_row) - set(_before) != {"suggested_intent"}:
                    continue
                if _row.get("expected_intent", None) != _before.get("expected_intent", None):
                    continue
                rows.append(_row)
    except Exception as _e:
        silent_exc(_e, where="m492_annotate_suggest::collect", level="debug")
    return rows


def _emit(rows, out_path: str) -> None:
    _text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    if not out_path:
        sys.stdout.write(_text)
        return
    try:
        from nucleus.data.write_guard import guard_write
        if not guard_write(out_path, explicit=False, component="M492AnnotateSuggest"):
            sys.stdout.write(_text)
            return
    except Exception as _e:
        silent_exc(_e, where="m492_annotate_suggest::_emit guard", level="debug")
    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as _f:
            _f.write(_text)
        print("写出 %d 条建议标注 → %s" % (len(rows), out_path), file=sys.stderr)
    except Exception as _e:
        silent_exc(_e, where="m492_annotate_suggest::_emit write", level="debug")
        sys.stdout.write(_text)


def main() -> None:
    ap = argparse.ArgumentParser(description="492-7 意图样本建议标注（不写真值）")
    ap.add_argument("--input", default=_DEFAULT_IN)
    ap.add_argument("--rules", default=_QICA_SRC)
    ap.add_argument("--out", default="", help="独立建议表路径；空 ⇒ stdout")
    ap.add_argument("--limit", type=int, default=0, help="0=不限")
    a = ap.parse_args()
    _rules = _load_rules(a.rules)
    _valid = _valid_intents()
    _rows = collect(a.input, _rules, _valid)
    if a.limit > 0:
        _rows = _rows[:a.limit]
    _emit(_rows, a.out)
    print("建议标注 %d 条（规则 %d 条，合法意图 %d 个）" % (
        len(_rows), len(_rules), len(_valid)), file=sys.stderr)


if __name__ == "__main__":
    main()
