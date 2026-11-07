# -*- coding: utf-8 -*-
"""第一批自有修复算子（code_repair 域）。

这三个算子不是示例、不是编造的样例，而是把 **473 批刀22b / 刀22c 我手工做的
两次收口原样抽出来**：有真实输入（`715f` / `aed5` 的落地补丁）、真实输出、
真实验收记录。它们是铁律 304「把经验编译成可执行算子」的第一个可运行证据。

共同约束
--------
* 全部确定性、不调外部 LLM、不写盘、不改传入对象。
* ``detect`` 对已合格输入必须返回 False（判别力，铁律 35）。
* ``verify`` 用**独立入口**（ast.parse + 重新解析文本），不复用 synthesize
  的中间变量（铁律 30）。
"""
from __future__ import annotations

import ast
import re
import textwrap

from nucleus._silent_except import silent_exc

from .kernel import Capability, CapabilityContext, CapabilityResult

DOMAIN_CODE_REPAIR = "code_repair"
_ORIG = "original_code"
_MOD = "modified_code"


def _width(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _first_def(code: str) -> tuple:
    """返回 (行号, 行文本, 缩进宽度)；找不到返回 (None, None, -1)。"""
    for _i, _l in enumerate(str(code or "").split("\n")):
        if re.match(r"^\s*(?:async\s+)?def\s", _l):
            return _i, _l, _width(_l)
    return None, None, -1


def _body_width(code: str, after: int) -> int:
    """def 之后第一个非空、非注释、非 docstring 起始行的宽度。"""
    for _l in str(code or "").split("\n")[after + 1:]:
        if not _l.strip() or _l.strip().startswith("#"):
            continue
        if _l.strip().startswith(('"""', "'''")):
            continue
        return _width(_l)
    return -1


def _syntax_ok(code: str) -> tuple:
    """独立语法入口：dedent 后整体 parse（兼容 0 格与 4 格 def）。"""
    try:
        ast.parse(textwrap.dedent(str(code or "")))
        return True, ""
    except (SyntaxError, ValueError) as _e:
        silent_exc(_e, where="autonomy.operators::_syntax_ok", level="debug")
        return False, f"{type(_e).__name__}: {_e}"


def _docstring_lines(code: str, after: int) -> list:
    """抽取 def 之后紧邻的 docstring 文本行（含三引号），取不到返回 []。"""
    _lines = str(code or "").split("\n")
    _i = after + 1
    while _i < len(_lines) and not _lines[_i].strip():
        _i += 1
    if _i >= len(_lines):
        return []
    _s = _lines[_i].strip()
    if not _s.startswith(('"""', "'''")):
        return []
    _q = _s[:3]
    if _s.count(_q) >= 2 and len(_s) > 3:
        return [_lines[_i]]
    _out = [_lines[_i]]
    for _j in range(_i + 1, len(_lines)):
        _out.append(_lines[_j])
        if _q in _lines[_j]:
            break
    return _out


class IndentLevelAlignCapability(Capability):
    """方法体整体多缩 N 格 ⇒ 平移回 ``def 缩进 + 4``。

    473 刀22c 现场：`set_self_awareness` 的 def 是 4 格、方法体首行 12 格，
    语法仍合法（Python 允许任意一致缩进），因此所有既有闸门全部放行。
    """

    capability_id = "code_repair.indent_level_align"
    domains = (DOMAIN_CODE_REPAIR,)
    error_type = "body_over_indented"

    def detect(self, ctx: CapabilityContext) -> bool:
        _code = str(ctx.artifact.get(_MOD) or "")
        _i, _l, _w = _first_def(_code)
        if _i is None or _w < 0:
            return False
        _bw = _body_width(_code, _i)
        return _bw > _w + 4

    def synthesize(self, ctx: CapabilityContext) -> CapabilityResult:
        _code = str(ctx.artifact.get(_MOD) or "")
        _i, _l, _w = _first_def(_code)
        _bw = _body_width(_code, _i)
        _delta = _bw - (_w + 4)
        _lines = _code.split("\n")
        for _j in range(_i + 1, len(_lines)):
            _ln = _lines[_j]
            if not _ln.strip():
                continue
            if re.match(r"^\s*def\s", _ln) and _width(_ln) <= _w:
                break
            _cur = _width(_ln)
            _lines[_j] = _ln[_delta:] if _cur >= _delta else _ln.lstrip(" ")
        _out = dict(ctx.artifact)
        _out[_MOD] = "\n".join(_lines)
        return CapabilityResult(
            capability_id=self.capability_id, status="applied", artifact=_out,
            changes=[f"body_indent {_bw} -> {_w + 4}"],
            reason="方法体整体左移至 def+4")

    def verify(self, before: dict, after: dict, ctx: CapabilityContext) -> tuple:
        _code = str(after.get(_MOD) or "")
        _ok, _why = _syntax_ok(_code)
        if not _ok:
            return False, _why
        _i, _l, _w = _first_def(_code)
        if _i is None:
            return False, "def 丢失"
        return (_body_width(_code, _i) == _w + 4), "body 宽度不等于 def+4"


class SignatureDocstringRestoreCapability(Capability):
    """LLM 生成常丢类型注解与 docstring ⇒ 从 original_code 回填。

    473 刀22b/c 现场：`(path: str | None = None) -> str` 被写成 `(path=None)`，
    整段 docstring 消失。这里只做**从原文提取后回填**，不编造任何语义。
    """

    capability_id = "code_repair.signature_docstring_restore"
    domains = (DOMAIN_CODE_REPAIR,)
    error_type = "signature_or_docstring_lost"

    def detect(self, ctx: CapabilityContext) -> bool:
        _o = str(ctx.artifact.get(_ORIG) or "")
        _m = str(ctx.artifact.get(_MOD) or "")
        _io, _lo, _ = _first_def(_o)
        _im, _lm, _ = _first_def(_m)
        if _io is None or _im is None:
            return False
        _lod = bool(_docstring_lines(_o, _io))
        _lmd = bool(_docstring_lines(_m, _im))
        _sig_lost = (("->" in _lo or ":" in _lo.split("(", 1)[-1])
                     and _lo.strip() != _lm.strip())
        return _sig_lost or (_lod and not _lmd)

    def synthesize(self, ctx: CapabilityContext) -> CapabilityResult:
        _o = str(ctx.artifact.get(_ORIG) or "")
        _m = str(ctx.artifact.get(_MOD) or "")
        _io, _lo, _ = _first_def(_o)
        _im, _lm, _ = _first_def(_m)
        _lines = _m.split("\n")
        _changes = []
        if _lo.strip() != _lm.strip():
            _lines[_im] = _lo
            _changes.append("signature_restored")
        _old_doc = _docstring_lines(_o, _io)
        _new_doc = _docstring_lines(_m, _im)
        if _old_doc and not _new_doc:
            _indent = " " * (_width(_lo) + 4)
            _insert = [_indent + _d.lstrip() if _d.strip() else "" for _d in _old_doc]
            _lines[_im + 1:_im + 1] = _insert
            _changes.append("docstring_restored")
        _out = dict(ctx.artifact)
        _out[_MOD] = "\n".join(_lines)
        return CapabilityResult(
            capability_id=self.capability_id, status="applied", artifact=_out,
            changes=_changes, reason="从 original_code 回填签名/docstring")

    def verify(self, before: dict, after: dict, ctx: CapabilityContext) -> tuple:
        _o = str(after.get(_ORIG) or "")
        _m = str(after.get(_MOD) or "")
        _ok, _why = _syntax_ok(_m)
        if not _ok:
            return False, _why
        _io, _lo, _ = _first_def(_o)
        _im, _lm, _ = _first_def(_m)
        if _im is None:
            return False, "def 丢失"
        if _lo.strip() != _lm.strip():
            return False, "签名未回填"
        _old_doc = _docstring_lines(_o, _io)
        if _old_doc and not _docstring_lines(_m, _im):
            return False, "docstring 未回填"
        return True, ""


class SilentExcBackfillCapability(Capability):
    """把 ``except X: pass`` 补成 ``silent_exc``（铁律 4，对 tests/ 同受管）。

    473 刀22b 现场：落地补丁引入裸 `except OSError: pass`，cw2 门禁会拦。
    """

    capability_id = "code_repair.silent_exc_backfill"
    domains = (DOMAIN_CODE_REPAIR,)
    error_type = "bare_silent_except"

    _PAT = re.compile(r"(?m)^([ \t]*)except([^\n]*):\n[ \t]*pass[ \t]*$")

    def detect(self, ctx: CapabilityContext) -> bool:
        return bool(self._PAT.search(str(ctx.artifact.get(_MOD) or "")))

    def synthesize(self, ctx: CapabilityContext) -> CapabilityResult:
        _code = str(ctx.artifact.get(_MOD) or "")
        _n = [0]

        def _sub(_m):
            _n[0] += 1
            _ind, _rest = _m.group(1), _m.group(2).strip()
            _exc = _rest or "Exception"
            _body = _ind + "    "
            return (f"{_ind}except {_exc} as _e474:\n"
                    f"{_body}silent_exc(_e474, where=\"autonomy.repaired\",\n"
                    f"{_body}           level=\"debug\")")

        _new = self._PAT.sub(_sub, _code)
        _out = dict(ctx.artifact)
        _out[_MOD] = _new
        return CapabilityResult(
            capability_id=self.capability_id, status="applied", artifact=_out,
            changes=[f"silent_exc_backfill x{_n[0]}"],
            reason="裸静默 except 补 silent_exc")

    def verify(self, before: dict, after: dict, ctx: CapabilityContext) -> tuple:
        _code = str(after.get(_MOD) or "")
        _ok, _why = _syntax_ok(_code)
        if not _ok:
            return False, _why
        if self._PAT.search(_code):
            return False, "仍有裸 pass"
        if "silent_exc(" not in _code:
            return False, "silent_exc 未写入"
        return True, ""


DEFAULT_OPERATORS = (IndentLevelAlignCapability,
                     SignatureDocstringRestoreCapability,
                     SilentExcBackfillCapability)


def build_default_kernel(**kwargs) -> object:
    """构造一个装好第一批算子的内核（不接任何生产调用点）。"""
    from .kernel import CapabilityKernel
    _k = CapabilityKernel(**kwargs)
    for _cls in DEFAULT_OPERATORS:
        _k.register(_cls())
    return _k
