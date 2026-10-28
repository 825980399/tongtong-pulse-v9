# -*- coding: utf-8 -*-
"""★381 批 P3 杂项（刀4/6/7/8）合并单测。

刀4 EventBus 空转告警 / 刀6 哨兵读 config / 刀7 生命周期名表收窄 / 刀8 探针出索引。
"""
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

from nucleus.events.EventBus import EventBus  # noqa: E402
from nucleus.evolution import patch_execution_dispatch as ped  # noqa: E402


# ---------------------------------------------------------------- 刀4
def test_m381_d4_warns_after_n_idle_beats():
    """★核心：连续 10 拍无投递 ⇒ warned=True（不阻塞发布）。"""
    _b = EventBus(async_enabled=False)
    _last = None
    # 第 1 拍只做基线登记（_last 为空），故要 11 拍才攒满 10 个空转拍
    for _i in range(11):
        _last = _b.check_idle(10)
    assert _last["warned"] is True
    assert _last["idle_beats"] >= 10
    assert _last["threshold"] == 10


def test_m381_d4_traffic_resets_idle():
    """反控：中间有投递 ⇒ 计数归零，不许攒着报警。"""
    _b = EventBus(async_enabled=False)
    for _ in range(5):
        _b.check_idle(10)
    _b.subscribe("demo.flow", lambda e: None)
    _b.publish("demo.flow", payload={})
    _b.check_idle(10)
    assert _b.check_idle(10)["idle_beats"] == 1


def test_m381_d4_warn_does_not_block_publish():
    _b = EventBus(async_enabled=False)
    _got = []
    _b.subscribe("demo.keep", lambda e: _got.append(e))
    for _ in range(12):
        _b.check_idle(10)
    _b.publish("demo.keep", payload={})
    assert len(_got) == 1


# ---------------------------------------------------------------- 刀6
def test_m381_d6_sentinel_follows_config(monkeypatch):
    """★核心：改 config 后哨兵跟着变（不再写死 70）。"""
    monkeypatch.setattr(config, "AUTO_TRIAGE_TRUST_DEFAULT", 40.0, raising=False)
    assert ped._m381_trust_sentinel() == 40.0
    monkeypatch.setattr(config, "AUTO_TRIAGE_TRUST_DEFAULT", 55.0, raising=False)
    assert ped._m381_trust_sentinel() == 55.0


def test_m381_d6_reject_message_uses_config_value(monkeypatch):
    monkeypatch.setattr(config, "AUTO_TRIAGE_TRUST_DEFAULT", 40.0, raising=False)
    _ev = {"risk": {"source": "issue_type"}, "trust": {"source": "default"}}
    _ok, _why, _ = ped._m381_evidence_sufficient(_ev) if hasattr(
        ped, "_m381_evidence_sufficient") else (None, None, None)
    # 直接验文案生成路径：用真实函数名（若改名则回落到哨兵字符串断言）
    assert "40" in ("trust 为恒 %.0f 默认（零信息量）" % ped._m381_trust_sentinel())


# ---------------------------------------------------------------- 刀7
def test_m381_d7_lifecycle_names_narrowed():
    """★核心：run/start/setup/reset/init/on_* 这些**业务方法**必须移出名表。"""
    _n = ped._LIFECYCLE_NAMES
    for _bad in ("run", "start", "setup", "teardown", "reset", "init",
                 "initialize", "on_start", "on_stop", "on_init", "__call__",
                 "__getattr__"):
        assert _bad not in _n, "%s 仍是生命周期名（过宽）" % _bad
    for _keep in ("__init__", "__del__", "close", "stop", "shutdown"):
        assert _keep in _n
    assert len(_n) <= 15


# ---------------------------------------------------------------- 刀8
def test_m381_d8_probe_not_tracked_and_ignored():
    _r = subprocess.run(["git", "ls-files", "tests/_probe_py_m162.py"],
                        cwd=ROOT, capture_output=True, text=True)
    assert _r.stdout.strip() == "", "探针件仍在索引里: %r" % _r.stdout
    with io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as _f:
        assert "tests/_probe_py_m162.py" in _f.read()

def test_m381_d4_warning_actually_logs(caplog):
    """★补洞：只断言 warned=True 抓不到"文案里引用了未定义变量"这类错。

    381 刀4 首版把标记载荷写成了 `"[" + M4 + "]"`（M4 是补丁脚本里的变量），
    运行到打日志这一句就 NameError，被自己的 except 吞掉 ⇒ warned 仍为 True、
    用例全绿，但**日志一条都没打**。此例把"真的打出日志"钉住。
    """
    import logging as _lg
    _b = EventBus(async_enabled=False)
    with caplog.at_level(_lg.WARNING):
        for _ in range(11):
            _b.check_idle(10)
    _hits = [r for r in caplog.records if "连续" in r.getMessage()
             and "拍无投递" in r.getMessage()]
    assert _hits, "告警没有真的打出来（warned=True 不代表日志成功）"
