# -*- coding: utf-8 -*-
"""470 批单测：刀1（对齐判据收紧）+ 刀2（观察窗口转延迟复验）。

覆盖的都是**实测踩到的形态**，不是想象出来的：
  · patch_llm_1789842716_c862 → modified 首行 `def _get_node_count(self):`（改名）
  · patch_llm_1790780348_b782 → modified 首行 `        def get_node_count(self):`（嵌套+改名）
  · patch_llm_1790248313_d881 → original 40 行 vs 方法 20 行（跨出边界）
  · patch_llm_1792659239_6fa4 → original 是 147 行方法的前 40 行（截断）

★判据必须挑「有判别力」的量：语法类判据（ast.parse 通过、方法总数不变）
  在**错误对齐**的补丁上也全部为真 —— 实测 c862 对齐后方法总数 78→78 不变，
  但目标方法 `_m70_kal_node_count` 已经不存在了。所以本文件一律用
  「目标方法是否还在」和「original 与 body 是否相等」这类结构性判据。
"""
import ast
import io
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.reasoning.SafeEvolutionExecutor import (  # noqa: E402
    SafeEvolutionExecutor,
    _m469_align_method_patch,
    _m470_body_span,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SRC = '''"""模块 docstring，不应被当作方法体。"""


class Demo:
    """类 docstring。"""

    def target(self, x):
        """方法 docstring，_read_method_body 会跳过它。"""
        try:
            return _kal().count(x)
        except Exception:
            return 0

    def other(self):
        return 1
'''

# 与 _read_method_body 同规则的方法体（跳过 docstring）
BODY = "        try:\n            return _kal().count(x)\n        except Exception:\n            return 0"


@pytest.fixture()
def demo(tmp_path):
    p = tmp_path / "demo_mod.py"
    p.write_text(SRC, encoding="utf-8")
    return str(p)


def _patch(fp, orig, mod, method="target", **kw):
    d = {"id": "p470", "file": fp, "method": method,
         "original_code": orig, "modified_code": mod}
    d.update(kw)
    return d


def _dedent_eq(a, b):
    return textwrap.dedent(a.replace("\r\n", "\n")).strip("\n") == \
        textwrap.dedent(b.replace("\r\n", "\n")).strip("\n")


class TestA_BodySpan:
    def test_A1_跳过docstring(self, demo):
        """body 起点必须跳过 docstring —— 与 _read_method_body 同规则。"""
        lines = io.open(demo, encoding="utf-8").read().split("\n")
        span = _m470_body_span(lines, "target")
        assert span is not None
        bs, be, ws, we, ind = span
        body = "\n".join(lines[bs:be])
        assert "方法 docstring" not in body, "没跳过 docstring"
        assert body.strip().startswith("try:"), "body 首行不是 try"
        assert ind == 4

    def test_A2_整方法区间含def(self, demo):
        lines = io.open(demo, encoding="utf-8").read().split("\n")
        bs, be, ws, we, _ind = _m470_body_span(lines, "target")
        assert lines[ws].strip().startswith("def target(")
        assert bs > ws, "body 起点必须晚于 def 行"

    def test_A3_同名不存在返回None(self, demo):
        lines = io.open(demo, encoding="utf-8").read().split("\n")
        assert _m470_body_span(lines, "no_such_method") is None


class TestB_AlignStrict:
    def test_B1_original等于方法体才对齐(self, demo):
        """original == body ⇒ 对齐；对齐后 original 变成整方法。"""
        whole = None
        lines = io.open(demo, encoding="utf-8").read().split("\n")
        bs, be, ws, we, _ = _m470_body_span(lines, "target")
        whole = "\n".join(lines[ws:we])
        mod = "def target(self, x):\n    return _kal().count(x) or 0"
        p = _patch(demo, BODY, mod)
        r = _m469_align_method_patch(p)
        assert r["how"] == "whole_method_aligned", r
        assert _dedent_eq(p["original_code"], whole), "original 没换成整方法"
        assert p.get("m469_granularity_aligned") is True
        assert p.get("m469_original_code_before") == BODY, "没留原值快照"

    def test_B2_original是片段必须拒绝(self, demo):
        """original 只是方法内一段（不是完整 body）⇒ 拒绝，不许扩成整方法。"""
        frag = "            return _kal().count(x)"
        mod = "def target(self, x):\n    return _kal().count(x) or 0"
        r = _m469_align_method_patch(_patch(demo, frag, mod))
        assert r["ok"] is False
        assert r["how"] in ("orig_fragment", "orig_mismatch"), r
        # 关键：拒绝时绝不能动补丁内容
        p = _patch(demo, frag, mod)
        _m469_align_method_patch(p)
        assert p["original_code"] == frag, "拒绝路径改了 original"

    def test_B3_modified改了方法名必须拒绝(self, demo):
        """modified 定义的是别的方法 ⇒ 拒绝（否则目标方法会消失）。"""
        mod = "def _get_count(self, x):\n    return 0"
        r = _m469_align_method_patch(_patch(demo, BODY, mod))
        assert r["ok"] is False
        assert r["how"] == "modified_other_method", r

    def test_B4_modified嵌套同名也拒绝(self, demo):
        """8 缩进的 `def get_count` 不是整方法 —— strip 后看起来像，但名字不符。"""
        mod = "        def get_count(self, x):\n            return 0"
        r = _m469_align_method_patch(_patch(demo, BODY, mod))
        assert r["ok"] is False
        assert r["how"] == "modified_other_method", r

    def test_B5_original已是整方法则as_is(self, demo):
        lines = io.open(demo, encoding="utf-8").read().split("\n")
        bs, be, ws, we, _ = _m470_body_span(lines, "target")
        whole = "\n".join(lines[ws:we])
        mod = "def target(self, x):\n    return 1"
        p = _patch(demo, whole, mod)
        r = _m469_align_method_patch(p)
        assert r["how"] == "as_is", r
        assert p["original_code"] == whole, "as_is 不应改 original"

    def test_B6_源码漂移拒绝(self, demo):
        """original 与当前方法体不等（源码已变）⇒ 拒绝，不按整方法替换。"""
        old = "        return _kal().count(x) + 1\n        # 旧版本没有 try"
        mod = "def target(self, x):\n    return 1"
        r = _m469_align_method_patch(_patch(demo, old, mod))
        assert r["ok"] is False, r
        assert r["how"] in ("orig_fragment", "orig_mismatch"), r

    def test_B7_对齐后目标方法仍在(self, demo):
        """★最有判别力的一条：对齐+替换后，目标方法必须还在，且新体非空。"""
        lines = io.open(demo, encoding="utf-8").read().split("\n")
        bs, be, ws, we, _ = _m470_body_span(lines, "target")
        mod = "def target(self, x):\n    return _kal().count(x) or -1"
        p = _patch(demo, BODY, mod)
        _m469_align_method_patch(p)
        src = io.open(demo, encoding="utf-8").read()
        cnt = src.count(p["original_code"])
        assert cnt == 1, "对齐后 original 在文件里出现 %d 次" % cnt
        after = src.replace(p["original_code"], p["modified_code"], 1)
        tree = ast.parse(after)
        names = [n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert "target" in names, "目标方法在对齐替换后消失"
        assert "other" in names, "同类的其他方法被误伤"
        node = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "target"][0]
        assert node.end_lineno - node.lineno + 1 >= 2, "新方法体是空的"


class TestC_WindowReverify:
    def _mk(self, elapsed, **kw):
        import time as _t
        d = {"id": "w470", "file": os.path.join(ROOT, "organs", "body", "PulseLiver.py"),
             "method": "_compress_group", "fixed_at": _t.time() - elapsed,
             "baseline_errors": 5, "applied": True}
        d.update(kw)
        return d

    def test_C1_窗口未到返回undecidable(self):
        ex = SafeEvolutionExecutor()
        r = ex.verify_fix_from_logs(self._mk(289))
        assert r["verified"] is False
        assert r.get("undecidable") is True, "窗口未到必须标 undecidable（否则被判 runtime_failed）"
        assert r.get("window_not_reached") is True
        assert 280 <= r.get("elapsed", 0) <= 300

    def test_C2_窗口已过正常判定(self):
        ex = SafeEvolutionExecutor()
        r = ex.verify_fix_from_logs(self._mk(400))
        assert r.get("window_not_reached") is None, "窗口已过却仍标 window_not_reached"
        assert r.get("undecidable") is False

    def test_C3_开关关掉回旧行为(self, monkeypatch):
        import config as _c
        monkeypatch.setattr(_c, "ENABLE_M470_WINDOW_REVERIFY", False, raising=False)
        ex = SafeEvolutionExecutor()
        r = ex.verify_fix_from_logs(self._mk(289))
        assert r["verified"] is False
        assert "undecidable" not in r, "关掉开关后仍带 undecidable"
        assert "window_not_reached" not in r

    def test_C4_复验上限读取(self):
        from nucleus.reasoning.SafeEvolutionExecutor import _m470_max_reverify
        assert _m470_max_reverify() >= 1


class TestD_ConfigRegistration:
    def test_D1_三键齐全且默认开(self):
        import config as c
        assert getattr(c, "ENABLE_M470_ALIGN_STRICT", None) is True
        assert getattr(c, "ENABLE_M470_WINDOW_REVERIFY", None) is True
        assert int(getattr(c, "M470_MAX_REVERIFY", 0)) >= 1

    def test_D2_配置块有读取点注释(self):
        """配置项必须写明读取点，否则下一个人不知道该关哪个开关。"""
        src = io.open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()
        blk = src[src.index("[M470-CFG]"):]
        assert "_m470_align_strict_on" in blk
        assert "_m470_window_reverify_on" in blk
        assert "_m470_max_reverify" in blk


class TestE_Wiring:
    def test_E1_判据在替换段之前(self):
        """关系判据必须在「回填 modified 缩进」之前 —— 否则已经改过补丁了。"""
        src = io.open(os.path.join(ROOT, "nucleus", "reasoning",
                                   "SafeEvolutionExecutor.py"), encoding="utf-8").read()
        i_judge = src.index("★470刀1：**先判关系，再决定要不要对齐**")
        i_write = src.index('patch["original_code"] = _whole')
        assert i_judge < i_write, "判据在写回之后执行"

    def test_E2_早退分支带window_not_reached(self):
        src = io.open(os.path.join(ROOT, "nucleus", "reasoning",
                                   "SafeEvolutionExecutor.py"), encoding="utf-8").read()
        seg = src[src.index("if _elapsed < 300:"):]
        seg = seg[:seg.index("_after = self._count_errors_for_location")]
        assert "undecidable" in seg, "早退分支没有 undecidable"
        assert "window_not_reached" in seg
