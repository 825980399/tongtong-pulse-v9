"""★487-2 门控测试：critical 每层 worker 下限提到 2（受 ENABLE_M487_WORKER_FLOOR 控制）。"""
import config
from nucleus.field.InfoField import InfoField


def _mk_field(monkeypatch):
    f = InfoField(max_history=50)
    _calls = []
    monkeypatch.setattr(f, "_resize_layer_pool",
                        lambda layer, n, force=False: _calls.append((layer, n)))
    f._load_level = "critical"
    f._last_resized_level = None
    return f, _calls


def test_critical_floor_on(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_WORKER_FLOOR", True)
    f, _calls = _mk_field(monkeypatch)
    f._resize_layer_pools_by_level()
    assert ("L1", 2) in _calls and ("L2", 2) in _calls and ("L3", 2) in _calls


def test_critical_floor_off_legacy(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M487_WORKER_FLOOR", False)
    f, _calls = _mk_field(monkeypatch)
    f._resize_layer_pools_by_level()
    # 旧行为：L1=1/L2=0/L3=0（旧「有意保护」）
    assert ("L1", 1) in _calls and ("L2", 0) in _calls and ("L3", 0) in _calls
