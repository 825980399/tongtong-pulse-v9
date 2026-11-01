# -*- coding: utf-8 -*-
"""第416批刀3 单测：O3 补盲 —— 「模块级函数名出现在值位」判据。

三类值传递场景（任务书点名）+ 两类反例（注释 / 定义处不算引用）
+ judge_entry 的拒因与向后兼容（老 index 不带新字段 ⇒ 行为不变）。
"""
import os

import nucleus.evolution.o3_delete_dryrun as dd
from nucleus.self_awareness.CallGraphAnalyzer import CallGraphAnalyzer

_SRC = '''
# my_func_in_comment 只在注释里出现，不算引用
"""my_func_in_docstring 只在模块 docstring 里出现，不算引用"""


def f_dict_name():
    return 1


def f_dict_str():
    return 2


def f_assign_rhs():
    return 3


def f_import():
    return 4


def f_def_only():
    return 5


def f_attr_only():
    return 6


def f_nested():
    return 7


TABLE_NAME = {"k": f_dict_name}
TABLE_STR = {"k": "f_dict_str"}
_picked = f_assign_rhs
_HANDLER = {"attr": "f_attr_only"}


def build():
    def f_nested():          # 方法内嵌套 def —— 不是模块级函数，不该被保护
        return 0
    return f_nested
'''


def _analyzer(tmp_path):
    """不跑 __init__（那要去建整张图），只挂本方法要用的三个属性。"""
    a = CallGraphAnalyzer.__new__(CallGraphAnalyzer)
    a.project_root = str(tmp_path)
    a.scan_dirs = ["."]
    a._m416_namevalref_cache = None
    return a


def test_three_value_positions_detected(tmp_path):
    (tmp_path / "sample.py").write_text(_SRC, encoding="utf-8")
    names = _analyzer(tmp_path)._m416_name_value_ref_names()
    # 源1 dict value（裸 Name）
    assert "f_dict_name" in names
    # 源1 dict value（字符串常量）
    assert "f_dict_str" in names
    # 源2 Assign 右值裸 Name
    assert "f_assign_rhs" in names
    # 源1 dict value 字符串（.getattr 那档的同形）
    assert "f_attr_only" in names


def test_import_binding_detected(tmp_path):
    (tmp_path / "importer.py").write_text(
        "from nucleus.evolution import f_import\n", encoding="utf-8")
    names = _analyzer(tmp_path)._m416_name_value_ref_names()
    assert "f_import" in names


def test_import_asname_detected(tmp_path):
    (tmp_path / "importer2.py").write_text(
        "from x.y import zz as f_alias\n", encoding="utf-8")
    names = _analyzer(tmp_path)._m416_name_value_ref_names()
    assert "f_alias" in names


def test_comment_and_docstring_not_counted(tmp_path):
    (tmp_path / "sample.py").write_text(_SRC, encoding="utf-8")
    names = _analyzer(tmp_path)._m416_name_value_ref_names()
    assert "my_func_in_comment" not in names
    assert "my_func_in_docstring" not in names


def test_definition_site_not_counted(tmp_path):
    (tmp_path / "sample.py").write_text(_SRC, encoding="utf-8")
    names = _analyzer(tmp_path)._m416_name_value_ref_names()
    # `def f_def_only():` 的 `f_def_only` 不是 ast.Name 节点
    assert "f_def_only" not in names


def test_cache_is_reused(tmp_path):
    (tmp_path / "sample.py").write_text(_SRC, encoding="utf-8")
    a = _analyzer(tmp_path)
    first = a._m416_name_value_ref_names()
    assert a._m416_namevalref_cache is not None
    # 走到缓存分支后返回同一个对象
    assert a._m416_name_value_ref_names() is first


def test_empty_project_returns_empty(tmp_path):
    a = _analyzer(tmp_path)
    assert a._m416_name_value_ref_names() == frozenset()


# --------------------------------------------------------------- judge_entry

def _idx(**row_over):
    row = {"public_api": False, "entry_like": False, "test_ref": False,
           "value_ref": False, "getattr_ref": False}
    row.update(row_over)
    return {"state": "ok", "files": {"a.py"}, "orphans": {("a.py", "f"): row}}


def _entry(**over):
    e = {"file": "a.py", "method": "f", "issue_type": "dead_code",
         "status": "open"}
    e.update(over)
    return e


def test_value_ref_name_rejects():
    j = dd.judge_entry(_entry(), _idx(value_ref_name=True))
    assert j["eligible"] is False
    assert any(r.startswith("value_ref_name") for r in j["reasons"])
    assert j["checks"]["value_ref_name"] is True


def test_value_ref_name_absent_is_backward_compatible():
    """老 index（不带 value_ref_name 字段）⇒ 判定结果与 415 完全一致。"""
    j = dd.judge_entry(_entry(), _idx())
    assert j["eligible"] is True
    assert "value_ref_name" in j["checks"]
    assert j["checks"]["value_ref_name"] is False


def test_ci_locked_rejects():
    idx = _idx()
    idx["ci_locked"] = {("a.py", "f")}
    j = dd.judge_entry(_entry(), idx)
    assert j["eligible"] is False
    assert any(r.startswith("ci_locked") for r in j["reasons"])


def test_ci_locked_absent_does_not_reject():
    j = dd.judge_entry(_entry(), _idx())
    assert not any(r.startswith("ci_locked") for r in j["reasons"])
    assert "ci_locked" not in j["checks"]


def test_ci_locked_other_file_does_not_reject():
    idx = _idx()
    idx["ci_locked"] = {("b.py", "f")}
    j = dd.judge_entry(_entry(), idx)
    assert j["eligible"] is True


def test_version_prefix_unchanged():
    """412 单测钉的是 "412." 前缀 —— 不许改成 416.x。"""
    assert dd.version().startswith("412.")


def test_real_repo_finds_names():
    """真实仓上跑一遍：必须能取到一批名字（防「恒空 = 假绿」）。"""
    a = CallGraphAnalyzer.__new__(CallGraphAnalyzer)
    a.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    a.scan_dirs = ["organs", "nucleus", "functions", "base", "utils"]
    a._m416_namevalref_cache = None
    names = a._m416_name_value_ref_names()
    assert len(names) > 500, "真实仓只取到 %d 个名字，明显不对" % len(names)
    # 415 实测的三条假死物证，必须都在
    assert "ch_entity" in names or "get_llm_evolution_engine" in names
