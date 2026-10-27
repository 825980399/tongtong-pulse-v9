# -*- coding: utf-8 -*-
"""373 顺带 · 刀13 第二批定向单测：高危 `__main__` 块收口 8 块（票 `T-372-刀13顺延49块-1`）

规则沿用 372 刀13 批1（`b13` 笔），见证据件
`docs/路灯与星轨对话/交付报告/证据件/373/刀13_第二批收口_20261027.md`。
"""

import ast
import json
import sys
from pathlib import Path

_EVID_DIR = Path("docs/路灯与星轨对话/交付报告/证据件/373")
_EVID_JSON = _EVID_DIR / "刀13_第二批收口_20261027.json"
_EVID_MD = _EVID_DIR / "刀13_第二批收口_20261027.md"


def _blocks_in(fp):
    """返回该文件里模块级 `__main__` 块的行号列表。"""
    _t = Path(fp).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    _out = []
    for _node in ast.parse(_t).body:
        if not isinstance(_node, ast.If) or not isinstance(_node.test, ast.Compare):
            continue
        _l = _node.test.left
        _cmp = _node.test.comparators
        if (isinstance(_l, ast.Name) and _l.id == "__name__" and _cmp
                and isinstance(_cmp[0], ast.Constant) and _cmp[0].value == "__main__"):
            _out.append(_node.lineno)
    return _out


def _evid():
    return json.loads(_EVID_JSON.read_text(encoding="utf-8"))


def test_01_evidence_shape_and_arithmetic():
    """① 证据件计数自洽：收口 8 块、顺延 2 块，行数与块行数之和精确对齐。"""
    d = _evid()
    assert d["collected_n"] == 8 and d["deferred_n"] == 2
    assert d["lines_removed"] == sum(b["lines"] for b in d["blocks"]) == 648
    # 基线 → 收口后 的差值必须**等于**收口块数（不是"大概少了点"）
    assert d["baseline"]["blocks"] - d["after"]["blocks"] == d["collected_n"]
    assert d["baseline"]["high_risk"] - d["after"]["high_risk"] == d["collected_n"]
    assert d["baseline"]["lines"] - d["after"]["lines"] == d["lines_removed"]


def test_02_collected_blocks_gone_deferred_kept():
    """② 收口的 8 个文件已无 `__main__` 块；顺延的 2 块**必须还在**（不许顺手删）。"""
    d = _evid()
    for _b in d["blocks"]:
        assert _blocks_in(_b["file"]) == [], f"{_b['file']} 仍有 __main__ 块"
    for _x in d["deferred"]:
        assert _blocks_in(_x["file"]), f"顺延块被误删: {_x['file']}（{_x['why']}）"


def test_03_current_scan_matches_evidence():
    """③ 现场复扫与证据件一致（防「删完又长回来」）。"""
    sys.path.insert(0, str(Path("tools")))
    import scan_main_blocks as S  # 复用扫描器本体（就这一处需要 tools/ 在 path 上）

    _rows = S.scan()
    _high = [r for r in _rows if r["side_effects"]["high"]]
    d = _evid()
    assert len(_rows) == d["after"]["blocks"], f"块数漂移: {len(_rows)}"
    assert len(_high) == d["after"]["high_risk"], f"高危数漂移: {len(_high)}"
    assert sum(r["lines"] for r in _rows) == d["after"]["lines"], "总行数漂移"


def test_04_f401_side_effect_handled():
    """④ 删块的真实副作用：`KnowledgeEvent` 已从导入行移除，且全仓无该符号的外部引用。"""
    _src = Path("organs/body/PulseBloodVessel.py").read_text(encoding="utf-8")
    assert "KnowledgeEvent" not in _src, "未使用导入未清理（ruff F401）"
    _importers = ("main.py", "nucleus/self_awareness/SelfAwarenessEngine.py",
                  "tests/test_m197_blade3_lfi_runtime.py",
                  "tools/lfi_self_awareness_compare.py")
    for _fp in _importers:
        _t = Path(_fp).read_text(encoding="utf-8", errors="replace")
        assert "PulseBloodVessel import KnowledgeEvent" not in _t, f"{_fp} 仍在导入该符号"


def test_05_evidence_md_matches_json():
    """⑤ 人读证据件与机读 JSON 同源（行数/块数三个数字必须同时出现）。"""
    _md = _EVID_MD.read_text(encoding="utf-8")
    d = _evid()
    assert "648" in _md and "93" in _md and "41" in _md
    for _n in (str(d["after"]["blocks"]), str(d["after"]["high_risk"]),
               str(d["lines_removed"])):
        assert _n in _md, f"证据 MD 缺数字 {_n}"
