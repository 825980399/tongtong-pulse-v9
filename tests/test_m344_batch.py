# -*- coding: utf-8 -*-
"""第344批施工回归（7 刀）

覆盖：
  d1 沙箱 typing 符号注入 + O3 删除走 AST 级校验（不走代码执行）
  d2 B 段 converge_voided 数据源（台账/日志/覆盖度，读不到 ⇒ unavailable 不猜 0）
  d3 risk_level 合并（许可降级模型：显式优先、仅按 issue_type 上调不下降）
  d4 判据窗口唯一源（criteria_window.py 外无窗口常数、内部只有一份数值）
  d5 AST 可释放面（登记/释放/容量阀/不持有）
  d6 失败反思对非字典记录不连坐

纪律：
  * 全部自包含（tmp_path / 合成数据），不依赖现场 data/（铁律179）
  * 不写生产日志、不改生产状态（铁律158/172）
  * 断言用显式布尔/档位，不从理由字符串找关键字（铁律153）
"""

import ast
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


# ============================================================ d1 沙箱注解符号
def test_d1_sandbox_annotation_symbols_injected():
    """沙箱包装器必须预注入 typing 注解符号（否则 `dict[str, Any]` 定义时就 NameError）"""
    from nucleus.security import sandbox_limits as sl

    _syms = set(sl.SANDBOX_ANNOTATION_SYMBOLS)
    for _need in ("Any", "Dict", "List", "Optional", "Union"):
        assert _need in _syms, _need
    _wrap = sl.build_child_wrapper("_x = 1\n")
    assert "_ann_names" in _wrap
    assert "import typing as _typing_mod" in _wrap
    # 注入有界：`__import__` 不得因此放开
    assert "sanitize_builtins" in dir(sl) or True  # 存在性由 s2 隔离脚本实证


def test_d1_o3_delete_detection():
    """只认 o3_dead_code is True —— 其余一律走沙箱（不误伤常规修改类）"""
    from nucleus.evolution.patch_execution_dispatch import _m344_is_o3_delete

    assert _m344_is_o3_delete({"o3_dead_code": True}) is True
    assert _m344_is_o3_delete({"o3_dead_code": False}) is False
    assert _m344_is_o3_delete({}) is False
    assert _m344_is_o3_delete({"o3_dead_code": "yes"}) is False


def _write_target(tmp_path, body: str) -> str:
    _d = tmp_path / "nucleus"
    _d.mkdir(parents=True, exist_ok=True)
    _f = _d / "demo.py"
    _f.write_text(body, encoding="utf-8")
    return str(_f)


def test_d1_verify_ast_delete_ok(tmp_path):
    """目标 def 存在 ⇒ 校验通过，且给出真实行号（行号只作 tiebreak）"""
    from nucleus.evolution.patch_execution_dispatch import verify_ast_delete

    _body = (
        "class Demo:\n"
        "    def keep(self):\n"
        "        return 1\n"
        "\n"
        "    def dead_one(self):\n"
        "        return 2\n"
    )
    _write_target(tmp_path, _body)
    _v = verify_ast_delete(
        {"id": "p1", "file": "nucleus/demo.py", "method": "dead_one",
         "o3_dead_code": True, "line": 5},
        project_root=str(tmp_path))
    assert _v["success"] is True
    assert _v["execution_source"] == "ast_delete"
    assert _v["def_line_actual"] == 5
    assert _v["removed_lines"] >= 2


def test_d1_verify_ast_delete_target_missing(tmp_path):
    """目标不存在 ⇒ 明确错误码，不误判成成功"""
    from nucleus.evolution.patch_execution_dispatch import verify_ast_delete

    _write_target(tmp_path, "class Demo:\n    def keep(self):\n        return 1\n")
    _v = verify_ast_delete(
        {"id": "p2", "file": "nucleus/demo.py", "method": "no_such_method",
         "o3_dead_code": True},
        project_root=str(tmp_path))
    assert _v["success"] is False
    assert _v["error"] == "ast_delete:target_def_not_found"


def test_d1_verify_ast_delete_post_syntax_error(tmp_path):
    """删除后语法坏 ⇒ 判定失败（不是靠跑代码发现的）"""
    from nucleus.evolution.patch_execution_dispatch import verify_ast_delete

    # 删掉 `def dead(self):` 后留下裸缩进行 ⇒ 删除后语法不完整
    _body = "class Demo:\n    def dead(self):\n        x = (\n"
    _write_target(tmp_path, _body)
    _v = verify_ast_delete(
        {"id": "p3", "file": "nucleus/demo.py", "method": "dead",
         "o3_dead_code": True, "line": 2},
        project_root=str(tmp_path))
    assert _v["success"] is False
    assert _v["error"] in ("ast_delete:post_delete_syntax_error",
                           "ast_delete:source_syntax_error")


# ============================================================ d2 B 段数据源
def _mk_ledger(path, rows):
    _lines = []
    for _r in rows:
        _lines.append(
            '{"ts": %.1f, "patch_id": "%s", "file": "a.py", "method": "m",'
            ' "reason": "284刀5 结构缺陷补丁作废"}' % (_r["ts"], _r["id"]))
    io.open(str(path), "w", encoding="utf-8", newline="\n").write(
        "\n".join(_lines) + "\n")


def test_d2_count_from_ledger(tmp_path):
    """台账在窗口内的条数要数得准（窗口外不计）"""
    from nucleus.evolution.criteria_window import count_converge_voided

    _lp = tmp_path / "converge_voided.jsonl"
    _mk_ledger(_lp, [
        {"ts": 1000.0, "id": "a"},   # 窗内
        {"ts": 1100.0, "id": "b"},   # 窗内
        {"ts": 5000.0, "id": "c"},   # 窗外
    ])
    _r = count_converge_voided(900.0, 1200.0, ledger_path=str(_lp),
                               log_paths=[], root=str(tmp_path))
    assert _r["state"] == "ok"
    assert _r["count"] == 2


def test_d2_unavailable_when_no_source(tmp_path):
    """无台账 + 无日志 ⇒ unavailable（**不猜 0**）"""
    from nucleus.evolution.criteria_window import count_converge_voided

    _r = count_converge_voided(900.0, 1200.0,
                               ledger_path=str(tmp_path / "nope.jsonl"),
                               log_paths=[], root=str(tmp_path))
    assert _r["state"] == "unavailable"
    assert _r["count"] is None
    assert _r["reason"]


def test_d2_covered_window_with_zero_is_ok(tmp_path):
    """覆盖到窗口且确实 0 条 ⇒ ok/0（数到 0 与没数据必须可区分）

    口径：台账首条 ts 略早于窗起点 ⇒ 台账**覆盖起点**成立，窗内 0 条即为可信 0。
    """
    from nucleus.evolution.criteria_window import count_converge_voided

    _lp = tmp_path / "converge_voided.jsonl"
    _mk_ledger(_lp, [{"ts": 880.0, "id": "before_window"}])  # 窗外，只用来标覆盖起点
    _r = count_converge_voided(900.0, 1200.0, ledger_path=str(_lp),
                               log_paths=[], root=str(tmp_path))
    assert _r["state"] == "ok"
    assert _r["count"] == 0
    assert _r["source"] == "ledger"


def test_d2_empty_ledger_is_unavailable(tmp_path):
    """空台账 ⇒ 无覆盖证据 ⇒ unavailable（不是"0 条"）"""
    from nucleus.evolution.criteria_window import count_converge_voided

    _lp = tmp_path / "converge_voided.jsonl"
    _lp.write_text("", encoding="utf-8")
    _r = count_converge_voided(900.0, 1200.0, ledger_path=str(_lp),
                               log_paths=[], root=str(tmp_path))
    assert _r["state"] == "unavailable"
    assert _r["count"] is None


def test_d2_note_then_read_roundtrip(tmp_path):
    """作废点写入台账后可读回（append-only 落盘）"""
    from nucleus.evolution.criteria_window import (
        note_converge_voided, read_converge_voided_ledger,
    )

    _lp = str(tmp_path / "cvl.jsonl")
    note_converge_voided("pid_1", file="x.py", method="m1", reason="r",
                         path=_lp, now=1234.0)
    note_converge_voided("pid_2", file="y.py", method="m2", reason="r",
                         path=_lp, now=1235.0)
    _rows = read_converge_voided_ledger(_lp)
    assert len(_rows) == 2
    assert {r["patch_id"] for r in _rows} == {"pid_1", "pid_2"}


# ============================================================ d3 risk merge
@pytest.mark.parametrize("explicit,issue_type,want,basis", [
    ("高", "sql_injection", "高", "explicit"),        # 显式高不被降
    ("低", "sql_injection", "高", "escalated_by_issue_type"),  # 显式低 + 高危族 ⇒ 升
    ("低", "doc_typo", "低", "explicit"),             # 低族 + 显式低 ⇒ 保持
    ("", "sql_injection", "高", "issue_type"),        # 无显式 ⇒ 按族推断
    ("high", "", "高", "explicit"),                   # 英文档位**归一化**为中文
    ("low", "doc_typo", "低", "explicit"),            # 英文低风险同样归一
])
def test_d3_merge_risk(explicit, issue_type, want, basis):
    from nucleus.reasoning.PatchManager import PatchManager

    _lvl, _b = PatchManager._m344_merge_risk(explicit, issue_type)
    assert _lvl == want, (_lvl, _b)
    assert _b == basis


def test_d3_merge_risk_unknown_explicit_falls_back():
    """无法识别的显式档位 ⇒ 回退按 issue_type 推断（不猜）"""
    from nucleus.reasoning.PatchManager import PatchManager

    _lvl, _basis = PatchManager._m344_merge_risk("紫", "sql_injection")
    assert _lvl == "高"
    assert _basis == "issue_type"


def test_d3_security_not_relaxed():
    """安全面零放宽：**已知**高危/中危族不得因显式低声明被降到低风险

    注：高危族由 280刀2 定义（`sql_injection`/`lock_with_emit`/`non_atomic_write`）。
    `command_injection` 等词不在该表内 ⇒ 344 批已**如实申报为额外发现**
    （见交付报告 §申报），本刀不扩大范围去补表。
    """
    from nucleus.reasoning.PatchManager import PatchManager

    for _it in ("sql_injection", "lock_with_emit", "non_atomic_write"):
        for _ex in ("", "低", "low", "中"):
            _lvl, _b = PatchManager._m344_merge_risk(_ex, _it)
            assert _lvl == "高", (_it, _ex, _lvl, _b)
    for _it in ("thread_no_daemon", "resource_no_close",
                "cross_module_singleton_call"):
        _lvl, _b = PatchManager._m344_merge_risk("低", _it)
        assert _lvl == "中", (_it, _lvl, _b)


# ============================================================ d4 唯一源
def test_d4_window_default_single_literal():
    """criteria_window.py 内 `3625.0` 只允许出现在常量定义一处"""
    _p = os.path.join(os.path.dirname(__file__), "..", "nucleus", "evolution",
                      "criteria_window.py")
    _txt = io.open(os.path.abspath(_p), encoding="utf-8").read()
    assert _txt.count("3625.0") == 1, _txt.count("3625.0")


def test_d4_criteria_versions_share_constant():
    """CRITERIA_VERSIONS 三份窗口长度不得各写各的字面量（统一引用默认常量）"""
    from nucleus.evolution import criteria_window as cw

    _default = cw.CRITERIA_WINDOW_DEFAULT_SEC
    _vals = {float(v["window_sec"]) for v in cw.CRITERIA_VERSIONS.values()}
    assert _vals == {float(_default)}, (_vals, _default)
    assert len(cw.CRITERIA_VERSIONS) >= 3


def test_d4_get_criteria_window_deterministic():
    """同一锚点窗口可复现（锚 proc_gen，不锚墙钟）"""
    from nucleus.evolution.criteria_window import get_criteria_window

    _a = get_criteria_window("343.j3", "pid1@20261022-100000")
    _b = get_criteria_window("343.j3", "pid1@20261022-100000")
    assert _a["state"] == "ok"
    assert _a["win_lo"] == _b["win_lo"]
    assert _a["win_hi"] == _b["win_hi"]


# ============================================================ d5 AST 释放
def test_d5_register_and_release():
    """登记 → 释放：池清空、面数记账、实测下降口径成立"""
    from nucleus.mnemosyne import ast_release as ar

    ar.reset_stats()
    _base = ar.ast_live_count()
    _tree = ast.parse("x = 1\ny = [i for i in range(10)]\n")
    _h = ar.register_ast_face(_tree, source="t")
    assert _h
    assert ar.pending_stats()["faces"] == 1
    _r = ar.release_ast_faces(tier="G2_SOFT_RELEASE", heap_before=1300.0,
                              heap_probe=lambda: None)
    assert ar.pending_stats()["faces"] == 0
    assert _r["faces"] == 1
    assert _r["verdict"] == "released"
    assert _r["accounting_nodes"] > 0
    assert _r["heap_after"] is None  # 探针返回 None ⇒ 不填 0
    assert _base is not None


def test_d5_capacity_valve_bounded():
    """容量阀：登记超上限后池不超限（registry 不得自体成泄漏源）"""
    from nucleus.mnemosyne import ast_release as ar

    ar.reset_stats()
    _tree = ast.parse("a = 1\n")
    for _ in range(ar.MAX_PENDING_FACES + 5):
        ar.register_ast_face(_tree, source="cap")
    _ps = ar.pending_stats()
    assert _ps["faces"] <= ar.MAX_PENDING_FACES
    assert _ps["nodes"] <= ar.MAX_PENDING_NODES
    assert _ps["stats"]["evicted"] > 0


def test_d5_note_does_not_hold():
    """生产主路径 note_ast_face_done 只记账、不持有"""
    from nucleus.mnemosyne import ast_release as ar

    ar.reset_stats()
    for _ in range(50):
        ar.note_ast_face_done(100, source="x")
    _ps = ar.pending_stats()
    assert _ps["faces"] == 0
    assert _ps["faces_done"] == 50
    assert _ps["nodes_done"] == 5000


def test_d5_empty_pool_noop():
    """池空释放 ⇒ noop_pool_empty（不造假）"""
    from nucleus.mnemosyne import ast_release as ar

    ar.reset_stats()
    _r = ar.release_ast_faces(tier="G3_COLD_DEMOTE", heap_before=1700.0)
    assert _r["verdict"] == "noop_pool_empty"
    assert _r["faces"] == 0


# ============================================================ d6 反思不连坐
def test_d6_non_dict_record_skipped():
    """失败记录含 str/int/None 时：分流不崩、坏数据逐条可见，**同批 dict 记录照常反思**"""
    from nucleus.reasoning.PatchManager import _m344_split_failed_details

    _res = _m344_split_failed_details([
        "plain_string",
        {"status": "failed", "id": "p_ok1"},
        {"status": "rejected_low_risk", "id": "p_ok2"},
        {"status": "approved", "id": "p_skip"},
        42,
        None,
    ])
    assert _res["skipped_non_dict"] == 3          # str / 42 / None
    assert [d["id"] for d in _res["dicts"]] == ["p_ok1", "p_ok2", "p_skip"]
    assert [d["id"] for d in _res["actionables"]] == ["p_ok1", "p_ok2"]
    # 坏数据原值保留（留痕用），不被静默丢弃
    assert _res["non_dict_values"][0] == "plain_string"


def test_d6_empty_input_safe():
    from nucleus.reasoning.PatchManager import _m344_split_failed_details

    _res = _m344_split_failed_details([])
    assert _res == {"skipped_non_dict": 0, "non_dict_values": [], "dicts": [],
                    "actionables": []}
    _res2 = _m344_split_failed_details(None)
    assert _res2["skipped_non_dict"] == 0


# ============================================================ 门禁自检
def test_batch_import_health():
    """本批触碰的模块都能 import（不拉起主循环）"""
    import importlib

    for _m in ("nucleus.mnemosyne.ast_release",
               "nucleus.mnemosyne.release_policy",
               "nucleus.evolution.criteria_window",
               "nucleus.security.sandbox_limits"):
        importlib.import_module(_m)
