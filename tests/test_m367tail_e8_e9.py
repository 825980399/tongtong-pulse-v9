#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E8 定位能力 + E9 JsonRepair 族日志降级 的回归用例（367 后续）。

背景（实测，不沿用票面假设）：
  · 46 个「全策略失败」独立事件 **100% 是 LLM 代码分析产物**（含「功能/关键步骤」字段，
    42/46 带 ```json fence），失败模式 = 缺逗号 33 / 缺冒号 12 / 非法转义 1；
  · 全部集中在 2026-09-15/16，此后零发生 ⇒ 票面「损坏 JSON 产物文件」不成立；
  · `JsonRepair:261` 日志 1053 条 vs 46 个真事件 = **22.9 倍重复**。
"""
from __future__ import annotations

import io
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nucleus.parsing import JsonRepair as jr  # noqa: E402

_JSON_REPAIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "nucleus", "parsing", "JsonRepair.py")


@pytest.fixture(autouse=True)
def _clean_failure_ctx():
    jr._LAST_PARSE_FAILURE.clear()
    yield
    jr._LAST_PARSE_FAILURE.clear()


class TestE8SourceContext:
    def test_01_backward_compatible_signature(self):
        """`source` 是可选参数：不传也能解析（唯一调用方之外不应被破坏）。"""
        obj, method = jr.parse_with_repair('{"a": 1}')
        assert obj == {"a": 1}
        assert method == "direct"

    def test_02_source_recorded_on_failure(self):
        """失败时把 source / length / head 记进 `_LAST_PARSE_FAILURE`。"""
        # 数组内两个字符串缺逗号：修复链**必须**救不回（见 test_10）
        bad = '{"关键步骤": ["a" "b"]}'
        obj, method = jr.parse_with_repair(bad, source="stomach.code_analysis")
        assert method == "none", "这条必须失败，否则说明语义篡改又回来了"
        assert jr._LAST_PARSE_FAILURE.get("source") == "stomach.code_analysis"
        assert jr._LAST_PARSE_FAILURE.get("length") == len(bad)
        assert jr._LAST_PARSE_FAILURE.get("head") == bad[:120]

    def test_03_source_default_empty_not_faked(self):
        """不传 source 时记空串（**不伪装成某个器官**）。"""
        obj, method = jr.parse_with_repair("{这不是 json")
        if method == "none":
            assert jr._LAST_PARSE_FAILURE.get("source") == ""

    def test_04_success_path_does_not_record(self):
        """成功路径不写失败上下文（否则会把上一拍的失败误当本次）。"""
        jr.parse_with_repair('{"ok": true}', source="x")
        assert "source" not in jr._LAST_PARSE_FAILURE


class TestE9LogDowngrade:
    def test_05_try_loads_failure_is_debug_not_warning(self, caplog):
        """`_try_loads` 失败按 **debug** 留痕（候选验证失败是控制流，不是异常事件）。"""
        with caplog.at_level(logging.DEBUG, logger="JsonRepair"):
            assert jr._try_loads("{坏掉的内容") is None
        recs = [r for r in caplog.records if "JSON解析失败" in r.getMessage()]
        assert recs, "失败必须留痕（降级≠静默）"
        assert all(r.levelno == logging.DEBUG for r in recs), \
            "级别必须是 DEBUG：%s" % [(r.levelname, r.getMessage()) for r in recs]

    def test_06_downgrade_is_not_silence(self):
        """降级后 handler 里**仍有** silent_exc 调用（铁律 156：不是静默降级）。"""
        src = io.open(_JSON_REPAIR, encoding="utf-8").read()
        start = src.index("def _try_loads")
        end = src.index("def repair_json_text")
        body = src[start:end]
        assert "silent_exc" in body, "except 体必须保留留痕调用"
        assert 'level="debug"' in body
        assert 'level="warning"' not in body, "不该还有 warning 级别的同一留痕"

    def test_07_final_failure_still_reported(self):
        """整次解析失败仍有上报（debug 级 [JSON定位刀]，带 source/长度/字符位）。"""
        src = io.open(_JSON_REPAIR, encoding="utf-8").read()
        start = src.index("[JSON定位刀] 修复失败")
        seg = src[start:start + 400]
        for token in ("source=", "前60字=", "长度=", "来源标记=", "字符位="):
            assert token in seg, "定位行缺字段：%s" % token

    def test_08_caller_passes_source(self):
        """唯一调用方显式传 source（否则 E8 的定位能力形同虚设）。"""
        stom = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "organs", "body", "PulseStomach.py")
        src = io.open(stom, encoding="utf-8").read()
        assert 'source="stomach.code_analysis"' in src

    def test_09_repair_still_works(self):
        """降级不影响修复能力：带 fence 的 JSON 仍能被救回。"""
        obj, method = jr.parse_with_repair('```json\n{"功能": "x", "关键步骤": ["a"]}\n```')
        assert obj is not None and obj.get("功能") == "x"
        assert method in ("extract", "repair")


class TestSemanticIntegrity:
    """★本刀施工中**新发现**的既有 bug：修复链会静默篡改语义。

     把「数组里两个字符串缺逗号」（）
    误判成「一个字符串含内部引号」，转义后合并成一个  ——
    元素个数从 2 变 1，且  让上游当成功用。
    这直接推翻 PulseStomach 原注释「不会引入语义错误结果」。
    """

    def test_10_no_element_merge(self):
        """数组内缺逗号**不得**被"修"成合并元素 —— 必须诚实地失败。"""
        obj, method = jr.parse_with_repair('{"关键步骤": ["a" "b"]}')
        assert method == "none", "宁可失败，不可把两个元素合并成一个：%r" % (obj,)

    def test_11_no_element_merge_multi(self):
        """三元素同理（防止只堵了两元素的特例）。"""
        obj, method = jr.parse_with_repair('{"x": ["1" "2" "3"]}')
        assert method == "none", "%r" % (obj,)

    def test_12_real_inner_quote_still_repaired(self):
        """真·内部引号仍要能修（收紧判据**不能**把能力一起砍掉）。"""
        obj, method = jr.parse_with_repair('{"k": "he said "hi" ok"}')
        assert method == "repair" and obj == {"k": 'he said "hi" ok'}

    def test_13_other_repairs_unaffected(self):
        """其余修复能力不受影响：fence / 尾随逗号 / 缺冒号。"""
        _fenced = "```json\n" + '{"a": 1}' + "\n```"
        assert jr.parse_with_repair(_fenced)[0] == {"a": 1}
        assert jr.parse_with_repair('{"a": 1,}')[0] == {"a": 1}
        assert jr.parse_with_repair('{"a" 1}')[0] == {"a": 1}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
