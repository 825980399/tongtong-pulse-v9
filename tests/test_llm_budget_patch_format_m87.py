# -*- coding: utf-8 -*-
"""第87批门控测试：LLM 修复预算上调 + 补丁 file 字段修复 + 验证决策校验日志采样。

覆盖：
  T-87a  SafeEvolutionExecutor._LLM_REPAIR_MAX_TOKENS / _LLM_REPAIR_MIN_TIMEOUT 取值
  T-87b  非器官标签二级反查（真实源码块 exec）+ 补丁 file 必填校验 + PatchManager 告警上下文
  T-87c  VerificationLearningHub 不一致日志采样（首条必打 + 每 N 条一条）

全部离线、不触网、不写生产目录。
"""
import importlib
import io
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEE_PATH = os.path.join(_ROOT, "nucleus", "reasoning", "SafeEvolutionExecutor.py")
_PM_PATH = os.path.join(_ROOT, "nucleus", "reasoning", "PatchManager.py")
_VL_PATH = os.path.join(_ROOT, "nucleus", "mnemosyne", "verification_learning_hub.py")


def _src(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def _code_only(text):
    """剥离整行注释后计数（注释里的示例符号不算调用点）。"""
    return "\n".join(ln for ln in text.split("\n")
                     if not ln.lstrip().startswith("#"))


class TestT87aBudget(unittest.TestCase):
    """T-87a：推理模型预算与超时下限。"""

    def test_10_max_tokens_raised_to_16384(self):
        see = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
        self.assertGreaterEqual(
            see._LLM_REPAIR_MAX_TOKENS, 16384,
            "实测 reasoning_content≈2.4万字 会吃光 8192 预算，content 仍为空；"
            "必须提到 16384")

    def test_11_min_timeout_raised_to_180(self):
        see = importlib.import_module("nucleus.reasoning.SafeEvolutionExecutor")
        self.assertGreaterEqual(
            see._LLM_REPAIR_MIN_TIMEOUT, 180,
            "8192 档实测已耗时约 27s，16384 档更长；evolution 默认 30s 余量过紧")

    def test_12_payload_wired_to_constant(self):
        code = _code_only(_src(_SEE_PATH))
        self.assertEqual(code.count('"max_tokens": _LLM_REPAIR_MAX_TOKENS,'), 1)
        # 超时取「配置值与下限的较大者」，不改全局超时配置
        self.assertEqual(code.count("_LLM_REPAIR_MIN_TIMEOUT)"), 1)


class TestT87bFileResolve(unittest.TestCase):
    """T-87b：非器官标签二级反查 + 补丁 file 必填校验。"""

    def _extract_block(self):
        """从真实源码切出「非器官标签二级反查」块（源码删除即测试失败）。"""
        src = _src(_SEE_PATH)
        marker = "if self_inspector and _organ and not _issue.get(\"file\"):"
        start = src.find(marker)
        self.assertNotEqual(start, -1, "★未找到二级反查块（T-87b 未落地）")
        # 从 marker 所在行的行首起，按缩进匹配到块结束
        line_start = src.rfind("\n", 0, start) + 1
        lines = src[line_start:].split("\n")
        base_indent = len(lines[0]) - len(lines[0].lstrip())
        body = [lines[0]]
        for ln in lines[1:]:
            if ln.strip() and (len(ln) - len(ln.lstrip())) <= base_indent:
                break
            body.append(ln)
        return textwrap.dedent("\n".join(body))

    def test_20_block_exec_resolves_non_organ_tag(self):
        """真实执行反查块：PulseSnapshot 这类非器官类名能被解析出文件路径。"""
        block = self._extract_block()
        target = os.path.join(_ROOT, "nucleus", "mnemosyne", "PulseSnapshot.py")
        self.assertTrue(os.path.isfile(target), "探针目标文件应存在")

        class _FakeInspector:
            def resolve_organ_file(self, tag):        # 只覆盖 organs/ → 恒 None
                return None

            def _lookup_class_in_project(self, name):  # 全项目类索引
                if name == "PulseSnapshot":
                    return {"file_path": target}
                return None

        _issue = {"type": "Traceback", "organ": "PulseSnapshot", "method": ""}
        _ns = {
            "self_inspector": _FakeInspector(),
            "_organ": "PulseSnapshot",
            "_issue": _issue,
            "_file": "",
            "_resolved_file": "",
            "os": os,
            "_module_logger": _NullLogger(),
        }
        exec(compile(block, "<m87-block>", "exec"), _ns)   # noqa: S102
        self.assertEqual(_issue.get("file"), target,
                         "★非器官标签未被反查 ⇒ 补丁 file 为空 ⇒ 沙箱拒绝")

    def test_21_does_not_touch_private_file_vars(self):
        """口径红线：只回填 issue['file']，不动 _file / _resolved_file。"""
        block = self._extract_block()
        target = os.path.join(_ROOT, "nucleus", "mnemosyne", "PulseSnapshot.py")

        class _FakeInspector:
            def resolve_organ_file(self, tag):
                return None

            def _lookup_class_in_project(self, name):
                return {"file_path": target}

        _issue = {"organ": "PulseSnapshot"}
        _ns = {
            "self_inspector": _FakeInspector(), "_organ": "PulseSnapshot",
            "_issue": _issue, "_file": "", "_resolved_file": "",
            "os": os, "_module_logger": _NullLogger(),
        }
        exec(compile(block, "<m87-block>", "exec"), _ns)   # noqa: S102
        self.assertEqual(_ns["_file"], "", "_file 不得被回填（PHASE13 统计口径）")
        self.assertEqual(_ns["_resolved_file"], "",
                         "_resolved_file 不得被回填（影响代码片段获取路径）")

    def test_22_empty_file_guard_before_patch_build(self):
        """file 为空的补丁不再构造（避免注定被沙箱拒绝的无效验证）。"""
        code = _code_only(_src(_SEE_PATH))
        self.assertEqual(code.count("_llm_no_file = not str(_issue.get(\"file\", \"\") or \"\").strip()"), 1)
        # ★第88批 T-88a 扩展：构造条件在同一行追加了「素材非代码」守卫
        #   （... and not _llm_bad_material）。此处改为版本无关复算：
        #   仍必须恰好一次出现 _llm_no_file 闸门，且它位于构造条件内并以 `):` 收尾。
        # ★第90批 T-90a 再扩展：条件又追加了 `and not _llm_no_snippet` 闸门，
        #   原「`_i+80` 窗口内必见 `):`」的写法随条件换行而失效 ⇒
        #   改为先定位条件切片，再在切片内复算（不再依赖固定字符窗口）。
        _i = code.find("and not _llm_no_file")
        self.assertGreaterEqual(_i, 0, "构造条件必须含 _llm_no_file 闸门")
        self.assertEqual(code.count("and not _llm_no_file"), 1,
                         "_llm_no_file 闸门在构造条件里只应出现一次")
        _c0 = code.find("if (_llm_clean and _llm_clean.strip() != _snippet.strip()")
        self.assertGreaterEqual(_c0, 0, "找不到补丁构造条件起始")
        _c1 = code.find("):", _c0)
        self.assertGreater(_c1, _c0, "构造条件未以 `):` 收尾")
        self.assertIn("and not _llm_no_file", code[_c0:_c1],
                      "闸门必须位于构造条件之内")
        self.assertLess(code.find("_llm_no_file = not str("),
                        code.find('_llm_patch = {\n'),
                        "校验必须发生在补丁构造之前")

    def test_23_patchmanager_warning_has_context(self):
        """PatchManager 的沙箱拒绝告警必须带来源/类型/file 实值。"""
        pm = importlib.import_module("nucleus.reasoning.PatchManager")

        class _Mgr(pm.PatchManager):
            def __init__(self):                     # 绕过 __init__ 重依赖
                pass

        _m = _Mgr()
        _m._project_root = _ROOT
        _calls = []

        class _Cap:
            def warning(self, msg, *a, **k):
                _calls.append(msg)

        _pm_mod = sys.modules["nucleus.reasoning.PatchManager"]
        _old = getattr(_pm_mod, "_module_logger", None)
        _pm_mod._module_logger = _Cap()
        try:
            _r = _m._verify_in_copy({"id": "probe", "source": "llm",
                                     "issue_type": "Traceback", "method": "load"})
        finally:
            if _old is not None:
                _pm_mod._module_logger = _old
        self.assertFalse(_r.get("passed"))
        self.assertTrue(_calls, "应有沙箱拒绝告警")
        _joined = "\n".join(_calls)
        self.assertIn("source=", _joined, "★告警缺来源上下文（无法定位是哪类补丁）")
        self.assertIn("file=", _joined)
        self.assertIn("keys=", _joined)


class TestT87cLogSampling(unittest.TestCase):
    """T-87c：验证决策校验日志采样。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="m87_vl_")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self._logs = []

    def _hub(self):
        vl = importlib.import_module("nucleus.mnemosyne.verification_learning_hub")
        h = vl.VerificationLearningHub(
            file_path=os.path.join(self._tmp, "vl.json"),
            distill_threshold=10 ** 9)          # 不触发蒸馏
        h.should_verify = lambda *a, **k: False  # 恒判 False → 恒不一致
        h._log = lambda level, message, exc_info=False: self._logs.append(message)
        return h

    def _fill(self, h, n):
        for _ in range(n):
            h.record(organ="code_learner", task_type="exploration_audit",
                     input_summary="x", local_result={}, confidence=0.90,
                     relevance_score=0.70, needs_verification=True)

    def test_30_sampling_reduces_noise(self):
        h = self._hub()
        self._fill(h, 250)
        _mism = [m for m in self._logs if "[验证决策校验]" in m]
        _every = int(getattr(
            importlib.import_module("nucleus.mnemosyne.verification_learning_hub"),
            "_VL_MISMATCH_LOG_EVERY", 0))
        self.assertGreaterEqual(_every, 1, "★采样常量未落地（T-87c）")
        _expect = 1 + 250 // _every               # 首条必打 + 每 N 条一条
        self.assertEqual(len(_mism), _expect,
                         "采样条数不符：期望 %d，实际 %d" % (_expect, len(_mism)))
        self.assertLess(len(_mism), 250 * 0.1, "噪声下降应 > 90%")

    def test_31_counter_complete_and_no_logic_change(self):
        h = self._hub()
        self._fill(h, 250)
        self.assertEqual(getattr(h, "_vl_mismatch_total", 0), 250,
                         "不一致累计计数必须完整（采样只影响打印）")
        self.assertEqual(len(h._entries), 250, "写入流程不得被采样改变")

    def test_32_first_entry_always_logged(self):
        h = self._hub()
        self._fill(h, 1)
        _mism = [m for m in self._logs if "[验证决策校验]" in m]
        self.assertEqual(len(_mism), 1, "首条不一致必须打印（保留可观测性）")


class _NullLogger:
    def __getattr__(self, name):
        def _noop(*a, **k):
            return None
        return _noop


if __name__ == "__main__":
    unittest.main()
