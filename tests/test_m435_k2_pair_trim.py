# -*- coding: utf-8 -*-
"""第435批刀2 单测：pair trim 改「尾部 block 读 + 分块复制 + 硬链接备份」。

守五件事：
  ① `_m435_tail_scan` 定位准确（末尾有/无换行两种口径）；
  ② `_backup_pair_file` 走 `os.link`（零拷贝，不再 copy2 全文）；
  ③ 硬链接失败 ⇒ 回退 copy2（备份一定在）；
  ④ `_trim_pair_file` 源码里**没有** `readlines()`（不全文读进内存）；
  ⑤ 行为不变：130 行裁到 90、首行 seq=40（与 431 刀2 同口径）。
"""
import inspect
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import config
from utils import pulse_tracer as pt


def _write_pairs(path, n):
    with open(path, "w", encoding="utf-8") as _f:
        _f.writelines(json.dumps({"seq": _i, "organ": "x"}) + "\n" for _i in range(n))


def _lines(path):
    with open(path, "r", encoding="utf-8") as _f:
        return _f.read().splitlines()


# ---------------------------------------------------------------- ① tail_scan
def test_tail_scan_with_trailing_newline(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_bytes(b"a\nb\nc\n")  # 二进制精确 LF（文本模式在 Windows 会转 CRLF）
    with open(p, "rb") as f:
        total, start = pt._m435_tail_scan(f, 2)
    assert total == 3, f"总行数={total}（应 3）"
    assert start == 2, f"起始偏移={start}（应 2，即 'b\\nc\\n'）"


def test_tail_scan_without_trailing_newline(tmp_path):
    p = tmp_path / "b.jsonl"
    p.write_bytes(b"a\nb\nc")
    with open(p, "rb") as f:
        total, start = pt._m435_tail_scan(f, 2)
    assert total == 3, f"总行数={total}（末尾无换行应补 1 得 3）"
    assert start == 2, f"起始偏移={start}（应 2，即 'b\\nc'）"


def test_tail_scan_crlf_file(tmp_path):
    """pairs 真实行尾是 CRLF（Windows 文本模式写入）——偏移应落在 `b` 的开头。"""
    p = tmp_path / "crlf.jsonl"
    p.write_bytes(b"a\r\nb\r\nc\r\n")
    with open(p, "rb") as f:
        total, start = pt._m435_tail_scan(f, 2)
    assert total == 3
    assert start == 3, f"CRLF 下起始偏移={start}（应 3，即 'b\\r\\nc\\r\\n'）"


def test_tail_scan_fewer_lines_than_keep(tmp_path):
    p = tmp_path / "c.jsonl"
    p.write_text("a\nb\n", encoding="utf-8")
    with open(p, "rb") as f:
        total, start = pt._m435_tail_scan(f, 10)
    assert total == 2
    assert start == 0, "行数不足 keep 时应返回 0（调用方据此判不裁）"


# ---------------------------------------------------------------- ②③ 备份
def test_backup_uses_hardlink(tmp_path, monkeypatch):
    _calls = []
    _orig = os.link

    def _spy(a, b, *args, **kw):
        _calls.append((str(a), str(b)))
        return _orig(a, b, *args, **kw)

    monkeypatch.setattr(pt.os, "link", _spy)
    p = tmp_path / "p.jsonl"
    p.write_text("a\nb\nc\n", encoding="utf-8")
    _bp = pt._backup_pair_file(str(p))
    assert len(_calls) == 1, "备份应走 os.link（硬链接零拷贝）"
    assert _bp.endswith(".trim.bak")


def test_backup_falls_back_to_copy2_when_link_fails(tmp_path, monkeypatch):
    def _link_fail(a, b, *args, **kw):
        raise OSError("link not supported")

    monkeypatch.setattr(pt.os, "link", _link_fail)
    p = tmp_path / "q.jsonl"
    p.write_text("a\nb\nc\n", encoding="utf-8")
    _bp = pt._backup_pair_file(str(p))
    assert os.path.exists(_bp), "硬链接失败应回退 copy2 留下备份"
    with open(_bp, encoding="utf-8") as f:
        assert f.read() == "a\nb\nc\n", "回退备份内容应等于原文"


# ---------------------------------------------------------------- ④ 不 readlines
def test_trim_pair_file_has_no_readlines():
    """去掉 docstring 后检查**代码体**——docstring 里可以提 readlines，实现里不许用。"""
    import ast
    import textwrap

    _src = textwrap.dedent(inspect.getsource(pt._trim_pair_file))
    _fn = ast.parse(_src).body[0]
    _body = _fn.body[1:] if (_fn.body and isinstance(_fn.body[0], ast.Expr)
                             and isinstance(_fn.body[0].value, ast.Constant)) else _fn.body
    _code = ast.unparse(ast.Module(body=_body, type_ignores=[]))
    assert "readlines" not in _code, "_trim_pair_file 代码体仍在 readlines 全文（刀2 未生效）"
    assert "_m435_tail_scan" in _code, "未走尾部扫描定位"


# ---------------------------------------------------------------- ⑤ 行为不变
def test_trim_behaviour_unchanged(tmp_path, monkeypatch):
    monkeypatch.setitem(config.RUNTIME_PARAMS, "pulse_pair_file_cap", 100)
    monkeypatch.setitem(config.RUNTIME_PARAMS, "pulse_pair_trim_low_ratio", 0.9)
    pt._PAIR_LINE_STATE["path"] = None
    pt._PAIR_LINE_STATE["lines"] = 0
    p = tmp_path / "r.jsonl"
    _write_pairs(str(p), 130)
    pt._trim_pair_file(str(p))
    _kept = [json.loads(x)["seq"] for x in _lines(str(p))]
    assert len(_kept) == 90, f"130 行应裁到 90，实际 {len(_kept)}"
    assert _kept[0] == 40, f"首行应 seq=40，实际 {_kept[0]}"
    assert _kept[-1] == 129
