# -*- coding: utf-8 -*-
"""432刀1：假死探测器「空 WARNING 刷屏」根因判据。

现场（logs/pulse.log + logs/pulse_crash.log，pid 18448）：
  `[pulse] WARNING:` 后面什么都没有。当日 06 点 14 次 / 07 点 60 次 /
  08 点 35 次，`pulse_crash.log` 累计 **145** 次 `WATCHDOG_DUMP`、6.1MB。

两层问题：
  ① 空 WARNING —— `_msg` 以换行符开头。logging 输出时首行是空串，后两行是
    **同一条多行消息的续行**（不带 level 前缀）⇒ 按 `WARNING:` 检索只看到空内容。
    消息其实一直都在，缺的是"首行"。
  ② 每分钟一条 —— 判据 `_stall >= 3` 只有 3×10s = 30s，而低活跃期
    `get_total_handled()` 实测**每 60s 才 +1**（`pulse_crash.log` 里
    175788→175789→175790→175791→175792，间隔严格 60s）⇒ 每两个 30s 窗口
    就有一个撞在"这 30s 内没涨"上 ⇒ 周期性必然误报。

判据设计（铁律 35：挑有判别力的量）
  · 阈值数值来自**源码**、空闲上限来自**日志**，两个不同入口 ⇒ 不是循环论证；
  · `_msg` 用 **AST 求值**取源码里真正会被执行的表达式，不做文本匹配 ——
    本刀要删/留的正是转义反斜杠，字符串匹配极易被"解析成真换行"坑掉。
"""

import ast
import io
import logging
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MAIN = os.path.join(ROOT, "main.py")

#: 低活跃期实测最长"无脉冲被处理"时长（秒）。来源：`logs/pulse_crash.log` 里
#: `WATCHDOG_DUMP ... handled=` 的相邻记录间隔——当日全部为 60s，无更长空窗。
IDLE_MAX_SEC = 60

#: 真冻结可接受的发现上限（秒）。超过它，探测器就失去意义。
FROZEN_DETECT_MAX_SEC = 300


def _src():
    with open(MAIN, encoding="utf-8") as _f:
        return _f.read()


def _stall_knobs():
    """从 main.py 源码取分层阈值的**实际赋值**（不是文档里的说法）。"""
    _s = _src()
    _n = re.search(r"^\s+_STALL_NOTE = (\d+)\b", _s, re.MULTILINE)
    _w = re.search(r"^\s+_STALL_WARN = (\d+)\b", _s, re.MULTILINE)
    assert _n is not None, "main.py 里找不到 `_STALL_NOTE = <int>`（分层判据被删了？）"
    assert _w is not None, "main.py 里找不到 `_STALL_WARN = <int>`（分层判据被删了？）"
    return int(_n.group(1)), int(_w.group(1))


def _render_msg_node(node):
    """把 `_msg = (...)` 的右值渲染成文本；FormattedValue 用 `<N>` 占位。

    这样拿到的是"源码里真正会被执行的表达式"展开后的形态，而不是某一行的字面量。
    """
    if isinstance(node, ast.JoinedStr):
        _out = []
        for _v in node.values:
            if isinstance(_v, ast.Constant):
                _out.append(str(_v.value))
            else:
                _out.append("<N>")
        return "".join(_out)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None  # 非字符串右值（如 `_msg = some_call()`）与本判据无关，跳过


def _find_msg_assigns():
    """找出所有 `_msg = <字符串>` 赋值的右值渲染文本（跳过非字符串右值）。"""
    _tree = ast.parse(_src(), filename=MAIN)
    _found = []
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Assign):
            for _t in _node.targets:
                if isinstance(_t, ast.Name) and _t.id == "_msg":
                    _r = _render_msg_node(_node.value)
                    if _r is not None:
                        _found.append(_r)
    return _found


def test_m432_d1_stall_thresholds_squeezed_both_ways():
    """判据 1：分层阈值被「实测空闲」与「发现上限」双向夹逼。

    · 下界：`_STALL_WARN × 10s` 必须**大于**实测最长空闲 60s，
      否则低活跃期仍会误报（这正是原来 30s 阈值栽的地方）；
    · 上界：不超过 300s，否则真冻结要等 5 分钟以上才被发现，探测器失去意义。
    """
    _note, _warn = _stall_knobs()
    assert _warn > _note, f"分层反了：WARN({_warn}) 应大于 NOTE({_note})"

    _warn_sec = _warn * 10
    assert _warn_sec > IDLE_MAX_SEC, (
        f"告警阈值 {_warn_sec}s 仍不大于实测最长空闲 {IDLE_MAX_SEC}s "
        f"⇒ 低活跃期会继续误报（原值 30s 就是这样刷出 145 次 dump 的）")
    assert _warn_sec <= FROZEN_DETECT_MAX_SEC, (
        f"告警阈值 {_warn_sec}s 超过 {FROZEN_DETECT_MAX_SEC}s "
        f"⇒ 真冻结的发现时间退化到不可接受")

    _note_sec = _note * 10
    assert _note_sec <= _warn_sec, "NOTE 阈值不该晚于 WARN"


def test_m432_d1_warning_msg_first_line_is_not_empty():
    """判据 2：`_msg` 首行非空，且不含换行符（"空 WARNING"假象的唯一来源）。

    为什么用 AST 而不是 `assert '\\n[假死探测器]' not in src`：
      main.py 里那个是**转义字面量**（反斜杠 + n 两个字符），在测试源码里写出来
      会被 Python 先解析成真换行 ⇒ 断言恒真，等于没测（铁律 39：探针要用判据的
      真实字面量，先读判据源再选用例）。
    """
    _msgs = _find_msg_assigns()
    assert _msgs, "main.py 里找不到 `_msg = ...` 赋值"

    _watchdog = [m for m in _msgs if "[假死探测器]" in m]
    assert _watchdog, (
        "找不到假死探测器的 `_msg`（消息里应含 [假死探测器]）—— "
        f"实际找到的 _msg 右值：{_msgs}")

    for _m in _watchdog:
        assert "\n" not in _m, (
            f"`_msg` 含换行符 ⇒ logging 首行会是空串，按 WARNING 检索只看到空内容。"
            f"实际：{_m!r}")
        assert _m.splitlines()[0].strip() if _m.splitlines() else "", "首行为空"
        assert _m.startswith("[假死探测器]"), (
            f"`_msg` 首行应以 [假死探测器] 开头（便于 grep 定位），实际：{_m[:40]!r}")


def test_m432_d1_message_actually_logs_a_nonempty_first_line():
    """判据 3：端到端 —— 用真 `logging` 打一遍，首行必须带内容。

    前两条是源码判据；这条把 `_msg` 喂给真实的 logging 管道，
    模拟 `grep "WARNING:"` 看到的东西。修复前那条会拿到空串。
    """
    _msgs = [m for m in _find_msg_assigns() if "[假死探测器]" in m]
    assert _msgs, "找不到假死探测器的 `_msg`"
    _text = _msgs[0]

    _buf = io.StringIO()
    _logger = logging.getLogger("m432_d1_probe")
    _logger.handlers = []
    _logger.propagate = False
    _h = logging.StreamHandler(_buf)
    _h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.WARNING)

    _logger.warning(_text)

    _lines = _buf.getvalue().splitlines()
    assert _lines, "logging 没有任何输出"
    _first = _lines[0]
    assert _first.startswith("WARNING: "), f"首行格式意外：{_first!r}"
    _payload = _first[len("WARNING: "):]
    assert _payload.strip(), (
        f"`grep WARNING:` 拿到的仍是空内容 —— 前导换行没去掉。完整首行：{_first!r}")
    assert "[假死探测器]" in _payload, (
        f"首行没带上器官标记，事后无法按名字检索。实际首行：{_first!r}")
