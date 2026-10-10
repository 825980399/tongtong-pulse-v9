# -*- coding: utf-8 -*-
"""178批 刀2 门控单测：RequestDeduplicator OBSERVE 埋点（隔离单跑）。

验证：
  - 开关开启时 observe_request 计数 + 指纹分布生效；
  - 开关关闭时零开销（计数不变）；
  - observe_span 上下文管理器在途计数正确进出；
  - 线程局部 user_name 写入/读取。
不依赖框架运行（曈曈停机态亦可跑）。
"""
import os
import sys

import pytest

# 隔离单跑：仅注入项目根（避免脏 PYTHONPATH 串版本）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from nucleus.field import request_dedup_observe as obs  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    obs.reset()
    yield
    obs.reset()


def test_observe_increments_when_enabled():
    config.ENABLE_SEMANTIC_CACHE_OBSERVE = True
    obs.set_current_user("alice")
    obs.observe_request("alice", "你好")
    snap = obs.snapshot()
    assert snap["observed"] == 1
    assert snap["distinct_fingerprints"] == 1
    # 相同 (user,prompt) 指纹应累计
    obs.observe_request("alice", "你好")
    assert obs.snapshot()["observed"] == 2


def test_observe_noop_when_disabled():
    config.ENABLE_SEMANTIC_CACHE_OBSERVE = False
    obs.observe_request("alice", "你好")
    assert obs.snapshot()["observed"] == 0
    config.ENABLE_SEMANTIC_CACHE_OBSERVE = True


def test_span_inflight_accounting():
    config.ENABLE_SEMANTIC_CACHE_OBSERVE = True
    with obs.observe_span("bob", "hi"):
        snap = obs.snapshot()
        assert snap["inflight"] == 1
        assert snap["observed"] == 1
    # 退出后 inflight 归零
    assert obs.snapshot()["inflight"] == 0


def test_thread_local_user_isolation():
    config.ENABLE_SEMANTIC_CACHE_OBSERVE = True
    obs.set_current_user("carol")
    assert obs.get_current_user() == "carol"
    # 默认回落
    obs.set_current_user("")
    assert obs.get_current_user() == "?"
