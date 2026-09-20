# -*- coding: utf-8 -*-
"""第80批 G0 存储止血 —— 真实端到端保真测试（禁止 mock 顶替）。

覆盖 A1 分层保真 / A2 保存成功 / A3 三遍保真零偏移 / A4 冷存不风暴 /
A5 增量日志安全 / A7 崩溃恢复。

测试数据用 tmp 构造分层节点（L1/L2/L3），绝不用真实 data/ 做破坏性用例。
验收唯一标准：真实 load→save→load 端到端保真，分层/内容零偏移。
"""
import os
import sys
import shutil
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from nucleus.mnemosyne.PulseNode import PulseNode  # noqa: E402
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot  # noqa: E402


class _FakePool:
    """最小 node_pool 桩：返回注入节点，无磁盘 IO（验证 A4 冷存不风暴）。"""

    def __init__(self, nodes):
        self._nodes = list(nodes)
        self.recall_calls = 0

    def get_all(self):
        return list(self._nodes)

    def get_all_including_evicted(self):
        # A4：冷存关时直接返回内存节点，绝不逐节点磁盘召回（无风暴）
        self.recall_calls += 1
        return list(self._nodes)


def _make_layered_nodes():
    nodes = []
    for i in range(5):
        nodes.append(PulseNode(value=f"L1内容-{i}", evol_level="L1", keywords=["k"]))
    for i in range(3):
        nodes.append(PulseNode(value=f"L2内容-{i}", evol_level="L2", keywords=["k"]))
    for i in range(2):
        nodes.append(PulseNode(value=f"L3内容-{i}", evol_level="L3", keywords=["k"]))
    return nodes


@pytest.fixture
def workdir():
    # ★第80批 T7：必须用项目内目录（非系统 %TEMP%）。
    #   P2-88 路径白名单会拒绝加载落在系统临时目录下的快照（防 pytest 残留误加载），
    #   故放到项目 tmp/ 下（Windows 路径用反斜杠，不匹配 /tmp/ 关键字）。
    d = os.path.join(ROOT, "tmp", f"m80_sandbox_{uuid.uuid4().hex}")
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def snapshot(workdir):
    # T1 止血：显式关三个毁库开关（生产中已默认 False，此处防御性确认）
    _orig = (
        config.PARQUET_AS_PRIMARY_STORAGE,
        config.SNAPSHOT_HOT_COLD_LOAD,
        config.SNAPSHOT_USE_INCREMENTAL_LOG,
    )
    config.PARQUET_AS_PRIMARY_STORAGE = False
    config.SNAPSHOT_HOT_COLD_LOAD = False
    config.SNAPSHOT_USE_INCREMENTAL_LOG = False
    sp = os.path.join(workdir, "snapshot.json")
    snap = PulseSnapshot(sp)
    snap.set_node_pool(_FakePool(_make_layered_nodes()))
    yield snap
    config.PARQUET_AS_PRIMARY_STORAGE, config.SNAPSHOT_HOT_COLD_LOAD, config.SNAPSHOT_USE_INCREMENTAL_LOG = _orig


def _count_levels(nodes):
    lv = {"L1": 0, "L2": 0, "L3": 0}
    for n in nodes:
        lv[str(getattr(n, "evol_level", "L1"))] += 1
    return lv


def test_A1_layered_fidelity(snapshot):
    """A1：真实 save→load 分层保真（验证 T2 回填 evol_level，L2/L3 不塌缩 L1）。"""
    assert snapshot.save(force_full=True), "save 应成功"
    assert os.path.exists(snapshot.snapshot_path)
    snap2 = PulseSnapshot(snapshot.snapshot_path)
    snap2.set_node_pool(_FakePool(_make_layered_nodes()))
    loaded = snap2.load()
    lv = _count_levels(loaded)
    assert lv == {"L1": 5, "L2": 3, "L3": 2}, f"分层塌缩（G0事故）: {lv}"


def test_A2_save_success(snapshot):
    """A2：保存成功且文件非空。"""
    assert snapshot.save(force_full=True) is True
    assert os.path.getsize(snapshot.snapshot_path) > 0


def test_A3_three_pass_zero_drift(snapshot):
    """A3：三遍 save→load 零偏移（分层与内容均保真）。"""
    expected_vals = sorted(getattr(n, "value", "") for n in _make_layered_nodes())
    for _i in range(3):
        assert snapshot.save(force_full=True)
        snap2 = PulseSnapshot(snapshot.snapshot_path)
        snap2.set_node_pool(_FakePool(_make_layered_nodes()))
        loaded = snap2.load()
        lv = _count_levels(loaded)
        assert lv == {"L1": 5, "L2": 3, "L3": 2}, f"第{_i}遍分层偏移: {lv}"
        vals = sorted(getattr(n, "value", "") for n in loaded)
        assert vals == expected_vals, f"第{_i}遍内容偏移"


def test_A4_cold_no_storm(snapshot):
    """A4：冷存关时保存对节点池的枚举次数有界（非逐节点召回风暴）。

    G0 真因是「冷存启用时 get_all_including_evicted 逐冷节点 _recall_cold_node 磁盘召回」，
    风暴发生在 PulseNodePool 内部（非 PulseSnapshot），且已由 T1 关冷存彻底消除。
    内存桩无法复现逐节点磁盘召回，此处以「枚举次数有界」作为回归护栏：
    一次全量保存枚举节点池的遍数恒定（观测为 3：主JSON + Parquet保底 + L1快照），
    不会随节点数线性增长（否则即风暴回归）。
    """
    snapshot.save(force_full=True)
    _calls = snapshot.node_pool.recall_calls
    assert _calls <= 4, f"节点池枚举次数异常偏高（疑似逐节点召回风暴）: {_calls}"


def test_A5_incremental_log_disabled(snapshot):
    """A5：增量日志开关关时，不写 snapshot_incremental.jsonl（T1 已关）。"""
    snapshot.save(force_full=True)
    inc = os.path.join(os.path.dirname(snapshot.snapshot_path), "snapshot_incremental.jsonl")
    assert not os.path.exists(inc), "增量日志不应写入（T1 已关 SNAPSHOT_USE_INCREMENTAL_LOG）"


def test_A7_crash_recovery_failed_copy_retained(snapshot, monkeypatch):
    """A7：写失败（os.replace 抛异常）时主快照不被截断，且保留 .failed 临时副本。"""
    import os as _os

    assert snapshot.save(force_full=True)
    _good = open(snapshot.snapshot_path, "rb").read()

    _real_replace = _os.replace

    def _boom(*a, **k):
        raise OSError("simulated crash")

    monkeypatch.setattr(_os, "replace", _boom)
    # 保存到独立目标，避免破坏已验证完好的主快照
    tgt = os.path.join(os.path.dirname(snapshot.snapshot_path), "crash_target.json")
    snap3 = PulseSnapshot(tgt)
    snap3.set_node_pool(_FakePool(_make_layered_nodes()))
    # T4：写失败（os.replace 抛 OSError）时 save() 优雅返回 False（不抛异常、不中断框架），
    # 主快照不被截断，且保留带时间戳 .failed_ 临时副本供恢复。
    _result = snap3.save(force_full=True)
    assert _result is False or _result is None, f"崩溃写应优雅失败(返回 False)，实际: {_result!r}"
    # 主快照仍完好（未被截断/覆盖）
    assert open(snapshot.snapshot_path, "rb").read() == _good, "主快照被崩溃写破坏"
    # 崩溃写保留 .failed 副本（T4②）：文件名形如 <tmp>.failed_<时间戳>，标记在中间
    _failed = [f for f in os.listdir(os.path.dirname(tgt)) if ".failed_" in f]
    assert _failed, "崩溃写应保留 .failed 临时副本供恢复"
    monkeypatch.setattr(_os, "replace", _real_replace)
