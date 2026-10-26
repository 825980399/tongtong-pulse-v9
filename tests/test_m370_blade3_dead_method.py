# -*- coding: utf-8 -*-
"""370 刀3 定向单测：O3 两方法**死方法监测标记**＋只读引用扫描（保留不删）。

★被测面：`nucleus/audit_problem_pool.py` 的 `plan_target_drift` / `restore_pool`
  与只读扫描器 `tools/ci/scan_dead_method_refs_370.py`。
★本刀红线：方法体零 diff、注释不改执行路径、不接生产调用方、不删方法、不新增 CI 阻断。
★核真伪：票面记 `restore_pool`「调用 0」，实测**测试面有 2 处调用** ⇒
  两条标记措辞必须不同（铁律166：不同事实不同措辞），本单测把该差异钉死。
"""
import ast
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

POOL_REL = "nucleus/audit_problem_pool.py"
SCAN_REL = "tools/ci/scan_dead_method_refs_370.py"
EVID_JSON = os.path.join(ROOT, "docs", "路灯与星轨对话", "交付报告", "证据件", "370",
                         "O3两方法死方法监测扫描_20261026.json")

_TARGETS = ("plan_target_drift", "restore_pool")


def _src(rel):
    with open(os.path.join(ROOT, rel), "rb") as f:
        return f.read().decode("utf-8", "replace").replace("\r\n", "\n")


def _func_nodes(src):
    tree = ast.parse(src)
    return {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name in _TARGETS}


def _body_dump(node):
    """方法体指纹：ast.dump 只接单节点，需用 Module 包装 body（list）。"""
    return ast.dump(ast.Module(body=node.body, type_ignores=[]))


# ------------------------------------------------------------------ 用例
def test_01_marks_present_and_bodies_untouched():
    """① 两方法标记注释在位，且**方法体/签名/装饰器零 diff**（注释不改执行路径）。"""
    new = _src(POOL_REL)
    old = subprocess.run(
        ["git", "show", "HEAD:%s" % POOL_REL], cwd=ROOT,
        capture_output=True, check=True).stdout.decode("utf-8").replace("\r\n", "\n")

    for name in _TARGETS:
        assert "★370刀3 死方法监测标记" in new, "%s 标记缺失" % name
        # 标记必须紧贴该方法的 def 行之前
        idx = new.index("def %s(" % name)
        head = new[max(0, idx - 900):idx]
        assert "★370刀3 死方法监测标记" in head, "%s 的标记不在其定义处" % name

    o, n = _func_nodes(old), _func_nodes(new)
    assert set(o) == set(n) == set(_TARGETS), "两方法均须仍在（不得被删）"
    for name in _TARGETS:
        assert _body_dump(o[name]) == _body_dump(n[name]), \
            "%s 方法体被改动（本刀只允许加注释）" % name
        assert ast.unparse(o[name].args) == ast.unparse(n[name].args), \
            "%s 签名被改动" % name
        assert [ast.unparse(d) for d in o[name].decorator_list] == \
               [ast.unparse(d) for d in n[name].decorator_list], "%s 装饰器被改动" % name


def test_02_scan_matches_evidence_and_is_reproducible():
    """② 扫描器可复现，四类计数与证据件 JSON 完全一致（脚本可重跑核对）。"""
    assert os.path.isfile(EVID_JSON), "证据件 JSON 未落盘：%s" % EVID_JSON
    with open(EVID_JSON, encoding="utf-8") as f:
        evid = json.load(f)
    assert {t["name"] for t in evid["targets"]} == set(_TARGETS)

    # 现跑一次（脚本只读，不改仓库）
    sys.path.insert(0, os.path.join(ROOT, "tools", "ci"))
    import scan_dead_method_refs_370 as sc  # noqa: E402
    fresh = sc.build()

    # ★自噬规避（铁律188）：本单测文件自身就写有这两个方法名，必然抬升
    #   「测试面文本提及」计数 ⇒ 该字段**不作一致性锚**（只记录、不比对），
    #   否则每新增一个引用方法名的测试文件都会把证据件判成过期。
    #   结构性字段（定义/调用/属性读）不会被纯文本提及影响，照常比对。
    _VOLATILE = {("test", "mention_n")}
    by_name = {t["name"]: t for t in fresh["targets"]}
    for t in evid["targets"]:
        f = by_name[t["name"]]
        for surface in ("prod", "test"):
            for k in ("def_n", "call_n", "attr_read_n", "mention_n"):
                if (surface, k) in _VOLATILE:
                    continue
                assert t["surfaces"][surface][k] == f["surfaces"][surface][k], \
                    "%s/%s/%s 与证据件不一致（%s vs %s）" % (
                        t["name"], surface, k,
                        t["surfaces"][surface][k], f["surfaces"][surface][k])

    # ★核真伪钉死：restore_pool 测试面确有 2 处调用（票面「调用0」不含测试面）
    assert by_name["restore_pool"]["surfaces"]["test"]["call_n"] == 2, \
        "restore_pool 测试面调用数变了，标记措辞须同步复核"
    assert by_name["restore_pool"]["surfaces"]["prod"]["call_n"] == 0
    assert by_name["plan_target_drift"]["surfaces"]["test"]["call_n"] == 0
    assert by_name["plan_target_drift"]["surfaces"]["prod"]["call_n"] == 0


def test_03_marks_state_facts_not_copied():
    """③ ★铁律166：两条标记的事实不同 ⇒ 措辞必须不同，不得「同上」。"""
    new = _src(POOL_REL)
    i_a = new.index("def plan_target_drift(")
    i_b = new.index("def restore_pool(")
    mark_a = new[max(0, i_a - 900):i_a]
    mark_b = new[max(0, i_b - 900):i_b]

    assert "测试调用0" in mark_a, "plan_target_drift 标记须写清测试调用0"
    assert "测试调用2" in mark_b, "restore_pool 标记须写清测试调用2（不得照抄）"
    assert "不是无调用方的死方法" in mark_b, "restore_pool 标记须点明核真伪结论"
    assert mark_a.strip() != mark_b.strip(), "两条标记不得同文"
    # 备份副本口径须点名，避免下次扫描再被 .bak 文件误导
    assert ".bak_batch350_ap.py" in mark_b


def test_04_no_new_production_import_or_call():
    """④ 边界：本刀不得新增任何生产调用方（生产面 call 仍为 0）。"""
    sys.path.insert(0, os.path.join(ROOT, "tools", "ci"))
    import scan_dead_method_refs_370 as sc  # noqa: E402
    fresh = {t["name"]: t for t in sc.build()["targets"]}
    for name in _TARGETS:
        assert fresh[name]["surfaces"]["prod"]["call_n"] == 0, \
            "%s 出现生产面调用 ⇒ 本刀越界接了调用方" % name
        assert fresh[name]["surfaces"]["prod"]["def_n"] == 1, \
            "%s 生产面定义数异常（应恰为 1）" % name
    # 扫描器本身不得被生产代码 import（只读面工具）
    for rel in ("nucleus", "organs", "base"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, rel)):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in filenames:
                if fn.endswith(".py"):
                    assert "scan_dead_method_refs_370" not in _src(
                        os.path.relpath(os.path.join(dirpath, fn), ROOT)), \
                        "扫描器被生产代码引用：%s" % fn


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
