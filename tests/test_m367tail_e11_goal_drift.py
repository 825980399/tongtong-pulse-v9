#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E11 学习目标漂移 的回归用例（367 后续）。

病根（实测铁证）：
  · 能力画像 `SelfAwarenessEngine` 产出 label = **"代码"**（`code_health`）；
  · `PulseInnerWorld._get_path_label_for_plan` 的知识标签表是 `{"/技术": "技术架构与编程", ...}`；
  · 两套词表**无交集** ⇒ `"代码" in label` 命中 **0/7** ⇒ `nodes_after` 恒 0
    ⇒ 必判「效果不佳」⇒ 3 次后强制切换，切走又切回（日志 10-24 21:48 / 23:47 两次）。

另修两处同段 bug：无样本被当 0% 成功率、`for word in label:` 单字匹配。
"""
from __future__ import annotations

import ast
import io
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from organs.brain.PulseInnerWorld import (  # noqa: E402
    _GOAL_AREA_PATH_PREFIXES, goal_area_path_prefixes,
)

_SRC_PATH = os.path.join(_ROOT, "organs", "brain", "PulseInnerWorld.py")


def _iter_name(for_node):
    """取 for X in <name>: 里的 <name>（不是 Name 则返回空串）。"""
    it = for_node.iter
    return getattr(it, "id", "") if isinstance(it, ast.Name) else ""


def _src():
    return io.open(_SRC_PATH, encoding="utf-8").read()


def _path_labels_from_source():
    """从源码里把 `_get_path_label_for_plan` 的 labels 字典解析出来（不靠硬编码）。"""
    tree = ast.parse(_src())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_get_path_label_for_plan":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and getattr(sub.targets[0], "id", "") == "labels":
                    return ast.literal_eval(sub.value)
    raise AssertionError("没找到 _get_path_label_for_plan 的 labels 字典")


class TestAliasMapping:
    def test_01_code_maps_to_tech_path(self):
        """铁证项：「代码」必须映射到 `/技术`（原来命中 0 条 ⇒ 恒判失败）。"""
        assert goal_area_path_prefixes("代码") == ("/技术",)

    def test_02_other_goal_names_map(self):
        """其余能力画像领域名的映射（覆盖 SelfAwarenessEngine 现有 label 集）。"""
        assert goal_area_path_prefixes("反思") == ("/反思",)
        assert goal_area_path_prefixes("身份") == ("/身份",)
        assert goal_area_path_prefixes("广泛涉猎") == ("/未分类",)

    def test_03_unknown_returns_empty_not_failure(self):
        """映射不到 ⇒ 空元组（调用方回退原判据），**绝不当成失败**。"""
        assert goal_area_path_prefixes("完全不认识的领域") == ()
        assert goal_area_path_prefixes("") == ()
        assert goal_area_path_prefixes(None) == ()

    def test_04_containment_longest_key_wins(self):
        """含关系兜底：「代码质量」也要命中 /技术。"""
        assert goal_area_path_prefixes("代码质量") == ("/技术",)

    def test_05_symmetry_with_path_labels(self):
        """★核心：知识路径标签表的**每条路径**都必须有别名键能映射到。

        这条是防「词表再次分叉」的守门员 —— 原文就是两套词表各说各话，
        而故障是**延时显形**（3 个评估周期、约 1 小时才切一次），
        没有这条测试就只能等日志漂移才发现。
        """
        labels = _path_labels_from_source()
        covered = set()
        for v in _GOAL_AREA_PATH_PREFIXES.values():
            covered.update(v)
        missing = [p for p in labels if p not in covered]
        assert not missing, "这些知识路径没有任何领域别名能命中：%s" % missing

    def test_06_alias_values_are_real_paths(self):
        """别名表的值必须是**真实存在**的知识路径（防手写错路径）。"""
        labels = _path_labels_from_source()
        bad = [v for vals in _GOAL_AREA_PATH_PREFIXES.values() for v in vals if v not in labels]
        assert not bad, "别名指向了不存在的知识路径：%s" % bad


class TestCriteriaFixes:
    def test_07_nodes_after_uses_alias(self):
        """nodes_after 计算必须走别名分支（而不是只留一个未使用的函数）。"""
        src = _src()
        seg = src[src.index("path_dist = self.node_pool.get_path_distribution()"):
                  src.index("nodes_before = latest_plan.get")]
        assert "goal_area_path_prefixes" in seg, "别名函数没被接到 nodes_after 计算上"
        assert "startswith(_p)" in seg

    def test_08_no_sample_guard_exists(self):
        """失败分支必须先挡「无样本」（无样本 ≠ 零成功率）。"""
        src = _src()
        assert "_has_sample" in src
        seg = src[src.index("if not _has_sample:"):]
        assert "不计失败" in seg[:400], "无样本分支必须显式说明不计失败"

    def test_09_no_per_char_matching(self):
        """单字匹配必须已消除（会因「与」「的」等单字误命中）。

        ★用 AST 而不是文本包含判定：本函数上方的修复注释里就写着那条 for 语句，
        文本检查会被自己的注释命中（铁律 227 同款坑）。
        """
        tree = ast.parse(_src())
        bad = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.For) and _iter_name(n) == "label"]
        assert not bad, "仍有 for-x-in-label 单字遍历：行 %s" % bad

    def test_10_sample_rate_still_computed(self):
        """有样本时仍照常算成功率（不能因为加了无样本判断就退化成不统计）。"""
        src = _src()
        assert "success_count = sum(1 for t in area_traces" in src
        assert "success_rate = success_count / len(area_traces)" in src


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
