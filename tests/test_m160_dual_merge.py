# -*- coding: utf-8 -*-
"""160下下 刀2（T-双源合并-1）单元测试：_m160_merge_dual R1-R6 + _m160_load_source。

不依赖运行框架：以 FakeSnap 继承 PulseSnapshot，仅覆盖 _log，直接调用双源合并逻辑。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.mnemosyne.PulseNode import PulseNode
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot


class FakeSnap(PulseSnapshot):
    def __init__(self):
        self._logs = []

    def _log(self, lvl, msg, *a, **k):
        self._logs.append((str(lvl), str(msg)))


def _mk(nid, q="clean", t=50.0, up=1.0, cr=1.0):
    n = PulseNode(value=nid)
    n.node_id = nid
    n.quality_flag = q
    n.trust_score = t
    n.updated_at = up
    n.created_at = cr
    return n


def test_quality_severity_table():
    assert PulseSnapshot._m160_quality_severity("placeholder_alias") == 3
    assert PulseSnapshot._m160_quality_severity("polluted") == 2
    assert PulseSnapshot._m160_quality_severity("suspect") == 1
    assert PulseSnapshot._m160_quality_severity("clean") == 0
    assert PulseSnapshot._m160_quality_severity(None) == 0


def test_r1_union_r2_time_r3_quality_r4_trust():
    fs = FakeSnap()
    pq = [_mk("A", "clean", 0.0, up=1.0), _mk("B", "clean", 50.0, up=1.0)]
    js = [_mk("A", "polluted", 50.0, up=2.0), _mk("C", "clean", 50.0, up=1.0)]
    merged = fs._m160_merge_dual(pq, js)
    assert sorted(n.node_id for n in merged) == ["A", "B", "C"]  # R1 并集
    mA = next(n for n in merged if n.node_id == "A")
    assert mA.updated_at == 2.0  # R2 时间大者胜（js 较新）
    assert mA.quality_flag == "polluted"  # R3 非 clean 取高
    assert mA.trust_score == 0.0  # R4 trust 0.0 取 0
    info = [m for lvl, m in fs._logs if "INFO" in lvl and "[A案dual]" in m]
    assert any("flag冲突取非clean=1" in m for m in info)
    assert any("合并=3" in m for m in info)


def test_r5_count_divergence_warning():
    fs = FakeSnap()
    fs._m160_merge_dual([_mk("A"), _mk("B")], [_mk("A"), _mk("B"), _mk("C")])
    warn = [m for lvl, m in fs._logs if "WARNING" in lvl and "双源计数分歧" in m]
    assert warn  # R5 计数分歧只告警不阻断


def test_r6_invariant_no_false_error():
    fs = FakeSnap()
    fs._m160_merge_dual([_mk("A"), _mk("B")], [_mk("A"), _mk("B")])
    err = [m for lvl, m in fs._logs if "ERROR" in lvl and "R6" in m]
    assert not err  # R6 不变式自检正常不打误报


def test_load_source_resolution():
    import config as _cfg
    fs = FakeSnap()
    _cfg.SNAPSHOT_LOAD_SOURCE = "dual"
    assert fs._m160_load_source() == "dual"
    _cfg.SNAPSHOT_LOAD_SOURCE = "bogus"
    assert fs._m160_load_source() == "parquet"  # 非法值回落 parquet
    del _cfg.SNAPSHOT_LOAD_SOURCE
    assert fs._m160_load_source() == "parquet"  # 缺失键按 legacy True
    _cfg.PARQUET_AS_PRIMARY_STORAGE = False
    assert fs._m160_load_source() == "json"  # 缺失键按 legacy False
    _cfg.PARQUET_AS_PRIMARY_STORAGE = True
