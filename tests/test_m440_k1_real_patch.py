# -*- coding: utf-8 -*-
"""441 刀1（补 440 欠账）：真补丁链路的**纪律**判据。

440 批在真实源码上跑通了链路，但施工中踩了两件事，这里各钉一条：
  ① 首版 harness 把 apply 写在闸门**之前** ⇒ 闸门判未通过时源码已被改（越权）；
  ② 写入用 `newline=""` 把 CRLF 写成 LF（3846 bare LF）。
本文件守的是「脚本层面必须做到的两条」，不重复验证业务逻辑。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)



def _harness_src() -> str:
    with open(os.path.join(_ROOT, "tmp", "_m440_k1_pilot.py"), encoding="utf-8") as _f:
        return _f.read()


def test_gate_runs_before_apply():
    """闸门判断必须出现在写源码之前（越权防线）。"""
    _s = _harness_src()
    _gate = _s.find('if not _dec["approved"]:')
    _apply = _s.find("with open(REL, \"w\"")
    assert _gate != -1, "harness 里找不到闸门前置判断"
    assert _apply != -1, "harness 里找不到写源码语句"
    assert _gate < _apply, f"闸门在第 {_gate} 字符、写盘在第 {_apply} 字符 —— 顺序反了"


def test_backup_before_apply():
    """必须先备份再改源码。"""
    _s = _harness_src()
    _bak = _s.find("shutil.copy2(REL, BAK)")
    _apply = _s.find("with open(REL, \"w\"")
    assert _bak != -1 and _apply != -1
    assert _bak < _apply


def test_harness_rolls_back_on_compile_error():
    """编译失败必须从备份回滚（不许留下半坏文件）。"""
    _s = _harness_src()
    assert "shutil.copy2(BAK, REL)" in _s, "编译失败路径没有回滚"


# ---------------------------------------------------------------- 行尾守恒
def _crlf_stats(path: str) -> tuple[int, int]:
    with open(path, "rb") as _f:
        _d = _f.read()
    _crlf = _d.count(b"\r\n")
    return _crlf, _d.count(b"\n") - _crlf


def test_production_target_keeps_its_line_ending():
    """440 改过的生产文件必须仍是**单一**行尾（不许 CRLF/LF 混杂）。"""
    _p = os.path.join(_ROOT, "organs", "motor", "PulseController.py")
    _crlf, _bare = _crlf_stats(_p)
    assert (_crlf == 0) != (_bare == 0), \
        f"行尾混杂：CRLF={_crlf} bare_LF={_bare}"


def test_crlf_conversion_roundtrip(tmp_path):
    """LF→CRLF 转换的可逆性（440 修复事故时用的就是这条口径）。"""
    _p = str(tmp_path / "x.py")
    _orig = b"a\nb\nc\n"
    with open(_p, "wb") as _f:
        _f.write(_orig)
    with open(_p, "rb") as _f:
        _d = _f.read()
    assert b"\r\n" not in _d
    with open(_p, "wb") as _f:
        _f.write(_d.replace(b"\n", b"\r\n"))
    _crlf, _bare = _crlf_stats(_p)
    assert _crlf == 3 and _bare == 0
    # 转回来必须逐字节等于原文
    with open(_p, "rb") as _f:
        _back = _f.read().replace(b"\r\n", b"\n")
    assert _back == _orig


# ---------------------------------------------------------------- 真补丁留下的事实
def test_real_patch_still_present():
    """440 的真补丁必须还在（补留痕），否则"破零"就退回原点了。"""
    _p = os.path.join(_ROOT, "organs", "motor", "PulseController.py")
    with open(_p, encoding="utf-8") as _f:
        _s = _f.read()
    assert "关键词提取失败（已留痕）" in _s, "440 的真补丁不见了"
