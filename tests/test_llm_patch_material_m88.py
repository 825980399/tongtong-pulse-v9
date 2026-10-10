# -*- coding: utf-8 -*-
"""第88批 T-88a 门控测试：LLM 补丁「素材来源」纠正 + 非代码素材守卫。

根因（实测 2026-09-20 11:18:20）：
  `[补丁完整性检查失败: 与原文相似度过低(0.03 < 0.5)，疑似严重残缺/截断]`
  真因不是「LLM 生成残缺」，而是**素材取错**：
    - 日志类问题无 method ⇒ get_method_body 失败 ⇒ 走 _extract_comparison_material 兜底；
    - 兜底读的是 _file/_resolved_file（第87批受 PHASE13 口径约束未回填，仍为空）
      ⇒ 读不到文件 ⇒ 素材退化成 **158 字 ERROR 日志文本**；
    - 该文本被当作补丁的 original_code，而 modified_code 是 LLM 生成的**代码**
      ⇒ 两类文本不可比 ⇒ 相似度 0.03；
    - 即便绕过完整性关，PatchManager._verify_in_copy 的
      「original_code not in full_content」仍会拦下 ⇒ **结构性不可应用补丁**。
  实证：pending_patches.json 里 2 条 verify=True 的 LLM 补丁，其 original_code
  分别是 694 字真实方法体（ratio=0.5193）与 738 字真实函数代码（ratio=0.5774）
  —— **能过闸的补丁 original_code 全是真代码**。

本测试用「从真实源码切片 + exec」的方式测真实实现，源码回退即失败。
"""

import io
import os
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC_PATH = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_SRC = io.open(_SRC_PATH, encoding="utf-8").read()

# 真实目标文件（用于「素材必须是文件子串」的端到端断言）
_TARGET_REL = "nucleus/reasoning/PatchManager.py"
_TARGET_ABS = os.path.join(_ROOT, _TARGET_REL.replace("/", os.sep))


# ---------------------------------------------------------------- 源码切片

def _slice(start_marker, end_marker, occurrence=1):
    """从真实源码切出 [start_marker, end_marker] 之间（含两端）的文本。"""
    idx = -1
    for _ in range(occurrence):
        idx = _SRC.find(start_marker, idx + 1)
        if idx < 0:
            return ""
    end = _SRC.find(end_marker, idx)
    if end < 0:
        return ""
    return textwrap.dedent(_SRC[idx:end + len(end_marker)])


def _material_block():
    """素材提取块：从 `if _fallback_on:` 起，到素材日志行止。"""
    start = "                if _fallback_on:"
    end = "f\"来源={_snippet_source}\")"
    return _slice(start, end)


def _guard_block():
    """补丁构造守卫块：从 `_llm_no_file =` 起，到补丁字典构造止。

    注意：真实结构是 `if <条件>:` + 单条 `_llm_patch = {...}` 赋值语句。
    若只切到条件行，片段因缺少语句体而 IndentationError；故切片末端停在
    字典最后一项，由本函数补回闭合 `}`（缩进 4 列 = dedent 后的字典首行缩进），
    使片段语法完整、可 exec 且不含 verify_in_copy 等重依赖调用。
    """
    start = "                    _llm_no_file = not str(_issue.get(\"file\", \"\") or \"\").strip()"
    end = "\"source\": \"llm\","
    blk = _slice(start, end)
    if not blk:
        return ""
    return blk + "\n" + " " * 4 + "}"


class _FakeLogger:
    def __init__(self):
        self.debug_msgs = []
        self.warning_msgs = []

    def debug(self, msg, *a, **k):
        self.debug_msgs.append(str(msg) % a if a else str(msg))

    def warning(self, msg, *a, **k):
        self.warning_msgs.append(str(msg) % a if a else str(msg))

    def info(self, msg, *a, **k):
        pass


class _FakeSelf:
    """轻量 self：仅提供守卫块用到的 _generate_diff_summary。"""

    def _generate_diff_summary(self, a, b):
        return "diff:%d->%d" % (len(a), len(b))


class _Switch:
    """临时改写 config 上的一个属性（不存在则新增，退出时删除/还原）。"""

    def __init__(self, **kw):
        self._kw = kw
        self._old = {}

    def __enter__(self):
        import config
        for k, v in self._kw.items():
            self._old[k] = (hasattr(config, k), getattr(config, k, None))
            setattr(config, k, v)
        return self

    def __exit__(self, *exc):
        import config
        for k, (had, v) in self._old.items():
            if had:
                setattr(config, k, v)
            else:
                try:
                    delattr(config, k)
                except AttributeError:
                    pass
        return False


class _StubInspector:
    """假 self_inspector：get_method_body 恒失败（模拟「日志类问题无方法名」）。"""

    def get_method_body(self, organ, method):
        return None


class _StandIn:
    """承载被测真实方法的替身（用真实类的未绑定方法绑定到自身）。"""

    def __init__(self):
        self._project_root = _ROOT
        self._m88_inspector = _StubInspector()
        # ★必须绑定真实实现：素材块内部会调用这两个方法（且前者回调后者），
        #   若用替身，「素材必须是目标文件子串」的核心断言语义会落空。
        self._extract_comparison_material = _bind("_extract_comparison_material", self)
        self._read_snippet_from_file = _bind("_read_snippet_from_file", self)


def _bind(method_name, obj):
    from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
    return getattr(SafeEvolutionExecutor, method_name).__get__(obj, type(obj))


def _run_material_block(file_attr="", resolved_attr="", issue_file="",
                        method="", switch_on=True, issue_desc="ERROR: boom " + "x" * 140):
    """执行真实素材提取块，返回块内可见的命名空间。"""
    blk = _material_block()
    if not blk:
        raise AssertionError("素材块切片为空（源码结构与预期不符）")
    ns = {
        "_fallback_on": True,
        "_snippet": "",
        "_snippet_source": "",
        "_m88_src": "",
        "_m88_mat_on": False,
        "_issue": {"file": issue_file, "description": issue_desc},
        "_related_logs": "",
        "_file": file_attr,
        "_resolved_file": resolved_attr,
        "_method": method,
        "_organ": "PatchManager",
        "_type": "ERROR",
        "_loc_label": "PatchManager",
        "_module_logger": _FakeLogger(),
        "self": _StandIn(),
        "str": str,
        "len": len,
        "bool": bool,
        "type": type,
        "getattr": getattr,
        "Exception": Exception,
        "True": True,
        "False": False,
    }
    with _Switch(ENABLE_M88_MATERIAL_FROM_RESOLVED_FILE=switch_on):
        exec(compile(blk, "<m88-material-block>", "exec"), ns)   # noqa: S102
    return ns


def _run_guard_block(issue_file, src_label, switch_on=True, material="ERROR: boom " + "x" * 140):
    """执行真实守卫块，返回块内可见的命名空间。"""
    blk = _guard_block()
    if not blk:
        raise AssertionError("守卫块切片为空（源码结构与预期不符）")
    lg = _FakeLogger()
    ns = {
        "_issue": {"file": issue_file},
        "_organ": "PatchManager",
        "_method": "",
        "_type": "ERROR",
        "_loc_label": "PatchManager",
        "_snippet": material,
        "_llm_clean": "def fixed():\n    return 1\n",
        "_m88_mat_on": switch_on,
        "_m88_src": src_label,
        "_llm_patch": None,
        "_module_logger": lg,
        "self": _FakeSelf(),
        "str": str,
        "bool": bool,
        "len": len,
        "int": int,
        "abs": abs,
        "hash": hash,
        "__import__": __import__,
        "Exception": Exception,
        "True": True,
        "False": False,
    }
    exec(compile(blk, "<m88-guard-block>", "exec"), ns)   # noqa: S102
    ns["_logger_ref"] = lg
    return ns


# ---------------------------------------------------------------- T-88a 素材层

class TestT88aMaterialSource(unittest.TestCase):

    def test_10_resolved_issue_file_is_used_as_material(self):
        """★核心：_file/_resolved_file 为空时，必须用第87批已解析的 issue['file']。"""
        _real = io.open(_TARGET_ABS, encoding="utf-8", errors="ignore").read()
        ns = _run_material_block(file_attr="", resolved_attr="", issue_file=_TARGET_ABS)
        snip = ns["_snippet"]
        self.assertTrue(snip, "素材不得为空")
        self.assertIn(snip, _real,
                      "素材必须是目标文件里的真实代码（否则补丁契约不成立）")

    def test_11_switch_off_falls_back_to_batch87_behavior(self):
        """开关关闭：素材来源只用 _file/_resolved_file（逐字回到第87批）。"""
        ns = _run_material_block(file_attr="", resolved_attr="", issue_file=_TARGET_ABS,
                                 switch_on=False)
        self.assertEqual(ns["_m88_mat_on"], False)
        self.assertEqual(ns["_m88_src"], "")
        self.assertNotIn(ns["_snippet"],
                         io.open(_TARGET_ABS, encoding="utf-8", errors="ignore").read(),
                         "开关关闭时不得读取 issue['file']")

    def test_12_source_label_is_code_from_file(self):
        ns = _run_material_block(file_attr="", resolved_attr="", issue_file=_TARGET_ABS)
        self.assertEqual(ns["_m88_src"], "code_from_file")
        self.assertEqual(ns["_snippet_source"], "code_from_file")

    def test_13_source_label_is_error_message_when_no_file(self):
        """无文件可定位 ⇒ 素材只能是 ERROR 文本（供守卫识别）。"""
        ns = _run_material_block(file_attr="", resolved_attr="", issue_file="")
        self.assertEqual(ns["_m88_src"], "error_message")
        self.assertEqual(ns["_snippet_source"], "error_message")

    def test_14_log_line_carries_source_field(self):
        ns = _run_material_block(file_attr="", resolved_attr="", issue_file=_TARGET_ABS)
        _msgs = [m for m in ns["_module_logger"].debug_msgs if "方法体为空" in m]
        self.assertTrue(_msgs, "应打印素材来源日志")
        self.assertIn("来源=code_from_file", _msgs[-1])

    def test_15_does_not_touch_private_file_vars(self):
        """★PHASE13 口径红线：只读 issue['file']，绝不写回 _file/_resolved_file。"""
        import re
        _mb, _gb = _material_block(), _guard_block()
        self.assertTrue(_mb, "素材块切片为空（源码结构与预期不符）")
        self.assertTrue(_gb, "守卫块切片为空（源码结构与预期不符）")
        blk = _mb + "\n" + _gb
        # 只读组合允许存在（_m88_mat_file = _file or _resolved_file）
        self.assertIn("_m88_mat_file = _file or _resolved_file", blk)
        self.assertIn("_m88_mat_file = str(_issue.get(\"file\", \"\") or \"\")", blk)
        # 但绝不允许「以 _file/_resolved_file 为赋值目标」的语句
        for _pat in (r"(?m)^\s*_file\s*=(?!=)", r"(?m)^\s*_resolved_file\s*=(?!=)"):
            self.assertIsNone(
                re.search(_pat, blk),
                "第88批不得写回 {} 形态（会改动 PHASE13 统计口径）".format(_pat))


# ---------------------------------------------------------------- T-88a 守卫层

class TestT88aNonCodeMaterialGuard(unittest.TestCase):

    def test_20_guard_flag_defined(self):
        self.assertEqual(
            _SRC.count('_llm_bad_material = bool(_m88_mat_on and _m88_src == "error_message")'),
            1, "守卫标记必须存在且唯一")

    def test_21_build_condition_excludes_bad_material(self):
        # ★第90批 T-90a：构造条件新增 `and not _llm_no_snippet` 闸门后，
        #   原断言依赖「`_llm_bad_material):` 必须紧邻收尾」这一**脆弱文本模式**
        #   （任何后续增删一个守卫都会误报）。改为版本无关复算：
        #   在构造条件切片内该守卫恰好出现一次，且定义在条件之前。
        _start = "if (_llm_clean and _llm_clean.strip() != _snippet.strip()"
        _i = _SRC.find(_start)
        self.assertGreaterEqual(_i, 0, "找不到补丁构造条件起始：{}".format(_start))
        _j = _SRC.find("):", _i)
        self.assertGreater(_j, _i, "构造条件未以 `):` 收尾")
        _cond = _SRC[_i:_j]
        self.assertEqual(_cond.count("and not _llm_bad_material"), 1,
                         "补丁构造条件必须恰好一次排除「素材非代码」：{!r}".format(_cond))
        self.assertLess(_SRC.find("_llm_bad_material = bool("), _i,
                        "守卫必须在构造条件之前定义")

    def test_22_guard_skips_patch_when_material_is_not_code(self):
        """★已定位到文件、但素材仍退化为 ERROR 文本 ⇒ 不得构造补丁，且必须留痕。

        （file 已由第87批二级反查解析出来，但按该路径读不出代码 ⇒ 素材非代码。
          这是 0.03 现场的「file 非空 + 素材是日志文本」形态。）
        """
        ns = _run_guard_block(issue_file=_TARGET_ABS, src_label="error_message",
                              switch_on=True)
        self.assertIsNone(ns["_llm_patch"], "★素材非代码时不得构造注定失败的补丁")
        _w = ns["_logger_ref"].warning_msgs
        self.assertTrue(any("对比素材非代码" in m for m in _w),
                        "必须留痕说明「素材非代码」，不得伪装成「LLM 生成残缺」")
        self.assertFalse(any("问题未定位到文件" in m for m in _w),
                         "file 已定位 ⇒ 不得走第87批的 no_file 分支")

    def test_23_guard_inactive_when_switch_off(self):
        """开关关闭 ⇒ 守卫不生效（逐字回到第87批：仍会构造补丁）。"""
        ns = _run_guard_block(issue_file=_TARGET_ABS, src_label="error_message",
                              switch_on=False)
        self.assertIsNotNone(ns["_llm_patch"], "开关关闭时守卫必须完全不生效")
        self.assertEqual(ns["_logger_ref"].warning_msgs, [])

    def test_24_guard_allows_patch_when_material_is_code(self):
        """素材为代码 ⇒ 正常构造补丁（不得误伤）。"""
        ns = _run_guard_block(issue_file=_TARGET_ABS, src_label="code_from_file",
                              switch_on=True, material="def f():\n    return 1\n")
        self.assertIsNotNone(ns["_llm_patch"])
        self.assertEqual(ns["_llm_patch"]["original_code"], "def f():\n    return 1\n")

    def test_25_no_file_branch_still_takes_precedence(self):
        """第87批的「未定位到文件」分支优先级不变（elif 不得覆盖它）。"""
        ns = _run_guard_block(issue_file="", src_label="error_message", switch_on=True)
        self.assertTrue(any("问题未定位到文件" in m
                            for m in ns["_logger_ref"].warning_msgs),
                        "缺 file 时优先走第87批分支")


# ---------------------------------------------------------------- 端到端：补丁契约

class TestT88aPatchContract(unittest.TestCase):

    def test_30_material_is_substring_of_target_file(self):
        """★补丁契约：素材必须是目标文件的子串（_verify_in_copy 硬校验）。

        这条直接证明「修1 之后 original_code 满足 PatchManager 的
        `original_code not in full_content` 前置」，即补丁不再是结构性不可应用。
        """
        from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
        obj = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        obj._project_root = _ROOT
        _mat = _bind("_extract_comparison_material", obj)
        # 模拟修1 后的有效文件路径（第87批已解析出的 issue["file"]）
        out = _mat({"description": "ERROR: 与原文无关的日志文本 " + "z" * 60},
                   "", _TARGET_ABS, "")
        self.assertTrue(out, "有真实文件时素材不得为空")
        _real = io.open(_TARGET_ABS, encoding="utf-8", errors="ignore").read()
        self.assertIn(out, _real,
                      "★素材必须是目标文件子串，否则补丁 original_code 无法应用")
        # 反证：ERROR 文本作为素材时不是子串（这就是 0.03 的现场）
        _err = "ERROR: 与原文无关的日志文本 " + "z" * 60
        self.assertNotIn(_err, _real)

    def test_31_similarity_recovers_when_material_is_code(self):
        """素材为代码时，与「LLM 输出（同代码的小改版）」的相似度必然远超 0.5。"""
        import difflib

        from nucleus.reasoning.SafeEvolutionExecutor import SafeEvolutionExecutor
        obj = SafeEvolutionExecutor.__new__(SafeEvolutionExecutor)
        obj._project_root = _ROOT
        _mat = _bind("_extract_comparison_material", obj)
        _orig = _mat({"description": "x"}, "", _TARGET_ABS, "")
        _mod = _orig.replace("\n", "\n", 1) + "\n# llm appended\n"
        ratio = difflib.SequenceMatcher(None, _orig.strip(), _mod.strip()).ratio()
        self.assertGreater(ratio, 0.5, "同源代码的修订版相似度必须 > 0.5（关3 可过）")
        # 反证：ERROR 文本 vs 代码 —— 相似度极低（0.03 现场）
        _err = "ERROR: boom " + "x" * 140
        _code = "class A:\n    def f(self):\n        return 1\n" * 8
        self.assertLess(difflib.SequenceMatcher(None, _err.strip(), _code.strip()).ratio(),
                        0.5)


class TestT88aSwitchContract(unittest.TestCase):

    def test_40_switch_read_via_getattr_default_true(self):
        """灰度开关必须走 getattr 兜底默认 True，且不得写进 config.py。"""
        self.assertEqual(
            _SRC.count('getattr(\n                            _cfg_m88, '
                       '"ENABLE_M88_MATERIAL_FROM_RESOLVED_FILE", True))'), 1)
        _cfg = io.open(os.path.join(_ROOT, "config.py"), encoding="utf-8").read()
        self.assertNotIn("ENABLE_M88_MATERIAL_FROM_RESOLVED_FILE", _cfg,
                         "红线：不得改 config.py（开关仅内联默认值）")

    def test_41_guard_flag_is_switch_gated(self):
        """守卫必须与开关同闸：开关关闭 ⇒ 行为逐字回到第87批。"""
        self.assertIn("_m88_mat_on and _m88_src", _SRC)
        self.assertIn("_m88_mat_on = False", _SRC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
