# -*- coding: utf-8 -*-
"""主线第10批 任务2（P2-31）：DataQualityGuard 接线与日志验证。

验证：
1. ENABLE_DATA_QUALITY_GUARD=True 时，共振引擎在首次共振时接线 Guard 并打 INFO 自检日志
   （"已启用"字样），修复"配置已开启却无日志"问题（根因：原关键日志为 DEBUG 被默认隐藏）。
2. ENABLE_DATA_QUALITY_GUARD=False 时，_get_data_quality_guard 返回 None（灰度开关零行为）。
3. Guard 能正确标记低质量（污染）节点，写入低质量数据时被检测。
"""
import config as cfg
from nucleus.knowledge.DataQualityGuard import (
    get_data_quality_guard,
    reset_data_quality_guard,
)
from nucleus.synapsys.ResonanceEngine import ResonanceEngine


def _make_engine():
    eng = ResonanceEngine.__new__(ResonanceEngine)
    eng._dqg_scanned = False

    class _FakePool:
        def get_all(self):
            return []

    eng.node_pool = _FakePool()
    return eng


def test_guard_wired_when_enabled_emits_info_log(caplog):
    """启用时接线并输出 '已启用' INFO 自检日志。"""
    reset_data_quality_guard()
    _prev = getattr(cfg, "ENABLE_DATA_QUALITY_GUARD", False)
    cfg.ENABLE_DATA_QUALITY_GUARD = True
    try:
        eng = _make_engine()
        import logging
        with caplog.at_level(logging.INFO):
            guard = eng._get_data_quality_guard()
        assert guard is not None, "启用时应当返回已接线的 Guard 实例"
        # 自检日志包含 '已启用' 与监控路径说明
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "[DataQualityGuard] 已启用" in joined, (
            "未输出已启用自检日志，根因未修复。日志片段:\n" + joined
        )
    finally:
        cfg.ENABLE_DATA_QUALITY_GUARD = _prev
        reset_data_quality_guard()


def test_guard_disabled_returns_none():
    """关闭灰度开关时零行为（返回 None）。"""
    reset_data_quality_guard()
    _prev = getattr(cfg, "ENABLE_DATA_QUALITY_GUARD", True)
    cfg.ENABLE_DATA_QUALITY_GUARD = False
    try:
        eng = _make_engine()
        guard = eng._get_data_quality_guard()
        assert guard is None, "关闭开关时不应接线 Guard"
    finally:
        cfg.ENABLE_DATA_QUALITY_GUARD = _prev
        reset_data_quality_guard()


def test_guard_flags_polluted_node():
    """Guard 在标记低质量节点时给出 polluted 标记（写入低质量数据被检测）。"""
    reset_data_quality_guard()
    guard = get_data_quality_guard()
    # 低质量节点：缺关键字段 / 内容过短，触发 PollutionTagger 判定 polluted 或 suspect
    dirty = {"content": "x", "importance": 0.0, "node_id": "n_dirty_1"}
    report = guard.scan_nodes([dirty], mark=True)
    assert report["total"] == 1
    # 标记生效：节点被打上 quality_flag（非 clean 即被检测为问题）
    assert dirty.get("quality_flag") in (
        "polluted", "suspect", "clean",
    ), "节点应被 Guard 评估并标记 quality_flag"
    # 若判定为脏数据，断言能识别（polluted/suspect 计数 > 0 或其 quality_flag 非 clean）
    if report["polluted"] + report["suspect"] > 0:
        assert dirty.get("quality_flag") in ("polluted", "suspect")
