# -*- coding: utf-8 -*-
"""376 刀4 单测：try 块平衡静态闸（关2a · 票 T-376-补丁语法补全-1 · P2）。

【票面要什么】跨夜 12 次 `patch_multi_1793...` 补丁验证失败，统一报
`SyntaxError: expected 'except' or 'finally' block (<llm-patch>, line 35~44)`
——LLM 生成补丁 try 块缺收尾。要求在入库前加一次 try 块平衡检查。

【本件钉九件事】
① 六种合法形态（含 try/except/finally、多 except、嵌套、except*）不被误拦；
② `try` 多于 handler ⇒ 判 `unbalanced`，deficit 与行号对得上；
③ 有 handler 无 try ⇒ 判 `orphan`（片段起点落在 try 内部）；
④ 字符串 / 注释 / 属性名里的关键字**不计数**（铁律227：tokenize 口径，非正则）；
⑤ tokenize 读不动 ⇒ `unknown` 且**不拦**（不猜，把裁决交回关2）；
⑥ **裁决等价性**：所有被关2a 拒的样例，关2 的 ast.parse 也必然失败
   ⇒ 关2a 不改变任何补丁的通过/拒绝结论，只换理由；
⑦ 文案可执行：缺几处、哪一行、片段末行长什么样，且末行**长度有界**；
⑧ **能力边界如实**：`try/except` 与 `try/finally` 一缺一（tries=2/handlers=2）
   是**假阴性**，仍由关2 兜住 —— 钉住它，避免把「下界探测器」当「完整校验器」；
⑨ **不自动补全**：`except Exception: raise` 补出来的版本四关全绿 —— 钉住这个事实，
   防将来有人顺手加「自动补全」还自认安全（行为差异见证据件）。
"""
import ast
import textwrap

import pytest

from nucleus.reasoning.PatchManager import (
    PatchManager,
    _m376_try_balance,
    _m376_try_balance_reason,
)

# 一份「像真的」原文：让关3 相似度不成为噪声源
_ORIG = (
    "try:\n"
    "    _v = int(s)\n"
    "except ValueError:\n"
    "    _v = 0\n"
    "return _v\n"
)

_LEGAL = {
    "单 try/except": "try:\n    x()\nexcept ValueError:\n    pass\n",
    "try/except/finally": "try:\n    x()\nexcept E:\n    pass\nfinally:\n    pass\n",
    "多 except": "try:\n    x()\nexcept A:\n    pass\nexcept B:\n    pass\n",
    "嵌套": ("try:\n    try:\n        x()\n    except A:\n        pass\n"
             "except B:\n    pass\n"),
    "except*": "try:\n    x()\nexcept* ValueError:\n    pass\n",
    "无 try": "x = 1\nreturn x\n",
}


def _gate(patch):
    """生产口径直调：四关不依赖 self（函数体内 `self.` 出现 0 次）。"""
    return PatchManager._check_llm_patch_completeness(None, patch)


def _gate2_raises(code):
    """关2 原判据（与生产逐字同口径）：dedent + 虚拟函数包装后 ast.parse。"""
    _norm = textwrap.dedent(code or "").strip()
    ast.parse("def _wrap():\n" + textwrap.indent(_norm, "    "), filename="<llm-patch>")


# ======================================================== ① 合法形态
@pytest.mark.parametrize("name,code", sorted(_LEGAL.items()))
def test_01_legal_forms_are_ok(name, code):
    tb = _m376_try_balance(code)
    assert tb["state"] == "ok", f"{name} 被误判成 {tb['state']}（{tb['detail']}）"


def test_02_legal_forms_not_rejected_by_gate2a():
    """关2a 不许对合法形态给出任何「try 块不平衡」文案。"""
    for name, code in _LEGAL.items():
        _r = _gate({"original_code": _ORIG, "modified_code": code})
        assert "try 块不平衡" not in (_r.get("reason") or ""), name


# ======================================================== ②③ 两种必然非法
def test_03_missing_handler_is_unbalanced():
    tb = _m376_try_balance("try:\n    x()\nreturn 1\n")
    assert tb["state"] == "unbalanced"
    assert (tb["tries"], tb["handlers"], tb["deficit"]) == (1, 0, 1)
    assert tb["open_lines"] == [1], "应点名第 1 行的 try 缺收尾"


def test_04_two_tries_one_handler_names_the_last_one():
    _code = "try:\n    x()\nexcept A:\n    pass\ntry:\n    y()\n"
    tb = _m376_try_balance(_code)
    assert (tb["tries"], tb["handlers"], tb["deficit"]) == (2, 1, 1)
    assert tb["open_lines"] == [5], "缺收尾的是第 5 行那个 try"


def test_05_orphan_handler_is_orphan_state():
    tb = _m376_try_balance("except ValueError:\n    pass\n")
    assert tb["state"] == "orphan"
    assert tb["tries"] == 0 and tb["handlers"] == 1
    assert tb["open_lines"] == [1]


# ======================================================== ④ tokenize 口径
def test_06_string_and_comment_keywords_are_not_counted():
    """铁律227：正则数关键字会把字符串/注释里的 try 也算进去。"""
    for code in ('x = "try:"\nreturn x\n',
                 "x = 'except ValueError:'\nreturn x\n",
                 "# try:\nx = 1\nreturn x\n",
                 'msg = """try:\n    pass\n"""\nreturn msg\n'):
        tb = _m376_try_balance(code)
        assert (tb["tries"], tb["handlers"]) == (0, 0), repr(code)


def test_07_decorator_and_attribute_are_not_statement_level():
    """`@try_dec` / `obj.try_x` 之类不能当成 try 语句。"""
    assert _m376_try_balance("obj.try_x = 1\nreturn 1\n")["tries"] == 0
    assert _m376_try_balance("@try_again\ndef f():\n    pass\n")["tries"] == 0


# ======================================================== ⑤ tokenize 读不动
def test_08_tokenize_failure_is_unknown_and_not_blocking():
    tb = _m376_try_balance("try:\n    x(\n")          # 括号未闭合
    assert tb["state"] == "unknown"
    assert tb["detail"].startswith("tokenize 失败（不拦）")
    _r = _gate({"original_code": _ORIG, "modified_code": "try:\n    x(\n"})
    assert "try 块不平衡" not in (_r.get("reason") or ""), "unknown 态不许被关2a 拦"


# ======================================================== ⑥ 裁决等价性
def test_09_verdict_equivalence_with_gate2():
    """关2a 拒的，关2 也必然拒 ⇒ 只换理由、不改裁决（零回归的结构性证明）。"""
    _bad = ["try:\n    x()\nreturn 1\n",
            "try:\n    x()\nexcept A:\n    pass\ntry:\n    y()\n",
            "except ValueError:\n    pass\n",
            "finally:\n    pass\n"]
    for code in _bad:
        assert _m376_try_balance(code)["state"] in ("unbalanced", "orphan"), code
        with pytest.raises(SyntaxError):
            _gate2_raises(code)


def test_10_gate2a_precedes_gate2_with_actionable_reason():
    _r = _gate({"original_code": _ORIG, "modified_code": "try:\n    x()\nreturn 1\n"})
    _why = _r.get("reason") or ""
    assert _r["complete"] is False
    assert _why.startswith("try 块不平衡"), _why
    assert "缺 1 处收尾" in _why
    assert "第 1 行" in _why
    assert "入库前静态闸拦截" in _why
    assert "expected 'except' or 'finally'" not in _why, "仍走了 ast 泛化文案 ⇒ 没抢在关2 前面"


# ======================================================== ⑦ 文案有界
def test_11_reason_tail_is_bounded():
    _code = "try:\n    x()\n" + "return '" + "z" * 500 + "'\n"
    tb = _m376_try_balance(_code)
    assert len(tb["tail"]) <= 60, "片段末行必须截断，不许把整行打进日志"
    assert len(_m376_try_balance_reason(tb)) < 400


def test_12_orphan_reason_mentions_fragment_window():
    tb = _m376_try_balance("except ValueError:\n    pass\n")
    assert tb["state"] == "orphan", "先钉状态：状态错了后面文案对也没意义"
    _why = _m376_try_balance_reason(tb)
    assert "却没有 try" in _why and "fragment 窗口错位" in _why
    # ★反面判据（V2 反向注入暴露）：原因函数曾用 `if unbalanced ... else 说 orphan`，
    #   任何非 unbalanced 态都被描述成「片段窗口错位」。ok / unknown 必须给中性文案。
    for _code in ("try:\n    x()\nexcept A:\n    pass\n", "try:\n    x(\n"):
        _tb = _m376_try_balance(_code)
        assert _tb["state"] != "orphan"
        assert "fragment 窗口错位" not in _m376_try_balance_reason(_tb)


# ======================================================== ⑧ 能力边界
def test_13_lower_bound_false_negative_is_documented():
    """`try/except/finally` 与「裸 try」并存 ⇒ tries=2 / handlers=2 判 ok，
    但代码其实非法。这是**已知下界**，由关2 兜住；钉住它以免被当成完整校验器。"""
    _code = ("try:\n    x()\nexcept A:\n    pass\nfinally:\n    pass\n"
             "try:\n    y()\n")
    tb = _m376_try_balance(_code)
    assert (tb["tries"], tb["handlers"], tb["state"]) == (2, 2, "ok")
    with pytest.raises(SyntaxError):
        _gate2_raises(_code)
    assert _gate({"original_code": _ORIG, "modified_code": _code})["complete"] is False


# ======================================================== ⑨ 不自动补全
def test_14_autofilled_handler_would_pass_all_gates():
    """★这是「为什么不做自动补全」的活证据：
    把缺的 except 用 `except Exception: raise` 补上，四关**全绿**——
    可原 handler 已被丢弃（本该降级 return，变成向上抛）。判据看不见这个语义变化。"""
    _auto = ("try:\n    _v = int(s)\nexcept Exception:\n    raise\nreturn _v\n")
    assert _gate({"original_code": _ORIG, "modified_code": _auto})["complete"] is True
    # 而未补全的同源形态必被拒 —— 两者差别只在「有没有那两行 raise」
    _trunc = "try:\n    _v = int(s)\nreturn _v\n"
    assert _gate({"original_code": _ORIG, "modified_code": _trunc})["complete"] is False


# ======================================================== 其他
def test_15_pure_and_deterministic():
    _code = "try:\n    x()\nreturn 1\n"
    _snap = _code
    _a = _m376_try_balance(_code)
    _b = _m376_try_balance(_code)
    assert _a == _b, "同一输入两次结果必须一致（纯函数）"
    assert _code == _snap, "入参不许被就地修改"
    assert _m376_try_balance("")["state"] == "ok"
    assert _m376_try_balance(None)["state"] == "ok"


def test_16_normal_patch_still_passes():
    """合法且改动细小的补丁，关2a 不许改变其 complete 结论。"""
    _mod = ("try:\n    _v = int(s, 10)\nexcept ValueError:\n    _v = 0\nreturn _v\n")
    assert _gate({"original_code": _ORIG, "modified_code": _mod})["complete"] is True
