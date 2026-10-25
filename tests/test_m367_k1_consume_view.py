# -*- coding: utf-8 -*-
"""367 刀1：消费口异常路径视图补强＋分支分类留痕（H1 · P2）。

验收分两类（main 主循环无法单测，如实分开）：
  * **真单测**：①节拍/开关来源三路径（纯函数）／④异常拍视图／⑥既有 363A3 不受破坏；
  * **结构断言**：②计数器留痕、③skip 分类、⑤非 dict 留痕 —— `main()` 是万行循环体，
    无法在单测里跑起来；用源码结构断言防回潮，读数正确性并入重启观察窗。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def m():
    import main as _m
    return _m


class TestSources:
    """① 节拍/开关**来源**判定（纯函数三路径 —— 任务书要点①）"""

    def test_01_env_wins(self, m, monkeypatch):
        monkeypatch.setenv("PULSE_POOL_CONSUME_EVERY", "50")
        monkeypatch.setenv("PULSE_POOL_CONSUME", "0")
        every, esrc, on, osrc = m._m367_pool_consume_sources()
        assert (every, esrc) == (50, "env")
        assert (on, osrc) == (False, "env")

    def test_02_config_when_no_env(self, m, monkeypatch):
        monkeypatch.delenv("PULSE_POOL_CONSUME_EVERY", raising=False)
        monkeypatch.delenv("PULSE_POOL_CONSUME", raising=False)
        import config as cfg
        monkeypatch.setattr(cfg, "POOL_CONSUME_EVERY_TICKS", 120, raising=False)
        monkeypatch.setattr(cfg, "ENABLE_AUDIT_POOL_CONSUME", False, raising=False)
        every, esrc, on, osrc = m._m367_pool_consume_sources()
        assert (every, esrc) == (120, "config")
        assert (on, osrc) == (False, "config")

    def test_03_default_when_nothing(self, m, monkeypatch):
        monkeypatch.delenv("PULSE_POOL_CONSUME_EVERY", raising=False)
        monkeypatch.delenv("PULSE_POOL_CONSUME", raising=False)
        import config as cfg
        monkeypatch.delattr(cfg, "POOL_CONSUME_EVERY_TICKS", raising=False)
        monkeypatch.delattr(cfg, "ENABLE_AUDIT_POOL_CONSUME", raising=False)
        every, esrc, on, osrc = m._m367_pool_consume_sources()
        assert (every, esrc) == (300, "default")
        assert (on, osrc) == (True, "default"), "无配置时默认开启是既有行为"

    def test_04_illegal_env_falls_back(self, m, monkeypatch):
        """非法 env 回落 300 且**来源记 default**（不伪装成 env 生效）。"""
        monkeypatch.setenv("PULSE_POOL_CONSUME_EVERY", "abc")
        every, esrc, _on, _osrc = m._m367_pool_consume_sources()
        assert (every, esrc) == (300, "default")


class TestExceptionView:
    """④ 异常拍必须带与正常路径**同结构**的视图（本刀核心缺口）"""

    def test_05_exception_view_same_fields(self, monkeypatch):
        from nucleus import audit_problem_pool_consumer as apc

        def _boom(*a, **k):
            raise RuntimeError("pool exploded")

        monkeypatch.setattr(apc, "consume_pool", _boom)
        r = apc.consume_heartbeat("/nonexistent-root-for-test")
        assert r["state"] == "exception"
        assert "RuntimeError" in r["reason"]
        view = r.get("m363_consume_view")
        assert isinstance(view, dict), "异常拍缺视图（本刀要堵的缺口）"
        assert set(view) == {"consume_state", "candidate_count",
                             "dispatch_selected", "verify_feedback_state"}, \
            "视图字段集必须与正常路径一致：%s" % sorted(view)
        assert view["consume_state"] == "exception"
        assert view["candidate_count"] is None, "没数过就是 None，不能写 0 冒充"
        assert view["dispatch_selected"] == 0

    def test_06_exception_not_in_five_states(self):
        """`exception` 只是视图取值，**不并入五态集合**（不能改五态语义）。"""
        from nucleus import audit_problem_pool_consumer as apc
        _src = io.open(apc.__file__, encoding="utf-8").read()
        # 五态字面量仍在，且 exception 不在任何"五态元组"定义里
        for _s in ("noop_pool_empty", "unavailable", "disabled", "write_failed"):
            assert _s in _src, _s


class TestStructure:
    """②③⑤ main 主循环不可单测 ⇒ 结构断言防回潮（读数正确性并入重启窗）"""

    def test_07_all_log_tags_present(self):
        _src = io.open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()
        for _tag in ("[351节拍] every=%d source=%s consume_on=%s consume_source=%s",
                     "[351节拍] counter=%d/every=%d wake=%s",
                     "[351节拍] skip=patch_gate_blocked",
                     "branch=shutdown_exc",
                     "branch=night_digest_exc",
                     "[363A3] heartbeat_exception state=exception reason=%s",
                     "[363A3] heartbeat_non_dict type=%s"):
            assert _tag in _src, "缺留痕标签：%s" % _tag

    def test_08_wait_return_used_only_for_log(self):
        """②`wait_heartbeat_pulse` 的返回值**只**用于 wake 标签，不改等待语义。"""
        _src = io.open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()
        assert '_pc_wake_last = ("heartbeat"' in _src
        assert "if _ctrl.wait_heartbeat_pulse(1) else \"timeout\")" in _src
        # 等待时长仍是 1s（不得被本刀改动）
        assert "_ctrl.wait_heartbeat_pulse(1)" in _src

    def test_09_no_threshold_or_default_changed(self):
        """⑥边界：节拍阈值与开关默认值不得被本刀改动。"""
        import config as cfg
        assert getattr(cfg, "POOL_CONSUME_EVERY_TICKS", 300) == 300
        _src = io.open(os.path.join(_ROOT, "main.py"), encoding="utf-8").read()
        assert "_pool_consume_every = 300" not in _src.replace(
            "_every, _esrc = 300", ""), "回落分支之外出现了硬写 300"

    def test_10_normal_path_view_unchanged(self, monkeypatch):
        """⑥既有 363A3 正常路径四字段输出不被本刀破坏。"""
        from nucleus import audit_problem_pool_consumer as apc
        monkeypatch.setattr(apc, "consume_pool", lambda *a, **k: {
            "state": "ok", "open_before": 3, "claimed": 2,
            "verify_feedback": {"state": "ok"}})
        monkeypatch.setattr(apc, "feedback_once", lambda *a, **k: {}, raising=False)
        r = apc.consume_heartbeat("/nonexistent-root-for-test")
        view = r["m363_consume_view"]
        assert set(view) == {"consume_state", "candidate_count",
                             "dispatch_selected", "verify_feedback_state"}
        assert view["consume_state"] == "ok"
        assert view["candidate_count"] == 3
        assert view["dispatch_selected"] == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
