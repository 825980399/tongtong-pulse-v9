# -*- coding: utf-8 -*-
"""370 刀1 定向单测：视觉流**回退分支**同构原子写（D4 真回归修复）。

★被测面：`organs/senses/PulseVisualCortex.py::_log_writer_loop` 的 rename 被拒回退分支。
★注入口径：只 monkeypatch `os.replace` / `builtins.open`，**不落生产 data/**（铁律158）。
★正负样本对称：每个用例都同时断言「该发生的发生了」与「不该发生的没发生」。
"""
import builtins
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from nucleus.const import LogLevel  # noqa: E402
from organs.senses.PulseVisualCortex import PulseVisualCortex  # noqa: E402

_REAL_OPEN = builtins.open


# ------------------------------------------------------------------ 夹具
class _OnceFlag:
    """一次性开关：首次为真、之后为假 ⇒ `_log_writer_loop` 只跑一轮即退出。"""

    def __init__(self):
        self._v = True

    def __bool__(self):
        v = self._v
        self._v = False
        return v


class _Harness:
    """只借用被测方法的薄壳：不构造真实器官（避免引擎/摄像头初始化副作用）。"""

    _log_writer_loop = PulseVisualCortex._log_writer_loop
    _m363_note_rename_fail = PulseVisualCortex._m363_note_rename_fail

    def __init__(self):
        import threading

        self.logs = []
        self._log_lock = threading.Lock()
        self._log_queue = []
        self._log_running = _OnceFlag()

    def _log(self, level, msg):
        self.logs.append((level, msg))

    def run(self, log_path):
        self._log_writer_loop(str(log_path))

    def texts(self):
        return "\n".join(str(m) for _lv, m in self.logs)

    def has(self, level, needle):
        return any(lv == level and needle in str(m) for lv, m in self.logs)


def _seed(path, rows):
    with _REAL_OPEN(str(path), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)


def _leftover_tmp(d):
    return [n for n in os.listdir(str(d))
            if n.startswith(".vision_stream_log.") and n.endswith(".tmp")]


def _new_replace(monkeypatch, fail_times):
    """fail_times: 前 N 次 os.replace 抛 PermissionError（模拟 WinError 5 占用）。"""
    real_replace = os.replace
    state = {"n": 0}

    def _fake(a, b, *x, **k):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise PermissionError(5, "拒绝访问（模拟 rename 被拒）")
        return real_replace(a, b, *x, **k)

    monkeypatch.setattr(os, "replace", _fake)
    return state


# ------------------------------------------------------------------ 用例
def test_01_rename_rejected_falls_back_to_atomic_write(tmp_path, monkeypatch):
    """① rename 被拒 ⇒ 回退分支走**临时文件替换**，既有日志不得被截断。"""
    log = tmp_path / "vision_stream_log.json"
    _seed(log, [{"seq": 1}, {"seq": 2}])
    state = _new_replace(monkeypatch, fail_times=1)

    h = _Harness()
    h._log_queue.append({"seq": 3})
    h.run(log)

    data = json.loads(_REAL_OPEN(str(log), encoding="utf-8").read())
    assert [d["seq"] for d in data] == [1, 2, 3], "回退分支不得截断既有条目"
    assert state["n"] == 2, "回退分支应再执行一次原子 replace（实际 %d）" % state["n"]
    assert h.has(LogLevel.WARNING, "370刀1"), "应留 370刀1 回退补写告警"
    assert not h.has(LogLevel.WARNING, "已回退为整体重写"), "未触发三级兜底时不该报退化"
    assert not _leftover_tmp(tmp_path), "临时文件不得残留"


def test_02_replace_retry_fails_then_truncate_then_error(tmp_path, monkeypatch):
    """② replace 再失败 ⇒ 兜底截断写＋WARNING；截断写也失败 ⇒ ERROR 留痕（不静默）。"""
    # --- 第一段：原子写两次都被拒 ⇒ 退化为截断写 ---
    log = tmp_path / "vision_stream_log.json"
    _seed(log, [{"seq": 1}])
    st = _new_replace(monkeypatch, fail_times=99)
    h = _Harness()
    h._log_queue.append({"seq": 2})
    h.run(log)
    data = json.loads(_REAL_OPEN(str(log), encoding="utf-8").read())
    assert [d["seq"] for d in data] == [1, 2], "兜底截断写仍需写入本批条目"
    assert h.has(LogLevel.WARNING, "已回退为整体重写"), "应留三级兜底 WARNING"
    assert st["n"] >= 2, "原子写应至少尝试两次（实际 %d）" % st["n"]

    # --- 第二段：截断写也失败 ⇒ ERROR 留痕（本批条目丢失口径保留）---
    log2 = tmp_path / "b" / "vision_stream_log.json"
    os.makedirs(str(log2.parent), exist_ok=True)
    _seed(log2, [{"seq": 9}])
    _new_replace(monkeypatch, fail_times=99)

    def _fake_open(path, mode="r", *a, **k):
        if str(path) == str(log2) and "w" in mode:
            raise OSError("EACCES simulated: 截断写被拒")
        return _REAL_OPEN(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", _fake_open)
    h2 = _Harness()
    h2._log_queue.append({"seq": 10})
    h2.run(log2)
    monkeypatch.setattr(builtins, "open", _REAL_OPEN)  # 立即还原，避免影响收尾断言

    assert h2.has(LogLevel.ERROR, "回退写也失败"), "截断写失败必须 ERROR 留痕，不得静默"
    assert h2.has(LogLevel.ERROR, "本批条目丢失"), "条目丢失口径须保留"
    assert not _leftover_tmp(log2.parent), "临时文件不得残留"


def test_03_main_path_atomic_write_not_regressed(tmp_path, monkeypatch):
    """③ 主路径（rename 未被拒）原子写行为不回归：不进回退、不留回退告警。"""
    log = tmp_path / "vision_stream_log.json"
    _seed(log, [{"seq": 1}])
    state = _new_replace(monkeypatch, fail_times=0)

    h = _Harness()
    h._log_queue.append({"seq": 2})
    h.run(log)

    data = json.loads(_REAL_OPEN(str(log), encoding="utf-8").read())
    assert [d["seq"] for d in data] == [1, 2]
    assert state["n"] == 1, "主路径应只 replace 一次（实际 %d）" % state["n"]
    assert not h.has(LogLevel.WARNING, "370刀1"), "主路径成功不得打回退告警"
    assert not h.has(LogLevel.WARNING, "已回退为整体重写"), "主路径成功不得打兜底告警"
    assert not hasattr(h, "_m363_rename_last"), "主路径成功不得记 rename 失败快照"
    assert not _leftover_tmp(tmp_path), "临时文件不得残留"


def test_04_no_temp_leftover_when_fallback_writes(tmp_path, monkeypatch):
    """④ 回退分支异常/成功各路径退出后，临时文件零残留（临时文件清理口径与主路径一致）。"""
    log = tmp_path / "vision_stream_log.json"
    _seed(log, [{"seq": 1}])
    _new_replace(monkeypatch, fail_times=99)  # 原子写恒失败 ⇒ 兜底截断写

    h = _Harness()
    h._log_queue.append({"seq": 2})
    h.run(log)

    # 主路径 mkstemp 出来的临时文件必须被回退分支接手并在 finally 清掉
    assert not _leftover_tmp(tmp_path), "回退分支退出后临时文件残留：%s" % _leftover_tmp(tmp_path)
    assert h.has(LogLevel.WARNING, "已回退为整体重写"), "应走三级兜底并留痕"


def test_05_rename_fail_probe_preserved(tmp_path, monkeypatch):
    """⑤ 边界：363 E5 的占用探测与失败计数不得被本刀删掉（不越权改闸）。"""
    log = tmp_path / "vision_stream_log.json"
    _seed(log, [{"seq": 1}])
    _new_replace(monkeypatch, fail_times=1)

    h = _Harness()
    h._log_queue.append({"seq": 2})
    h.run(log)

    assert h._m363_rename_fail == 1, "rename 失败计数口径须保留"
    assert isinstance(getattr(h, "_m363_rename_last", None), dict), "末次快照须保留"
    assert "target_locked" in h._m363_rename_last, "占用探测字段须保留"


class _BoomWriter:
    """模拟「截断写已打开（文件已被 truncate）但写中途失败」——363 E5 裸写的真危害面。"""

    def __init__(self):
        self.closed = False

    def write(self, _s):
        raise OSError("EIO simulated: 写中途失败")

    def flush(self):
        pass

    def fileno(self):
        raise OSError("no fileno")

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_06_fallback_must_not_truncate_existing_log(tmp_path, monkeypatch):
    """⑥ ★判别力锚：rename 被拒场景下，回退分支**绝不能**走裸 `open(log_path,'w')`。

    构造：让裸写「打开成功（文件已 truncate）但写中途抛」——这正是 363 E5 会
    把既有视觉流日志写坏的路径。新实现全程走临时文件 ⇒ 该路径根本不被触发。
    """
    log = tmp_path / "vision_stream_log.json"
    _seed(log, [{"seq": 1}])
    state = _new_replace(monkeypatch, fail_times=1)  # 主路径被拒，回退重试放行

    def _fake_open(path, mode="r", *a, **k):
        if str(path) == str(log) and "w" in mode:
            _REAL_OPEN(path, mode, *a, **k).close()  # 真截断 ⇒ 裸写必丢既有内容
            return _BoomWriter()
        return _REAL_OPEN(path, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", _fake_open)
    h = _Harness()
    h._log_queue.append({"seq": 2})
    h.run(log)
    monkeypatch.setattr(builtins, "open", _REAL_OPEN)

    data = json.loads(_REAL_OPEN(str(log), encoding="utf-8").read())
    assert [d["seq"] for d in data] == [1, 2], (
        "既有日志被写坏＝回退分支仍走了裸截断写；实测 %r" % data)
    assert state["n"] == 2, "应走原子 replace 重试，而非裸写"
    assert not h.has(LogLevel.ERROR, "本批条目丢失"), "原子写路径不应触发条目丢失 ERROR"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
