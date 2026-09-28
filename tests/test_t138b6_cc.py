# -*- coding: utf-8 -*-
"""第138批 T-138a：self_inspector 圈复杂度（CC）检测器单元测。

覆盖：
  B6 cc_growth：fixture 正控（cc=7 分支链）/ 直线函数 cc=1 / 首扫建基线=0告警 /
               新增 >=50 高复杂度块告警 / 键统一 '/' 分隔
"""
import json
import os

from nucleus.self_inspector import SelfInspector
from nucleus.const import SELF_INSPECTOR_B6_OBS_MIN, SELF_INSPECTOR_B6_GATE_MIN


def _make_inspector(root, boot_ts=0.0):
    ins = SelfInspector()
    ins._project_root = root
    ins._boot_ts = boot_ts  # 默认绕过 boot 静默
    return ins


# cc=7 的正控样例：6 个 if 分支链 => 圈复杂度 7
_CC7_SRC = (
    "def branchy(x):\n"
    "    if x > 1:\n"
    "        a = 1\n"
    "    if x > 2:\n"
    "        a = 2\n"
    "    if x > 3:\n"
    "        a = 3\n"
    "    if x > 4:\n"
    "        a = 4\n"
    "    if x > 5:\n"
    "        a = 5\n"
    "    if x > 6:\n"
    "        a = 6\n"
    "    return a\n"
)


def _make_high_cc_src(n):
    """生成 cc=n 的直线 if 链函数。"""
    _lines = ["def heavy(x):", "    a = 0"]
    for i in range(n - 1):
        _lines.append(f"    if x > {i}:")
        _lines.append(f"        a += {i}")
    _lines.append("    return a")
    return "\n".join(_lines) + "\n"


def test_b6_cc7_positive_control(tmp_path):
    """正控：cc=7 的 6 分支 if 链 -> cc_visit complexity==7。"""
    ins = _make_inspector(str(tmp_path))
    blocks = ins._si_cc_blocks(_CC7_SRC)
    assert len(blocks) == 1, f"应只取 Function 块，实际 {blocks}"
    name, cc, lineno = blocks[0]
    assert name == "branchy"
    assert cc == 7, f"6 个 if 分支链应为 cc=7，实际 {cc}"


def test_b6_straight_line_cc1(tmp_path):
    """直线函数 cc=1。"""
    ins = _make_inspector(str(tmp_path))
    blocks = ins._si_cc_blocks("def flat():\n    return 1\n")
    assert len(blocks) == 1
    assert blocks[0][1] == 1, f"直线函数应为 cc=1，实际 {blocks[0][1]}"


def test_b6_class_block_filtered(tmp_path):
    """radon 也返回 Class 级块 -> 只取 Function/Method。"""
    ins = _make_inspector(str(tmp_path))
    src = "class C:\n    def m(self):\n        return 1\n"
    blocks = ins._si_cc_blocks(src)
    names = [b[0] for b in blocks]
    assert "C" not in names, f"Class 级块应被剔除，实际 {names}"
    assert "m" in names, f"方法 m 应保留，实际 {names}"


def test_b6_first_scan_zero_and_baseline(tmp_path):
    """首扫：写基线 data/cc_baseline.json，告警=0。"""
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "mod.py").write_text(_CC7_SRC, encoding="utf-8")
    assert ins._detect_cc_growth() == [], "首扫应只记基线不告警"
    base = os.path.join(str(tmp_path), "data", "cc_baseline.json")
    assert os.path.exists(base), "首扫应建 cc 基线"
    data = json.load(open(base, encoding="utf-8"))
    assert "per_file" in data and "blocks_ge50" in data
    assert "mod.py" in data["per_file"], "per_file 应含 mod.py（键 / 分隔）"


def test_b6_baseline_key_uses_slash(tmp_path):
    """基线键统一 '/' 分隔（跨机可移植）。"""
    ins = _make_inspector(str(tmp_path))
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text(_CC7_SRC, encoding="utf-8")
    ins._detect_cc_growth()
    base = os.path.join(str(tmp_path), "data", "cc_baseline.json")
    data = json.load(open(base, encoding="utf-8"))
    assert "pkg/mod.py" in data["per_file"], f"键应为 pkg/mod.py，实际 {list(data['per_file'])}"


def test_b6_observes_ge25(tmp_path):
    """观察账：ge25 阈值计数正确（cc=25 -> ge25>=1，ge50==0）。"""
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "mod.py").write_text(_make_high_cc_src(SELF_INSPECTOR_B6_OBS_MIN),
                                     encoding="utf-8")
    ins._detect_cc_growth()
    base = os.path.join(str(tmp_path), "data", "cc_baseline.json")
    data = json.load(open(base, encoding="utf-8"))
    pf = data["per_file"]["mod.py"]
    assert pf["ge25"] >= 1, f"cc={SELF_INSPECTOR_B6_OBS_MIN} 应计入 ge25，实际 {pf}"
    assert pf["ge50"] == 0, f"cc={SELF_INSPECTOR_B6_OBS_MIN} 不应计入 ge50，实际 {pf}"


def test_b6_alert_on_new_ge50_block(tmp_path):
    """棘轮：新增 >=50 高复杂度块应告警。"""
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "mod.py").write_text("def flat():\n    return 1\n", encoding="utf-8")
    ins._detect_cc_growth()  # 首扫：blocks_ge50 空
    (tmp_path / "mod2.py").write_text(_make_high_cc_src(SELF_INSPECTOR_B6_GATE_MIN),
                                      encoding="utf-8")
    issues = ins._detect_cc_growth()
    assert any(i["type"] == "cc_ge50_new" for i in issues), "新增 cc>=50 块应告警"
    assert any("mod2.py" in i["file"] for i in issues), "告警 file 应指向 mod2.py"


def test_b6_ratchet_no_repeat_alert(tmp_path):
    """棘轮幂等：同一高复杂度块二次扫描不再告警（只降不升）。"""
    ins = _make_inspector(str(tmp_path))
    (tmp_path / "mod.py").write_text(_make_high_cc_src(SELF_INSPECTOR_B6_GATE_MIN),
                                     encoding="utf-8")
    first = ins._detect_cc_growth()  # 首扫记基线
    assert first == []
    second = ins._detect_cc_growth()  # 已入基线 -> 不重复报
    assert second == [], f"同一块不应重复告警，实际 {second}"


def test_b6_registered_in_global_detectors():
    """已注册进 GLOBAL_DETECTORS。"""
    assert SelfInspector.GLOBAL_DETECTORS.get("cc_growth") == "_detect_cc_growth"
