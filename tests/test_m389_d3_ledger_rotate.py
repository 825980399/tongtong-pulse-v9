# -*- coding: utf-8 -*-
"""389刀3：DL3 决策账本轮转（软上限 → 归档前段 + 原子保留后段）。

票面前提核实（T0）
==================
`LEDGER_SOFT_MAX_LINES = 200000` 在 385刀2 就写下了，但**只是常量**：
全仓没有一行轮转代码，注释还写着"不自动切"。所以票面要求的三件事
（轮转/验证/单测）在施工前**一件都没做** —— 本刀补实现。

审计约束（不能因为轮转就丢旧行）
================================
洞鉴 §6.4：账本 append-only、**不删旧行**。所以轮转把**前段移进归档文件**
`dec_<epoch>.jsonl.rot-<ts>`，活跃文件只保留最近 `LEDGER_KEEP_LINES` 行。
`find_by_decision_id` / `count_rows` 默认只读活跃文件 —— 查历史要显式给归档路径。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config as _cfg  # noqa: E402
from nucleus.decision import ledger as L  # noqa: E402
from nucleus.decision import shadow as S  # noqa: E402

SRC = os.path.join(ROOT, "nucleus", "decision", "ledger.py")


@pytest.fixture(autouse=True)
def _reset_state():
    L._LINES_BY_PATH.clear()
    S.reset_stats()
    yield
    L._LINES_BY_PATH.clear()
    S.reset_stats()


def _write_rows(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as _f:
        for _i in range(n):
            _f.write('{"result": {"decision_id": "dec_%d"}}\n' % _i)


def _read(path):
    with open(str(path), "r", encoding="utf-8") as _f:
        return [x for x in _f.read().splitlines() if x]


def test_m389_d3_no_rotate_under_soft_max(tmp_path):
    _p = tmp_path / "dec_x.jsonl"
    _write_rows(_p, 5)
    _r = L.rotate_if_needed(str(_p), soft_max=10, keep=3, explicit=True)
    assert _r["rotated"] is False and _r["reason"] == "under_soft_max"
    assert len(_read(_p)) == 5
    assert L.archive_paths(str(_p)) == []


def test_m389_d3_rotate_keeps_tail_and_archives_head(tmp_path):
    """★核心：超上限 ⇒ 保留最近 keep 行，前段进归档，**一行不丢**。"""
    _p = tmp_path / "dec_x.jsonl"
    _write_rows(_p, 12)
    _orig = _read(_p)

    _r = L.rotate_if_needed(str(_p), soft_max=10, keep=4, explicit=True)
    assert _r["rotated"] is True, _r
    assert _r["lines"] == 12 and _r["archived"] == 8 and _r["kept"] == 4

    _tail = _read(_p)
    assert _tail == _orig[-4:], "活跃文件必须是最新 4 行且顺序不变"
    _arc = L.archive_paths(str(_p))
    assert len(_arc) == 1, _arc
    assert _read(_arc[0]) == _orig[:8], "归档必须是前 8 行且顺序不变"
    assert _read(_arc[0]) + _tail == _orig, "归档+活跃 == 原文件（审计不丢行）"


def test_m389_d3_rotation_is_atomic_tmp_removed(tmp_path):
    _p = tmp_path / "dec_x.jsonl"
    _write_rows(_p, 8)
    L.rotate_if_needed(str(_p), soft_max=5, keep=2, explicit=True)
    assert not (tmp_path / "dec_x.jsonl.tmp").exists(), "临时文件必须被 os.replace 吃掉"


def test_m389_d3_denied_by_write_guard_leaves_file_intact(tmp_path, monkeypatch):
    """写盘守卫拒绝 ⇒ 不轮转、不改文件（**不许半途截断**）。"""
    _p = tmp_path / "dec_x.jsonl"
    _write_rows(_p, 9)
    _before = _read(_p)
    monkeypatch.setattr(L, "_guard", lambda *a, **k: False)
    _r = L.rotate_if_needed(str(_p), soft_max=5, keep=2, explicit=False)
    assert _r["rotated"] is False and _r["reason"] == "denied"
    assert _read(_p) == _before
    assert L.archive_paths(str(_p)) == []


def test_m389_d3_keep_out_of_range_is_reported(tmp_path):
    _p = tmp_path / "dec_x.jsonl"
    _write_rows(_p, 6)
    _r = L.rotate_if_needed(str(_p), soft_max=3, keep=6, explicit=True)
    assert _r["rotated"] is False and _r["reason"] == "keep_out_of_range"


def test_m389_d3_no_file_is_not_an_error(tmp_path):
    _r = L.rotate_if_needed(str(tmp_path / "nope.jsonl"), soft_max=1, keep=1)
    assert _r["rotated"] is False and _r["reason"] == "no_file"


def test_m389_d3_append_triggers_rotation(tmp_path, monkeypatch):
    """★核心：`append` 走软上限即自动轮转（不需要外部调用 rotate）。"""
    monkeypatch.setattr(_cfg, "ENABLE_DEC_SHADOW", True, raising=False)
    monkeypatch.setattr(L, "LEDGER_SOFT_MAX_LINES", 5, raising=False)
    monkeypatch.setattr(L, "LEDGER_KEEP_LINES", 2, raising=False)
    _p = tmp_path / "dec_auto.jsonl"

    for _i in range(7):
        _r = S.observe_patch(patch_id="o3_%d" % _i, file="a.py", status="pending",
                             path=str(_p), explicit=True)
        assert _r is not None and _r["status"] == "ok", _r

    _active = _read(_p)
    _arc = L.archive_paths(str(_p))
    assert _arc, "第 6 条 append 后应触发轮转"
    _total = _active + [x for _a in _arc for x in _read(_a)]
    assert len(_total) == 7, (len(_active), [len(_read(a)) for a in _arc])
    assert len(_active) <= 5, "轮转后活跃文件必须低于软上限"


def test_m389_d3_append_keeps_working_when_rotate_fails(tmp_path, monkeypatch):
    """轮转失败绝不影响本次写入（观测面不许反噬业务）。"""
    monkeypatch.setattr(_cfg, "ENABLE_DEC_SHADOW", True, raising=False)
    monkeypatch.setattr(L, "LEDGER_SOFT_MAX_LINES", 1, raising=False)
    monkeypatch.setattr(L, "LEDGER_KEEP_LINES", 1, raising=False)
    monkeypatch.setattr(L, "rotate_if_needed",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    _p = tmp_path / "dec_fail.jsonl"
    _r = S.observe_patch(patch_id="o3_x", file="a.py", path=str(_p), explicit=True)
    assert _r["status"] == "ok", "写入成功不该被轮转失败改判"
    assert len(_read(_p)) == 1


def test_m389_d3_soft_max_constants_are_the_ticket_values():
    """票面值：软上限 20 万行、保留 10 万行。"""
    assert L.LEDGER_SOFT_MAX_LINES == 200000
    assert L.LEDGER_KEEP_LINES == 100000


def test_m389_d3_line_count_handles_missing_trailing_newline(tmp_path):
    """行数口径：末行无换行符也要算一行（否则软上限会永远差 1）。"""
    _p = tmp_path / "dec_n.jsonl"
    _p.write_bytes(b'{"a":1}\n{"a":2}\n{"a":3}')
    assert L._count_lines_fast(str(_p)) == 3
    _p.write_bytes(b"")
    assert L._count_lines_fast(str(_p)) == 0
    _p.write_bytes(b'{"a":1}')
    assert L._count_lines_fast(str(_p)) == 1


def test_m389_d3_callsite_hooked_in_append():
    """★防回退：`append` 必须挂轮转收尾（否则永远不会触发）。"""
    _s = open(SRC, encoding="utf-8").read()
    assert "note_append_and_maybe_rotate(_p)" in _s
    _i = _s.index('    _SEEN_IDS.add(_did)')
    _before = _s[max(0, _i - 300):_i]
    assert "note_append_and_maybe_rotate(_p)" in _before, "必须在写成功后、入幂等集前"


def test_m389_d3_audit_semantics_documented():
    """理由留证：为什么"移进归档"而不是"删旧行"。"""
    _s = open(SRC, encoding="utf-8").read()
    assert "★389刀3" in _s
    _i = _s.index("def archive_paths")
    _blk = _s[_i:_i + 900]
    assert "不删旧行" in _blk or "归档" in _blk


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
