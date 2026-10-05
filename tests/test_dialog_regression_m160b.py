# -*- coding: utf-8 -*-
"""第160批 下上 刀6（对话回归自动化）验证测试。

覆盖：题集可加载、判定矩阵逻辑（泄漏/占位符/兜底误触发/合法正文/半自动挂旗）、
脚本 --selftest 通过。
"""
from __future__ import annotations

import os
import sys

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ not in sys.path:
    sys.path.insert(0, _PROJ)

_REG = os.path.join(_PROJ, "tools", "regression", "dialog_regression.py")
_YAML = os.path.join(_PROJ, "docs", "测试", "对话回归题集_20261004.yaml")

# 离线导入脚本模块（__main__ 保护下不会执行 main）
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("dialog_regression_m160b", _REG)
dialog_regression = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dialog_regression)


def test_yaml_loads():
    _data = dialog_regression.load_cases(_YAML)
    assert _data.get("cases"), "题集 cases 为空"
    assert len(_data["cases"]) >= 10, "题集题量过少: %d" % len(_data["cases"])


def test_leak_detected():
    _r = dialog_regression.judge_all(
        "种子1(综合): 内部内容 这是我第一次醒来 由8条相关知识归纳",
        {"auto": ["内部字段泄漏"]})
    assert _r["passed"] is False
    assert "内部字段泄漏" in _r["failed_cats"]


def test_placeholder_detected():
    _r = dialog_regression.judge_all(
        "参考 [BLUEPRINT_CONSTITUTION] 与 [核心智慧] 内容", {"auto": ["占位符"]})
    assert _r["passed"] is False
    assert "占位符" in _r["failed_cats"]


def test_fallback_misuse_detected():
    _r = dialog_regression.judge_all(
        "暂时给不出确定的答案，我再想想。", {"auto": ["兜底误触发"]})
    assert _r["passed"] is False
    assert "兜底误触发" in _r["failed_cats"]


def test_legit_text_passes_all():
    _r = dialog_regression.judge_all(
        "今天天气很好，我想出去走走。",
        {"auto": ["内部字段泄漏", "占位符", "裸格式", "兜底误触发", "身份", "超时"]})
    assert _r["passed"] is True, _r


def test_semi_auto_only_flags():
    _r = dialog_regression.judge_all(
        "一些回答", {"auto": ["身份"], "manual": ["答非所问", "实质内容"]})
    assert "答非所问" in _r["manual_flags"]
    assert "实质内容" in _r["manual_flags"]


def test_selftest_passes():
    _r = dialog_regression.run_selftest(_YAML)
    assert _r["selftest"] == "PASS"
    assert _r["cases"] >= 10
