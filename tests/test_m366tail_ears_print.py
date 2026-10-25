# -*- coding: utf-8 -*-
"""366 后续 · 回归：PulseEars 业务代码禁 print（365-C2 引入，本批修）。

病根：`_m365_note_voice_failure` 在 `logger_self._log` 失败时用 `print(_msg)` 兜底，
而 `test_log_governance_m37::test_30` 明确「业务代码不得有 print（框架运行时信息必须走日志）」
⇒ 该文件 print 计数 1 ⇒ 判红。修法：改走模块级 logger（可见但不占 stdout）。

★为何要单独锁：这条回归是**跨批潜伏**的 —— C2 落地时 m37 的红被"N5 未逐例归族"掩盖，
本批归族才查出来。锁住它，避免下次再有人在这里加 print。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EARS = os.path.join(_ROOT, "organs", "senses", "PulseEars.py")


class TestEarsNoPrint:
    def test_01_fallback_logger_in_place(self):
        """兜底出口必须在位：模块级 logger 存在且是 Logger 实例。"""
        from organs.senses import PulseEars as pe

        assert hasattr(pe, "_m365_fb_log")
        assert pe._m365_fb_log.name == "pulse.organ.ears"

    def test_02_no_print_in_note_voice_failure(self):
        """留痕函数体内不得有 print（m37 口径），但必须仍有兜底调用。"""
        src = io.open(_EARS, encoding="utf-8").read()
        _start = src.index("def m365_note_voice_failure")
        _end = src.index("def m365_voice_failure_report")
        body = src[_start:_end]
        assert "print(" not in body, "留痕函数里又出现了 print"
        assert "_m365_fb_log.warning" in body, "兜底出口丢了（异常会无声消失）"

    def test_03_still_never_raises_when_logger_broken(self):
        """logger 自身抛异常时，留痕函数仍不得向上抛（降级不能变成崩溃）。"""
        from organs.senses import PulseEars as pe

        class _Boom:
            def _log(self, *a, **k):
                raise RuntimeError("logger boom")

        cls = pe.m365_note_voice_failure(_Boom(), RuntimeError("mic gone"), "t366tail")
        assert cls, "应返回分类名"
        assert int(pe.m365_voice_failure_report()["total"]) >= 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
