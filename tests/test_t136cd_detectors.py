# -*- coding: utf-8 -*-
"""往期批次 相关任务/d：self_inspector 循环依赖 + 不可达代码检测器单元测。

覆盖：
  C1 import_cycles：首扫建基线=0告警；新增环告警；Tarjan 能检出假循环
  D1 unreachable_code：首扫建基线=0告警；新增死语句告警；return/raise 后死语句能检出
"""
import ast
import os

from nucleus.self_inspector import SelfInspector


def _make_inspector(root, boot_ts=0.0):
    ins = SelfInspector()
    ins._project_root = root
    ins._boot_ts = boot_ts  # 默认绕过 boot 静默
    return ins


# ============ C1 import cycles ============

def test_c1_find_detects_fake_cycle(tmp_path):
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    (tmp_path / "c.py").write_text("import b\nimport a\n", encoding="utf-8")
    cycles = ins._find_import_cycles()
    assert any(set(c) == {"a", "b"} for c in cycles), f"应检出 a↔b 环，实际 {cycles}"


def test_c1_first_scan_zero_and_baseline(tmp_path):
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    assert ins._detect_import_cycles() == []
    base = os.path.join(str(tmp_path), "data", "import_cycles_baseline.json")
    assert os.path.exists(base), "首扫应建 import_cycles 基线"
    import json
    data = json.load(open(base, encoding="utf-8"))
    assert any(set(v) == {"a", "b"} for v in data.values()), "基线应含 a↔b 环"


def test_c1_alert_on_new_cycle(tmp_path):
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "a.py").write_text("import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    ins._detect_import_cycles()  # 首扫建基线 {a,b}
    (tmp_path / "c.py").write_text("import d\n", encoding="utf-8")
    (tmp_path / "d.py").write_text("import c\n", encoding="utf-8")
    issues = ins._detect_import_cycles()
    assert any("c" in i["description"] and "d" in i["description"]
               for i in issues), "新增 c↔d 环应告警"


# ---- ★往期批次 相关任务②：边层级分账 ----

def test_c1_function_level_cycle_not_flagged(tmp_path):
    """函数体内延迟 import 构成的「环」不算模块级环（运行期才执行）。"""
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "a.py").write_text(
        "def f():\n    import b\n    return b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text(
        "def g():\n    import a\n    return a\n", encoding="utf-8")
    cycles = ins._find_import_cycles()
    assert not any(set(c) == {"a", "b"} for c in cycles), \
        f"函数级 import 环不应计入模块级环，实际 {cycles}"


def test_c1_resolve_splits_module_and_function(tmp_path):
    """_si_resolve_import_mods 返回 (模块级, 函数级) 二元组。"""
    ins = _make_inspector(str(tmp_path))
    src = "import os\nfrom x import y\n\ndef f():\n    import z\n    from w import q\n"
    mods, fn_mods = ins._si_resolve_import_mods(src)
    assert "os" in mods and "x" in mods, f"模块级应含 os/x，实际 {mods}"
    assert "z" in fn_mods and "w" in fn_mods, f"函数级应含 z/w，实际 {fn_mods}"
    assert "z" not in mods and "w" not in mods, "函数级 import 不应进模块级表"


def test_c1_observation_ledger_records_function_edges(tmp_path):
    """函数级边写入观察账 _si_import_cycles_observed。"""
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "a.py").write_text(
        "def f():\n    import b\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("x = 1\n", encoding="utf-8")
    ins._find_import_cycles()
    obs = getattr(ins, "_si_import_cycles_observed", [])
    assert any(s == "a" and d == "b" for (s, d, _k) in obs), \
        f"应记录 a→b 函数级观察边，实际 {obs}"


# ============ D1 unreachable code ============

def test_d1_scan_block_flags_after_return(tmp_path):
    ins = _make_inspector(str(tmp_path))
    src = "def f():\n    return 1\n    print('dead')\n"
    tree = ast.parse(src)
    issues = []
    ins._si_scan_unreachable_block(tree.body, issues, "mod.py", "/root", "/root/mod.py")
    assert any(i["type"] == "dead_code_after_return" for i in issues), "应检出 return 后死语句"


def test_d1_first_scan_zero_and_baseline(tmp_path):
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "mod.py").write_text(
        "def f():\n    return 1\n    print('dead')\n", encoding="utf-8")
    assert ins._detect_unreachable_code() == []
    base = os.path.join(str(tmp_path), "data", "unreachable_code_baseline.json")
    assert os.path.exists(base), "首扫应建 unreachable_code 基线"
    import json
    data = json.load(open(base, encoding="utf-8"))
    assert len(data) >= 1, "首扫应把现有死语句记基线"


def test_d1_alert_on_new_dead(tmp_path):
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "mod.py").write_text(
        "def f():\n    return 1\n    print('dead')\n", encoding="utf-8")
    ins._detect_unreachable_code()  # 首扫建基线（mod.py 死语句）
    (tmp_path / "mod2.py").write_text(
        "def g():\n    raise ValueError\n    x = 1\n", encoding="utf-8")
    issues = ins._detect_unreachable_code()
    assert any("mod2.py" in i["file"] for i in issues), "新增不可达代码应告警"
