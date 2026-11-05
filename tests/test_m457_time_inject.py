# -*- coding: utf-8 -*-
"""457 刀3：肺在组装 system prompt 时注入「当前时间」。

★本文件守四件事
  1. 格式：`当前时间：YYYY-MM-DD HH:MM:SS（星期X）`，且**实时**（误差 ≤3s）；
  2. 只对用户对话注入：`_current_call_is_background=True` ⇒ 空串；
  3. 开关 `ENABLE_INJECT_CURRENT_TIME=False` ⇒ 空串（system 逐字回退）；
  4. 真的进了 messages[0]（不是只写了个函数没人调）。
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from organs.body import PulseLung as LUNG_MOD
from organs.body.PulseLung import PulseLung

PAT = re.compile(r"^当前时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})（星期[一二三四五六日]）$")


def _lung(background: bool = False):
    _l = PulseLung.__new__(PulseLung)
    _l._log = lambda *a, **k: None
    _l._current_call_is_background = background
    _l.get_dialog_history = list
    return _l


# ---------------------------------------------------------------- 纯函数
def test_prefix_format_and_freshness():
    _p = _lung()._m457_time_prefix()
    _m = PAT.match(_p)
    assert _m, _p
    _ts = time.mktime(time.strptime(_m.group(1), "%Y-%m-%d %H:%M:%S"))
    assert abs(time.time() - _ts) <= 3, (_p, time.time() - _ts)


def test_weekday_matches_system():
    _p = _lung()._m457_time_prefix()
    _wd = "一二三四五六日"[time.localtime().tm_wday]
    assert f"（星期{_wd}）" in _p


def test_background_call_gets_no_prefix():
    assert _lung(background=True)._m457_time_prefix() == ""


def test_switch_off_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_INJECT_CURRENT_TIME", False, raising=False)
    assert _lung()._m457_time_prefix() == ""


def test_default_switch_is_on():
    assert config.ENABLE_INJECT_CURRENT_TIME is True


# ---------------------------------------------------------------- 真进 messages
def test_system_prompt_starts_with_time():
    _msgs = _lung()._build_channel_messages("现在几点了？")
    assert isinstance(_msgs, list) and _msgs
    _sys = _msgs[0]
    assert _sys["role"] == "system"
    assert _sys["content"].startswith("当前时间："), _sys["content"][:40]
    # 用户内容一字不改
    assert _msgs[-1] == {"role": "user", "content": "现在几点了？"}


def test_system_prompt_unchanged_when_background():
    _msgs = _lung(background=True)._build_channel_messages("现在几点了？")
    assert not _msgs[0]["content"].startswith("当前时间：")


def test_system_prompt_unchanged_when_switch_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_INJECT_CURRENT_TIME", False, raising=False)
    _msgs = _lung()._build_channel_messages("现在几点了？")
    assert not _msgs[0]["content"].startswith("当前时间：")


def test_nonchannel_path_also_injected():
    """源码接线断言：单端点兜底路径（不走渠道）也必须带时间前缀。

    ★为什么用源码断言：那条路径要真实 urllib + 有效 key 才能跑通，
      单测里跑它等于打外网；这里只钉「调用点确实接上了」。
    """
    with open(LUNG_MOD.__file__, "r", encoding="utf-8") as _f:
        _src = _f.read()
    assert _src.count("self._m457_time_prefix() + _render_placeholders(") == 1
