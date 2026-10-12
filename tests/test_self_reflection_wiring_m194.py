# -*- coding: utf-8 -*-
"""194刀2：SelfReflectionEngine 失败路接线隔离单测。

偏差：烛微S3.1补正(1)称「全仓grep仅本体文件+工厂函数命中」，但实测 main.py:3934
（SelfVerifier 验证失败路径）已是真实外部调用方 -> 前提被证伪（铁律109）。
故本刀不重复加生产接线（避免死代码），仅以隔离单测证明「失败信号驱动反思」行为
+ 外部消费方存在。

验收：外部消费方存在（main.py AST 断言）+ 隔离单测含失败信号驱动反思断言。
"""
import ast
import json
import os


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _main_calls_reflect_and_improve() -> bool:
    """AST 断言：main.py 中存在对 reflect_and_improve 的属性调用（外部消费方）。"""
    path = os.path.join(_REPO, "main.py")
    tree = ast.parse(open(path, "r", encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "reflect_and_improve":
                return True
    return False


def test_self_reflection_external_consumer_exists():
    """外部消费方存在断言：main.py 的 SelfVerifier 失败路径调用 reflect_and_improve。"""
    assert _main_calls_reflect_and_improve(), "main.py 中未找到 reflect_and_improve 外部调用"


def test_failure_signal_drives_reflection_with_llm():
    """失败信号（failed_patch + verification_detail）驱动反思，返回改进版补丁。"""
    from nucleus.evolution.SelfReflectionEngine import SelfReflectionEngine

    engine = SelfReflectionEngine(_REPO)
    fake = json.dumps({
        "root_cause": "返回None导致推理失败",
        "modified_code": "def foo():\n    return 'hi'\n",
    })
    engine._call_llm = lambda system, prompt, max_tokens=2048: fake

    failed = {
        "file": "organs/test.py", "method": "foo",
        "original_code": "def foo():\n    return 1\n",
        "modified_code": "def foo():\n    return None\n",
        "diff_summary": "改坏了",
    }
    verif = {"passed": False, "checks": {"basic_reasoning": False},
             "detail": {"basic_reasoning": "无答案"}}

    result = engine.reflect_and_improve(failed, verif, 80.0, 60.0)
    assert result is not None, "失败信号未驱动出反思结果"
    assert result.get("repair_source") == "llm_reflection"
    assert result.get("modified_code") == "def foo():\n    return 'hi'\n"
    assert result.get("original_code") == failed["original_code"]


def test_failure_signal_graceful_without_llm():
    """LLM 不可用时，失败信号路径优雅降级（返回 None，不抛异常）。"""
    from nucleus.evolution.SelfReflectionEngine import SelfReflectionEngine

    engine = SelfReflectionEngine(_REPO)
    engine._call_llm = lambda system, prompt, max_tokens=2048: None  # 模拟无 API

    failed = {
        "file": "organs/test.py", "method": "foo",
        "original_code": "def foo():\n    return 1\n",
        "modified_code": "def foo():\n    return None\n",
        "diff_summary": "改坏了",
    }
    verif = {"passed": False, "checks": {"basic_reasoning": False},
             "detail": {"basic_reasoning": "无答案"}}

    result = engine.reflect_and_improve(failed, verif, 80.0, 60.0)
    assert result is None, "无 LLM 时应优雅降级返回 None"
