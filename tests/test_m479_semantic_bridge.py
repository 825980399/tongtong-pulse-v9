"""★479：语义桥的门控单测。

覆盖三层：
  ① 桥客户端（BridgeEncoder）—— 开关/可用性/embed 契约/失败上限/反证；
  ② VectorEncoder 集成 —— 鸭子类型真的能当 `_model` 用（encode 全链路）；
  ③ 配置契约 —— 默认安全、脚本与解释器路径存在。

★live 测试会真 spawn 3.12 sidecar（约 0.1~1.6s），属必要的端到端验收；
  非 live 用例用不存在的路径做反证，不碰真进程。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.semantic import bridge_encoder as BE
from nucleus.semantic.VectorEncoder import VectorEncoder

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _clean_singleton():
    """每个用例前后都清单例，避免跨用例污染（尤其 live 进程）。"""
    BE.reset_bridge_for_test()
    yield
    BE.reset_bridge_for_test()


class TestBridgeClient:
    def test_01_switch_off_returns_none(self, monkeypatch):
        """★反证：开关关闭 ⇒ get_bridge 返回 None（逐字回滚路径）。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_SEMANTIC_BRIDGE", False)
        assert BE.get_bridge() is None

    def test_02_switch_on_returns_instance(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_SEMANTIC_BRIDGE", True)
        _b = BE.get_bridge()
        assert _b is not None
        assert isinstance(_b, BE.BridgeEncoder)

    def test_03_singleton_is_stable(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_SEMANTIC_BRIDGE", True)
        assert BE.get_bridge() is BE.get_bridge()

    def test_04_missing_script_not_available(self):
        """反证：脚本不存在 ⇒ available False，且不抛。"""
        _b = BE.BridgeEncoder(script=os.path.join("tmp", "__no_such_sidecar__.py"))
        assert _b.available is False
        assert "不存在" in _b.status()["last_error"]

    def test_05_missing_python_not_available(self):
        """反证：桥解释器不存在 ⇒ available False。"""
        _b = BE.BridgeEncoder(python_exe=os.path.join("tmp", "__no_such_py__.exe"))
        assert _b.available is False
        assert "不存在" in _b.status()["last_error"]

    def test_06_embed_empty_returns_empty(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_SEMANTIC_BRIDGE", True)
        _b = BE.get_bridge()
        assert _b.embed([]) == []

    def test_07_live_embed_returns_512(self, monkeypatch):
        """★端到端：真 spawn 3.12 sidecar，返回 512 维。"""
        import config
        monkeypatch.setattr(config, "ENABLE_M479_SEMANTIC_BRIDGE", True)
        _b = BE.get_bridge()
        _v = _b.embed(["曈曈的语义内核如何工作"])
        assert len(_v) == 1, _b.status()
        assert len(_v[0]) == 512, f"维度应为 512，实际 {len(_v[0])}"
        assert all(isinstance(x, float) for x in _v[0][:5])

    def test_08_live_batch_returns_all(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_SEMANTIC_BRIDGE", True)
        _b = BE.get_bridge()
        _texts = ["第一条", "第二条", "第三条"]
        _v = _b.embed(_texts, batch_size=2)
        assert len(_v) == 3
        assert all(len(r) == 512 for r in _v)

    def test_09_fail_limit_disables(self):
        """反证：连续失败达上限 ⇒ 永久放弃（防每批都 spawn 拖慢主链路）。"""
        _b = BE.BridgeEncoder(script=os.path.join("tmp", "__no_such_sidecar__.py"))
        for _ in range(BE._FAIL_LIMIT):
            _b._note_fail("模拟失败")
        assert _b.available is False
        assert _b._fails >= BE._FAIL_LIMIT


class TestVectorEncoderIntegration:
    """鸭子类型契约：桥能当 `_model` 用，encode 全链路不变。"""

    def _encoder_with_bridge(self, monkeypatch):
        import config
        monkeypatch.setattr(config, "ENABLE_M479_SEMANTIC_BRIDGE", True)
        _e = VectorEncoder()  # 不走单例，避免污染
        _b = BE.get_bridge()
        assert _b is not None and _b.available, _b.status() if _b else "no bridge"
        _e._model = _b
        _e._state = "ready"
        return _e

    def test_20_encode_via_bridge_returns_512(self, monkeypatch):
        _e = self._encoder_with_bridge(monkeypatch)
        _v = _e.encode(["语义桥接管测试"])
        assert _v is not None, _e._last_error
        assert _v.shape == (1, 512), _v.shape

    def test_21_encode_is_l2_normalized(self, monkeypatch):
        """VectorEncoder 的归一化必须照常生效（桥不负责归一化）。"""
        import numpy as np
        _e = self._encoder_with_bridge(monkeypatch)
        _v = _e.encode(["归一化检查"])
        _n = float(np.linalg.norm(_v[0]))
        assert abs(_n - 1.0) < 1e-5, f"模长应≈1.0，实际 {_n}"

    def test_22_encode_one_via_bridge(self, monkeypatch):
        _e = self._encoder_with_bridge(monkeypatch)
        _v = _e.encode_one("单条")
        assert _v is not None and _v.shape == (512,)

    def test_23_batch_via_bridge(self, monkeypatch):
        _e = self._encoder_with_bridge(monkeypatch)
        _v = _e.encode(["a", "b", "c", "d"])
        assert _v is not None and _v.shape == (4, 512)

    def test_24_semantic_space_matches_store(self, monkeypatch):
        """★关键：桥编出的向量必须与存量 14899 条同语义空间（>0.6）。"""
        import numpy as np
        _e = self._encoder_with_bridge(monkeypatch)
        _v = _e.encode(["曈曈的语义内核如何工作"])
        assert _v is not None
        _p = os.path.join(ROOT, "data", "knowledge", "vectors.npz")
        if not os.path.isfile(_p):
            pytest.skip("存量向量库不存在")
        _m = np.load(_p)["matrix"]
        _sim = float((_m @ _v[0]).max())
        assert _sim > 0.6, f"与存量库最高相似仅 {_sim}，可能不是同一语义空间"


class TestConfigContract:
    def test_30_switch_exists(self):
        import config
        assert hasattr(config, "ENABLE_M479_SEMANTIC_BRIDGE")
        assert isinstance(config.ENABLE_M479_SEMANTIC_BRIDGE, bool)
        assert hasattr(config, "SEMANTIC_BRIDGE_PYTHON")
        assert hasattr(config, "SEMANTIC_BRIDGE_SCRIPT")

    def test_31_sidecar_script_exists(self):
        import config
        _p = os.path.join(ROOT, config.SEMANTIC_BRIDGE_SCRIPT)
        assert os.path.isfile(_p), f"sidecar 脚本不存在: {_p}"

    def test_32_bridge_python_exists(self):
        import config
        assert os.path.isfile(config.SEMANTIC_BRIDGE_PYTHON), \
            f"桥解释器不存在: {config.SEMANTIC_BRIDGE_PYTHON}"

    def test_33_encode_untouched_by_bridge(self):
        """桥接必须只改 `_model`，不得改 encode 的判据逻辑（防回潮）。"""
        from pathlib import Path
        _src = (Path(ROOT) / "nucleus" / "semantic" / "VectorEncoder.py").read_text(
            encoding="utf-8", errors="replace")
        assert "_m479_try_bridge(cache_dir)" in _src
        # 归一化与维度校验仍在（这两句是质量红线，不能被桥悄悄绕过）
        assert "norm = np.linalg.norm(arr, axis=1, keepdims=True)" in _src
        assert "arr.shape[1] != _dim" in _src


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
