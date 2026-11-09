# -*- coding: utf-8 -*-
"""480 刀13：元状态跨重启不再丢（台账 D1 / D4 / F3）。

## 判据来源（活体 2026-11-09 实测）

  · `SNAPSHOT_SAVE_JSON_BACKUP=False` ⇒ 主 JSON 不再写；
  · `data/knowledge/pulse_knowledge_snapshot.json` **已不存在**（只剩 11-03 的 805MB .bak）；
  · `extra_state` 只存在 JSON 头部，Parquet 主存储不携带；
  · `load()` 走 Parquet 时完全不读 JSON ⇒ `_extra_state` 保持 `{}`。

净效果：每次重启元状态**彻底归零**。

## 本文件钉住的三条

  ① 写：`_m480_save_meta_state()` 真能落盘且内容对；
  ② 读：`_extra_state` 为空时能从小文件恢复；
  ③ **只补空**：主路径已读到元状态时，小文件**绝不覆盖**（这是最容易被写错的一条）。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config
from nucleus.mnemosyne.PulseSnapshot import PulseSnapshot


def _mk(tmp_path, extra=None, cache=None):
    """最小快照对象：绕过 __init__ 的重依赖，只注入本刀用到的字段。"""
    s = PulseSnapshot.__new__(PulseSnapshot)
    s._extra_state = dict(extra or {})
    s.meta_state_path = str(tmp_path / "snapshot_meta_state.json")
    s._last_saved_checksum = "abc123"
    s.inference_cache = dict(cache or {})
    s._logs = []
    s._log = lambda level, msg, **k: s._logs.append(msg)
    return s


def test_m480_meta_state_save_creates_file(tmp_path):
    _s = _mk(tmp_path, extra={"organ_states": {"肝": {"x": 1}}, "interest": {"code": 0.55}})
    assert _s._m480_save_meta_state() is True
    _p = _s.meta_state_path
    assert os.path.exists(_p)
    with open(_p, encoding="utf-8") as _f:
        _d = json.load(_f)
    assert _d["extra_state"]["interest"]["code"] == 0.55
    assert _d["schema"] == "snapshot_meta_state/480.v1"


def test_m480_meta_state_restore_when_empty(tmp_path):
    _s = _mk(tmp_path, extra={"interest": {"code": 0.55}})
    _s._m480_save_meta_state()

    _s2 = _mk(tmp_path)                     # 新进程：_extra_state 为空
    assert _s2._extra_state == {}
    assert _s2._m480_restore_meta_state_if_empty() is True
    assert _s2._extra_state["interest"]["code"] == 0.55


def test_m480_meta_state_does_not_overwrite_nonempty(tmp_path):
    """核心判据：主路径已读到元状态时，小文件**绝不覆盖**。"""
    _s = _mk(tmp_path, extra={"interest": {"code": 0.55}})
    _s._m480_save_meta_state()

    _s2 = _mk(tmp_path, extra={"interest": {"code": 0.99}})   # 主路径读到了新值
    assert _s2._m480_restore_meta_state_if_empty() is False
    assert _s2._extra_state["interest"]["code"] == 0.99, "旧值把新值盖了 —— 这是本刀最怕的错"


def test_m480_meta_state_missing_file_is_safe(tmp_path):
    _s = _mk(tmp_path)
    assert _s._m480_restore_meta_state_if_empty() is False
    assert _s._extra_state == {}


def test_m480_meta_state_corrupt_file_is_safe(tmp_path):
    _s = _mk(tmp_path)
    with open(_s.meta_state_path, "w", encoding="utf-8") as f:
        f.write("{ not json")
    assert _s._m480_restore_meta_state_if_empty() is False


def test_m480_meta_state_switch_off(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_M480_META_STATE", False, raising=False)
    _s = _mk(tmp_path, extra={"x": 1})
    assert _s._m480_save_meta_state() is False
    assert not os.path.exists(_s.meta_state_path)
    assert _s._m480_restore_meta_state_if_empty() is False


def test_m480_meta_state_default_on():
    assert PulseSnapshot._m480_meta_state_on() is True


def test_m480_d1_wired_in_save_and_load():
    """接线断言：save 成功分支与 load 末尾都必须调用（防只在辅助函数里写对了没接）。"""
    with open(os.path.join(_ROOT, "nucleus", "mnemosyne", "PulseSnapshot.py"),
              encoding="utf-8") as _f:
        _src = _f.read()
    assert "self._m480_save_meta_state()" in _src
    assert "self._m480_restore_meta_state_if_empty()" in _src
    _i_save = _src.find("self._m480_save_meta_state()")
    _i_l1 = _src.find("self.save_l1()", _i_save)
    assert 0 < _i_save < _i_l1, "写元状态应在 save_l1 之前（同一成功分支内）"
