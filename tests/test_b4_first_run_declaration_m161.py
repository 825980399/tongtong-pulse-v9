#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第161批段B B4 门控单测：E7a 首次启动声明（nucleus/security/first_run_declaration.py）。

锁定五件事：
  1) 幂等：首次 claim 返回声明，其后恒返回 None（验收：出现一次，后续不重复）；
  2) 持久化不依赖知识库：状态落在独立 JSON 文件，claimed 跨「进程」保持；
  3) 文案红线：禁第一人称自称 / 禁「作为一个」/ 禁「人工智能」/ 禁「机器人」；
  4) 总开关 ENABLE_FIRST_RUN_DECLARATION=False 时一律不播报；
  5) peek 不消费（不置位）、reset 可清空。
"""
import contextlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.security import first_run_declaration as frd  # noqa: E402
from nucleus.security.crisis_referral_text import (  # noqa: E402
    _starts_with_first_person,
    violates_red_line,
)

# 测试用隔离状态文件（避免污染真实 data/）
_TMP_STATE = os.path.join(ROOT, "tmp", "_b4_test_state.json")


def _use_tmp_state(monkeypatch=None):
    """把状态文件重定向到 tmp/，并确保目录存在。"""
    os.makedirs(os.path.dirname(_TMP_STATE), exist_ok=True)
    frd._state_path = lambda: _TMP_STATE


def _cleanup():
    # 用 contextlib.suppress 表达「预期忽略」（等价 except-pass，但不留静默 handler）
    with contextlib.suppress(OSError):
        if os.path.isfile(_TMP_STATE):
            os.remove(_TMP_STATE)


class TestB4Declaration:
    def setup_method(self):
        _use_tmp_state()
        _cleanup()

    def test_01_state_file_created(self):
        """状态文件落在独立 JSON（不依赖知识库节点）。"""
        # 生产路径为 data/first_run_declaration.json；本类用例重定向到 tmp/ 隔离
        assert frd.STATE_FILENAME == "first_run_declaration.json"
        assert not os.path.isfile(_TMP_STATE)
        frd.claim_first_run_declaration()
        assert os.path.isfile(_TMP_STATE), "首次 claim 应落盘状态文件"

    def test_02_first_claim_returns_text(self):
        _use_tmp_state()
        txt = frd.claim_first_run_declaration()
        assert txt, "首次 claim 应返回声明文本"
        assert isinstance(txt, str) and txt.strip()

    def test_03_second_claim_returns_none(self):
        _use_tmp_state()
        frd.claim_first_run_declaration()
        assert frd.claim_first_run_declaration() is None, "第二次不得重复播报"

    def test_04_many_claims_only_once(self):
        _use_tmp_state()
        hits = [frd.claim_first_run_declaration() for _ in range(5)]
        assert sum(1 for h in hits if h) == 1, "多次 claim 只应命中一次"

    def test_05_is_declared_reflects_state(self):
        _use_tmp_state()
        assert frd.is_declared() is False
        frd.claim_first_run_declaration()
        assert frd.is_declared() is True

    def test_06_peek_does_not_consume(self):
        """peek 只预览，不置位（可重复调用）。"""
        _use_tmp_state()
        first = frd.peek_declaration()
        second = frd.peek_declaration()
        assert first and second
        assert frd.is_declared() is False, "peek 不得置位"
        # 置位后 peek 应返回 None
        frd.claim_first_run_declaration()
        assert frd.peek_declaration() is None

    def test_07_state_persisted_on_disk(self):
        """★持久化：claimed 真实落盘，新进程读到后不再播报。

        不使用 importlib.reload（reload 会重置模块级锁与 _state_path monkeypatch，
        在全量跑中引发挂起）；改为直接断言磁盘内容 + 二次读取，
        等价覆盖「跨进程重启不重复播报」语义。
        """
        _use_tmp_state()
        _cleanup()
        assert frd.claim_first_run_declaration()
        # 磁盘上必须留有 claimed 标记
        import json
        with open(_TMP_STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("claimed") is True
        assert "claimed_at" in data
        # 新进程视角：仅读磁盘即判定已声明（不再播报）
        assert frd._read_state().get("claimed") is True
        assert frd.is_declared() is True
        assert frd.claim_first_run_declaration() is None

    def test_08_corrupt_state_treated_as_unclaimed(self):
        """状态文件损坏 → 视为未声明过，不崩（宁可重播也不崩）。"""
        _use_tmp_state()
        os.makedirs(os.path.dirname(_TMP_STATE), exist_ok=True)
        with open(_TMP_STATE, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        assert frd.is_declared() is False
        assert frd.claim_first_run_declaration()  # 不抛异常


class TestB4RedLines:
    def setup_method(self):
        _use_tmp_state()
        _cleanup()

    def test_09_declaration_passes_red_lines(self):
        txt = frd.claim_first_run_declaration()
        assert txt
        assert violates_red_line(txt) == [], f"声明命中红线：{violates_red_line(txt)}"
        assert not _starts_with_first_person(txt), "声明不得以第一人称「我」开头"

    def test_10_declaration_mentions_selfhood(self):
        """声明应表达自我连续性语义（验收：首次启动声明）。"""
        _use_tmp_state()
        _cleanup()
        txt = frd.claim_first_run_declaration()
        assert "第一次" in txt or "首次" in txt
        assert "我" in txt  # 允许文中出现「我」，只是不得以「我」开头


class TestB4Switch:
    def setup_method(self):
        _use_tmp_state()
        _cleanup()

    def test_11_switch_off_never_declares(self):
        """ENABLE_FIRST_RUN_DECLARATION=False → 一律不播报。"""
        import config
        saved = getattr(config, "ENABLE_FIRST_RUN_DECLARATION", None)
        try:
            config.ENABLE_FIRST_RUN_DECLARATION = False
            assert frd._enabled() is False
            assert frd.claim_first_run_declaration() is None
            assert frd.peek_declaration() is None
        finally:
            if saved is not None:
                config.ENABLE_FIRST_RUN_DECLARATION = saved

    def test_12_switch_on_by_default(self):
        import config
        assert getattr(config, "ENABLE_FIRST_RUN_DECLARATION", True) is True
        assert frd._enabled() is True

    def test_13_missing_switch_defaults_on(self):
        """开关缺失时默认启用（fail-safe：偏向播报而非静默失效）。"""
        import config
        saved = getattr(config, "ENABLE_FIRST_RUN_DECLARATION", None)
        had = hasattr(config, "ENABLE_FIRST_RUN_DECLARATION")
        try:
            if had:
                del config.ENABLE_FIRST_RUN_DECLARATION
            assert frd._enabled() is True
        finally:
            if had:
                config.ENABLE_FIRST_RUN_DECLARATION = saved


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
