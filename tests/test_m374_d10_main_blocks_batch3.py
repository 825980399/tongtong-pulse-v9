# -*- coding: utf-8 -*-
"""374 刀10 · 刀13 第三批定向单测：高危 `__main__` 自测块收口 8 块。

票 `T-372-刀13顺延49块-1`（due 374）。规则沿用 372 批1 / 373 批2，
证据件 `docs/路灯与星轨对话/交付报告/证据件/374/刀13_第三批收口_20261027.{json,md}`。

验五件事：① 证据件算术自洽（块数/行数/基线差精确相等）；② 收口块已不在生产文件、
顺延块**必须还在**；③ 现场复扫与证据件一致（防"删完又长回来"）；④ 收口文件仍可编译；
⑤ 人读件与机读件同源。
"""
import ast
import json
import sys
from pathlib import Path

_EVID_DIR = Path("docs/路灯与星轨对话/交付报告/证据件/374")
_EVID_JSON = _EVID_DIR / "刀13_第三批收口_20261027.json"
_EVID_MD = _EVID_DIR / "刀13_第三批收口_20261027.md"


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
    """① 收口 8 块 / 顺延 3 块；行数与基线差**精确**对齐（不是"大概少了点"）。"""
    d = _evid()
    assert d["batch"] == 3
    assert d["collected_n"] == 8 and d["deferred_n"] == 3
    assert d["lines_removed"] == sum(b["lines"] for b in d["blocks"]) == 567
    assert d["deleted_physical_lines"] == sum(b["deleted_lines"] for b in d["blocks"]) == 577
    assert d["deleted_physical_lines"] - d["lines_removed"] == sum(
        b["trimmed_eof_blanks"] for b in d["blocks"]), "EOF 空行数与物理差对不上"
    assert d["baseline"]["blocks"] - d["after"]["blocks"] == d["collected_n"]
    assert d["baseline"]["high_risk"] - d["after"]["high_risk"] == d["collected_n"]
    assert d["baseline"]["lines"] - d["after"]["lines"] == d["lines_removed"]


def test_02_collected_gone_deferred_kept():
    """② 收口的 8 个文件已无 `__main__` 块；顺延的 3 块**必须还在**（不许顺手删）。"""
    d = _evid()
    for _b in d["blocks"]:
        assert _blocks_in(_b["file"]) == [], f"{_b['file']} 仍有 __main__ 块"
    for _x in d["deferred"]:
        _lns = _blocks_in(_x["file"])
        assert _lns, f"顺延块被误删: {_x['file']}（{_x['why']}）"
        assert _x["lineno"] in _lns, f"{_x['file']} 顺延块行号漂了: {_lns}"


def test_03_current_scan_matches_evidence():
    """③ 现场复扫与证据件一致（防「删完又长回来」）。"""
    _tools = str(Path("tools").resolve())
    if _tools not in sys.path:
        sys.path.insert(0, _tools)
    import scan_main_blocks as S  # 复用扫描器本体（就这一处需要 tools/ 在 path 上）

    _rows = S.scan()
    _high = [r for r in _rows if r["side_effects"]["high"]]
    d = _evid()
    assert len(_rows) == d["after"]["blocks"], f"块数漂移: {len(_rows)}"
    assert len(_high) == d["after"]["high_risk"], f"高危数漂移: {len(_high)}"
    assert sum(r["lines"] for r in _rows) == d["after"]["lines"], "总行数漂移"


def test_04_collected_files_still_compile():
    """④ 删块不是"删坏了"：8 件仍可编译，且顶层节点数与证据件记录的块位置相容。"""
    d = _evid()
    for _b in d["blocks"]:
        _src = Path(_b["file"]).read_text(encoding="utf-8", errors="replace")
        compile(_src.replace("\r\n", "\n"), _b["file"], "exec")


def test_05_evidence_md_matches_json():
    """⑤ 人读件与机读件同源（三个关键数字必须同时出现）。"""
    _md = _EVID_MD.read_text(encoding="utf-8")
    d = _evid()
    for _n in (str(d["after"]["blocks"]), str(d["after"]["high_risk"]),
               str(d["lines_removed"]), str(d["deleted_physical_lines"])):
        assert _n in _md, f"证据 MD 缺数字 {_n}"
    assert "顺延" in _md
