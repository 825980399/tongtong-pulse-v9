# -*- coding: utf-8 -*-
"""160下下 刀4（T-重建Parquet主存储-1）单元测试：m160_rebuild_parquet_from_nodes。

不依赖运行框架：FakeSnap 继承 PulseSnapshot，覆盖 _log 并注入 snapshot_path。
覆盖：dry_run 统计+不写盘+幂等；实跑写盘+校验+分区目录出现；verify 失败→回滚。
"""
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot
from nucleus.mnemosyne.PulseNode import PulseNode


class FakeSnap(PulseSnapshot):
    def __init__(self, snapshot_path):
        self._logs = []
        self.snapshot_path = snapshot_path
        self.node_pool = None

    def _log(self, lvl, msg, *a, **k):
        self._logs.append((str(lvl), str(msg)))


def _mk(node_id, value, level):
    return PulseNode.from_dict({
        "node_id": node_id,
        "value": value,
        "evol_level": level,
        "ephemeral": False,
        "trust_score": 50.0,
        "quality_flag": "clean",
    })


def _tmp_snap_path():
    d = tempfile.mkdtemp(prefix="m160knife4_")
    return os.path.join(d, "test_snapshot.json"), d


def test_dry_run_no_write_and_idempotent():
    sp, d = _tmp_snap_path()
    try:
        fs = FakeSnap(sp)
        nodes = [_mk("A", "va", "L1"), _mk("B", "vb", "L2"), _mk("C", "vc", "L3")]
        r1 = fs.m160_rebuild_parquet_from_nodes(dry_run=True, nodes=nodes)
        assert r1["status"] == "dry_run", r1
        assert r1["node_count"] == 3
        assert r1["lv_counts"] == {"L1": 1, "L2": 1, "L3": 1}
        # 不写盘：parquet 目录不应存在
        assert not os.path.isdir(fs._m68_parquet_dir())
        # 幂等：再跑一次完全一致且不写盘
        r2 = fs.m160_rebuild_parquet_from_nodes(dry_run=True, nodes=nodes)
        assert r2 == r1
        assert not os.path.isdir(fs._m68_parquet_dir())
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rebuild_writes_and_verifies():
    sp, d = _tmp_snap_path()
    try:
        fs = FakeSnap(sp)
        nodes = [_mk("A", "va", "L1"), _mk("B", "vb", "L2"), _mk("C", "vc", "L3")]
        r = fs.m160_rebuild_parquet_from_nodes(dry_run=False, nodes=nodes)
        if r.get("status") == "skipped" and r.get("reason") == "no_pyarrow":
            import pytest
            pytest.skip("pyarrow 不可用，跳过实跑写盘测试")
        assert r["status"] == "rebuilt", r
        # 临时目录已清理
        parent = os.path.dirname(fs._m68_parquet_dir())
        assert not any(x.startswith("parquet.m160tmp-") for x in os.listdir(parent))
        # 正式目录出现分区
        pdir = fs._m68_parquet_dir()
        assert os.path.isdir(pdir)
        entries = set(x for x in os.listdir(pdir) if x.startswith("evol_level="))
        assert entries == {"evol_level=L1", "evol_level=L2", "evol_level=L3"}, entries
        # 校验通过
        assert fs._m68_verify_parquet() > 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_rebuild_rollback_on_verify_failure():
    sp, d = _tmp_snap_path()
    try:
        fs = FakeSnap(sp)
        nodes = [_mk("A", "va", "L1")]
        # 先成功建一份，确认 base 存在
        r0 = fs.m160_rebuild_parquet_from_nodes(dry_run=False, nodes=nodes)
        if r0.get("status") == "skipped":
            import pytest
            pytest.skip("pyarrow 不可用，跳过回滚测试")
        assert r0["status"] == "rebuilt"
        # 模拟 verify 失败
        fs._m68_verify_parquet = lambda root=None: -1
        r = fs.m160_rebuild_parquet_from_nodes(dry_run=False, nodes=nodes)
        assert r["status"] == "failed", r
        # 临时目录清理
        parent = os.path.dirname(fs._m68_parquet_dir())
        assert not any(x.startswith("parquet.m160tmp-") for x in os.listdir(parent))
        # base 仍存在（回滚成功）
        assert os.path.isdir(fs._m68_parquet_dir())
    finally:
        shutil.rmtree(d, ignore_errors=True)
