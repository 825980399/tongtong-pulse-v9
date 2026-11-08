# -*- coding: utf-8 -*-
"""477 批门控单测：眼睛/嘴巴/耳朵本地化（3.12 感官桥）。

分两层：
① **live 真机**：caps / look 真的去 spawn 3.12 sidecar 并取回结果（测试进程本身
   就跑在 3.12，与生产 3.14t spawn 3.12 同构；若本机无相机/无 3.12，则 skip）；
② **接线防回潮**：PulseMouth / PulseEars / PulseVisualCortex 源码必须保留桥调用，
   避免后续重构把接线删掉却没人发现。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("PULSE_FRAMEWORK", "1")

import config
from nucleus.senses import sensory_bridge as SB


def _src(rel: str) -> str:
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8", errors="replace") as _f:
        return _f.read()


class TestConfigDefaults:
    def test_bridge_enabled_by_default(self):
        assert config.ENABLE_SENSORY_BRIDGE is True

    def test_voice_listen_off_by_default(self):
        # 3.14t 下 sounddevice/PortAudio 必崩 ⇒ 原生语音线程必须默认关
        assert config.ENABLE_VOICE_LISTEN is False

    def test_bridge_python_configured(self):
        assert str(config.SENSORY_BRIDGE_PYTHON or "").strip()


class TestClientGraceful:
    def test_disabled_returns_false(self, monkeypatch):
        monkeypatch.setattr(config, "ENABLE_SENSORY_BRIDGE", False)
        _r = SB.caps()
        assert _r.get("ok") is False and _r.get("error") == "disabled"

    def test_missing_python_returns_false(self, monkeypatch):
        monkeypatch.setattr(config, "ENABLE_SENSORY_BRIDGE", True)
        monkeypatch.setattr(config, "SENSORY_BRIDGE_PYTHON", "Z:/nope/python.exe")
        _r = SB.caps()
        assert _r.get("ok") is False
        assert "bridge_python_missing" in str(_r.get("error"))

    def test_empty_speak_text(self):
        _r = SB.speak("")
        assert _r.get("ok") is False and _r.get("error") == "empty_text"


class TestLiveSidecar:
    """真机 live：必须真的 spawn 3.12 并拿到 JSON。"""

    @pytest.fixture(autouse=True)
    def _need_py312(self):
        _py = str(config.SENSORY_BRIDGE_PYTHON or "")
        if not (_py and os.path.isfile(_py)):
            pytest.skip("本机无 3.12 解释器，跳过 live 验收")

    def test_caps_live(self):
        _r = SB.caps()
        assert _r.get("ok") is True, _r
        _caps = _r.get("caps") or {}
        assert {"camera", "mic", "tts", "vosk"}.issubset(_caps.keys())

    def test_look_live(self):
        _r = SB.look()
        assert _r.get("ok") is True, _r
        assert int(_r.get("width") or 0) > 0 and int(_r.get("height") or 0) > 0
        assert isinstance(_r.get("desc"), str) and _r["desc"]

    def test_listen_live_short(self):
        # 2 秒短录音：只验证管道通（静音环境返回空文本是正常结果）
        _r = SB.listen(seconds=2)
        assert _r.get("ok") is True, _r
        assert "text" in _r


class TestWiringAntiRegression:
    def test_mouth_uses_bridge(self):
        _s = _src("organs/motor/PulseMouth.py")
        assert "nucleus.senses.sensory_bridge import speak" in _s
        assert "sensory_bridge" in _s

    def test_ears_bridge_loop_present(self):
        _s = _src("organs/senses/PulseEars.py")
        assert "def _bridge_listen_loop" in _s
        assert "_m477_bridge_listen" in _s
        assert "_m477_bridge_enabled" in _s

    def test_vision_bridge_look_present(self):
        _s = _src("organs/senses/PulseVisualCortex.py")
        assert "def m477_look" in _s
        assert '== "look"' in _s
        assert "_has_bridge_vision" in _s

    def test_ears_hardcoded_true_gone(self):
        _s = _src("organs/senses/PulseEars.py")
        assert "self._voice_enabled = True" not in _s
