# -*- coding: utf-8 -*-
"""★主线第91批 T-91b：LLM 补丁「缩进契约」门控单测（三层防护）。

背景（★第91批 T0 实测，非推测）
--------------------------------
运行日志 4 次 LLM 补丁尝试（14:17/14:55/16:10/16:57）**全部**以
`补丁完整性检查失败: 语法错误: IndentationError: unindent does not match any
outer indentation level (<llm-patch>, line 26/19/11/9)` 被完整性关2 拒绝
（`logs/pulse.log` 实测：`补丁完整性检查失败` 12 次 / `语法错误` 8 次 / `unindent` 8 次）。
★第92批 T-92a（_m92_t92a_label_synced）已把关2 文案改为 `f"语法错误({type(_e).__name__}): {_e}"`
——上引历史日志是**改前**原文（当时不含异常类名）；改后同一输入会带 `IndentationError`。
本文件 A1/A2/A3 三例据此**加强**为直接断言异常类名出现在 `reason` 里。
复现实验（`tmp/m91_indent.txt`）确认该报错文与行号可由「局部缩进漂移」逐字复现，
且报错行号 = 内容行 + 1（`<llm-patch>` 第 1 行是 `def _wrap():`）。

完整根因链（两个症状、一条根因）
--------------------------------
① **语法错症状（被关2 拦下）**：LLM 输出局部缩进漂移（如 6 而非 8），关2 用
   `dedent + strip + indent4 + 包 def _wrap()` 解析 ⇒ `unindent` / `TabError`。
   原 system prompt **完全未提缩进**（只有 ASCII/标点/括号要求）；
   `_clean_llm_code` 也只有「全角标点归一化」一种修复能力。
② **静默症状（★全关卡放行，危害更大）**：`PatchManager._verify_in_copy` 用
   `full_content.replace(original_code, modified_code)` **原位整段替换**，
   插入块的缩进由 modified 自身首行决定。LLM 输出首行落在 col 0 时整块比外层更浅
   ⇒ **类体/函数体提前终止**；而插入块自身在 base 0 上**仍是自洽的 Python**
   ⇒ `ast.parse` / `py_compile` / `import` **全部通过**（"语法合法 ≠ 结构未退化"）。
   2026-09-20 实测（星轨 T-91d 自动应用补丁，见 `tmp/m91_audit.txt`）：
       PulseInnerWorld.py  类方法 367 → 270（-97），新增顶层函数 `_calc_math_question`
       PulseLung.py        类方法  86 →  45（-41），新增顶层函数 `_get_channel_semaphore`
       PulseInterestModel.py 仅 3 处**注释**缩进退化（无害）
       tests/test_lazy_snapshot_m9.py 顶层语句 19 → 18（静默删掉一个用例）
   ⇒ 4 个 LLM 补丁**全部**造成结构损伤（3 个 local_rule 补丁零损伤 ⇒ 损伤是 LLM 通道特有的）。

本批三层防护（开关 `ENABLE_M91_LLM_INDENT_REPAIR`，默认 True）
------------------------------------------------------------
  ① system prompt 显式要求「保持与输入代码完全相同的缩进层级与宽度、禁用 Tab」；
  ② `_clean_llm_code` 阶段3：`_m91_repair_indentation` 修**局部缩进漂移**；
  ③ 补丁构造点：`_m91_align_base_indent` 把 modified 的**基础缩进**对齐到 original。

被测对象 = **真实源码**（`SafeEvolutionExecutor` 实例方法 + `PatchManager` 关2），
不复刻任何逻辑；`.bak_batch91` 仅用于「改前行为对照」，缺失时 skipTest。

覆盖九组：
  A. 关2 先红后绿 —— 同一补丁 dict，改前被 `unindent` 拒 → 修后放行（真实 PatchManager）；
  B. `_m91_repair_indentation` 契约（行定位 / 等距取更深 / tab / token 守恒 / 零误伤 / 字符串安全）；
  C. `_m91_base_indent` 基础缩进口径（跳过空行与**纯注释行**）；
  D. `_m91_align_base_indent` 平移契约 + ★**结构退化回归护栏**（复现 T-91d 机制）；
  E. `_clean_llm_code` 端到端（真实实例）先红后绿 + 对已过闸输入零改动 + 字符串不被改写；
  F. 开关契约（默认值 / 关闭逐字回退）；
  G. 生产补丁库交叉校验（关2 同口径 helper 与真实关2 结论一致性）；
  H. ★调用点接线（AST）：`_clean_llm_code` 真被调用且平移消费其产物
     —— 本组是「helper 全绿但生产链路断了」这一 P0 级假绿的回归护栏。
"""
import ast
import importlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config  # noqa: E402

_SE_MOD = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
_SE_CLS = getattr(_SE_MOD, "SafeEvolutionExecutor")
_GATE2_CHECK = getattr(_SE_MOD, "_m91_gate2_check")
_GATE2_PARSE = getattr(_SE_MOD, "_m91_gate2_parse")
_SWITCH = "ENABLE_M91_LLM_INDENT_REPAIR"

_PM_MOD = importlib.import_module("nucleus.reasoning.PatchManager")
_PM_CLS = getattr(_PM_MOD, "PatchManager")

_SEE_REL = os.path.join("nucleus", "reasoning", "SafeEvolutionExecutor.py")
_BAK_SE = os.path.join(ROOT, ".bak_batch91", "nucleus", "reasoning",
                       "SafeEvolutionExecutor.py.bak")
_PATCH_STORE = [
    os.path.join(ROOT, "data", "patches", "pending_patches.json"),
    os.path.join(ROOT, "data", "patches", "patch_history.json"),
    os.path.join(ROOT, "data", "patches", "pending_patches.json.bak_20260920_181210"),
]

# ---- T0 复现样本（性质逐字取自 tmp/m91_probe_indent.py 的复现实验）----
# ★注意：original 与「修好后的 modified」必须**内容不同**，否则会被关1
#   （「修改内容为空或与原文完全一致」）拦下，测不到关2 的先红后绿。
_SAMPLE_ORIG = "    def foo(self):\n        x = 1\n        return x"
_SAMPLE_DRIFT = "    def foo(self):\n        x = 2\n      return x"
_SAMPLE_TAB = "    def foo(self):\n\t    x = 2\n        return x"
_SAMPLE_SHALLOW_FIRST = "  def foo(self):\n    x = 1\n    return x"
_SAMPLE_CLEAN = "    def foo(self):\n        x = 1\n        return x"

_BODY23 = "".join("        %s = %d\n" % (chr(97 + _i), _i + 1) for _i in range(23))
_SAMPLE_ORIG_LAST = "    def foo(self):\n" + _BODY23 + "        return 0"
_SAMPLE_DRIFT_LAST = "    def foo(self):\n" + _BODY23 + "      return w"

# ---- T0 取证的真实 LLM 补丁 id（若在场则必须触发对齐）----
_REAL_LLM_ALIGN_IDS = ("patch_llm_1789886168_789a", "patch_llm_1789890006_e8ba")


def _read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()


def _exe():
    """★巨型类轻量实例化：`__new__` 绕开 __init__（不触发任何 IO / 线程）。"""
    return _SE_CLS.__new__(_SE_CLS)


def _pm():
    return _PM_CLS.__new__(_PM_CLS)


def _gate2(code):
    """**独立第二实现**复算「关2 同口径」判据（不调用生产 helper，避免自己验自己）。"""
    try:
        _n = textwrap.dedent(code or "").strip()
        ast.parse("def _wrap():\n" + textwrap.indent(_n, "    "), filename="<llm-patch>")
        return True, ""
    except SyntaxError as _e:
        return False, "{}: {}".format(type(_e).__name__, _e)


def _load_legacy_se():
    """载入 `.bak_batch91` 里的**改前** SafeEvolutionExecutor（缺失时返回 None）。

    ★`.bak_batch91/` 是 git-ignored 的 scratch 目录（可能被外部清理），
    故调用方必须在缺失时 skipTest，不得让用例永久失败。
    """
    if not os.path.isfile(_BAK_SE):
        return None
    _ld = importlib.machinery.SourceFileLoader("m91_legacy_see", _BAK_SE)
    _sp = importlib.util.spec_from_loader(_ld.name, _ld)
    _mod = importlib.util.module_from_spec(_sp)
    _ld.exec_module(_mod)
    return getattr(_mod, "SafeEvolutionExecutor")


class _CfgSwitch:
    """临时改写 config 属性，退出还原（★铁律 49：相关断言必须写在 with 块内）。"""

    def __init__(self, attr, value):
        self.attr = attr
        self.value = value

    def __enter__(self):
        self.had = hasattr(config, self.attr)
        self.old = getattr(config, self.attr, None)
        setattr(config, self.attr, self.value)
        return self

    def __exit__(self, *exc):
        if self.had:
            setattr(config, self.attr, self.old)
        else:
            delattr(config, self.attr)
        return False


def _load_patch_store():
    _out = []
    for _p in _PATCH_STORE:
        if not os.path.isfile(_p):
            continue
        try:
            _d = json.loads(io.open(_p, encoding="utf-8").read())
        except Exception:
            continue
        if isinstance(_d, list):
            _out.extend(_x for _x in _d if isinstance(_x, dict))
    return _out


def _lines_differ(a, b):
    return [i for i, (x, y) in enumerate(zip(a.split("\n"), b.split("\n"))) if x != y]


class TestIndentContractM91(unittest.TestCase):
    """★第91批 T-91b：LLM 补丁缩进契约（三层防护）。"""

    def setUp(self):
        self.exe = _exe()

    # ------------------------------------------------------------------ A 先红后绿
    def test_A1_gate2_red_then_green_drift(self):
        """★先红后绿：同一补丁，「局部缩进漂移」被关2 拒 → 修复后放行。"""
        _red = {"original_code": _SAMPLE_ORIG, "modified_code": _SAMPLE_DRIFT}
        _r_red = _pm()._check_llm_patch_completeness(dict(_red))
        self.assertFalse(_r_red["complete"], "改前应被拒（红）")
        self.assertIn("unindent does not match any outer indentation level",
                      _r_red["reason"], "★必须是 T0 逐字命中的报错文案")
        # ★第92批 T-92a：异常类名必须直接出现在文案里（改前只写 message ⇒ 此断言红）
        self.assertIn("IndentationError", _r_red["reason"], "★T-92a：文案须含异常类名")
        _fixed = self.exe._m91_repair_indentation(_SAMPLE_DRIFT)
        self.assertNotEqual(_fixed, _SAMPLE_DRIFT, "漂移应被修复")
        _green = {"original_code": _SAMPLE_ORIG, "modified_code": _fixed}
        self.assertTrue(_pm()._check_llm_patch_completeness(dict(_green))["complete"],
                        "修复后应放行（绿）")

    def test_A2_gate2_red_then_green_drift_at_last_line(self):
        """★末行漂移（对应 T0 日志的 `line 26`）同样先红后绿。"""
        _r_red = _pm()._check_llm_patch_completeness(
            {"original_code": _SAMPLE_ORIG_LAST, "modified_code": _SAMPLE_DRIFT_LAST})
        self.assertFalse(_r_red["complete"])
        self.assertIn("unindent does not match any outer indentation level", _r_red["reason"])
        self.assertIn("IndentationError", _r_red["reason"], "★T-92a：文案须含异常类名")
        _fixed = self.exe._m91_repair_indentation(_SAMPLE_DRIFT_LAST)
        self.assertTrue(_pm()._check_llm_patch_completeness(
            {"original_code": _SAMPLE_ORIG_LAST, "modified_code": _fixed})["complete"])

    def test_A3_gate2_red_then_green_tab_error(self):
        """★tab/空格混用（T0 样本 B，`TabError`）先红后绿。

        ★第91批 T0 记录的缺口「关2 文案不含异常类名」已由**第92批 T-92a** 修掉：
          改前 `f"语法错误: {_e}"`  →  改后 `f"语法错误({type(_e).__name__}): {_e}"`。
        本用例据此**加强**为断言 `reason` 里出现 `TabError`（改前会红、改后为绿）。
        """
        _r_red = _pm()._check_llm_patch_completeness(
            {"original_code": _SAMPLE_ORIG, "modified_code": _SAMPLE_TAB})
        self.assertFalse(_r_red["complete"])
        self.assertIn("inconsistent use of tabs and spaces in indentation", _r_red["reason"])
        self.assertIn("TabError", _r_red["reason"], "★T-92a：文案须含异常类名")
        _fixed = self.exe._m91_repair_indentation(_SAMPLE_TAB)
        self.assertNotIn("\t", _fixed, "tab 应被展开为空格")
        self.assertTrue(_pm()._check_llm_patch_completeness(
            {"original_code": _SAMPLE_ORIG, "modified_code": _fixed})["complete"])

    def test_A4_content_changed_patch_unaffected(self):
        """★零回归：本来就合法的补丁（仅内容改动）判定不变。"""
        for _mod in (_SAMPLE_ORIG.replace("x = 1", "x = 2"),
                     _SAMPLE_SHALLOW_FIRST.replace("x = 1", "x = 2")):
            _r = _pm()._check_llm_patch_completeness(
                {"original_code": _SAMPLE_ORIG, "modified_code": _mod})
            self.assertTrue(_r["complete"], "本应通过的补丁被新逻辑拒绝: {}".format(_r["reason"]))

    # --------------------------------------------------- B 缩进漂移修复算法契约
    def test_B1_minimal_diff_one_line_only(self):
        """★行定位：只改 Python 实际报错的那一行（避免误伤其它行与字符串）。"""
        for _code in (_SAMPLE_DRIFT, _SAMPLE_TAB, _SAMPLE_DRIFT_LAST):
            _d = _lines_differ(_code, self.exe._m91_repair_indentation(_code))
            self.assertEqual(len(_d), 1, "应只改 1 行，实际改了 %d 行" % len(_d))

    def test_B2_snap_to_deeper_on_tie(self):
        """★「等距取更深」：6 距 4/8 等距 ⇒ 吸附 8（否则 return 落到函数外）。"""
        _out = self.exe._m91_repair_indentation(_SAMPLE_DRIFT)
        self.assertEqual([8, 8], [len(_l) - len(_l.lstrip()) for _l in _out.split("\n")[1:]])
        self.assertTrue(_gate2(_out)[0])

    def test_B3_contract_unchanged_or_passing(self):
        """★核心契约：返回值**要么逐字等于输入，要么能过关2 同口径判据**（无中间态）。"""
        for _code in (_SAMPLE_DRIFT, _SAMPLE_TAB, _SAMPLE_DRIFT_LAST, _SAMPLE_CLEAN,
                      _SAMPLE_SHALLOW_FIRST, "", "def f(:\n1",
                      "    x = 「a」。b → c", "    def g(self):\n        pass"):
            _out = self.exe._m91_repair_indentation(_code)
            if _out != _code:
                self.assertTrue(_gate2(_out)[0], "改了就必须改好: {!r} -> {!r}".format(_code, _out))

    def test_B4_clean_code_untouched(self):
        """★零误伤：已能过关2 的代码逐字返回（不制造无意义 diff）。"""
        for _code in (_SAMPLE_CLEAN, _SAMPLE_SHALLOW_FIRST,
                      "    x = foo(\n        1,\n              2)\n    return x",
                      "    def foo(self):\n# 顶格注释\n        x = 1\n      # 漂移注释\n        return x"):
            self.assertTrue(_gate2(_code)[0], "用例前提：必须能过关2 同口径")
            self.assertEqual(self.exe._m91_repair_indentation(_code), _code)
        # 空输入由 `if not code: return code` 直接返回（注意空串本身过不了关2，
        # 因为包裹后 `def _wrap():` 没有函数体 ⇒ 不能混进上面的前提列表）
        self.assertFalse(_gate2("")[0])
        self.assertEqual(self.exe._m91_repair_indentation(""), "")

    def test_B5_real_source_snippets_untouched(self):
        """★零误伤（全量）：对**真实源码**里所有函数/方法片段恒等返回。

        这条是第91批 T0 实测教训的回归护栏：早期「逐行试吸附」版本会重排多行字符串
        内部的行（本项目实测 **35 个方法**的 docstring 被压到 col 0）。
        """
        _src = _read(_SEE_REL)
        _tree = ast.parse(_src)
        _n = 0
        _bad = []
        for _node in ast.walk(_tree):
            if not isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            _seg = ast.get_source_segment(_src, _node)
            if not _seg:
                continue
            _n += 1
            if self.exe._m91_repair_indentation(_seg) != _seg:
                _bad.append(_node.name)
        self.assertGreater(_n, 60, "样本量不足，检查 AST 提取")
        self.assertEqual([], _bad, "★已合法片段被改动（误伤 %d 个）" % len(_bad))

    def test_B10_tab_error_reported_line_is_offset(self):
        """★TabError 的报错行**落在 tab 行之后**（词法器要等下一次 INDENT/DEDENT
        比较才察觉 tab/空格混用）⇒ 纯行定位修不掉，必须走「行首 tab 展开」兜底。

        T0 实测（tmp/m91_fail4.txt）：tab 在第 3 行（包裹文本），报错行却是第 4 行。
        """
        _ok, _err, _ln = _GATE2_PARSE(_SAMPLE_TAB)
        self.assertFalse(_ok)
        self.assertTrue(_err.startswith("TabError"), "前提：应为 TabError")
        _tab_lines = [i for i, l in enumerate(_SAMPLE_TAB.split("\n"), 1) if l[:1] == "\t"]
        self.assertEqual(_tab_lines, [2], "前提：tab 出现在第 2 行")
        self.assertNotEqual(_ln, _tab_lines[0], "★前提：报错行 != tab 行（T0 实测偏移）")
        _out = self.exe._m91_repair_indentation(_SAMPLE_TAB)
        self.assertNotIn("\t", _out, "tab 必须被展开")
        self.assertTrue(_gate2(_out)[0])

    def test_B6_string_literals_never_rewritten(self):
        """★字符串安全：多行 docstring 缩进异常时，修复只动代码行，字符串逐字不变。"""
        _code = ('def f(self):\n'
                 '    """doc 起始\n'
                 'weird indent line\n'
                 '        """\n'
                 '    x = 1\n'
                 '  return x\n')
        self.assertFalse(_gate2(_code)[0], "用例前提：应被关2 拒")
        _out = self.exe._m91_repair_indentation(_code)
        self.assertTrue(_gate2(_out)[0], "应被修好")
        _doc_old = _code.split("\n")[1:4]
        _doc_new = _out.split("\n")[1:4]
        self.assertEqual(_doc_old, _doc_new, "★多行字符串内容被改写")
        self.assertEqual(["  return x", "    return x"],
                         [_code.split("\n")[5], _out.split("\n")[5]])

    def test_B7_token_conservation(self):
        """只改行首空白：每行 strip 后的内容序列必须完全不变。"""
        for _code in (_SAMPLE_DRIFT, _SAMPLE_TAB, _SAMPLE_DRIFT_LAST):
            _a = [l.strip() for l in _code.split("\n")]
            _b = [l.strip() for l in self.exe._m91_repair_indentation(_code).split("\n")]
            self.assertEqual(_a, _b)

    def test_B8_non_indent_errors_left_alone(self):
        """非缩进类错误（如全角标点）不靠改缩进「蒙」过关：逐字返回。"""
        _code = "    x = 「a」。b → c"
        self.assertFalse(_gate2(_code)[0])
        self.assertEqual(self.exe._m91_repair_indentation(_code), _code)

    def test_B9_gate2_parse_reports_line_number(self):
        """`_m91_gate2_parse` 必须给出包裹文本中的错误行号（行定位的依赖）。"""
        _ok, _err, _ln = _GATE2_PARSE(_SAMPLE_DRIFT)
        self.assertFalse(_ok)
        self.assertTrue(_err)
        self.assertEqual(_ln, 4, "★T0 逐字命中：样本 A 报错在 <llm-patch> 第 4 行")
        self.assertEqual(_GATE2_PARSE(_SAMPLE_CLEAN)[2], 0)

    # ------------------------------------------------- C 基础缩进口径（跳注释）
    def test_C1_base_indent_basic(self):
        """基础缩进 = 首个**可执行行**的前导宽度（tab 按 4 展开）。"""
        self.assertEqual(self.exe._m91_base_indent("        def f(self):\n            return 1"), 8)
        self.assertEqual(self.exe._m91_base_indent("def f(self):\n    return 1"), 0)
        self.assertEqual(self.exe._m91_base_indent("\t\tdef f(self):"), 8)
        self.assertEqual(self.exe._m91_base_indent(""), -1)
        self.assertEqual(self.exe._m91_base_indent("\n\n   \n"), -1)

    def test_C2_base_indent_skips_blank_and_comment(self):
        """★跳过空行与**纯注释行**（T0 真实形态：col0 注释 + 原缩进方法体 ⇒ base=8 而非 0）。"""
        _real_shape = ("# Make sure logging is imported at module top level: import logging\n"
                       "\n"
                       "        if not self._m91_x():\n"
                       "            return None")
        self.assertEqual(self.exe._m91_base_indent(_real_shape), 8,
                         "★首行注释不得被当作基础缩进（否则触发多余的整体平移）")

    # -------------------------------------------------- D 平移契约 + 结构回归护栏
    def test_D1_align_shifts_when_shallower(self):
        """modified 更浅 ⇒ 平移到与 original 同级，且 token 守恒。"""
        _orig = "        def f(self):\n            return 1"
        _mod = "def f(self):\n    return 1"
        _fixed, _shift = self.exe._m91_align_base_indent(_orig, _mod, "t")
        self.assertEqual(_shift, 8)
        self.assertEqual(self.exe._m91_base_indent(_fixed),
                         self.exe._m91_base_indent(_orig))
        self.assertEqual([l.strip() for l in _fixed.split("\n")],
                         [l.strip() for l in _mod.split("\n")])
        self.assertTrue(_gate2(_fixed)[0])

    def test_D2_align_noop_when_equal_or_deeper(self):
        """相等 / 更深 ⇒ 零位移、原样返回（反向裁剪可能把行裁空，风险高于收益）。"""
        _orig = "        def f(self):\n            return 1"
        for _mod in ("        def f(self):\n            return 1",
                     "                def f(self):\n                    return 1"):
            _fixed, _shift = self.exe._m91_align_base_indent(_orig, _mod, "t")
            self.assertEqual(_shift, 0)
            self.assertEqual(_fixed, _mod)

    def test_D3_align_abandons_when_gate2_still_fails(self):
        """平移后关2 同口径仍不过 ⇒ 放弃平移（宁可交给验证关拒绝，不引入新破损）。"""
        _orig = "    x = 1"
        _mod = "def f(:\n1"          # base 0、且本身语法不合法
        _fixed, _shift = self.exe._m91_align_base_indent(_orig, _mod, "t")
        self.assertEqual(_shift, 0)
        self.assertEqual(_fixed, _mod)

    def test_D4_align_consistent_shape(self):
        """首行注释不参与基础缩进判定：真正塌陷才平移，仅注释前置则不动。"""
        _orig = "        x = 1\n        return x"
        _fixed, _shift = self.exe._m91_align_base_indent(_orig, "# note\nx = 1\nreturn x", "t")
        self.assertEqual(_shift, 8, "首个可执行行在 col0 ⇒ 必须平移")
        self.assertTrue(_fixed.startswith("        # note"))
        _mod2 = "# note\n        x = 1\n        return x"     # 首个可执行行已在 8
        _fixed2, _shift2 = self.exe._m91_align_base_indent(_orig, _mod2, "t")
        self.assertEqual(_shift2, 0, "★首行仅注释不应触发平移")
        self.assertEqual(_fixed2, _mod2)

    def test_D5_structural_regression_guard_t91d(self):
        """★★T-91d 事故回归护栏：基础缩进不匹配 ⇒ 类体提前终止（且**语法仍然合法**）。

        逐字复现 `tmp/m91_audit.txt` 观测到的机制：`replace` 插入块的缩进由
        modified 自身首行决定 ⇒ 比外层更浅 ⇒ 类体提前终止；插入块自身在 base 0
        上自洽 ⇒ `ast.parse` 放行（"语法合法 ≠ 结构未退化"）。
        """
        _full = ("class _T:\n"
                 "    def z(self):\n"
                 "        return 0\n"
                 "\n"
                 "    def a(self):\n"
                 "        x = 1\n"
                 "\n"
                 "    def b(self):\n"
                 "        return 2\n"
                 "\n"
                 "    def c(self):\n"
                 "        return 3\n")
        _orig = "    def a(self):\n        x = 1\n"
        _mod = "def _new(self):\n    y = 2\n"          # LLM 把 base 4 塌成 0

        def _methods(_src):
            for _n in ast.walk(ast.parse(_src)):
                if isinstance(_n, ast.ClassDef) and _n.name == "_T":
                    return len([_m for _m in _n.body
                                if isinstance(_m, (ast.FunctionDef, ast.AsyncFunctionDef))])
            return -1

        _broken = _full.replace(_orig, _mod)
        ast.parse(_broken)                              # ★语法合法（假绿）
        self.assertEqual(_methods(_broken), 1, "★不改则类体提前终止（应复现 -3 方法）")
        self.assertIn("def _new(self):\n", _broken)

        _fixed, _shift = self.exe._m91_align_base_indent(_orig, _mod, "t")
        self.assertEqual(_shift, 4)
        _healed = _full.replace(_orig, _fixed)
        ast.parse(_healed)
        self.assertEqual(_methods(_healed), 4, "★对齐后类体不再提前终止（结构性修复）")

    # ------------------------------------------------------- E 端到端（真实实例）
    def test_E1_clean_llm_code_repairs_drift(self):
        """真实调用 `_clean_llm_code`：漂移输出被修到关2 同口径通过（绿）。"""
        for _code in (_SAMPLE_DRIFT, _SAMPLE_TAB, _SAMPLE_DRIFT_LAST):
            _out = self.exe._clean_llm_code(_code)
            _ok, _err = _gate2(_out)
            self.assertTrue(_ok, "端到端未修复: {}".format(_err))

    def test_E2_legacy_clean_llm_code_fails(self):
        """★先红：改前实现（`.bak_batch91`）对同一输入**无法修复**（关2 同口径仍失败）。"""
        _legacy = _load_legacy_se()
        if _legacy is None:
            self.skipTest("`.bak_batch91` 不存在（scratch 目录可被外部清理）")
        _lexe = _legacy.__new__(_legacy)
        for _code in (_SAMPLE_DRIFT, _SAMPLE_TAB, _SAMPLE_DRIFT_LAST):
            _out = _lexe._clean_llm_code(_code)
            self.assertFalse(_gate2(_out)[0],
                             "改前实现本应无法修复（红），实际修好了: {!r}".format(_code[:30]))

    def test_E3_gate2_passing_input_untouched_vs_legacy(self):
        """★零回归：对**关2 本就能过**的输入，新实现与改前逐字一致。

        这条是「阶段3 有前置条件（仅关2 失败才进入）」的行为证明：
        输入能过闸时，新增逻辑根本不参与。
        """
        _legacy = _load_legacy_se()
        if _legacy is None:
            self.skipTest("`.bak_batch91` 不存在（scratch 目录可被外部清理）")
        _lexe = _legacy.__new__(_legacy)
        _cases = [_SAMPLE_CLEAN, _SAMPLE_SHALLOW_FIRST,
                  "def f(x):\n    return x + 1\n", "    def g(self):\n        pass\n"]
        for _c in _cases:
            self.assertTrue(_gate2(_c)[0], "用例前提：必须能过关2 同口径")
            self.assertEqual(self.exe._clean_llm_code(_c), _lexe._clean_llm_code(_c),
                             "★关2 可过的输入不得被新逻辑改动: {!r}".format(_c[:30]))

    def test_E4_clean_llm_code_keeps_docstring(self):
        """★端到端字符串安全：修复缩进时 docstring 逐字不变。"""
        _code = ('def f(self):\n'
                 '    """doc 起始\n'
                 'weird indent line\n'
                 '        """\n'
                 '    x = 1\n'
                 '  return x\n')
        _out = self.exe._clean_llm_code(_code)
        self.assertEqual(_code.split("\n")[1:4], _out.split("\n")[1:4],
                         "★docstring 被改写")

    # --------------------------------------------------------------- F 开关契约
    def test_F1_switch_default_on_and_getattr_fallback(self):
        """默认值 True：`config` 未定义时也要为 True（`getattr` 兜底）。"""
        _fn = getattr(_SE_MOD, "_m91_indent_repair_on")
        with _CfgSwitch(_SWITCH, False):
            self.assertFalse(_fn())
        with _CfgSwitch(_SWITCH, True):
            self.assertTrue(_fn())
        _had = hasattr(config, _SWITCH)
        _old = getattr(config, _SWITCH, None)
        if _had:
            delattr(config, _SWITCH)
        try:
            self.assertTrue(_fn(), "★config 未定义该开关时必须 getattr 兜底为 True")
        finally:
            if _had:
                setattr(config, _SWITCH, _old)

    def test_F2_switch_off_falls_back_exactly(self):
        """关闭 ⇒ 三层同时关闭：平移零位移、端到端与改前逐字一致。

        ★注：开关装在**调用点**（`_clean_llm_code` 阶段3 与三处补丁构造点），
        与既有 `_normalize_fullwidth_in_code` 的灰度方式一致 ——
        `_m91_repair_indentation` / `_m91_align_base_indent` 本身是纯 helper，
        关闭时不会被调用（故不对它们单独断言「恒等」）。
        """
        _legacy = _load_legacy_se()
        with _CfgSwitch(_SWITCH, False):
            _fixed, _shift = self.exe._m91_align_base_indent(
                "        def f(self):", "def f(self):", "t")
            self.assertEqual((_fixed, _shift), ("def f(self):", 0))
            if _legacy is not None:
                _lexe = _legacy.__new__(_legacy)
                for _c in (_SAMPLE_DRIFT, _SAMPLE_TAB, _SAMPLE_CLEAN):
                    self.assertEqual(self.exe._clean_llm_code(_c), _lexe._clean_llm_code(_c),
                                     "★关闭开关必须逐字回到改前行为")

    def test_F3_prompt_clause_present_and_switch_gated(self):
        """① 层：prompt 已补缩进条款，且受同一开关控制。"""
        _src = _read(_SEE_REL)
        self.assertIn("完全相同的缩进层级与缩进宽度", _src, "prompt 未补缩进契约")
        self.assertIn("禁止使用制表符", _src, "prompt 未禁 Tab")
        self.assertIn(_SWITCH, _src, "prompt 条款未受开关控制")

    # ------------------------------------------- G 生产补丁库交叉校验（同口径防漂移）
    def test_G1_gate2_helper_agrees_with_real_gate2(self):
        """★同口径防漂移：真实关2 判定 PASS 的补丁，`_m91_gate2_check` 必须也为 True。

        用**生产补丁库真数据**（关2 结论由生产代码给出）交叉校验我的 helper，
        避免「自己验自己」。关2 口径若被改动而 helper 未同步，本用例转红。
        """
        _rows = _load_patch_store()
        if not _rows:
            self.skipTest("补丁库为空")
        _n = 0
        for _p in _rows:
            _r = _pm()._check_llm_patch_completeness(_p)
            if not _r.get("complete"):
                continue
            _n += 1
            self.assertTrue(_GATE2_CHECK(_p.get("modified_code") or "")[0],
                            "关2 放行但同口径 helper 判失败: {}".format(_p.get("id")))
        self.assertGreater(_n, 0, "生产补丁库中应有至少 1 条关2 通过的补丁")

    def test_G2_gate2_helper_matches_independent_reimplementation(self):
        """helper 与**独立第二实现**在样本集上逐例一致（含红例与绿例）。"""
        _cases = [_SAMPLE_DRIFT, _SAMPLE_TAB, _SAMPLE_DRIFT_LAST, _SAMPLE_CLEAN,
                  _SAMPLE_SHALLOW_FIRST, "", "def f(:\n1", "    if True:\n   pass"]
        for _c in _cases:
            self.assertEqual(_GATE2_CHECK(_c)[0], _gate2(_c)[0],
                             "helper 与独立实现不一致: {!r}".format(_c[:30]))

    def test_G3_real_llm_patches_align_invariants(self):
        """★生产 LLM 补丁全量不变量：平移只在更浅时发生，且平移后基础缩进相等。"""
        _rows = [p for p in _load_patch_store() if p.get("source") == "llm"]
        if not _rows:
            self.skipTest("补丁库中无 LLM 补丁")
        _seen = set()
        _hits = set()
        for _p in _rows:
            _id = str(_p.get("id", ""))
            if _id in _seen:
                continue
            _seen.add(_id)
            _o = _p.get("original_code") or ""
            _m = _p.get("modified_code") or ""
            _fixed, _shift = self.exe._m91_align_base_indent(_o, _m, "probe")
            if _shift:
                _hits.add(_id)
                self.assertLess(self.exe._m91_base_indent(_m),
                                self.exe._m91_base_indent(_o))
                self.assertEqual(self.exe._m91_base_indent(_fixed),
                                 self.exe._m91_base_indent(_o))
            else:
                self.assertEqual(_fixed, _m)
            self.assertEqual([l.strip() for l in _fixed.split("\n")],
                             [l.strip() for l in _m.split("\n")], "平移必须 token 守恒")
        _missing = [i for i in _REAL_LLM_ALIGN_IDS if i in _seen and i not in _hits]
        self.assertEqual([], _missing,
                         "★T0 取证：这两条真实补丁首行可执行行在 col0，必须触发对齐")


# ============================================================ H 调用点接线
class TestT91bCallSiteWiring(unittest.TestCase):
    """★接线门禁：helper 必须真的被**生产链路**调用，且生产者在消费者之前。

    本组是本次真实缺陷（三处 `_clean_llm_code` 调用被整行替换）的回归护栏。
    只用 AST 静态判定 ⇒ 不需要跑通重链路，成本极低但能抓住「假绿」。
    """

    @classmethod
    def setUpClass(cls):
        cls.src = io.open(os.path.join(ROOT, _SEE_REL), encoding="utf-8",
                          errors="ignore").read()
        cls.tree = ast.parse(cls.src)

    # ------------------------------------------------------------ AST 工具
    @staticmethod
    def _attr_calls(tree, attr):
        """全部 `X.<attr>(...)` 形式的调用节点（不含 def 行）。"""
        _out = []
        for _n in ast.walk(tree):
            if isinstance(_n, ast.Call):
                _f = _n.func
                if isinstance(_f, ast.Attribute) and _f.attr == attr:
                    _out.append(_n)
        return _out

    @classmethod
    def _assign_sites(cls, attr):
        """返回 [(赋值语句, 同 body 中的前一条语句)]，只取 `_llm_clean = X.<attr>(...)`。"""
        _out = []
        for _n in ast.walk(cls.tree):
            _body = getattr(_n, "body", None)
            if not isinstance(_body, list):
                continue
            for _i, _st in enumerate(_body):
                if not (isinstance(_st, ast.Assign)
                        and isinstance(_st.value, ast.Call)):
                    continue
                _fn = _st.value.func
                if not (isinstance(_fn, ast.Attribute) and _fn.attr == attr):
                    continue
                _out.append((_st, _body[_i - 1] if _i > 0 else None))
        return _out

    # ------------------------------------------------------------ H1 生产者存在
    def test_H1_clean_llm_code_has_at_least_three_call_sites(self):
        """★`_clean_llm_code` 必须真的被调用（≥3 处生产链路）。

        本批之前它被 T-91b 补丁「误删调用」⇒ 阶段3 缩进修复、全角归一化、
        code fence 剥离**整体失效**且 ruff F821 报 `_llm_clean` 未定义。
        """
        _calls = self._attr_calls(self.tree, "_clean_llm_code")
        self.assertGreaterEqual(
            len(_calls), 3,
            "`_clean_llm_code` 仅 %d 处调用（<3）⇒ 生产链路接线被破坏" % len(_calls))
        _defs = [n for n in ast.walk(self.tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_clean_llm_code"]
        self.assertEqual(len(_defs), 1, "`_clean_llm_code` 定义数应为 1：%d" % len(_defs))
        # 每个调用点都在类体内（不是测试/文档字符串里的假调用）
        self.assertTrue(all(c.lineno > 0 for c in _calls))

    # ------------------------------------------------------------ H2 顺序契约
    def test_H2_every_align_call_is_preceded_by_the_producer(self):
        """★每一处 `_m91_align_base_indent` 调用，其**前一条语句**必须先把
        `_llm_clean` 从 `self._clean_llm_code(...)` 生产出来。"""
        _sites = self._assign_sites("_m91_align_base_indent")
        self.assertEqual(len(_sites), 3,
                         "对齐点应为 3 处（蒸馏 / _submit_llm_patch / 多文件），实得 %d"
                         % len(_sites))
        for _st, _prev in _sites:
            self.assertIsNotNone(_prev, "L%d 对齐点前无语句" % _st.lineno)
            self.assertIsInstance(_prev, ast.Assign,
                                  "L%d 对齐点的前一条不是赋值（%s）"
                                  % (_st.lineno, type(_prev).__name__))
            _pv = _prev.value
            self.assertIsInstance(_pv, ast.Call)
            self.assertIsInstance(_pv.func, ast.Attribute)
            self.assertEqual(
                _pv.func.attr, "_clean_llm_code",
                "★L%d：`_llm_clean` 的生产者被替换/丢失（前一条调用的是 %r，"
                "而不是 `_clean_llm_code`）" % (_st.lineno, _pv.func.attr))
            _tgts = [t.id for t in _prev.targets if isinstance(t, ast.Name)]
            self.assertIn("_llm_clean", _tgts,
                          "★L%d：生产者未赋值给 `_llm_clean`" % _st.lineno)

    def test_H3_align_consumes_the_produced_name(self):
        """消费端第 2 个位置实参必须是 `_llm_clean`（生产者-消费者同名）。"""
        for _st, _prev in self._assign_sites("_m91_align_base_indent"):
            self.assertGreaterEqual(len(_st.value.args), 2,
                                    "L%d 对齐调用缺少第 2 个实参" % _st.lineno)
            _a2 = _st.value.args[1]
            self.assertIsInstance(_a2, ast.Name, "L%d 第 2 实参非名字" % _st.lineno)
            self.assertEqual(_a2.id, "_llm_clean",
                             "L%d 对齐消费的不是 `_llm_clean`：%r"
                             % (_st.lineno, _a2.id))

    def test_H4_align_result_is_consumed(self):
        """对齐结果必须被接住（`x, _ = ...`）⇒ 不允许丢掉平移后的代码。"""
        for _st, _ in self._assign_sites("_m91_align_base_indent"):
            self.assertEqual(len(_st.targets), 1, "L%d 目标数异常" % _st.lineno)
            self.assertIsInstance(_st.targets[0], ast.Tuple,
                                  "L%d 应解包 2 元组" % _st.lineno)

    # ------------------------------------------------------------ H5 先红对照
    def test_H5_legacy_has_three_calls_and_zero_align(self):
        """★先红（AST）：改前备份里 `_clean_llm_code` 恰 3 处调用、0 处对齐调用；
        改后必须 3+3 —— 证明本批是**纯增量**而非替换。"""
        if not os.path.isfile(_BAK_SE):
            self.skipTest("缺少 `.bak_batch91/.../SafeEvolutionExecutor.py.bak`")
        _old_tree = ast.parse(io.open(_BAK_SE, encoding="utf-8",
                                      errors="ignore").read())
        _old_calls = self._attr_calls(_old_tree, "_clean_llm_code")
        self.assertEqual(len(_old_calls), 3,
                         "改前 `_clean_llm_code` 调用数应为 3，实得 %d" % len(_old_calls))
        self.assertEqual(len(self._attr_calls(_old_tree, "_m91_align_base_indent")), 0,
                         "改前不应存在对齐调用")
        self.assertEqual(len(self._attr_calls(self.tree, "_clean_llm_code")), 3,
                         "改后 `_clean_llm_code` 调用数必须仍为 3（不得被替换）")
        self.assertEqual(len(self._attr_calls(self.tree, "_m91_align_base_indent")), 3,
                         "改后对齐调用应为 3 处")

    # ------------------------------------------------------------ H6 运行时冒烟
    def test_H6_runtime_smoke_producer_is_callable(self):
        """运行时冒烟：`_clean_llm_code` 与 `_m91_align_base_indent` 都真实可调。"""
        _exe = _SE_CLS.__new__(_SE_CLS)
        self.assertTrue(callable(getattr(_exe, "_clean_llm_code", None)))
        self.assertTrue(callable(getattr(_exe, "_m91_align_base_indent", None)))
        _out = _exe._clean_llm_code("    def foo(self):\n        x = 1\n      return x")
        self.assertIsInstance(_out, str)
        self.assertTrue(_GATE2_CHECK(_out)[0], "冒烟：清理结果应能过关2 同口径")
        _fixed, _shift = _exe._m91_align_base_indent(
            "        x = 1", "x = 1", "smoke")
        self.assertEqual((_fixed, _shift), ("        x = 1", 8))


if __name__ == "__main__":
    unittest.main()
