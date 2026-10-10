"""179B 判据 R 收口：config.ENABLE_REQUEST_DEDUP 默认翻 True（保留 env 回退）。

验证：
- 默认（无 env 覆盖）应为 True；
- 显式 PULSE_REQUEST_DEDUP=0 可回退为 False（灰度可关，安全网）。
"""
import os

import config as _cfg


def test_default_true_without_env():
    saved = os.environ.pop("PULSE_REQUEST_DEDUP", None)
    try:
        import importlib

        importlib.reload(_cfg)
        assert _cfg.ENABLE_REQUEST_DEDUP is True, (
            "179B 判据R收口后 ENABLE_REQUEST_DEDUP 默认应为 True"
        )
    finally:
        if saved is not None:
            os.environ["PULSE_REQUEST_DEDUP"] = saved
        importlib.reload(_cfg)


def test_env_zero_can_revert():
    saved = os.environ.get("PULSE_REQUEST_DEDUP")
    os.environ["PULSE_REQUEST_DEDUP"] = "0"
    try:
        import importlib

        importlib.reload(_cfg)
        assert _cfg.ENABLE_REQUEST_DEDUP is False, (
            "env PULSE_REQUEST_DEDUP=0 应可回退为 False"
        )
    finally:
        if saved is None:
            os.environ.pop("PULSE_REQUEST_DEDUP", None)
        else:
            os.environ["PULSE_REQUEST_DEDUP"] = saved
        importlib.reload(_cfg)
