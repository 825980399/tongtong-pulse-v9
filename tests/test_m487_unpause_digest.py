"""★487-1 门控测试：CRITICAL 等级下 stomach_digest（消费类）不得被暂停。"""
import config
from nucleus.runtime_metrics import AdaptiveFrequencyController


def test_stomach_unpaused_at_critical_when_on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_UNPAUSE_DIGEST", True)
    monkeypatch.setattr(config, "ENABLE_ADAPTIVE_FREQUENCY", True)
    ctrl = AdaptiveFrequencyController()
    ctrl._level = "CRITICAL"
    # 消费类 stomach_digest 必须恒执行（不进 CRITICAL 暂停分支）
    assert ctrl.should_execute("stomach_digest") is True


def test_production_op_still_paused_at_critical(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_UNPAUSE_DIGEST", True)
    monkeypatch.setattr(config, "ENABLE_ADAPTIVE_FREQUENCY", True)
    ctrl = AdaptiveFrequencyController()
    ctrl._level = "CRITICAL"
    # 生产类（organ_scan 非消费、非关键操作）仍按 CRITICAL 暂停
    assert ctrl.should_execute("organ_scan") is False


def test_stomach_paused_when_gate_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_UNPAUSE_DIGEST", False)
    monkeypatch.setattr(config, "ENABLE_ADAPTIVE_FREQUENCY", True)
    ctrl = AdaptiveFrequencyController()
    ctrl._level = "CRITICAL"
    # 开关关 ⇒ 逐字回改前（CRITICAL 时 stomach_digest 也被暂停）
    assert ctrl.should_execute("stomach_digest") is False
